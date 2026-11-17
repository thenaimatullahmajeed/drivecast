"""Instagram as a DESTINATION through Meta's official Content Publishing API.

Two ways to connect a client's Instagram Professional (Business/Creator) account:
  • via Facebook  — the Instagram account linked to a Facebook Page the client already
                    connected; posts with that Page token on graph.facebook.com.
  • via Instagram — "Instagram Login" (the client presses Allow on instagram.com);
                    a 60-day token on graph.instagram.com, refreshed automatically.
No passwords, no cookies for posting.

Publishing: Instagram fetches the file itself from a short-lived public link on this
panel (/media/<token>), we wait until the container is FINISHED, then media_publish.
Once media_publish is sent, a lost answer is Uncertain and never retried (no doubles).
"""
import json
import os
import secrets
import threading
import time
from urllib.parse import urlencode

from . import db
from .errors import CodedError
from .net import Uncertain

FB_GRAPH = "https://graph.facebook.com/{v}"
IG_GRAPH = "https://graph.instagram.com/{v}"
IG_AUTH = "https://www.instagram.com/oauth/authorize"
IG_TOKEN = "https://api.instagram.com/oauth/access_token"
IG_SCOPES = ["instagram_business_basic", "instagram_business_content_publish"]
DAILY_LIMIT = 50          # Instagram's API publishing limit per account per 24 h (kept conservative)
POLL_EVERY, POLL_MAX = 6, 15 * 60

# ------------------------------------------------------------------ public media links
_links = {}
_lock = threading.Lock()


def share(path, ttl=3600):
    """A one-hour unguessable link Instagram can fetch the file from."""
    base = (db.meta_get("public_url_override") or db.meta_get("public_url") or "").rstrip("/")
    if not base.startswith("https://"):
        raise CodedError("IGD_NO_PUBLIC_URL", base or "none")
    tok = secrets.token_urlsafe(24)
    with _lock:
        now = time.time()
        for k in [k for k, v in _links.items() if v[1] < now]:
            _links.pop(k, None)
        _links[tok] = (path, now + ttl)
    ext = os.path.splitext(path)[1].lower() or ".bin"
    return f"{base}/media/{tok}{ext}", tok


def unshare(tok):
    with _lock:
        _links.pop(tok, None)


def resolve_link(tok):
    tok = tok.split(".")[0]
    with _lock:
        v = _links.get(tok)
    if not v or v[1] < time.time() or not os.path.isfile(v[0]):
        return None
    return v[0]


# ------------------------------------------------------------------ API plumbing
def _version():
    from . import facebook
    return facebook.version()


def _base(via):
    return (FB_GRAPH if via == "facebook" else IG_GRAPH).format(v=_version())


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
    code, sub, msg = err.get("code"), err.get("error_subcode"), err.get("message", "")
    detail = json.dumps(err)[:800]
    if code in (190, 102, 463, 467) or sub in (458, 460, 463, 464, 467, 492):
        raise CodedError("IGD_TOKEN_EXPIRED", detail)
    if sub == 2207042 or code == 9 or "limit" in msg.lower() and "publish" in msg.lower():
        raise CodedError("IGD_LIMIT", detail)
    if code in (4, 17, 32, 613):
        raise CodedError("FB_RATE_LIMIT", detail)
    if code in (10, 200) or sub == 2207050:
        raise CodedError("FB_PERMISSION", detail, perm=msg)
    if r.status_code >= 500 or code in (1, 2):
        raise CodedError("FB_BUSY", detail)
    raise CodedError("IGD_MEDIA", detail, why=err.get("error_user_msg") or msg)


def _call(session, via, method, path, params, stage):
    try:
        if method == "GET":
            r = session.get(_base(via) + path, params=params, timeout=(20, 90))
        else:
            r = session.post(_base(via) + path, data=params, timeout=(20, 120))
    except Exception as e:
        raise CodedError("PROXY_DEAD", f"instagram {stage}: {e}")
    return _check(r, stage)


# ------------------------------------------------------------------ Instagram Login (OAuth)
def consent_url(app_id, redirect_uri, state):
    q = {"client_id": app_id, "redirect_uri": redirect_uri, "response_type": "code", "state": state,
         "scope": ",".join(IG_SCOPES), "enable_fb_login": "0", "force_authentication": "1"}
    return IG_AUTH + "?" + urlencode(q)


def exchange_code(session, app_id, app_secret, code, redirect_uri):
    try:
        r = session.post(IG_TOKEN, data={"client_id": app_id, "client_secret": app_secret, "grant_type": "authorization_code",
                                         "redirect_uri": redirect_uri, "code": code}, timeout=(20, 60))
    except Exception as e:
        raise CodedError("PROXY_DEAD", f"instagram token: {e}")
    data = _check(r, "code exchange")
    if "data" in data and data["data"]:
        data = data["data"][0]
    short = data.get("access_token")
    if not short:
        raise CodedError("IGD_TOKEN_EXPIRED", json.dumps(data)[:300])
    try:
        r = session.get("https://graph.instagram.com/access_token",
                        params={"grant_type": "ig_exchange_token", "client_secret": app_secret, "access_token": short},
                        timeout=(20, 60))
    except Exception as e:
        raise CodedError("PROXY_DEAD", f"instagram long token: {e}")
    ll = _check(r, "long-lived token")
    return ll.get("access_token") or short, time.time() + int(ll.get("expires_in") or 3600)


def refresh(session, token):
    r = session.get("https://graph.instagram.com/refresh_access_token",
                    params={"grant_type": "ig_refresh_token", "access_token": token}, timeout=(20, 60))
    d = _check(r, "refresh")
    return d.get("access_token") or token, time.time() + int(d.get("expires_in") or 5184000)


def me(session, token):
    d = _call(session, "instagram", "GET", "/me",
              {"fields": "user_id,username,profile_picture_url,account_type", "access_token": token}, "me")
    if d.get("account_type") and d["account_type"] not in ("BUSINESS", "MEDIA_CREATOR", "CREATOR"):
        raise CodedError("IGD_NOT_PRO", d["account_type"])
    return {"ig_user_id": str(d.get("user_id") or d.get("id")), "username": d.get("username"),
            "picture": d.get("profile_picture_url")}


def linked_account(session, page_id, page_token):
    """The Instagram Professional account linked to a Facebook Page, if any."""
    d = _call(session, "facebook", "GET", f"/{page_id}",
              {"fields": "instagram_business_account{id,username,profile_picture_url}", "access_token": page_token}, "linked ig")
    a = d.get("instagram_business_account")
    if not a:
        return None
    return {"ig_user_id": a["id"], "username": a.get("username"), "picture": a.get("profile_picture_url")}


# ------------------------------------------------------------------ publishing
def published_today(dest_id):
    return db.one("""SELECT COUNT(*) n FROM uploads WHERE destination=? AND status IN ('published','uncertain','uploading')
                     AND reserved_at>?""", (f"instagram:{dest_id}", time.time() - 86400))["n"]


def _wait(session, via, token, cid, stage):
    t0 = time.time()
    while True:
        d = _call(session, via, "GET", f"/{cid}", {"fields": "status_code,status", "access_token": token}, stage)
        st = d.get("status_code")
        if st == "FINISHED":
            return
        if st in ("ERROR", "EXPIRED"):
            raise CodedError("IGD_MEDIA", json.dumps(d)[:400], why=d.get("status") or st)
        if time.time() - t0 > POLL_MAX:
            raise CodedError("FB_BUSY", f"container {cid} still {st}")
        time.sleep(POLL_EVERY)


def publish(session, dest, token, items, caption, on_progress=None):
    """items: list of (path, 'video'|'photo'). One video → Reel; one photo → image; several → carousel.
    Returns {'post_id'}."""
    via, uid = dest["via"], dest["ig_user_id"]
    caption = (caption or "")[:2200]
    shared = []
    try:
        def container(path, kind, child=False):
            url, tok = share(path)
            shared.append(tok)
            p = {"access_token": token}
            if kind == "video":
                p.update({"media_type": "VIDEO" if child else "REELS", "video_url": url})
                if not child:
                    p["share_to_feed"] = "true"
            else:
                p["image_url"] = url
            if child:
                p["is_carousel_item"] = "true"
            else:
                p["caption"] = caption
            d = _call(session, via, "POST", f"/{uid}/media", p, "create container")
            if not d.get("id"):
                raise CodedError("IGD_MEDIA", json.dumps(d)[:300], why="no container id")
            return d["id"]

        items = items[:10]
        if len(items) == 1:
            cid = container(*items[0])
            if on_progress:
                on_progress(0.4)
            _wait(session, via, token, cid, "processing")
        else:
            kids = []
            for i, (p, k) in enumerate(items):
                kid = container(p, k, child=True)
                if k == "video":
                    _wait(session, via, token, kid, "processing")
                kids.append(kid)
                if on_progress:
                    on_progress(0.6 * (i + 1) / len(items))
            d = _call(session, via, "POST", f"/{uid}/media",
                      {"media_type": "CAROUSEL", "children": ",".join(kids), "caption": caption, "access_token": token},
                      "carousel container")
            cid = d.get("id")
            _wait(session, via, token, cid, "processing")
        if on_progress:
            on_progress(0.9)
        # point of no return: from here a lost answer is Uncertain
        try:
            out = _call(session, via, "POST", f"/{uid}/media_publish", {"creation_id": cid, "access_token": token}, "publish")
        except CodedError as e:
            if e.code in ("PROXY_DEAD", "FB_BUSY"):
                raise Uncertain("Instagram may have published this post — the answer was lost", e.detail)
            raise
        if not out.get("id"):
            raise Uncertain("Instagram did not confirm the post", json.dumps(out)[:200])
        return {"post_id": out["id"]}
    finally:
        for t in shared:
            unshare(t)
