"""Facebook Pages via the official Graph API — no cookies, no session scraping.

Flow (one per IP group, like the Instagram account):
  owner's Meta app (App ID + App Secret, workspace level)
    -> Facebook Login dialog, the client presses Allow -> a short-lived user token
    -> exchanged for a long-lived user token (~60 days)
    -> /me/accounts gives one long-lived Page token per Page the user administers.
Posting uses the Page token. Every server-side call goes through the group proxy,
so Facebook sees the same IP as that group's YouTube and Instagram traffic.

Graph version is configurable: Meta ships a new one about every few months and the
old ones keep working for ~2 years, but if a field changes the owner can bump it.
"""
import json
import os
import time
from urllib.parse import urlencode

from .errors import CodedError
from .net import AuthRevoked, Permanent, QuotaExceeded, Transient, Uncertain

DEFAULT_VERSION = "v21.0"
DIALOG = "https://www.facebook.com/{v}/dialog/oauth"
GRAPH = "https://graph.facebook.com/{v}"
# Minimum to list pages and publish posts/photos/videos on a Page you administer.
SCOPES = ["pages_show_list", "pages_read_engagement", "pages_manage_posts", "pages_manage_engagement",
          "pages_read_user_content", "publish_video", "business_management"]
IMG = {".jpg", ".jpeg", ".png", ".gif", ".webp"}


def version():
    from . import db
    return db.meta_get("fb_graph_version") or DEFAULT_VERSION


def _g(path=""):
    return GRAPH.format(v=version()) + path


# ------------------------------------------------------------------ OAuth
def consent_url(app_id, redirect_uri, state, scopes=None):
    q = {"client_id": app_id, "redirect_uri": redirect_uri, "state": state,
         "response_type": "code", "scope": ",".join(scopes or SCOPES)}
    return DIALOG.format(v=version()) + "?" + urlencode(q)


def _get(session, path, params, stage, is_post=False, **kw):
    url = _g(path)
    try:
        r = (session.post if is_post else session.get)(url, params=None if is_post else params,
                                                       data=params if is_post else None, timeout=(20, 120), **kw)
    except Exception as e:
        raise CodedError("PROXY_DEAD", f"facebook {stage}: {e}")
    return _check(r, stage)


def _check(r, stage):
    if r.status_code < 400:
        try:
            return r.json()
        except ValueError:
            return {}
    try:
        err = r.json().get("error", {})
    except ValueError:
        err = {"message": r.text[:300]}
    code, sub = err.get("code"), err.get("error_subcode")
    msg = err.get("message", "")
    detail = json.dumps(err)[:800]
    if code in (190, 102, 463, 467) or sub in (458, 460, 463, 464, 467, 492):
        raise CodedError("FB_TOKEN_EXPIRED", detail)
    if code in (10, 200, 294, 299) or sub == 1363047:
        raise CodedError("FB_PERMISSION", detail, perm=msg)
    if code in (4, 17, 32, 613) or (code == 80001):  # rate / throttle
        raise CodedError("FB_RATE_LIMIT", detail)
    if code == 368:  # temporarily blocked for policy
        raise CodedError("FB_BLOCKED", detail)
    if r.status_code >= 500 or code in (1, 2):
        raise CodedError("FB_BUSY", detail)
    raise CodedError("FB_REJECTED", detail, why=msg)


def exchange_code(session, app_id, app_secret, code, redirect_uri):
    """Authorization code -> short-lived user token, then long-lived."""
    short = _get(session, "/oauth/access_token",
                 {"client_id": app_id, "client_secret": app_secret, "redirect_uri": redirect_uri, "code": code},
                 "code exchange")
    if not short.get("access_token"):
        raise CodedError("FB_REJECTED", json.dumps(short)[:300], why="no token returned")
    return long_lived(session, app_id, app_secret, short["access_token"])


def long_lived(session, app_id, app_secret, user_token):
    data = _get(session, "/oauth/access_token",
                {"grant_type": "fb_exchange_token", "client_id": app_id, "client_secret": app_secret,
                 "fb_exchange_token": user_token}, "long-lived token")
    tok = data.get("access_token")
    if not tok:
        raise CodedError("FB_TOKEN_EXPIRED", json.dumps(data)[:300])
    exp = time.time() + int(data.get("expires_in") or 5184000)
    return tok, exp


def me(session, user_token):
    return _get(session, "/me", {"access_token": user_token, "fields": "id,name"}, "me")


def pages(session, user_token):
    """Every Page this user administers, each with its own long-lived Page token."""
    out, after = [], None
    for _ in range(20):
        params = {"access_token": user_token, "limit": 100,
                  "fields": "id,name,access_token,category,tasks,picture{url}"}
        if after:
            params["after"] = after
        data = _get(session, "/me/accounts", params, "list pages")
        for p in data.get("data", []):
            tasks = p.get("tasks") or []
            out.append({"page_id": p["id"], "name": p.get("name"), "category": p.get("category"),
                        "token": p.get("access_token"), "picture": (p.get("picture", {}).get("data") or {}).get("url"),
                        "can_post": "CREATE_CONTENT" in tasks or "MANAGE" in tasks or not tasks})
        after = (data.get("paging", {}).get("cursors", {}) or {}).get("after")
        if not after or not data.get("data"):
            break
    return out


# ------------------------------------------------------------------ publishing (Page token)
def _post(session, path, params, stage, files=None):
    url = _g(path)
    try:
        r = session.post(url, data=params, files=files, timeout=(30, 600))
    except Exception as e:
        raise CodedError("PROXY_DEAD", f"facebook {stage}: {e}")
    return _check(r, stage)


def publish_video(session, page_id, page_token, path, title, description, on_progress=None):
    """Resumable video upload to a Page, then publish. Returns {'post_id','video_id'}.

    Uncertain rule (same as YouTube): once bytes are sent, a lost final response is
    reported as Uncertain and never retried, so a video is never posted twice."""
    size = os.path.getsize(path)
    start = _post(session, f"/{page_id}/videos",
                  {"upload_phase": "start", "file_size": size, "access_token": page_token}, "video start")
    sess_id = start.get("upload_session_id")
    video_id = start.get("video_id")
    if not sess_id:
        raise CodedError("FB_REJECTED", json.dumps(start)[:300], why="no upload session")
    start_offset = int(start.get("start_offset", 0))
    end_offset = int(start.get("end_offset", 0))
    sent_any = False
    with open(path, "rb") as f:
        while start_offset < end_offset:
            f.seek(start_offset)
            chunk = f.read(end_offset - start_offset)
            try:
                r = session.post(_g(f"/{page_id}/videos"),
                                 data={"upload_phase": "transfer", "upload_session_id": sess_id,
                                       "start_offset": start_offset, "access_token": page_token},
                                 files={"video_file_chunk": ("chunk", chunk)}, timeout=(30, 600))
                data = _check(r, "video transfer")
            except CodedError:
                if sent_any:
                    raise Uncertain("Facebook upload was interrupted after sending video data", "transfer")
                raise
            sent_any = True
            start_offset = int(data.get("start_offset", end_offset))
            end_offset = int(data.get("end_offset", end_offset))
            if on_progress and size:
                on_progress(min(1.0, start_offset / size))
    try:
        fin = _post(session, f"/{page_id}/videos",
                    {"upload_phase": "finish", "upload_session_id": sess_id,
                     "description": (description or title)[:9000], "title": title[:255] if title else None,
                     "access_token": page_token}, "video finish")
    except CodedError:
        raise Uncertain("Facebook received the video but did not confirm the post", "finish")
    if not fin.get("success", True):
        raise Uncertain("Facebook did not confirm the video post", json.dumps(fin)[:200])
    return {"post_id": None, "video_id": video_id}


def publish_photos(session, page_id, page_token, paths, message):
    """One photo -> a photo post; several -> one multi-photo post. Returns {'post_id'}."""
    if not paths:
        raise CodedError("FB_REJECTED", "no photos")
    if len(paths) == 1:
        with open(paths[0], "rb") as f:
            out = _post(session, f"/{page_id}/photos",
                        {"caption": (message or "")[:9000], "published": "true", "access_token": page_token},
                        "photo", files={"source": (os.path.basename(paths[0]), f.read())})
        return {"post_id": out.get("post_id") or out.get("id")}
    fbids = []
    for p in paths[:10]:
        with open(p, "rb") as f:
            out = _post(session, f"/{page_id}/photos",
                        {"published": "false", "temporary": "true", "access_token": page_token},
                        "photo upload", files={"source": (os.path.basename(p), f.read())})
        if not out.get("id"):
            raise CodedError("FB_REJECTED", json.dumps(out)[:200], why="photo upload returned no id")
        fbids.append(out["id"])
    params = {"message": (message or "")[:9000], "access_token": page_token}
    for i, fid in enumerate(fbids):
        params[f"attached_media[{i}]"] = json.dumps({"media_fbid": fid})
    out = _post(session, f"/{page_id}/feed", params, "multi-photo post")
    return {"post_id": out.get("id")}


def post_stats(session, page_token, post_ids):
    """Views/reactions per post. Facebook's insight fields vary; this stays best-effort."""
    out = {}
    for pid in post_ids:
        try:
            data = _get(session, f"/{pid}",
                        {"access_token": page_token,
                         "fields": "insights.metric(post_video_views,post_impressions_unique).as(i),"
                                   "likes.summary(true),comments.summary(true),shares"}, "post stats")
        except CodedError:
            continue
        views = None
        for row in (data.get("i", {}).get("data") or []):
            vals = row.get("values") or [{}]
            if row.get("name") == "post_video_views":
                views = vals[0].get("value")
        out[pid] = {"views": views, "likes": (data.get("likes", {}).get("summary", {}) or {}).get("total_count"),
                    "comments": (data.get("comments", {}).get("summary", {}) or {}).get("total_count"),
                    "shares": (data.get("shares", {}) or {}).get("count")}
    return out
