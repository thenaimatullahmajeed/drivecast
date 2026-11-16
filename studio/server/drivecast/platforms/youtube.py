"""YouTube adapter: resumable direct-publish upload, channel identity and stats.

Upload safety: before the final response arrives we cannot know whether YouTube
created the video, so any break after bytes were sent ends as ``Uncertain`` and
is never retried automatically.
"""
import json
import os
import re
import time

from ..google import YOUTUBE_SCOPES, authed
from ..net import AuthRevoked, Permanent, QuotaExceeded, Transient, Uncertain

PLATFORM = "youtube"
SCOPES = YOUTUBE_SCOPES
API = "https://www.googleapis.com/youtube/v3"
UPLOAD = "https://www.googleapis.com/upload/youtube/v3/videos"
CHUNK = 8 * 1024 * 1024
MIME = {".mp4": "video/mp4", ".mov": "video/quicktime", ".m4v": "video/x-m4v", ".webm": "video/webm",
        ".mkv": "video/x-matroska", ".avi": "video/x-msvideo", ".3gp": "video/3gpp", ".mpeg": "video/mpeg",
        ".mpg": "video/mpeg", ".wmv": "video/x-ms-wmv", ".flv": "video/x-flv"}


def title_from_filename(name):
    """File name without extension, cleaned for YouTube (max 100 chars, no < >)."""
    base = os.path.splitext(name)[0]
    base = base.replace("<", "").replace(">", "")
    base = re.sub(r"\s+", " ", base).strip()
    if len(base) > 100:
        cut = base[:100]
        base = cut.rsplit(" ", 1)[0] if " " in cut[60:] else cut
    return base or "Untitled"


def _reason(r):
    try:
        errs = r.json().get("error", {}).get("errors", [])
        return ",".join(e.get("reason", "") for e in errs) or r.json().get("error", {}).get("status", "")
    except ValueError:
        return ""


def _raise_for(r, stage):
    reason = _reason(r)
    if r.status_code == 403 and ("quota" in reason.lower() or "uploadLimitExceeded" in reason):
        raise QuotaExceeded("YouTube daily upload limit reached for this app or channel", reason)
    if r.status_code == 403 and "forbidden" in reason.lower():
        raise AuthRevoked("YouTube refused this channel's access. Reconnect it.", reason)
    if r.status_code in (429,) or r.status_code >= 500:
        raise Transient(f"YouTube is busy ({stage})", f"{r.status_code} {reason}")
    raise Permanent(f"YouTube rejected the video ({stage}): {reason or r.status_code}", r.text[:400])


def whoami(session, box):
    r = authed(session, box, "GET", API + "/channels",
               params={"part": "snippet,statistics", "mine": "true"})
    if r.status_code >= 400:
        _raise_for(r, "channel lookup")
    items = r.json().get("items", [])
    if not items:
        raise Permanent("This Google account has no YouTube channel. Create one or pick the right account.")
    it = items[0]
    sn, st = it.get("snippet", {}), it.get("statistics", {})
    thumb = (sn.get("thumbnails", {}).get("default") or {}).get("url")
    return {"external_id": it["id"], "title": sn.get("title", "YouTube channel"),
            "handle": sn.get("customUrl"), "thumbnail": thumb,
            "subscribers": int(st.get("subscriberCount", 0) or 0), "total_views": int(st.get("viewCount", 0) or 0)}


def publish(session, box, path, title, description, on_progress=None):
    """Upload and publish Public right now. Returns {'video_id','visibility'}."""
    size = os.path.getsize(path)
    mime = MIME.get(os.path.splitext(path)[1].lower(), "video/mp4")
    body = {"snippet": {"title": title, "description": description[:5000], "categoryId": "22"},
            "status": {"privacyStatus": "public", "selfDeclaredMadeForKids": False}}
    # 1) open a resumable session. Nothing exists on YouTube yet: errors here are safe.
    try:
        r = authed(session, box, "POST", UPLOAD, params={"uploadType": "resumable", "part": "snippet,status"},
                   headers={"Content-Type": "application/json; charset=UTF-8",
                            "X-Upload-Content-Length": str(size), "X-Upload-Content-Type": mime},
                   data=json.dumps(body), timeout=(20, 60))
    except (AuthRevoked, QuotaExceeded, Permanent, Transient):
        raise
    except Exception as e:
        raise Transient("Could not start the upload", str(e))
    if r.status_code not in (200, 201) or "Location" not in r.headers:
        _raise_for(r, "start")
    location = r.headers["Location"]
    # 2) send bytes. From here on a lost response means "uncertain".
    sent = 0
    with open(path, "rb") as f:
        while True:
            f.seek(sent)
            chunk = f.read(CHUNK)
            end = sent + len(chunk) - 1
            headers = {"Content-Length": str(len(chunk)), "Content-Range": f"bytes {sent}-{end}/{size}"}
            try:
                r = session.put(location, data=chunk, headers=headers, timeout=(20, 300))
            except Exception as e:
                sent = _resume_offset(session, location, size, e)
                if sent is None:
                    return _final_from_status(session, location, size)
                continue
            if r.status_code in (200, 201):
                return _done(r)
            if r.status_code == 308:
                rng = r.headers.get("Range")
                sent = int(rng.split("-")[1]) + 1 if rng else 0
                if on_progress:
                    on_progress(sent / size)
                continue
            if r.status_code >= 500:
                sent = _resume_offset(session, location, size, r.status_code)
                if sent is None:
                    return _final_from_status(session, location, size)
                continue
            if r.status_code in (400, 403, 404):
                # YouTube refused mid-way; the session is dead and no video was finalised.
                _raise_for(r, "upload")
            raise Uncertain("The upload ended without a clear answer from YouTube", f"HTTP {r.status_code}")


def _resume_offset(session, location, size, cause, tries=4):
    """Ask YouTube how much it has. None means it says the upload finished."""
    for i in range(tries):
        time.sleep(min(30, 3 * (2 ** i)))
        try:
            r = session.put(location, headers={"Content-Length": "0", "Content-Range": f"bytes */{size}"},
                            timeout=(20, 60))
        except Exception:
            continue
        if r.status_code == 308:
            rng = r.headers.get("Range")
            return int(rng.split("-")[1]) + 1 if rng else 0
        if r.status_code in (200, 201):
            return None
        if r.status_code == 404:
            raise Uncertain("YouTube lost the upload session after bytes were sent", str(cause))
    raise Uncertain("Connection dropped during upload and YouTube could not be reached to confirm", str(cause))


def _final_from_status(session, location, size):
    r = session.put(location, headers={"Content-Length": "0", "Content-Range": f"bytes */{size}"}, timeout=(20, 60))
    if r.status_code in (200, 201):
        return _done(r)
    raise Uncertain("YouTube finished the upload but the result could not be read", f"HTTP {r.status_code}")


def _done(r):
    try:
        data = r.json()
    except ValueError:
        raise Uncertain("YouTube accepted the upload but returned no video ID")
    vid = data.get("id")
    if not vid:
        raise Uncertain("YouTube accepted the upload but returned no video ID")
    return {"video_id": vid, "visibility": data.get("status", {}).get("privacyStatus", "unknown")}


def video_stats(session, box, ids):
    out = {}
    for i in range(0, len(ids), 50):
        r = authed(session, box, "GET", API + "/videos",
                   params={"part": "statistics,status", "id": ",".join(ids[i:i + 50])})
        if r.status_code >= 400:
            _raise_for(r, "stats")
        for it in r.json().get("items", []):
            st = it.get("statistics", {})
            out[it["id"]] = {"views": int(st.get("viewCount", 0) or 0), "likes": int(st.get("likeCount", 0) or 0),
                             "comments": int(st.get("commentCount", 0) or 0),
                             "visibility": it.get("status", {}).get("privacyStatus")}
    return out


def channel_stats(session, box):
    me = whoami(session, box)
    return {"subscribers": me["subscribers"], "total_views": me["total_views"], "title": me["title"],
            "handle": me["handle"], "thumbnail": me["thumbnail"]}
