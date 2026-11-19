"""Instagram source + client Inbox service.

One pass (every minute) does, per Instagram account (= per IP group), at most ONE
paced action, in this order of urgency:
  1. a due profile check (finds FRESH posts first),
  2. download a fresh post,
  3. discover one older page (only when the ready buffer is low),
  4. download an older post (daily cap + buffer limits).
Each download: best quality → quality gate → deep clean + DriveCast tag →
upload to the channel's Drive folder → ledger 'in_drive'. The phone copy is
removed a few hours after the post is live.

Bulkhead: a problem stops only its scope (one post, one source, one IG account).
"""
import logging
import os
import random
import re
import secrets
import shutil
import threading
import time

from . import db
from . import cleaner, config, drive, store, titles
from . import instagram as ig
from .errors import CodedError, info as err_info
from .net import DriveCastError, session_for

log = logging.getLogger("drivecast.igsync")
LOCAL_KEEP = 6 * 3600          # phone copy kept this long after the post is live
ACCOUNT_DAILY = 160            # Instagram actions per test account per day
CHECK_MIN, CHECK_MAX = 45 * 60, 90 * 60
_tick_lock = threading.Lock()
_next_action = {}              # account id -> earliest next action time (in memory, randomised)
_clients = {}                  # account id -> (cookies_enc, Client)


def VERSION():
    from . import VERSION as v
    return v


# ------------------------------------------------------------------ helpers
def new_ref():
    while True:
        r = "DC-" + "".join(secrets.choice("ABCDEFGHJKLMNPQRSTUVWXYZ23456789") for _ in range(6))
        if not db.one("SELECT 1 FROM media WHERE ref=?", (r,)):
            return r


def media_root():
    return getattr(config, "MEDIA_DIR", None) or os.path.join(config.DATA_DIR, "media")


def channel_dir(ch):
    name = re.sub(r'[\\/:*?"<>|]+', "_", (ch.get("folder_name") or ch["title"] or f"channel-{ch['id']}")).strip() or f"channel-{ch['id']}"
    d = os.path.join(media_root(), name)
    os.makedirs(d, exist_ok=True)
    nm = os.path.join(media_root(), ".nomedia")
    if not os.path.exists(nm):
        try:
            open(nm, "w").close()
        except OSError:
            pass
    return d


def _today():
    return time.strftime("%Y-%m-%d", time.gmtime())


def _bump_day(table, row):
    day = _today()
    if row.get("day") != day:
        db.run(f"UPDATE {table} SET day=?, day_count=0 WHERE id=?", (day, row["id"]))
        row["day"], row["day_count"] = day, 0
    return row


def account_for_group(gid):
    return db.one("SELECT * FROM ig_accounts WHERE group_id=?", (gid,))


def client_for(acc, group):
    from . import security
    cached = _clients.get(acc["id"])
    if cached and cached[0] == (acc["cookies_enc"], acc.get("proxy_mode")):
        return cached[1]
    cookies = ig.parse_cookies(security.unseal(acc["cookies_enc"]))
    sess = session_for(None if acc.get("proxy_mode") == "phone" else group)  # reading only; posting never skips the proxy
    c = ig.Client(sess, cookies, user_agent=acc.get("user_agent"), language=acc.get("language") or lang_for(group))
    _clients[acc["id"]] = ((acc["cookies_enc"], acc.get("proxy_mode")), c)
    return c


LANGS = {"US": "en-US,en;q=0.9", "GB": "en-GB,en;q=0.9", "CA": "en-CA,en;q=0.9,fr-CA;q=0.6", "AU": "en-AU,en;q=0.9",
         "DE": "de-DE,de;q=0.9,en;q=0.7", "FR": "fr-FR,fr;q=0.9,en;q=0.7", "PK": "en-PK,en;q=0.9,ur;q=0.7", "AE": "en-AE,en;q=0.9,ar;q=0.6"}


def lang_for(group):
    return LANGS.get((group or {}).get("country") or "", "en-US,en;q=0.9")


def forget_client(acc_id):
    _clients.pop(acc_id, None)


def _ready_target(ch):
    plan = db.jload(ch["plan_json"], {})
    return min(24, max(4, int(plan.get("posts_per_day") or 2) * 6))


def ready_count(ch_id):
    """Videos really kept ready (downloaded or in Drive). Items only *found* on the profile are not ready yet."""
    return db.one("""SELECT COUNT(*) n FROM media WHERE channel_id=? AND kind='video'
                     AND state IN ('downloading','cleaned','in_drive')""", (ch_id,))["n"]


# ------------------------------------------------------------------ error handling (scopes)
def _account_problem(acc, e, group):
    now = time.time()
    if e.code == "IG_RATE_LIMIT":
        n = min(6, (acc["backoff"] or 0) + 1)
        until = now + min(12 * 3600, 1800 * (2 ** (n - 1)))
        db.run("UPDATE ig_accounts SET status='resting', paused_until=?, backoff=?, error_code=?, error_detail=?, updated_at=? WHERE id=?",
               (until, n, e.code, e.detail, now, acc["id"]))
        _rest_same_account(acc, until)
        msg = err_info(e.code, group=group["number"], until=time.strftime("%H:%M UTC", time.gmtime(until)))
        db.event("warn", "instagram", msg["message"], acc["workspace_id"], None, e.code, e.detail)
    elif e.code == "IG_FEEDBACK":
        db.run("UPDATE ig_accounts SET status='resting', paused_until=?, error_code=?, error_detail=?, updated_at=? WHERE id=?",
               (now + 6 * 3600, e.code, e.detail, now, acc["id"]))
        db.event("warn", "instagram", err_info(e.code, group=group["number"])["message"], acc["workspace_id"], None, e.code, e.detail)
    elif e.code in ("IG_COOKIES_EXPIRED", "IG_CHALLENGE"):
        db.run("UPDATE ig_accounts SET status='needs_cookies', error_code=?, error_detail=?, updated_at=? WHERE id=?",
               (e.code, e.detail, now, acc["id"]))
        forget_client(acc["id"])
        db.event("error", "instagram", err_info(e.code, group=group["number"])["message"], acc["workspace_id"], None, e.code,
                 f"group_id={group['id']}\n{e.detail}")
    elif e.code == "PROXY_DEAD":
        db.run("UPDATE ig_accounts SET paused_until=?, updated_at=? WHERE id=?", (now + 900, now, acc["id"]))
    else:
        db.run("UPDATE ig_accounts SET paused_until=?, error_code=?, error_detail=?, updated_at=? WHERE id=?",
               (now + 1800, e.code, e.detail, now, acc["id"]))


def _source_problem(src, ch, e):
    now = time.time()
    hold = {"IG_HOME_REDIRECT": 3 * 3600, "IG_UNKNOWN": 3600}.get(e.code)
    if hold:
        db.run("UPDATE sources SET next_check=?, error_code=?, error_detail=?, updated_at=? WHERE id=?",
               (now + hold, e.code, e.detail, now, src["id"]))
    else:  # needs the owner (not found / private)
        db.run("UPDATE sources SET next_check=?, error_code=?, error_detail=?, updated_at=? WHERE id=?",
               (now + 12 * 3600, e.code, e.detail, now, src["id"]))
    db.event("warn" if hold else "error", "instagram", f"{ch['title']}: " + err_info(e.code, handle=src["handle"])["message"],
             ch["workspace_id"], ch["id"], e.code, e.detail)


# ------------------------------------------------------------------ discovery
def _wanted(item, content_type):
    if content_type == "videos":
        return item["kind"] == "video"
    if content_type == "photos":
        return item["kind"] in ("photo", "carousel") and bool(item["images"])
    return item["kind"] == "video" or bool(item["images"])


def _insert(src, ch, item, lane):
    now = time.time()
    kind = item["kind"] if item["kind"] == "video" else ("carousel" if item["kind"] == "carousel" else "photo")
    try:
        with db.tx() as c:
            c.execute("""INSERT INTO media(workspace_id,channel_id,source_id,origin,ext_id,code,kind,lane,taken_at,caption,
                         has_audio,duration,state,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,'found',?,?)""",
                      (ch["workspace_id"], ch["id"], src["id"], "instagram", item["pk"], item["code"], kind, lane,
                       item["taken_at"], item["caption"], 1 if item.get("has_audio") else None, item.get("duration"), now, now))
        return True
    except Exception:  # UNIQUE: already in the ledger — never twice
        return False


def check_source(src, ch, cl):
    """Profile check. New posts after the source started are FRESH."""
    now = time.time()
    uid = src["ig_user_id"]
    if not uid:
        p = cl.profile(src["handle"])
        if p["is_private"] and not p["followed"]:
            raise CodedError("IG_PROFILE_PRIVATE", "", handle=src["handle"])
        uid = p["id"]
        db.run("UPDATE sources SET ig_user_id=?, full_name=?, picture=? WHERE id=?", (uid, p["full_name"], p["picture"], src["id"]))
    items, cursor = cl.feed(uid, handle=src["handle"])
    fresh = 0
    newest = src["newest_seen"] or 0
    first = not db.one("SELECT 1 FROM media WHERE source_id=?", (src["id"],))
    for it in items:
        if not it["pk"] or not _wanted(it, src["content_type"]):
            continue
        t = it["taken_at"] or 0
        if t > src["started_at"]:
            fresh += _insert(src, ch, it, "fresh")
        elif src["include_old"]:
            _insert(src, ch, it, "backfill")
        newest = max(newest, t)
    if first and not fresh:
        fresh += _kickoff(src, ch, items)
    upd = {"newest_seen": newest, "last_check": now, "error_code": None, "error_detail": None,
           "next_check": now + random.uniform(CHECK_MIN, CHECK_MAX) * (1.5 if time.gmtime(now).tm_hour in range(20, 24) else 1)}
    if src["include_old"] and not src["backfill_cursor"] and not src["backfill_done"]:
        upd["backfill_cursor"] = cursor or ""
        if not cursor:
            upd["backfill_done"] = 1
    sets = ", ".join(f"{k}=?" for k in upd)
    db.run(f"UPDATE sources SET {sets}, total_found=(SELECT COUNT(*) FROM media WHERE source_id=?), updated_at=? WHERE id=?",
           list(upd.values()) + [src["id"], now, src["id"]])
    if fresh:
        db.event("info", "instagram", f"{ch['title']}: {fresh} new post(s) on Instagram — downloading now.", ch["workspace_id"], ch["id"])
    db.resolve("IG_HOME_REDIRECT", ch["workspace_id"], ch["id"])
    return fresh


def _kickoff(src, ch, items):
    """A brand-new client starts with its most recent post, even if it is older than the moment it was added:
    Drive is never empty on day one. Everything else follows the normal rules."""
    wanted = [it for it in items if it["pk"] and _wanted(it, src["content_type"])]
    if not wanted:
        return 0
    top = max(wanted, key=lambda it: it["taken_at"] or 0)
    _insert(src, ch, top, "fresh")
    db.run("UPDATE media SET lane='fresh' WHERE source_id=? AND ext_id=? AND state='found'", (src["id"], top["pk"]))
    return 1


def discover_older(src, ch, cl):
    items, cursor = cl.feed(src["ig_user_id"], src["backfill_cursor"] or None, handle=src["handle"])
    for it in items:
        if it["pk"] and _wanted(it, src["content_type"]):
            _insert(src, ch, it, "fresh" if (it["taken_at"] or 0) > src["started_at"] else "backfill")
    db.run("UPDATE sources SET backfill_cursor=?, backfill_done=?, total_found=(SELECT COUNT(*) FROM media WHERE source_id=?), updated_at=? WHERE id=?",
           (cursor or "", 0 if cursor else 1, src["id"], time.time(), src["id"]))


# ------------------------------------------------------------------ live progress (what the person sees)
def sync_progress(ch_id, title="First video"):
    """One honest 0-100 bar for the whole trip: Instagram → phone → clean → Google Drive."""
    from . import worker

    def p(stage, f=None):
        pct = None
        if stage.startswith("Downloading"):
            pct = 0.05 + 0.35 * f if f is not None else 0.05
        elif stage.startswith("Removing"):
            pct = 0.45
        elif stage.startswith("Saving"):
            pct = 0.5 + 0.48 * (f or 0)
        elif stage.startswith("Saved"):
            pct = 1.0
        worker._stage(ch_id, stage, pct, title)
        worker.PROGRESS[ch_id]["by"] = "sync"
    return p


def sync_clear(ch_id):
    from . import worker
    if (worker.PROGRESS.get(ch_id) or {}).get("by") == "sync":
        worker.PROGRESS.pop(ch_id, None)


def _want_key(ch_id):
    return f"postwhenready:{ch_id}"


def want_post(ch_id):
    """The person pressed Post now while nothing was ready: post the moment the first video reaches Drive."""
    db.meta_set(_want_key(ch_id), str(time.time()))


def _after_ready(ch):
    from . import scheduler
    from . import worker
    raw = db.meta_get(_want_key(ch["id"]))
    if not raw:
        return
    db.run("DELETE FROM meta WHERE key=?", (_want_key(ch["id"]),))
    if time.time() - float(raw) > 6 * 3600:
        return
    fresh = store.channel(ch["id"])
    if fresh and fresh["state"] == "live" and scheduler.post_now(fresh):
        worker.PROGRESS.pop(ch["id"], None)  # the poster takes over with its own bar


def pending_kickoff(ch_id):
    return bool(db.one("""SELECT 1 FROM sources WHERE channel_id=? AND state='active' AND kind='instagram' AND last_check IS NULL""", (ch_id,))
                or db.one("SELECT 1 FROM media WHERE channel_id=? AND lane='fresh' AND state IN ('found','downloading')", (ch_id,)))


def kick(ch_id):
    """Start right now (no waiting for the next minute): check the profile, take the latest video, put it in Drive."""
    for a in db.all("SELECT id FROM ig_accounts"):
        _next_action.pop(a["id"], None)
    db.run("UPDATE sources SET next_check=0, error_code=NULL WHERE channel_id=? AND state='active' AND kind='instagram'", (ch_id,))

    if os.environ.get("DC_NO_KICK"):  # tests drive the passes themselves
        return

    def run():
        for _ in range(10):
            try:
                tick(wait=30)
            except Exception:
                log.exception("kick pass failed")
            if not pending_kickoff(ch_id):
                break
            time.sleep(6)
    threading.Thread(target=run, name="ig-kick", daemon=True).start()


# ------------------------------------------------------------------ download → clean → Drive
def _drive_ctx(ch, group):
    ws = store.workspace(ch["workspace_id"])
    if not ws.get("drive_token_enc"):
        raise CodedError("DRIVE_NOT_CONNECTED")
    if not ch.get("folder_id"):
        raise CodedError("DRIVE_FOLDER_GONE")
    return session_for(group), store.drive_box(ws)


def process(m, src, ch, cl, group, progress=None):
    """Download one ledger item and put its clean copy in Drive."""
    now = time.time()
    db.run("UPDATE media SET state='downloading', attempts=attempts+1, updated_at=? WHERE id=?", (now, m["id"]))
    folder = channel_dir(ch)
    ref = m["ref"] or new_ref()
    db.run("UPDATE media SET ref=? WHERE id=?", (ref, m["id"]))
    if progress:
        progress("Downloading from Instagram", None)
    item = cl.media(m["ext_id"], handle=src["handle"])
    used = [r["title"] for r in db.all("SELECT title FROM media WHERE channel_id=? AND title IS NOT NULL", (ch["id"],))]
    title = titles.choose(caption=item["caption"], channel_title=ch["title"],
                          bank=db.jload(ch.get("titles_json"), []), used=used, seed=ref)
    tmpdir = os.path.join(folder, ".work-" + ref)
    os.makedirs(tmpdir, exist_ok=True)
    try:
        dsess, dbox = _drive_ctx(ch, group)
        props = {"dc_ref": ref, "dc_media": m["id"], "dc_lane": m["lane"], "dc_taken": int(m["taken_at"] or 0), "dc_src": "instagram"}
        if m["kind"] == "video":
            plan = ig.best_video_plan(item)
            if not plan:
                raise CodedError("IG_MEDIA_GONE", "no video in post")
            v = os.path.join(tmpdir, "v.mp4")
            cl.download(plan["video"], v, progress and (lambda n, total=None: progress("Downloading from Instagram", (n / total) if total else None)))
            raw = v
            if plan["mode"] == "dash" and plan.get("audio"):
                a = os.path.join(tmpdir, "a.mp4")
                cl.download(plan["audio"], a)
                raw = cleaner.merge(v, a, os.path.join(tmpdir, "m.mp4"))
            store_video(m, ch, raw, title, item["caption"], folder, ref, dsess, dbox, props, progress)
        else:
            photos = drive.child_folder(dsess, dbox, ch["folder_id"], "Photos")
            safe = re.sub(r'[\\/:*?"<>|]+', " ", title)[:70].strip()
            ids = []
            for i, img in enumerate(item["images"], 1):
                raw = os.path.join(tmpdir, f"p{i}.jpg")
                cl.download(img["url"], raw)
                out = os.path.join(folder, f"{safe} [{ref}] {i}.jpg")
                cleaner.clean_jpeg(raw, out, ref, VERSION())
                ids.append(drive.upload(dsess, dbox, out, f"{safe} {i:02d}.jpg", photos, "image/jpeg", {**props, "dc_index": i}))
            db.run("""UPDATE media SET state='waiting_facebook', title=?, caption=?, local_path=?, drive_file_id=?,
                      error_code=NULL, updated_at=? WHERE id=?""",
                   (title, item["caption"], folder, ",".join(ids), time.time(), m["id"]))
        return True
    except CodedError as e:
        if e.scope == "post":
            state = "failed" if e.code == "CLEAN_FAILED" else "skipped"
            db.run("UPDATE media SET state=?, skip_reason=?, error_code=?, updated_at=? WHERE id=?",
                   (state, err_info(e.code)["title"], e.code, time.time(), m["id"]))
            return False
        db.run("UPDATE media SET state='found', next_try=?, error_code=?, updated_at=? WHERE id=?",
               (time.time() + 1800, e.code, time.time(), m["id"]))
        raise
    except DriveCastError as e:  # Drive / network problems: try again later, never lose the item
        db.run("UPDATE media SET state='found', next_try=?, error_code=?, updated_at=? WHERE id=?",
               (time.time() + 1800, "DRIVE_QUOTA" if "rate" in str(e).lower() else "IG_UNKNOWN", time.time(), m["id"]))
        db.event("warn", "drive", f"{ch['title']}: {e.user_message} — will retry.", ch["workspace_id"], ch["id"], "DRIVE_QUOTA", str(e))
        return False
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
        if progress:
            sync_clear(ch["id"])


def store_video(m, ch, raw, title, caption, folder, ref, dsess, dbox, props, progress=None):
    """Shared last mile for every source: quality gate → deep clean + DriveCast tag → Drive → ledger."""
    meta = cleaner.probe(raw)
    bad = cleaner.quality_gate(meta)
    if bad:
        raise CodedError(bad, f"{meta}")
    safe = re.sub(r'[\\/:*?"<>|]+', " ", title)[:80].strip() or ref
    final = os.path.join(folder, f"{safe} [{ref}].mp4")
    if progress:
        progress("Removing hidden data", None)
    cleaner.clean_video(raw, final, ref, VERSION(), meta.get("vcodec"))
    if progress:
        progress("Saving to Google Drive", 0)
    fid = drive.upload(dsess, dbox, final, f"{safe}.mp4", ch["folder_id"], "video/mp4", props,
                       on_progress=progress and (lambda f: progress("Saving to Google Drive", f)))
    db.run("""UPDATE media SET state='in_drive', title=?, caption=?, width=?, height=?, duration=?, has_audio=?,
              local_path=?, local_bytes=?, drive_file_id=?, error_code=NULL, updated_at=? WHERE id=?""",
           (title, caption, meta["width"], meta["height"], meta["duration"], 1 if meta["has_audio"] else 0,
            final, os.path.getsize(final), fid, time.time(), m["id"]))
    if progress:
        progress("Saved in Google Drive", 1.0)
    try:
        _after_ready(ch)
    except Exception:
        log.exception("post-when-ready failed")
    return fid


# ------------------------------------------------------------------ the pass
def _publishing(ch_id):
    return db.one("SELECT 1 FROM jobs WHERE channel_id=? AND status='running'", (ch_id,)) is not None


def _next_item(src, lane):
    order = "taken_at ASC" if lane == "fresh" else "taken_at DESC"
    return db.one(f"""SELECT * FROM media WHERE source_id=? AND lane=? AND state='found'
                      AND IFNULL(next_try,0)<=? ORDER BY {order} LIMIT 1""", (src["id"], lane, time.time()))


def account_pass(acc, now=None, serve=None):
    """One step for one test account. serve = another pool whose own account cannot read right now (resting,
    expired or missing): this account reads for it meanwhile. Posting is never affected — reading only."""
    now = now or time.time()
    g = store.group(acc["group_id"])
    sg = serve or g
    if not g or (g["status"] == "down" and acc.get("proxy_mode") != "phone") or acc["status"] == "needs_cookies":
        return "blocked"
    if (acc["paused_until"] or 0) > now or _next_action.get(acc["id"], 0) > now or route_resting(route_of(acc), now):
        return "waiting"
    acc = _bump_day("ig_accounts", acc)
    if acc["day_count"] >= ACCOUNT_DAILY:
        return "daily_cap"
    sources = db.all("""SELECT s.*, c.id cid FROM sources s JOIN channels c ON c.id=s.channel_id
                        JOIN workspaces w ON w.id=c.workspace_id
                        WHERE s.state='active' AND s.kind='instagram' AND c.group_id=? AND c.state!='setup'
                          AND NOT (IFNULL(w.paused_until,0)>? AND w.pause_collect=0)""", (sg["id"], now))
    if not sources:
        return "idle"
    try:
        cl = client_for(acc, g)
    except ValueError as e:
        _account_problem(acc, CodedError("IG_COOKIES_EXPIRED", str(e)), g)
        return "blocked"

    def act(kind, src, fn):
        ch = store.channel(src["channel_id"])
        try:
            fn(src, ch)
            db.run("UPDATE ig_accounts SET day_count=day_count+1, last_request=?, status='ok', backoff=0, error_code=NULL WHERE id=?",
                   (time.time(), acc["id"]))
            db.resolve("IG_COOKIES_EXPIRED", acc["workspace_id"])
            db.resolve("IG_CHALLENGE", acc["workspace_id"])
        except CodedError as e:
            if e.code == "IG_RATE_LIMIT":
                rest_route(route_of(acc))  # every account on this network waits too, instead of being limited one by one
            if e.scope in ("ig", "group"):
                _account_problem(acc, e, g)
            elif e.scope == "source":
                _source_problem(src, ch, e)
            elif e.scope in ("library", "channel", "server"):
                db.event("error", "pipeline", f"{ch['title']}: " + e.info()["message"], ch["workspace_id"], ch["id"], e.code, e.detail)
                db.run("UPDATE sources SET next_check=? WHERE id=?", (time.time() + 1800, src["id"]))
        gap = (20, 45) if kind == "check" else (75, 200)
        if kind == "check" and pending_kickoff(src["channel_id"]):
            gap = (1, 3)  # a new client's first video follows straight away
        _next_action[acc["id"]] = time.time() + random.uniform(*gap)
        return kind

    # 1. due profile checks
    for s in sorted(sources, key=lambda s: s["next_check"] or 0):
        if (s["next_check"] or 0) <= now:
            return act("check", s, lambda s, ch: check_source(s, ch, cl))
    # 2. fresh downloads
    for s in sources:
        ch = store.channel(s["channel_id"])
        if _publishing(ch["id"]):
            continue
        m = _next_item(s, "fresh")
        if m:
            return act("fresh", s, lambda s, ch, m=m: process(m, s, ch, cl, sg, sync_progress(ch["id"], "First video" if m["lane"] == "fresh" else "Next video")))
    # 3/4. older posts, only while the channel's ready buffer is low
    for s in sorted(sources, key=lambda s: ready_count(s["channel_id"])):
        ch = store.channel(s["channel_id"])
        if not s["include_old"] or _publishing(ch["id"]) or ready_count(ch["id"]) >= _ready_target(ch):
            continue
        s = _bump_day("sources", s)
        if s["day_count"] >= s["daily_cap"]:
            continue
        m = _next_item(s, "backfill")
        if m:
            db.run("UPDATE sources SET day_count=day_count+1 WHERE id=?", (s["id"],))
            return act("backfill", s, lambda s, ch, m=m: process(m, s, ch, cl, sg, sync_progress(ch["id"], "Older video")))
        if s["ig_user_id"] and not s["backfill_done"]:
            return act("discover", s, lambda s, ch: discover_older(s, ch, cl))
    return "idle"


def tick(wait=0):
    if not _tick_lock.acquire(timeout=wait):
        return
    try:
        _tick()
    finally:
        _tick_lock.release()


def _tick():
    if db.meta_get("superseded_by"):  # this phone was replaced: never download twice
        return
    if not cleaner.available():
        if db.one("SELECT 1 FROM sources WHERE state='active'"):
            db.event("error", "server", err_info("FFMPEG_MISSING")["message"], None, None, "FFMPEG_MISSING")
        return
    busy = {}
    for acc in db.all("SELECT * FROM ig_accounts"):
        try:
            busy[acc["group_id"]] = account_pass(acc)
        except Exception:  # one account can never stop the others
            log.exception("instagram account %s pass failed", acc["id"])
    try:
        borrow(busy)
    except Exception:
        log.exception("instagram borrow pass failed")
    cleanup_local()


def borrow(results, now=None):
    """Pools whose own test account cannot read (resting, expired, none) are read by another free account
    of the same workspace — so a busy account never stops a client's new posts."""
    now = now or time.time()
    for g in db.all("""SELECT DISTINCT g.* FROM ip_groups g JOIN channels c ON c.group_id=g.id JOIN sources s ON s.channel_id=c.id
                       WHERE s.kind='instagram' AND s.state='active' AND g.moved_to IS NULL"""):
        own = account_for_group(g["id"])
        if own and _usable(own, now) and results.get(g["id"]) not in ("blocked",):
            continue
        for kind, a in readers(g["workspace_id"], g, now):
            if kind != "acc" or a["group_id"] == g["id"] or results.get(a["group_id"]) not in ("idle", None):
                continue
            if own and route_of(a) == route_of(own):
                continue  # same network as the resting account: it would be limited too
            if _next_action.get(a["id"], 0) > now:
                continue
            results[a["group_id"]] = account_pass(a, now, serve=g)
            break


def cleanup_local(now=None):
    now = now or time.time()
    rows = db.all("""SELECT * FROM media WHERE local_path IS NOT NULL AND local_deleted_at IS NULL AND
                     ((state='published' AND published_at<?) OR (state='waiting_facebook' AND updated_at<?)
                      OR (state IN ('skipped','failed') AND updated_at<?))""", (now - LOCAL_KEEP, now - LOCAL_KEEP, now - 3600))
    for m in rows:
        p = m["local_path"]
        try:
            if m["kind"] == "video" and os.path.isfile(p):
                os.remove(p)
            elif m["kind"] != "video" and m["ref"] and os.path.isdir(p):
                for f in os.listdir(p):
                    if f"[{m['ref']}]" in f:
                        os.remove(os.path.join(p, f))
        except OSError:
            continue
        db.run("UPDATE media SET local_deleted_at=? WHERE id=?", (now, m["id"]))


# ------------------------------------------------------------------ client Inbox
def inbox_pass():
    """Files the client uploads to Inbox: download → clean → into the channel folder → remove the original."""
    if db.meta_get("superseded_by"):  # this phone was replaced: never download twice
        return
    for ch in db.all("SELECT * FROM channels WHERE inbox_id IS NOT NULL AND folder_id IS NOT NULL AND state!='setup'"):
        g = store.group(ch["group_id"])
        if not g or g["status"] == "down":
            continue
        try:
            _inbox_channel(ch, g)
        except Exception as e:
            log.warning("inbox %s: %s", ch["id"], e)
            from .errors import is_timeout
            if is_timeout(e):  # the pool's proxy did not answer in time — not a Drive problem
                db.event("warn", "inbox", f"{ch['title']}: Inbox check waited too long for Pool {g['number']}'s proxy — will retry.",
                         ch["workspace_id"], ch["id"], "PROXY_SLOW", str(e))
                continue
            code = getattr(e, "code", None) or "DRIVE_QUOTA"
            db.event("warn", "inbox", f"{ch['title']}: could not process the Inbox — will retry.",
                     ch["workspace_id"], ch["id"], code, str(e))


def _inbox_channel(ch, g):
    sess, box = _drive_ctx(ch, g)
    for f in drive.files(sess, box, ch["inbox_id"], limit=10):
        mime = f.get("mimeType", "")
        if not (mime.startswith("video/") or mime.startswith("image/")):
            continue
        ref = new_ref()
        kind = "video" if mime.startswith("video/") else "photo"
        now = time.time()
        try:
            with db.tx() as c:
                cur = c.execute("""INSERT INTO media(workspace_id,channel_id,origin,ext_id,kind,lane,ref,state,created_at,updated_at,taken_at)
                                   VALUES(?,?,'inbox',?,?,'drive',?,'downloading',?,?,?)""",
                                (ch["workspace_id"], ch["id"], f["id"], kind, ref, now, now, now))
                mid = cur.lastrowid
        except Exception:
            continue  # already handled
        folder = channel_dir(ch)
        tmp = os.path.join(folder, ".in-" + ref)
        os.makedirs(tmp, exist_ok=True)
        try:
            raw = os.path.join(tmp, "in" + (os.path.splitext(f["name"])[1] or (".mp4" if kind == "video" else ".jpg")))
            drive.download(sess, box, f["id"], raw)
            name_title = titles.from_filename(f["name"])
            used = [r["title"] for r in db.all("SELECT title FROM media WHERE channel_id=? AND title IS NOT NULL", (ch["id"],))]
            title = name_title or titles.fallback(ch["title"], db.jload(ch.get("titles_json"), []), set(used), ref)
            safe = re.sub(r'[\\/:*?"<>|]+', " ", title)[:80].strip()
            props = {"dc_ref": ref, "dc_media": mid, "dc_lane": "drive", "dc_src": "inbox"}
            if kind == "video":
                out = os.path.join(folder, f"{safe} [{ref}].mp4")
                meta = cleaner.probe(raw)
                cleaner.clean_video(raw, out, ref, VERSION(), meta.get("vcodec"))
                fid = drive.upload(sess, box, out, f"{safe}.mp4", ch["folder_id"], "video/mp4", props)
                state = "in_drive"
            else:
                out = os.path.join(folder, f"{safe} [{ref}].jpg")
                cleaner.clean_jpeg(raw, out, ref, VERSION())
                photos = drive.child_folder(sess, box, ch["folder_id"], "Photos")
                fid = drive.upload(sess, box, out, f"{safe}.jpg", photos, "image/jpeg", props)
                state = "waiting_facebook"
            drive.remove(sess, box, f["id"], ch["inbox_id"])
            db.run("UPDATE media SET state=?, title=?, local_path=?, drive_file_id=?, updated_at=? WHERE id=?",
                   (state, title, out, fid, time.time(), mid))
        except CodedError as e:
            db.run("UPDATE media SET state='failed', error_code=?, skip_reason=?, updated_at=? WHERE id=?",
                   (e.code, e.info()["title"], time.time(), mid))
            db.event("error", "inbox", f"{ch['title']}: {e.info()['message']} ({f['name']})", ch["workspace_id"], ch["id"], e.code, e.detail)
        except Exception:
            db.run("DELETE FROM media WHERE id=?", (mid,))  # nothing happened yet: try again next pass
            raise
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


# ------------------------------------------------------------------ profile check that never dead-ends
PROFILE_CACHE = 6 * 3600
VAULT_REST = 1800


ROUTE_REST = 1800


def route_of(a):
    """The network a reader uses. Instagram also limits by IP: every account on the phone's own internet shares one."""
    if a.get("proxy_mode") == "phone" or "label" in a and "proxy_mode" not in a:  # vault rows read via the phone
        return "phone"
    return f"g{a.get('group_id')}"


def route_resting(route, now=None):
    return float(db.meta_get("ig_route_rest:" + route) or 0) > (now or time.time())


def rest_route(route, secs=ROUTE_REST):
    db.meta_set("ig_route_rest:" + route, str(time.time() + secs))


def _usable(a, now):
    return a.get("status") != "needs_cookies" and (a.get("paused_until") or 0) <= now and not route_resting(route_of(a), now)


def readers(ws_id, g, now=None):
    """Who can read Instagram for this pool, best first: the pool's own test account, then the workspace's
    other test accounts, then the vault. The same Instagram account is listed once (two pools can share it)."""
    now = now or time.time()
    out, seen = [], set()
    own = account_for_group(g["id"]) if g else None
    rows = ([own] if own else []) + db.all("SELECT * FROM ig_accounts WHERE workspace_id=? ORDER BY IFNULL(last_request,0)", (ws_id,))
    for a in rows:
        k = str(a.get("ig_user_id") or a.get("username") or f"#{a['id']}")
        if k in seen:
            continue
        seen.add(k)
        if _usable(a, now):
            out.append(("acc", a))
    for v in db.all("SELECT * FROM ig_vault WHERE workspace_id=? AND status IN ('ok','unchecked') ORDER BY IFNULL(checked_at,0)", (ws_id,)):
        k = str(v.get("ig_user_id") or v.get("username") or f"v{v['id']}")
        if k in seen or float(db.meta_get(f"igv_rest:{v['id']}") or 0) > now or route_resting("phone", now):
            continue
        seen.add(k)
        out.append(("vault", v))
    return out


def _vault_client(v, g):
    from . import security
    cookies = ig.parse_cookies(security.unseal(v["cookies_enc"]))
    return ig.Client(session_for(None), cookies, user_agent=v.get("user_agent"), language=v.get("language") or lang_for(g))


def _rest_same_account(a, until):
    """Instagram limits the account, not the pool: every pool row of the same account rests together."""
    if a.get("ig_user_id"):
        db.run("UPDATE ig_accounts SET status='resting', paused_until=MAX(IFNULL(paused_until,0),?) WHERE ig_user_id=? AND id!=?",
               (until, a["ig_user_id"], a["id"]))


def lookup(ws_id, g, raw):
    """Check a client's Instagram profile. Tries every reader in turn, then the public page; if Instagram is busy
    for all of them, the client is still accepted ('pending') and checked automatically later. Never a dead end."""
    handle = ig.clean_handle(raw)  # ValueError for a bad link
    cached = db.jload(db.meta_get("igp:" + handle), None)
    if cached and time.time() - cached.get("at", 0) < PROFILE_CACHE:
        return dict(cached["p"], via=cached["p"].get("via", "recent check"))
    tried, not_found, limited = [], False, set()
    for kind, a in readers(ws_id, g)[:4]:
        ag = store.group(a.get("group_id")) if a.get("group_id") else g
        route = route_of(a) if kind == "acc" else "phone"
        if route in limited or route_resting(route):
            continue  # this network was just told to slow down: asking with another account only makes it worse
        try:
            cl = client_for(a, ag or g) if kind == "acc" else _vault_client(a, ag or g)
            old = cl.min_gap
            cl.min_gap = (1, 2)
            try:
                p = cl.profile(handle)
            finally:
                cl.min_gap = old
            p["via"] = "@" + (a.get("username") or "test account")
            db.meta_set("igp:" + handle, __import__("json").dumps({"at": time.time(), "p": p}))
            return p
        except CodedError as e:
            tried.append(e.code)
            if e.code == "IG_PROFILE_NOT_FOUND":
                not_found = True
                break  # confirm with the public page below
            if e.code == "IG_RATE_LIMIT":
                limited.add(route)
                rest_route(route)
            if kind == "acc" and e.scope in ("ig", "group"):
                _account_problem(a, e, ag or g)
                if e.code == "IG_RATE_LIMIT":
                    _rest_same_account(a, db.one("SELECT paused_until FROM ig_accounts WHERE id=?", (a["id"],))["paused_until"])
            elif kind == "vault":
                db.meta_set(f"igv_rest:{a['id']}", str(time.time() + VAULT_REST))
        except Exception as e:
            tried.append(str(e)[:80])
    try:
        if "phone" in limited or route_resting("phone"):
            raise CodedError("IG_RATE_LIMIT", "phone network resting")
        p = ig.public_profile(session_for(None), handle)
        p["via"] = "public page"
        db.meta_set("igp:" + handle, __import__("json").dumps({"at": time.time(), "p": p}))
        return p
    except CodedError as e:
        if e.code == "IG_PROFILE_NOT_FOUND":
            raise
        tried.append(e.code)
    except Exception as e:
        tried.append(str(e)[:80])
    return {"username": handle, "id": None, "full_name": None, "picture": None, "is_private": False, "followed": False,
            "posts": None, "pending": True, "maybe_typo": not_found, "tried": tried[-3:]}
