"""Publishing worker. One job = pick the next Drive video for a channel and
publish it through that channel's IP group.

Bulkhead rule: every job runs inside its own try/except. A failure changes only
that channel (and, for proxy problems, that IP group) — never the whole server.
"""
import json
import logging
import os
import shutil
import tempfile
import time

from . import db, drive, store
from .net import (AuthRevoked, DriveCastError, Permanent, ProxyDown, QuotaExceeded, Uncertain,
                  outgoing_ip, session_for)
from .errors import CodedError
from .platforms import get as platform
from .timeplan import next_pacific_midnight

log = logging.getLogger("drivecast.worker")
LEASE = 3 * 3600
BREAKER_AFTER = 3          # consecutive failures before a channel cools down
BREAKER_COOLDOWN = 3600    # first cool-down; doubles each time, max 12 h
TMP_DIR = None
PROGRESS = {}  # channel_id -> live status of the post being made right now


def _stage(ch_id, stage, pct=None, title=None):
    p = PROGRESS.setdefault(ch_id, {"started": time.time()})
    p.update({"stage": stage, "at": time.time()})
    if pct is not None:
        p["pct"] = round(max(0.0, min(1.0, pct)) * 100)
    if title:
        p["title"] = title


def claim(worker_id, now=None):
    """Atomically take the oldest queued job whose channel has nothing running."""
    now = now or time.time()
    with db.tx() as c:
        row = c.execute("""
            SELECT j.id FROM jobs j WHERE j.status='queued'
              AND NOT EXISTS (SELECT 1 FROM jobs r WHERE r.channel_id=j.channel_id AND r.status='running')
            ORDER BY j.created_at LIMIT 1""").fetchone()
        if not row:
            return None
        c.execute("UPDATE jobs SET status='running', lease_owner=?, lease_until=? WHERE id=?",
                  (worker_id, now + LEASE, row[0]))
        return dict(c.execute("SELECT * FROM jobs WHERE id=?", (row[0],)).fetchone())


def finish(job, status, error=None, slot_status=None, note=None, upload_id=None):
    with db.tx() as c:
        c.execute("UPDATE jobs SET status=?, finished_at=?, error=?, lease_owner=NULL WHERE id=?",
                  (status, time.time(), error, job["id"]))
        if job.get("slot_id") and slot_status:
            c.execute("UPDATE slots SET status=?, note=?, upload_id=COALESCE(?,upload_id), updated_at=? WHERE id=?",
                      (slot_status, note, upload_id, time.time(), job["slot_id"]))


def recover_interrupted():
    """After a crash/restart: jobs left 'running' are closed safely."""
    for j in db.all("SELECT * FROM jobs WHERE status='running'"):
        finish(j, "failed", "Interrupted by restart", "failed", "Server restarted during this post")
    for u in db.all("SELECT * FROM uploads WHERE status IN ('reserved','uploading')"):
        if u["status"] == "uploading":
            db.run("UPDATE uploads SET status='uncertain', error=? WHERE id=?",
                   ("Server stopped while uploading — check YouTube Studio before re-posting", u["id"]))
            db.event("error", "uncertain", f"“{u['title']}” may or may not be on YouTube. Check YouTube Studio.",
                     u["workspace_id"], u["channel_id"])
        else:  # nothing was sent yet: release the file
            db.run("DELETE FROM uploads WHERE id=?", (u["id"],))


def _breaker(ch, err_msg):
    n = ch["consecutive_failures"] + 1
    if n >= BREAKER_AFTER:
        cool = min(12 * 3600, BREAKER_COOLDOWN * (2 ** (n - BREAKER_AFTER)))
        db.run("UPDATE channels SET consecutive_failures=?, paused_until=?, state='attention', state_reason=? WHERE id=?",
               (n, time.time() + cool, f"{n} failures in a row — cooling down. Last: {err_msg}", ch["id"]))
    else:
        db.run("UPDATE channels SET consecutive_failures=? WHERE id=?", (n, ch["id"]))


def _group_down(g, why):
    db.run("UPDATE ip_groups SET status='down', last_error=?, fail_count=fail_count+1, last_check=? WHERE id=?",
           (why, time.time(), g["id"]))
    db.event("error", "proxy", f"Group {g['number']} is not responding — its channels are held. {why}",
             g["workspace_id"])


def run(job, adapters=None):
    """Execute one job. ``adapters`` lets tests inject fakes:
    {'session': fn(group), 'ip': fn(session), 'drive': module, 'platform': module,
     'facebook': module, 'igpost': module}."""
    a = adapters or {}
    make_session = a.get("session", session_for)
    check_ip = a.get("ip", outgoing_ip)
    dmod = a.get("drive", drive)
    ch = store.channel(job["channel_id"])
    if not ch:
        return finish(job, "failed", "Channel removed", "skipped", "Channel removed")
    ws = store.workspace(ch["workspace_id"])
    now = time.time()

    # --- gates: nothing is touched unless the route is fully healthy ---
    if (ws.get("paused_until") or 0) > now:
        return finish(job, "failed", "Paused", "held", "Paused — everything is on hold")
    if db.meta_get("superseded_by"):
        return finish(job, "failed", "Phone replaced", "held", "This phone was replaced by another — posting stopped here")
    # 'attention' only runs again when it came from the cool-down breaker and the cool-down ended
    breaker_over = ch["state"] == "attention" and ch["paused_until"] and ch["paused_until"] <= now
    if not (ch["state"] == "live" or breaker_over) or (ch["paused_until"] or 0) > now:
        return finish(job, "failed", "Channel not live", "held", ch["state_reason"] or "Channel paused")
    g = store.group(ch["group_id"])
    if not g:
        store.set_channel_state(ch["id"], "attention", "No IP group — choose one")
        return finish(job, "failed", "No IP group", "held", "No IP group")
    if g["status"] == "down":
        return finish(job, "failed", "Proxy down", "held", f"Group {g['number']} not responding")
    dests = destinations(ch, a)
    if not dests:
        return finish(job, "failed", "No destination", "held", "No destination is on — connect YouTube, Facebook or Instagram")
    if any(d["kind"] == "youtube" for d in dests):
        lim = store.limits_for_workspace(ws["id"])
        if store.uploads_today(ws["id"], now) >= lim["daily_cap"]:
            dests = [d for d in dests if d["kind"] != "youtube"]
            if not dests:
                return finish(job, "failed", "Daily cap", "held", "Daily upload cap reached — resets at midnight Pacific")
    if not ch["folder_id"]:
        return finish(job, "failed", "No folder", "held", "No Drive folder connected")

    session = make_session(g)
    phase, upload_id, tmp, primary = "prepare", None, None, None
    _stage(ch["id"], "Checking proxy IP", 0)
    try:
        # --- never leave from the wrong IP ---
        try:
            ip, ms = check_ip(session)
        except Exception as e:
            _group_down(g, str(e)[:120])
            return finish(job, "failed", "Proxy down", "held", f"Group {g['number']} not responding")
        if g.get("outgoing_ip") and ip != g["outgoing_ip"]:
            _group_down(g, f"Outgoing IP changed to {ip} (expected {g['outgoing_ip']})")
            return finish(job, "failed", "IP changed", "held", "Proxy IP changed — confirm the group")
        db.run("UPDATE ip_groups SET outgoing_ip=COALESCE(outgoing_ip,?), status='ok', latency_ms=?, last_check=? WHERE id=?",
               (ip, ms, now, g["id"]))

        dbox = store.drive_box(ws)
        # --- pick the next item: Fresh → Drive (your edits / Inbox) → older posts → Encore ---
        files = dmod.videos(session, dbox, ch["folder_id"])
        if any(d["photo"] for d in dests):
            files = files + photo_candidates(ch)
        files = order_queue(files)
        db.run("UPDATE channels SET queue_count=?, queue_checked_at=? WHERE id=?", (len(files), now, ch["id"]))
        snapshot(ch, files)
        chosen, encore, m, targets = None, None, None, []
        for f in files:
            mid = _media_id(f)
            fm = db.one("SELECT * FROM media WHERE id=?", (mid,)) if mid else None
            targets = [d for d in dests if (d["photo"] if f.get("photo") else d["video"]) and not loop_blocked(d, ch, fm)]
            if not targets:
                continue
            primary = targets[0]
            try:
                with db.tx() as c:
                    cur = c.execute("""INSERT INTO uploads(workspace_id,channel_id,destination,file_id,file_name,title,
                                       status,reserved_at,outgoing_ip,media_id) VALUES(?,?,?,?,?,?,'reserved',?,?,?)""",
                                    (ws["id"], ch["id"], primary["key"], f["id"], f["name"], _plain_title(f["name"]),
                                     now, ip, mid))
                    upload_id = cur.lastrowid
                chosen, m = f, fm
                break
            except Exception:  # UNIQUE(file_id,destination): already handled before
                continue
        if not chosen and ch.get("encore", 1):
            vd = [d for d in dests if d["video"]]
            encore = encore_pick(ch, vd[0]["key"], now) if vd else None
            if encore:
                m = db.one("SELECT * FROM media WHERE id=?", (encore["media_id"],)) if encore.get("media_id") else None
                targets = [d for d in vd if not loop_blocked(d, ch, m)]
                primary = targets[0] if targets else None
            if encore and primary:
                with db.tx() as c:
                    cur = c.execute("""INSERT INTO uploads(workspace_id,channel_id,destination,file_id,file_name,title,
                                       status,reserved_at,outgoing_ip,media_id,encore_of) VALUES(?,?,?,?,?,?,'reserved',?,?,?,?)""",
                                    (ws["id"], ch["id"], primary["key"], encore["key"], encore["file_name"], encore["title"], now, ip,
                                     encore["media_id"], encore["original"]))
                    upload_id = cur.lastrowid
                chosen = {"id": encore["file_id"], "name": encore["file_name"], "size": None, "appProperties": {}}
            else:
                encore = None
        if not chosen:
            if not files:
                db.event("warn", "empty", f"{ch['title']}: nothing left to post — add videos or connect a source.", ws["id"], ch["id"], "QUEUE_EMPTY")
            return finish(job, "done", None, "skipped", "Nothing new to post")
        key = encore["key"] if encore else chosen["id"]
        is_photo = bool(chosen.get("photo"))

        # --- the file(s): the phone copy when we still have it, else from Drive ---
        title0 = encore["title"] if encore else (m and m.get("title")) or _plain_title(chosen["name"])
        tmp = tempfile.mkdtemp(prefix="dc-", dir=TMP_DIR)
        processing, path, photos = None, None, []
        if is_photo:
            _stage(ch["id"], "Getting the photos", 0, title0)
            photos = photo_paths(m, tmp, session, dmod, dbox)
        else:
            path = os.path.join(tmp, "video" + (os.path.splitext(chosen["name"])[1] or ".mp4"))
            if not encore:
                processing = dmod.child_folder(session, dbox, ch["folder_id"], "Processing")
                dmod.move(session, dbox, chosen["id"], ch["folder_id"], processing)
            local = m and m.get("local_path") and not m.get("local_deleted_at") and os.path.isfile(m["local_path"]) and m["local_path"]
            if local:
                _stage(ch["id"], "Using the phone copy", 1.0, title0)
                shutil.copyfile(local, path)
            else:
                _stage(ch["id"], "Downloading from Drive", 0, title0)
                size = int(chosen.get("size") or 0)
                try:
                    dmod.download(session, dbox, chosen["id"], path,
                                  on_progress=(lambda n: _stage(ch["id"], "Downloading from Drive", n / size)) if size else None)
                except TypeError:  # test doubles without progress support
                    dmod.download(session, dbox, chosen["id"], path)
            # --- privacy: anything that did not come through DriveCast is cleaned before it leaves ---
            props = chosen.get("appProperties") or {}
            if not props.get("dc_ref") and not encore and CLEAN_BEFORE_PUBLISH:
                from . import cleaner, igsync
                _stage(ch["id"], "Removing hidden data", None)
                cleaned = os.path.join(tmp, "clean.mp4")
                cleaner.clean_video(path, cleaned, igsync.new_ref(), _version())
                path = cleaned

        title, desc = _title_and_description(ch, chosen, m, encore)
        pack = None
        if not encore:  # an Encore keeps the text it went out with the first time
            from . import ai
            _stage(ch["id"], "Writing the captions", None)
            try:
                pack = ai.prepare(ch, chosen, m, title, desc, [d["kind"] for d in targets], video=path, tmp=tmp,
                                  file_key=key, photo=is_photo)
            except CodedError:
                raise  # the AI check held this post
            except Exception as e:  # AI never stops a post
                log.warning("AI captions skipped: %s", e)
        from .ai import text_for
        ptitle, pdesc = text_for(pack, primary["kind"], title, desc)
        db.run("UPDATE uploads SET status='uploading', title=? WHERE id=?", (ptitle, upload_id))
        phase = "upload"
        res = send(primary, ch, session, a, path, photos, ptitle, pdesc)
        phase = "after"
        db.run("UPDATE uploads SET status='published', external_video_id=?, post_id=?, visibility=?, published_at=? WHERE id=?",
               (res.get("video_id"), res.get("post_id"), res.get("visibility") or "public", time.time(), upload_id))
        # stats for this channel come back ~5 minutes after the post, then every 20 minutes
        db.run("UPDATE channels SET consecutive_failures=0, paused_until=NULL, state='live', state_reason=NULL, stats_at=? WHERE id=?",
               (time.time() - 15 * 60, ch["id"]))
        _dest_ok(primary)
        if pack and primary["kind"] == "youtube":
            from . import ai
            ai.after_youtube(pack, ch, session, res.get("video_id"))
        if m:
            db.run("UPDATE media SET state='published', published_at=?, updated_at=? WHERE id=? AND state!='published'",
                   (time.time(), time.time(), m["id"]))
        # --- the other destinations: each one isolated, a problem there never undoes this post ---
        for d in targets[1:]:
            extra(d, ch, ws, key, chosen, m, session, a, path, photos, title, desc, ip, pack=pack)
        if processing:
            try:
                done = dmod.child_folder(session, dbox, ch["folder_id"], "Done")
                dmod.move(session, dbox, chosen["id"], processing, done)
            except Exception as e:  # the post is live; a failed tidy-up must not undo that
                db.event("warn", "drive", f"Published, but could not move the file to Done: {e}", ws["id"], ch["id"])
        if res.get("visibility") not in (None, "public"):
            db.event("warn", "visibility", f"“{ptitle}” was accepted but {primary['label']} shows it as {res['visibility']}.",
                     ws["id"], ch["id"])
        return finish(job, "done", None, "done", res.get("video_id") or res.get("post_id"), upload_id)

    except Uncertain as e:
        where = primary["label"] if primary else "the platform"
        db.run("UPDATE uploads SET status='uncertain', error=? WHERE id=?", (e.user_message, upload_id))
        db.event("error", "uncertain", f"{ch['title']}: {e.user_message}. Check {where} — it will not be retried.",
                 ws["id"], ch["id"], "YT_UNCERTAIN")
        return finish(job, "failed", str(e), "uncertain", e.user_message, upload_id)
    except CodedError as e:
        inf = e.info(group=g["number"])
        if e.code == "PROXY_DEAD":
            _release(upload_id, session, dmod, ch)
            _group_down(g, e.detail[:120])
            return finish(job, "failed", inf["title"], "held", "Proxy down")
        _dest_problem(primary, e)
        permanent = e.scope == "post" and e.code not in ("FB_BUSY",)
        _release(upload_id, session, dmod, ch, permanent=permanent)
        extra_txt = " File moved to Failed." if permanent else ""
        db.event("error" if inf.get("fix") or permanent else "warn", (primary or {}).get("kind") or "server",
                 f"{ch['title']}: {inf['message']}{extra_txt}", ws["id"], ch["id"], e.code, e.detail)
        return finish(job, "failed", inf["title"], "failed" if permanent else "held", inf["title"])
    except Exception as e:
        err = e.user_message if isinstance(e, DriveCastError) else f"Unexpected error: {e}"
        if phase == "upload" and not isinstance(e, DriveCastError):
            # an unknown crash while bytes may have been sent is treated as uncertain
            db.run("UPDATE uploads SET status='uncertain', error=? WHERE id=?", (err, upload_id))
            db.event("error", "uncertain", f"{ch['title']}: upload interrupted. Check {primary['label'] if primary else 'the platform'}.",
                     ws["id"], ch["id"])
            return finish(job, "failed", err, "uncertain", err, upload_id)
        _release(upload_id, session, dmod, ch, permanent=isinstance(e, Permanent))
        if isinstance(e, AuthRevoked):
            store.set_channel_state(ch["id"], "attention", "Reconnect this channel — Google access ended")
            db.event("error", "auth", f"{ch['title']}: {err}", ws["id"], ch["id"])
            return finish(job, "failed", err, "held", "Reconnect channel")
        if isinstance(e, QuotaExceeded):
            db.run("UPDATE channels SET paused_until=? WHERE id=?", (next_pacific_midnight(time.time()), ch["id"]))
            db.event("warn", "quota", f"{ch['title']}: {err}. Posting resumes after midnight Pacific.", ws["id"], ch["id"])
            return finish(job, "failed", err, "held", "Quota reached")
        if isinstance(e, ProxyDown):
            _group_down(g, err)
            return finish(job, "failed", err, "held", "Proxy down")
        if isinstance(e, Permanent):
            db.event("warn", "rejected", f"{ch['title']}: {err}. File moved to Failed.", ws["id"], ch["id"])
            return finish(job, "failed", err, "failed", err)
        _breaker(ch, err)
        log.warning("job %s transient failure: %s", job["id"], e)
        return finish(job, "failed", err, "failed", err)
    finally:
        PROGRESS.pop(ch["id"], None)
        if tmp:
            shutil.rmtree(tmp, ignore_errors=True)


def _origin(f):
    """Where a queued file came from, for the 'Up next' list: Instagram / YouTube / TikTok / Client Inbox / your Drive."""
    p = f.get("appProperties") or {}
    src = p.get("dc_src")
    if not src and p.get("dc_media"):
        m = db.one("SELECT origin FROM media WHERE id=?", (int(p["dc_media"]),))
        src = m and m["origin"]
    if not src:
        src = "drive" if not p.get("dc_ref") else "instagram"
    return src


def snapshot(ch, files, limit=12):
    """Remember the real posting order (the same order the worker uses) so the panel can show it without a Drive call."""
    items = []
    for f in files[:limit]:
        p = f.get("appProperties") or {}
        m = db.one("SELECT title, taken_at, ref, width, height FROM media WHERE id=?", (int(p["dc_media"]),)) if p.get("dc_media") else None
        items.append({"name": (m and m["title"]) or _plain_title(f.get("name")), "origin": _origin(f),
                      "lane": p.get("dc_lane") or ("drive" if not p.get("dc_ref") else "fresh"), "photo": bool(f.get("photo")),
                      "taken": (m and m["taken_at"]) or int(p.get("dc_taken") or 0) or None, "ref": (m and m["ref"]) or p.get("dc_ref"),
                      "size": f"{m['width']}×{m['height']}" if m and m["width"] else None, "added": f.get("createdTime")})
    db.run("UPDATE channels SET queue_json=?, queue_at=? WHERE id=?", (json.dumps(items), time.time(), ch["id"]))
    return items


def refresh_queues():
    """Every 10 minutes: the real 'Up next' for every live client (one Drive listing each, through its group)."""
    for ch in db.all("SELECT * FROM channels WHERE state='live' AND folder_id IS NOT NULL"):
        g = store.group(ch["group_id"])
        if not g or g["status"] == "down" or ch["id"] in PROGRESS:
            continue
        try:
            ws = store.workspace(ch["workspace_id"])
            files = drive.videos(session_for(g), store.drive_box(ws), ch["folder_id"])
            if any(d["photo"] for d in destinations(ch)):
                files = files + photo_candidates(ch)
            snapshot(ch, order_queue(files))
        except Exception as e:
            log.info("queue refresh %s: %s", ch["id"], e)


def _plain_title(name):
    return os.path.splitext(os.path.basename(name or ""))[0].strip() or "Untitled"


# ------------------------------------------------------------------ destinations
def destinations(ch, a=None):
    """Where this client's posts go, in order: YouTube (if on) → Facebook Page → Instagram.
    Only destinations that can post right now are listed."""
    out = []
    if ch.get("youtube_on", 1) and ch.get("token_enc") and ch.get("platform") == "youtube":
        out.append({"kind": "youtube", "key": f"{ch['platform']}:{ch['external_id']}", "label": "YouTube",
                    "video": True, "photo": False})
    if ch.get("fb_page_id"):
        pg = db.one("SELECT * FROM fb_pages WHERE channel_id=? AND page_id=?", (ch["id"], ch["fb_page_id"]))
        acc = pg and db.one("SELECT * FROM fb_accounts WHERE id=?", (pg["fb_account_id"],))
        if pg and pg.get("can_post") and acc and acc["status"] != "needs_reconnect":
            out.append({"kind": "facebook", "key": f"facebook:{pg['page_id']}", "label": "Facebook",
                        "video": True, "photo": True, "page": pg, "acc": acc})
    if ch.get("ig_dest_id"):
        d = db.one("SELECT * FROM ig_dest WHERE id=?", (ch["ig_dest_id"],))
        if d and d["status"] == "ok":
            from . import igpost
            if igpost.published_today(d["id"]) < igpost.DAILY_LIMIT:
                out.append({"kind": "instagram", "key": f"instagram:{d['id']}", "label": "Instagram",
                            "video": True, "photo": True, "dest": d})
    return out


def loop_blocked(d, ch, m):
    """Loop guard: a post is never sent back to the account it was read from."""
    if not m or not m.get("source_id"):
        return False
    src = db.one("SELECT * FROM sources WHERE id=?", (m["source_id"],))
    if not src:
        return False
    if d["kind"] == "youtube" and m["origin"] == "youtube":
        return bool(src.get("ig_user_id")) and src["ig_user_id"] == ch.get("external_id")
    if d["kind"] == "instagram" and m["origin"] == "instagram":
        dest = d["dest"]
        same_id = src.get("ig_user_id") and str(src["ig_user_id"]) == str(dest.get("ig_user_id"))
        same_name = (src["handle"] or "").lstrip("@").lower() == (dest.get("username") or "").lower()
        return bool(same_id or same_name)
    return False


def photo_candidates(ch):
    """Photo / carousel posts waiting in the ledger, shaped like Drive files for order_queue."""
    rows = db.all("""SELECT * FROM media WHERE channel_id=? AND kind IN ('photo','carousel') AND state='waiting_facebook'
                     AND drive_file_id IS NOT NULL""", (ch["id"],))
    return [{"id": f"media:{r['id']}", "name": f"{r['title'] or 'Photo'}.jpg", "photo": True, "size": None,
             "createdTime": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(r["created_at"])),
             "appProperties": {"dc_media": r["id"], "dc_lane": r["lane"], "dc_taken": int(r["taken_at"] or 0),
                               "dc_ref": r["ref"]}} for r in rows]


def photo_paths(m, tmp, session, dmod, dbox):
    import glob
    paths = []
    if m.get("local_path") and not m.get("local_deleted_at") and os.path.isdir(m["local_path"]) and m.get("ref"):
        paths = sorted(glob.glob(os.path.join(glob.escape(m["local_path"]), f"*[[]{m['ref']}[]] *.jpg")),
                       key=lambda p: int(os.path.splitext(p)[0].rsplit(" ", 1)[-1] or 0))
    if not paths:
        for i, fid in enumerate([x for x in (m.get("drive_file_id") or "").split(",") if x], 1):
            p = os.path.join(tmp, f"p{i:02d}.jpg")
            dmod.download(session, dbox, fid, p)
            paths.append(p)
    if not paths:
        raise CodedError("IG_MEDIA_GONE", "photos missing locally and in Drive")
    return paths[:10]


def _ig_token(dest, session, a):
    from . import igpost, security
    tok = security.unseal(dest["token_enc"])
    if dest["via"] == "instagram" and (dest.get("token_expires") or 0) < time.time() + 7 * 86400:
        tok, exp = (a.get("igpost") or igpost).refresh(session, tok)
        db.run("UPDATE ig_dest SET token_enc=?, token_expires=?, updated_at=? WHERE id=?",
               (security.seal(tok), exp, time.time(), dest["id"]))
    return tok


def send(d, ch, session, a, path, photos, title, desc):
    """Publish to one destination. Returns {'video_id','post_id','visibility'}."""
    from . import facebook as fbm, igpost as igm, security
    label = d["label"]
    prog = lambda f: _stage(ch["id"], f"Posting to {label}", f)  # noqa: E731
    _stage(ch["id"], f"Posting to {label}", 0, title)
    if d["kind"] == "youtube":
        pmod = a.get("platform") or platform(ch["platform"])
        cbox = store.channel_box(ch)
        try:
            return pmod.publish(session, cbox, path, title, desc, on_progress=prog)
        except TypeError as te:
            if "on_progress" not in str(te):
                raise
            return pmod.publish(session, cbox, path, title, desc)
    if d["kind"] == "facebook":
        fb = a.get("facebook") or fbm
        token = security.unseal(d["page"]["token_enc"])
        if photos:
            r = fb.publish_photos(session, d["page"]["page_id"], token, photos, desc or title)
        else:
            r = fb.publish_video(session, d["page"]["page_id"], token, path, title, desc, on_progress=prog)
        return {"video_id": r.get("video_id"), "post_id": r.get("post_id"), "visibility": "public"}
    if d["kind"] == "instagram":
        ig = a.get("igpost") or igm
        items = [(p, "photo") for p in photos] if photos else [(path, "video")]
        r = ig.publish(session, d["dest"], _ig_token(d["dest"], session, a), items, desc or title, on_progress=prog)
        return {"post_id": r.get("post_id"), "visibility": "public"}
    raise CodedError("FB_NO_PAGE", d["kind"])


def _dest_ok(d):
    if not d:
        return
    if d["kind"] == "facebook":
        db.run("UPDATE fb_accounts SET status='ok', error_code=NULL WHERE id=?", (d["acc"]["id"],))
    elif d["kind"] == "instagram":
        db.run("UPDATE ig_dest SET status='ok', error_code=NULL, error_detail=NULL WHERE id=?", (d["dest"]["id"],))


def _dest_problem(d, e):
    if not d:
        return
    if d["kind"] == "facebook" and e.code == "FB_TOKEN_EXPIRED":
        db.run("UPDATE fb_accounts SET status='needs_reconnect', error_code=?, error_detail=? WHERE id=?",
               (e.code, e.detail, d["acc"]["id"]))
    if d["kind"] == "instagram" and e.code in ("IGD_TOKEN_EXPIRED", "IGD_NOT_PRO"):
        db.run("UPDATE ig_dest SET status='needs_reconnect', error_code=?, error_detail=?, updated_at=? WHERE id=?",
               (e.code, e.detail, time.time(), d["dest"]["id"]))


def extra(d, ch, ws, key, chosen, m, session, a, path, photos, title, desc, ip, pack=None):
    """One more destination for the same item. Isolated: its problems stay its own."""
    from .ai import text_for
    title, desc = text_for(pack, d["kind"], title, desc)
    now = time.time()
    try:
        with db.tx() as c:
            cur = c.execute("""INSERT INTO uploads(workspace_id,channel_id,destination,file_id,file_name,title,
                               status,reserved_at,outgoing_ip,media_id) VALUES(?,?,?,?,?,?,'uploading',?,?,?)""",
                            (ws["id"], ch["id"], d["key"], key, chosen["name"], title, now, ip, _media_id(chosen)))
            uid = cur.lastrowid
    except Exception:
        return  # already posted there (UNIQUE file_id+destination)
    try:
        res = send(d, ch, session, a, path, photos, title, desc)
        db.run("UPDATE uploads SET status='published', external_video_id=?, post_id=?, visibility='public', published_at=? WHERE id=?",
               (res.get("video_id"), res.get("post_id"), time.time(), uid))
        _dest_ok(d)
    except Uncertain as e:
        db.run("UPDATE uploads SET status='uncertain', error=? WHERE id=?", (getattr(e, "user_message", str(e)), uid))
        db.event("error", "uncertain", f"{ch['title']}: {d['label']} result unknown — check it there. It will not be retried.",
                 ws["id"], ch["id"], "YT_UNCERTAIN")
    except CodedError as e:
        g = store.group(ch["group_id"])
        inf = e.info(group=g["number"] if g else "?")
        _dest_problem(d, e)
        if e.scope == "post" and e.code != "FB_BUSY":
            db.run("UPDATE uploads SET status='failed', error=? WHERE id=?", (inf["title"], uid))
        else:
            db.run("DELETE FROM uploads WHERE id=?", (uid,))
        db.event("warn", d["kind"], f"{ch['title']} → {d['label']}: {inf['message']}", ws["id"], ch["id"], e.code, e.detail)
    except Exception as e:  # never let one destination break the others
        db.run("DELETE FROM uploads WHERE id=?", (uid,))
        db.event("warn", d["kind"], f"{ch['title']}: could not post to {d['label']} — {e}", ws["id"], ch["id"])


CLEAN_BEFORE_PUBLISH = True
ENCORE_COOLDOWN = 60 * 86400
ENCORE_MAX = 3
LANE_RANK = {"fresh": 0, "drive": 1, None: 1, "backfill": 2}


def _version():
    from . import VERSION
    return VERSION


def _media_id(f):
    try:
        return int((f.get("appProperties") or {}).get("dc_media") or 0) or None
    except (TypeError, ValueError):
        return None


def order_queue(files):
    """Fresh first (oldest fresh first), then your own files (oldest first),
    then older Instagram posts (newest first)."""
    def key(f):
        p = f.get("appProperties") or {}
        lane = p.get("dc_lane")
        rank = LANE_RANK.get(lane, 1)
        if lane == "backfill":
            return (rank, -int(p.get("dc_taken") or 0), f.get("createdTime") or "")
        return (rank, 0, f.get("createdTime") or "")
    return sorted(files, key=key)


def encore_pick(ch, dest, now):
    """Best performer that is allowed to run again: 60-day cooldown, max 3 re-posts, 1 Encore a day."""
    day_start = now - 86400
    if db.one("SELECT 1 FROM uploads WHERE channel_id=? AND encore_of IS NOT NULL AND reserved_at>? AND status!='failed'",
              (ch["id"], day_start)):
        return None
    rows = db.all("""SELECT u.*, (SELECT COUNT(*) FROM uploads e WHERE e.encore_of=u.id AND e.status!='failed') reposts,
                            (SELECT MAX(published_at) FROM uploads e WHERE (e.encore_of=u.id OR e.id=u.id) AND e.status='published') last_at,
                            (SELECT COALESCE(SUM(views),0) FROM uploads e WHERE e.encore_of=u.id OR e.id=u.id) all_views
                     FROM uploads u WHERE u.channel_id=? AND u.encore_of IS NULL AND u.status='published' AND u.destination=?""",
                  (ch["id"], dest))
    ok = [r for r in rows if r["reposts"] < ENCORE_MAX and (r["last_at"] or 0) < now - ENCORE_COOLDOWN]
    if not ok:
        return None
    best = max(ok, key=lambda r: (r["all_views"] or 0, -(r["last_at"] or 0)))
    from . import titles
    n = best["reposts"] + 1
    return {"key": f"{best['file_id']}#e{n}", "file_id": best["file_id"], "file_name": best["file_name"],
            "title": titles.encore_title(best["title"], n), "media_id": best.get("media_id"), "original": best["id"]}


def _title_and_description(ch, f, m, encore):
    from . import titles
    if encore:
        return encore["title"], titles.description(m.get("caption") if m else None, encore["title"])
    used = [r["title"] for r in db.all("SELECT title FROM uploads WHERE channel_id=? AND status='published'", (ch["id"],))]
    caption = m.get("caption") if m else None
    title = (m and m.get("title")) or titles.choose(name=f["name"], caption=caption, channel_title=ch["title"],
                                                    bank=db.jload(ch.get("titles_json"), []), used=used, seed=f["id"])
    if title in used and not (m and m.get("title")):
        title = titles.fallback(ch["title"], db.jload(ch.get("titles_json"), []), set(used), f["id"])
    return title, titles.description(caption, title)


def _release(upload_id, session, dmod, ch, permanent=False):
    """Nothing was published: free the reservation and put the file back
    (or into Failed when the file itself is the problem)."""
    if not upload_id:
        return
    u = db.one("SELECT * FROM uploads WHERE id=?", (upload_id,))
    if not u:
        return
    if "#e" in (u["file_id"] or ""):  # an Encore never moved its file
        db.run("DELETE FROM uploads WHERE id=?", (upload_id,))
        return
    if (u["file_id"] or "").startswith("media:"):  # photo post: lives in the ledger, not in the queue folder
        if permanent:
            db.run("UPDATE uploads SET status='failed', error='Rejected' WHERE id=?", (upload_id,))
            db.run("UPDATE media SET state='failed', skip_reason='Refused by the platform', updated_at=? WHERE id=?",
                   (time.time(), u["media_id"]))
        else:
            db.run("DELETE FROM uploads WHERE id=?", (upload_id,))
        return
    try:
        dbox = store.drive_box(store.workspace(ch["workspace_id"]))
        processing = dmod.child_folder(session, dbox, ch["folder_id"], "Processing")
        if permanent:
            failed = dmod.child_folder(session, dbox, ch["folder_id"], "Failed")
            dmod.move(session, dbox, u["file_id"], processing, failed)
        else:
            dmod.move(session, dbox, u["file_id"], processing, ch["folder_id"])
    except Exception:
        pass  # file may still be in the source folder; that is fine
    if permanent:
        db.run("UPDATE uploads SET status='failed', error='Rejected' WHERE id=?", (upload_id,))
    else:
        db.run("DELETE FROM uploads WHERE id=?", (upload_id,))
