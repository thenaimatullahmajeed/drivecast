"""YouTube channel + TikTok profile as SOURCES (a client's own public videos).

Reading only — nothing is ever posted from here. The client gives a link; no client
sign-in is needed. Per source, one paced action per pass, in this order:
  1. a due check of the newest videos (fresh first),
  2. download a fresh video,
  3. walk older videos (newest first) only while the ready buffer (~1 week) is low.
Download: best ≤1080p (H.264 preferred so Instagram/Facebook accept it), audio merged,
TikTok without the watermark. Then the same last mile as Instagram: quality gate →
deep clean + DriveCast tag → the channel's Drive folder → ledger. Same ledger = never
twice, never missed, never mixed between clients.

Reading goes through the phone's own internet by default (YouTube and TikTok trust
home/mobile IPs far more than datacenter proxies); the owner can switch a source to
its group proxy. Posting always uses the group proxy — that rule lives in the worker.

The downloader (yt-dlp) ships inside the app and can be updated from the panel
without a new APK: the newest release is saved to the data folder and used first.
"""
import calendar
import glob
import importlib
import logging
import os
import random
import re
import shutil
import sys
import tempfile
import threading
import time
import xml.etree.ElementTree as ET

from . import config, db, store, titles
from .errors import CodedError, info as err_info
from .net import DriveCastError, proxy_url, session_for

log = logging.getLogger("drivecast.vidsync")
SITES = {"youtube": "YouTube", "tiktok": "TikTok"}
CHECK = {"youtube": (40 * 60, 80 * 60), "tiktok": (60 * 60, 120 * 60)}
GAP = {"youtube": (40, 120), "tiktok": (90, 240)}
PAGE = 30                      # older videos listed per discovery step
SOURCE_DAILY = 12              # older-video downloads per source per day
_next = {}                     # source id -> next action time
_pass_lock = threading.Lock()
_lock = threading.Lock()
YTDLP_URL = "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp"


# ------------------------------------------------------------------ downloader (updatable)
def _zip_path():
    return os.path.join(config.DATA_DIR, "tools", "yt-dlp.zip")


_loaded = {"mtime": None}


def ytdlp():
    """The yt_dlp module: the updated copy in the data folder if present, else the built-in one."""
    z = _zip_path()
    mt = os.path.getmtime(z) if os.path.isfile(z) else None
    with _lock:
        if _loaded["mtime"] != mt:
            for k in [k for k in sys.modules if k == "yt_dlp" or k.startswith("yt_dlp.")]:
                del sys.modules[k]
            if z in sys.path:
                sys.path.remove(z)
            if mt:
                sys.path.insert(0, z)
            importlib.invalidate_caches()
            _loaded["mtime"] = mt
        try:
            return importlib.import_module("yt_dlp")
        except Exception as e:
            if mt:  # a broken update never blocks: fall back to the built-in copy
                sys.path.remove(z)
                os.replace(z, z + ".bad")
                _loaded["mtime"] = None
                for k in [k for k in sys.modules if k == "yt_dlp" or k.startswith("yt_dlp.")]:
                    del sys.modules[k]
                try:
                    return importlib.import_module("yt_dlp")
                except Exception:
                    pass
            raise CodedError("YTDLP_MISSING", str(e))


def downloader_version():
    try:
        return ytdlp().version.__version__
    except Exception:
        return None


def update_downloader():
    """Fetch the newest yt-dlp release (a zip that Python can import) into the data folder."""
    os.makedirs(os.path.dirname(_zip_path()), exist_ok=True)
    s = session_for(None)
    r = s.get(YTDLP_URL, timeout=(20, 180), allow_redirects=True)
    r.raise_for_status()
    data = r.content
    if len(data) < 500_000 or b"yt_dlp/__init__.py" not in data:
        raise CodedError("YTDLP_MISSING", "download did not look like yt-dlp")
    tmp = _zip_path() + ".new"
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, _zip_path())
    v = downloader_version()
    db.meta_set("ytdlp_updated_at", str(time.time()))
    db.resolve("YTDLP_OLD")
    db.resolve("YTDLP_MISSING")
    return v


# ------------------------------------------------------------------ links
YT_RX = re.compile(r"(?:youtube\.com/(?:(?:channel/(?P<cid>UC[\w-]{22}))|@(?P<h>[\w.\-]+)|(?:c|user)/(?P<c>[\w.\-]+)))|youtu\.be/")
TT_RX = re.compile(r"tiktok\.com/@(?P<h>[\w.\-]+)")


def parse_link(kind, text):
    """Normalise what the client pasted into a profile URL + display handle."""
    t = (text or "").strip()
    if kind == "youtube":
        if t.startswith("@"):
            t = "https://www.youtube.com/" + t
        elif re.fullmatch(r"UC[\w-]{22}", t):
            t = "https://www.youtube.com/channel/" + t
        m = YT_RX.search(t)
        if not m or not (m.group("cid") or m.group("h") or m.group("c")):
            raise ValueError("Paste the channel link, e.g. https://www.youtube.com/@name")
        if m.group("cid"):
            return f"https://www.youtube.com/channel/{m.group('cid')}", m.group("cid")
        if m.group("h"):
            return f"https://www.youtube.com/@{m.group('h')}", "@" + m.group("h")
        return f"https://www.youtube.com/c/{m.group('c')}", m.group("c")
    if kind == "tiktok":
        if t.startswith("@"):
            t = "https://www.tiktok.com/" + t
        elif re.fullmatch(r"[\w.\-]{2,24}", t):
            t = "https://www.tiktok.com/@" + t
        m = TT_RX.search(t)
        if not m:
            raise ValueError("Paste the profile link, e.g. https://www.tiktok.com/@name")
        return f"https://www.tiktok.com/@{m.group('h')}", "@" + m.group("h")
    raise ValueError("unknown source")


# ------------------------------------------------------------------ yt-dlp plumbing
class _Log:
    def __init__(self):
        self.lines = []

    def debug(self, m):
        pass

    def info(self, m):
        pass

    def warning(self, m):
        self.lines.append(str(m))

    def error(self, m):
        self.lines.append(str(m))


def _cookiefile(src, tmpdir):
    if not src.get("cookies_enc"):
        return None
    from . import instagram, security
    text = security.unseal(src["cookies_enc"])
    try:
        jar = instagram.parse_cookies(text) if not text.lstrip().startswith("# Netscape") else None
    except ValueError:
        jar = None
    path = os.path.join(tmpdir, "cookies.txt")
    with open(path, "w") as f:
        if jar is None:
            f.write(text if text.lstrip().startswith("# Netscape") else "# Netscape HTTP Cookie File\n")
        else:
            dom = ".tiktok.com" if src["kind"] == "tiktok" else ".youtube.com"
            f.write("# Netscape HTTP Cookie File\n")
            for k, v in jar.items():
                f.write(f"{dom}\tTRUE\t/\tTRUE\t{int(time.time()) + 86400 * 365}\t{k}\t{v}\n")
    return path


def _opts(src, tmpdir, **extra):
    o = {"quiet": True, "no_warnings": True, "noprogress": True, "logger": _Log(), "socket_timeout": 30,
         "retries": 3, "extractor_retries": 2, "noplaylist": True, "cachedir": os.path.join(config.DATA_DIR, "tools", "ytdlp-cache"),
         "http_headers": {"Accept-Language": "en-US,en;q=0.9"}}
    if (src.get("proxy_mode") or "phone") == "group":
        ch = store.channel(src["channel_id"])
        g = store.group(ch["group_id"]) if ch else None
        if g:
            o["proxy"] = proxy_url(g)
    ck = _cookiefile(src, tmpdir)
    if ck:
        o["cookiefile"] = ck
    o.update(extra)
    return o


def _classify(e, src):
    """yt-dlp errors → catalogue codes with the right scope."""
    t = str(e).lower()
    site, handle = SITES[src["kind"]], src["handle"]
    if "sign in to confirm" in t or "not a bot" in t or "captcha" in t:
        return CodedError("VID_BOT_CHECK", str(e)[:800], site=site, handle=handle)
    if "429" in t or "too many requests" in t or "rate" in t and "limit" in t:
        return CodedError("VID_RATE_LIMIT", str(e)[:800], site=site, handle=handle)
    if "login" in t or "log in" in t or "cookies" in t and "required" in t or "private" in t and src["kind"] == "tiktok":
        return CodedError("VID_LOGIN_NEEDED", str(e)[:800], site=site, handle=handle)
    if "does not exist" in t or "404" in t or "not found" in t or "unable to find" in t and "user" in t:
        return CodedError("VID_NOT_FOUND", str(e)[:800], site=site, handle=handle)
    if "unsupported url" in t or "unable to extract" in t or "no video formats" in t:
        return CodedError("YTDLP_OLD", str(e)[:800], site=site)
    return CodedError("VID_RATE_LIMIT", str(e)[:800], site=site, handle=handle)


def _post_error(e):
    t = str(e).lower()
    if "live" in t and ("event" in t or "premiere" in t or "is live" in t):
        return CodedError("VID_LIVE", str(e)[:500])
    if any(k in t for k in ("private video", "video unavailable", "removed", "has been terminated", "not available",
                            "members-only", "copyright", "age")):
        return CodedError("VID_GONE", str(e)[:500])
    return None


def _extract(src, url, tmpdir, flat=True, **extra):
    Y = ytdlp()
    opts = _opts(src, tmpdir, extract_flat="in_playlist" if flat else False, skip_download=True, **extra)
    try:
        with Y.YoutubeDL(opts) as ydl:
            return ydl.extract_info(url, download=False)
    except Exception as e:
        raise _classify(e, src)


def _ts(entry):
    for k in ("timestamp", "release_timestamp"):
        if entry.get(k):
            return float(entry[k])
    d = entry.get("upload_date")
    if d and len(d) == 8:
        return calendar.timegm(time.strptime(d, "%Y%m%d"))
    return None


def _video_url(kind, vid, handle=None):
    if kind == "youtube":
        return f"https://www.youtube.com/watch?v={vid}"
    return f"https://www.tiktok.com/{handle or '@_'}/video/{vid}"


# ------------------------------------------------------------------ listing
def youtube_channel_id(src, tmpdir):
    if src.get("ig_user_id"):
        return src["ig_user_id"]
    info = _extract(src, (src.get("url") or "").rstrip("/") + "/videos", tmpdir, playlistend=1)
    cid = info.get("channel_id") or info.get("uploader_id") or info.get("id")
    if not cid or not str(cid).startswith("UC"):
        raise CodedError("VID_NOT_FOUND", f"no channel id in {info.get('id')}", site="YouTube", handle=src["handle"])
    db.run("UPDATE sources SET ig_user_id=?, full_name=?, picture=? WHERE id=?",
           (cid, info.get("channel") or info.get("uploader") or info.get("title"),
            (info.get("thumbnails") or [{}])[-1].get("url"), src["id"]))
    return cid


def rss_latest(cid, session=None):
    """YouTube's public feed: the 15 newest uploads with exact publish times. No quota, no sign-in."""
    s = session or session_for(None)
    r = s.get("https://www.youtube.com/feeds/videos.xml", params={"channel_id": cid}, timeout=(15, 30))
    if r.status_code == 404:
        raise CodedError("VID_NOT_FOUND", "feed 404", site="YouTube", handle=cid)
    r.raise_for_status()
    ns = {"a": "http://www.w3.org/2005/Atom", "yt": "http://www.youtube.com/xml/schemas/2015",
          "media": "http://search.yahoo.com/mrss/"}
    root = ET.fromstring(r.content)
    out = []
    for e in root.findall("a:entry", ns):
        vid = e.findtext("yt:videoId", None, ns)
        pub = e.findtext("a:published", None, ns)
        t = None
        if pub:
            try:
                t = calendar.timegm(time.strptime(pub[:19], "%Y-%m-%dT%H:%M:%S"))
            except ValueError:
                t = None
        title = e.findtext("a:title", None, ns)
        desc = None
        g = e.find("media:group", ns)
        if g is not None:
            desc = g.findtext("media:description", None, ns)
        out.append({"id": vid, "taken_at": t, "title": title, "caption": desc})
    return out


def _insert(src, ch, vid, lane, taken_at, title=None, caption=None):
    now = time.time()
    try:
        with db.tx() as c:
            c.execute("""INSERT INTO media(workspace_id,channel_id,source_id,origin,ext_id,code,kind,lane,taken_at,caption,title,
                         state,created_at,updated_at) VALUES(?,?,?,?,?,NULL,'video',?,?,?,NULL,'found',?,?)""",
                      (ch["workspace_id"], ch["id"], src["id"], src["kind"], vid, lane, taken_at, caption or title, now, now))
        return True
    except Exception:
        return False


def check_source(src, ch, tmpdir):
    """Newest videos. Anything published after the source was added is FRESH."""
    now = time.time()
    fresh = 0
    newest = src["newest_seen"] or 0
    if src["kind"] == "youtube":
        cid = youtube_channel_id(src, tmpdir)
        try:
            items = rss_latest(cid)
        except CodedError:
            raise
        except Exception:  # feed hiccup: fall back to the listing
            items = None
        if items is None:
            info = _extract(src, f"https://www.youtube.com/channel/{cid}/videos", tmpdir, playlistend=15)
            items = [{"id": e.get("id"), "taken_at": _ts(e), "title": e.get("title")} for e in info.get("entries") or []]
        # Shorts live on their own tab; check it too (cheap flat listing)
        try:
            sh = _extract(src, f"https://www.youtube.com/channel/{cid}/shorts", tmpdir, playlistend=10)
            known = {i["id"] for i in items}
            items += [{"id": e.get("id"), "taken_at": _ts(e), "title": e.get("title")}
                      for e in sh.get("entries") or [] if e.get("id") not in known]
        except CodedError:
            pass
    else:
        info = _extract(src, src["url"], tmpdir, playlistend=15)
        if not src.get("full_name"):
            db.run("UPDATE sources SET full_name=?, ig_user_id=? WHERE id=?",
                   (info.get("uploader") or info.get("title"), info.get("uploader_id") or info.get("id"), src["id"]))
        items = [{"id": e.get("id"), "taken_at": _ts(e), "title": e.get("title"), "caption": e.get("description")}
                 for e in info.get("entries") or []]
    first = not db.one("SELECT 1 FROM media WHERE source_id=?", (src["id"],))
    for it in items:
        if not it.get("id"):
            continue
        t = it.get("taken_at")
        if t and t > src["started_at"]:
            fresh += _insert(src, ch, it["id"], "fresh", t, it.get("title"), it.get("caption"))
        elif src["include_old"]:
            _insert(src, ch, it["id"], "backfill", t, it.get("title"), it.get("caption"))
        newest = max(newest, t or 0)
    if first and not fresh and not src["include_old"]:
        # "only new" still starts with the latest video, so Drive is never empty on day one
        dated = [it for it in items if it.get("id")]
        if dated:
            top = max(dated, key=lambda it: it.get("taken_at") or 0)
            _insert(src, ch, top["id"], "fresh", top.get("taken_at"), top.get("title"), top.get("caption"))
            db.run("UPDATE media SET lane='fresh' WHERE source_id=? AND ext_id=? AND state='found'", (src["id"], top["id"]))
            fresh += 1
    db.run("""UPDATE sources SET newest_seen=?, last_check=?, error_code=NULL, error_detail=NULL, next_check=?,
              total_found=(SELECT COUNT(*) FROM media WHERE source_id=?), updated_at=? WHERE id=?""",
           (newest, now, now + random.uniform(*CHECK[src["kind"]]), src["id"], now, src["id"]))
    if fresh:
        db.event("info", src["kind"], f"{ch['title']}: {fresh} new video(s) on {SITES[src['kind']]} — downloading now.",
                 ch["workspace_id"], ch["id"])
    for code in ("VID_BOT_CHECK", "VID_LOGIN_NEEDED", "VID_RATE_LIMIT", "VID_NOT_FOUND"):
        db.resolve(code, ch["workspace_id"], ch["id"])
    return fresh


def discover_older(src, ch, tmpdir):
    """One page of older videos, newest first. The cursor is just a position in the list."""
    cur = src["backfill_cursor"] or ""
    tab, _, n = cur.partition(":") if ":" in cur else ("videos", "", "0")
    start = int(n or 0) + 1
    if src["kind"] == "youtube":
        cid = youtube_channel_id(src, tmpdir)
        info = _extract(src, f"https://www.youtube.com/channel/{cid}/{tab}", tmpdir, playliststart=start, playlistend=start + PAGE - 1)
        entries = info.get("entries") or []
        if len(entries) >= PAGE:
            nxt = f"{tab}:{start + len(entries) - 1}"
        else:  # long videos done → walk the Shorts tab, then finish
            nxt = "shorts:0" if tab == "videos" else None
    else:
        info = _extract(src, src["url"], tmpdir, playliststart=start, playlistend=start + PAGE - 1)
        entries = info.get("entries") or []
        nxt = f"videos:{start + len(entries) - 1}" if len(entries) >= PAGE else None
    base = src["started_at"]
    for i, e in enumerate(entries):
        if not e.get("id"):
            continue
        t = _ts(e)
        lane = "fresh" if t and t > src["started_at"] else "backfill"
        # listings often hide dates; keep the list order (newest first) with a synthetic, descending time
        _insert(src, ch, e["id"], lane, t or (base - 86400 - (start + i) * 60 - (10 ** 6 if tab == "shorts" else 0)),
                e.get("title"), e.get("description"))
    done = 1 if not nxt else 0
    db.run("""UPDATE sources SET backfill_cursor=?, backfill_done=?, total_found=(SELECT COUNT(*) FROM media WHERE source_id=?),
              updated_at=? WHERE id=?""", (nxt or "", done, src["id"], time.time(), src["id"]))


def add_links(src, ch, links):
    """Paste-a-link fallback: the owner pastes video links; they join the ledger as fresh."""
    added = 0
    for raw in links:
        raw = raw.strip()
        if not raw:
            continue
        if src["kind"] == "youtube":
            m = re.search(r"(?:v=|youtu\.be/|shorts/)([\w-]{11})", raw)
        else:
            m = re.search(r"/video/(\d{8,25})", raw)
        if m:
            added += _insert(src, ch, m.group(1), "fresh", time.time(), None, None)
    return added


# ------------------------------------------------------------------ download
def _formats(kind):
    if kind == "youtube":
        # H.264 first (Instagram/Facebook friendly), never above 1080p; audio separately, merged by our ffmpeg
        return ("bv*[height<=1080][vcodec^=avc1]/bv*[height<=1080][vcodec^=avc]/bv*[height<=1080]",
                "ba[ext=m4a]/ba[acodec^=mp4a]/ba")
    # TikTok: the clean "play" file, not the watermarked "download" one
    return ("b[format_note!*=watermark][vcodec!=none][acodec!=none]/b[vcodec!=none][acodec!=none]/b", None)


def _download_one(src, url, fmt, out_base, tmpdir, progress=None):
    Y = ytdlp()

    def hook(d):
        if progress and d.get("status") == "downloading" and d.get("total_bytes"):
            progress(f"Downloading from {SITES[src['kind']]}", d.get("downloaded_bytes", 0) / d["total_bytes"])

    opts = _opts(src, tmpdir, format=fmt, outtmpl=out_base + ".%(ext)s", progress_hooks=[hook],
                 postprocessors=[], merge_output_format=None, overwrites=True, concurrent_fragment_downloads=2)
    try:
        with Y.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
    except Exception as e:
        raise _post_error(e) or _classify(e, src)
    if info.get("is_live") or info.get("live_status") in ("is_live", "is_upcoming"):
        raise CodedError("VID_LIVE", "live")
    files = glob.glob(out_base + ".*")
    files = [f for f in files if not f.endswith((".part", ".ytdl", ".json"))]
    if not files:
        raise CodedError("VID_GONE", "nothing downloaded")
    return files[0], info


def process(m, src, ch, group, progress=None):
    from . import cleaner, igsync
    now = time.time()
    db.run("UPDATE media SET state='downloading', attempts=attempts+1, updated_at=? WHERE id=?", (now, m["id"]))
    folder = igsync.channel_dir(ch)
    ref = m["ref"] or igsync.new_ref()
    db.run("UPDATE media SET ref=? WHERE id=?", (ref, m["id"]))
    tmpdir = tempfile.mkdtemp(prefix=".work-" + ref, dir=folder)
    try:
        dsess, dbox = igsync._drive_ctx(ch, group)
        url = _video_url(src["kind"], m["ext_id"], src["handle"])
        vfmt, afmt = _formats(src["kind"])
        vpath, info = _download_one(src, url, vfmt, os.path.join(tmpdir, "v"), tmpdir, progress)
        raw = vpath
        if afmt:
            apath, _ = _download_one(src, url, afmt, os.path.join(tmpdir, "a"), tmpdir)
            raw = cleaner.merge(vpath, apath, os.path.join(tmpdir, "m.mp4"))
        caption = info.get("description") or m.get("caption")
        used = [r["title"] for r in db.all("SELECT title FROM media WHERE channel_id=? AND title IS NOT NULL", (ch["id"],))]
        title = titles.choose(name=info.get("title"), caption=caption, channel_title=ch["title"],
                              bank=db.jload(ch.get("titles_json"), []), used=used, seed=ref)
        taken = _ts(info) or m["taken_at"]
        if taken and m["taken_at"] != taken:
            db.run("UPDATE media SET taken_at=? WHERE id=?", (taken, m["id"]))
        props = {"dc_ref": ref, "dc_media": m["id"], "dc_lane": m["lane"], "dc_taken": int(taken or 0), "dc_src": src["kind"]}
        igsync.store_video(m, ch, raw, title, caption, folder, ref, dsess, dbox, props, progress)
        return True
    except CodedError as e:
        if e.scope == "post":
            db.run("UPDATE media SET state=?, skip_reason=?, error_code=?, updated_at=? WHERE id=?",
                   ("failed" if e.code == "CLEAN_FAILED" else "skipped", err_info(e.code)["title"], e.code, time.time(), m["id"]))
            return False
        db.run("UPDATE media SET state='found', next_try=?, error_code=?, updated_at=? WHERE id=?",
               (time.time() + 1800, e.code, time.time(), m["id"]))
        raise
    except DriveCastError as e:
        db.run("UPDATE media SET state='found', next_try=?, error_code='DRIVE_QUOTA', updated_at=? WHERE id=?",
               (time.time() + 1800, time.time(), m["id"]))
        db.event("warn", "drive", f"{ch['title']}: {e.user_message} — will retry.", ch["workspace_id"], ch["id"], "DRIVE_QUOTA", str(e))
        return False
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
        if progress:
            igsync.sync_clear(ch["id"])


# ------------------------------------------------------------------ the pass
def _ready_target(ch):
    """About one week of posts kept ready in Drive (≥4, ≤42)."""
    plan = db.jload(ch["plan_json"], {})
    return min(42, max(4, int(plan.get("posts_per_day") or 2) * 7))


def _problem(src, ch, e):
    now = time.time()
    hold = {"VID_RATE_LIMIT": 3 * 3600, "VID_BOT_CHECK": 4 * 3600, "YTDLP_OLD": 6 * 3600, "YTDLP_MISSING": 6 * 3600,
            "VID_LOGIN_NEEDED": 12 * 3600, "VID_NOT_FOUND": 12 * 3600}.get(e.code, 3600)
    db.run("UPDATE sources SET next_check=?, error_code=?, error_detail=?, updated_at=? WHERE id=?",
           (now + hold, e.code, e.detail, now, src["id"]))
    _next[src["id"]] = now + hold
    inf = e.info(site=SITES[src["kind"]], handle=src["handle"])
    db.event("error" if inf.get("fix") else "warn", src["kind"], f"{ch['title']}: {inf['message']}",
             ch["workspace_id"], None if e.scope == "server" else ch["id"], e.code, e.detail)


def source_pass(src, now=None, fast=False):
    """fast = a brand-new source: no waiting between steps and no daily cap until about a week is ready."""
    if not _pass_lock.acquire(blocking=fast):
        return "busy"
    try:
        return _source_pass(src, now, fast)
    finally:
        _pass_lock.release()


def _source_pass(src, now=None, fast=False):
    from . import igsync
    now = now or time.time()
    if not fast and _next.get(src["id"], 0) > now:
        return "waiting"
    ch = store.channel(src["channel_id"])
    if not ch or ch["state"] == "setup":
        return "idle"
    ws = store.workspace(ch["workspace_id"])
    if (ws.get("paused_until") or 0) > now and not ws.get("pause_collect"):
        return "paused"
    g = store.group(ch["group_id"])
    if (src.get("proxy_mode") or "phone") == "group" and (not g or g["status"] == "down"):
        return "blocked"
    if igsync._publishing(ch["id"]):
        return "busy"
    tmpdir = tempfile.mkdtemp(prefix="dc-src-")
    kind = None
    try:
        if (src["next_check"] or 0) <= now:
            kind = "check"
            check_source(src, ch, tmpdir)
        else:
            m = igsync._next_item(src, "fresh")
            if m:
                kind = "fresh"
                process(m, src, ch, g, igsync.sync_progress(ch["id"], "First video" if fast else "New video"))
            elif src["include_old"] and igsync.ready_count(ch["id"]) < _ready_target(ch):
                src = igsync._bump_day("sources", src)
                m = igsync._next_item(src, "backfill")
                if m and (fast or src["day_count"] < min(src["daily_cap"] or SOURCE_DAILY, SOURCE_DAILY)):
                    kind = "backfill"
                    db.run("UPDATE sources SET day_count=day_count+1 WHERE id=?", (src["id"],))
                    process(m, src, ch, g, igsync.sync_progress(ch["id"], "Week of backup"))
                elif not m and not src["backfill_done"]:
                    kind = "discover"
                    discover_older(src, ch, tmpdir)
        if kind:
            _next[src["id"]] = time.time() + random.uniform(*((2, 6) if fast else GAP[src["kind"]]))
        return kind or "idle"
    except CodedError as e:
        if e.scope in ("source", "server", "group", "ig"):
            _problem(src, ch, e)
        else:
            db.event("error", "pipeline", f"{ch['title']}: " + e.info()["message"], ch["workspace_id"], ch["id"], e.code, e.detail)
            _next[src["id"]] = time.time() + 1800
        return "error"
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def kick(ch_id):
    """A new YouTube / TikTok source: check it now and download about a week of videos straight away."""
    if os.environ.get("DC_NO_KICK"):
        return
    ids = [r["id"] for r in db.all("SELECT id FROM sources WHERE channel_id=? AND state='active' AND kind IN ('youtube','tiktok')", (ch_id,))]
    if not ids:
        return
    for i in ids:
        _next.pop(i, None)
        db.run("UPDATE sources SET next_check=0, error_code=NULL WHERE id=?", (i,))

    def run():
        end = time.time() + 2 * 3600
        while time.time() < end:
            did = False
            for i in ids:
                s = db.one("SELECT * FROM sources WHERE id=? AND state='active'", (i,))
                if not s:
                    continue
                try:
                    r = source_pass(s, fast=True)
                except Exception:
                    log.exception("video source kick failed")
                    return
                if r == "error":
                    return
                if r in ("check", "fresh", "backfill", "discover"):
                    did = True
                elif r in ("busy", "waiting"):
                    time.sleep(15)
                    did = True
            if not did:
                return
            time.sleep(random.uniform(2, 6))
    threading.Thread(target=run, name="vid-kick", daemon=True).start()


def tick():
    from . import cleaner
    if db.meta_get("superseded_by"):  # this phone was replaced: never download twice
        return
    srcs = db.all("SELECT * FROM sources WHERE state='active' AND kind IN ('youtube','tiktok')")
    if not srcs:
        return
    # YouTube and TikTok change often: keep the downloader fresh by itself, once a week
    if time.time() - float(db.meta_get("ytdlp_updated_at") or 0) > 7 * 86400 \
            and time.time() - float(db.meta_get("ytdlp_tried_at") or 0) > 6 * 3600:
        db.meta_set("ytdlp_tried_at", str(time.time()))
        try:
            update_downloader()
        except Exception as e:
            log.warning("downloader auto-update failed: %s", e)
    if not cleaner.available():
        db.event("error", "server", err_info("FFMPEG_MISSING")["message"], None, None, "FFMPEG_MISSING")
        return
    for s in srcs:
        try:
            source_pass(s)
        except Exception:
            log.exception("video source %s pass failed", s["id"])
