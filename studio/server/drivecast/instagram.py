"""Minimal Instagram web client (the same endpoints Instagram's own web app and
gallery-dl use). Every request goes through the Pool's proxy with that
pool's own signed-in test account. Nothing here logs in with a password;
the owner pastes cookies from a browser.

Every failure becomes an errors.CodedError so callers stop only the right scope.
"""
import json
import random
import re
import time
import xml.etree.ElementTree as ET

from .errors import CodedError

APP_ID = "936619743392459"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/131.0.0.0 Safari/537.36")
BASE = "https://www.instagram.com"
REQUIRED = ("sessionid", "ds_user_id", "csrftoken")


# ------------------------------------------------------------------ cookies
def parse_cookies(text):
    """Accepts a Netscape cookies.txt, a JSON export, or a "name=value; name=value" header."""
    text = (text or "").strip()
    jar = {}
    if not text:
        raise ValueError("Paste the cookies first.")
    if text.startswith("[") or text.startswith("{"):
        try:
            data = json.loads(text)
        except ValueError:
            raise ValueError("That looks like JSON but it is not valid.")
        items = data if isinstance(data, list) else data.get("cookies", [])
        for c in items:
            if isinstance(c, dict) and "instagram.com" in str(c.get("domain", "instagram.com")):
                jar[c.get("name")] = c.get("value")
    elif "\t" in text:
        for line in text.splitlines():
            if line.startswith("#HttpOnly_"):
                line = line[len("#HttpOnly_"):]
            if not line or line.startswith("#"):
                continue
            parts = line.split("\t")
            if len(parts) >= 7 and "instagram.com" in parts[0]:
                jar[parts[5]] = parts[6].strip()
    else:
        for part in re.split(r";\s*|\n", text):
            if "=" in part:
                k, v = part.split("=", 1)
                jar[k.strip()] = v.strip()
    jar = {k: v for k, v in jar.items() if k and v}
    missing = [k for k in REQUIRED if k not in jar]
    if missing:
        raise ValueError("These Instagram cookies are missing: " + ", ".join(missing) +
                         ". Export them while signed in to instagram.com.")
    return jar


# ------------------------------------------------------------------ session
class Client:
    """One Instagram test account = one stable identity: always the same browser
    User-Agent (the one the cookies came from), the same language, the same IP."""

    def __init__(self, session, cookies, min_gap=(7, 14), user_agent=None, language=None):
        self.s = session
        self.cookies = cookies
        self.min_gap = min_gap
        self.last = 0.0
        for k, v in cookies.items():
            self.s.cookies.set(k, v, domain=".instagram.com", path="/")
        self.s.headers.update({
            "User-Agent": user_agent or UA, "X-IG-App-ID": APP_ID, "X-ASBD-ID": "129477", "X-IG-WWW-Claim": "0",
            "X-Requested-With": "XMLHttpRequest", "X-CSRFToken": cookies.get("csrftoken", ""),
            "Accept": "*/*", "Accept-Language": language or "en-US,en;q=0.9", "Referer": BASE + "/",
        })

    def _pace(self):
        gap = random.uniform(*self.min_gap)
        wait = self.last + gap - time.time()
        if wait > 0:
            time.sleep(wait)
        self.last = time.time()

    def get(self, path, params=None, handle=None):
        self._pace()
        try:
            r = self.s.get(BASE + path, params=params, timeout=(20, 40), allow_redirects=False)
        except Exception as e:
            raise CodedError("PROXY_DEAD", f"instagram {path}: {e}")
        return classify(r, handle)

    def whoami(self):
        """Signed-in check the way ReelVault 5.1 proved works: the home page of a signed-in
        browser carries fb_dtsg (and the viewer id). Never uses the mobile-app endpoints."""
        self._pace()
        try:
            r = self.s.get(BASE + "/", timeout=(20, 40), allow_redirects=False,
                           headers={"Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                                    "X-Requested-With": None, "X-IG-App-ID": None, "X-ASBD-ID": None, "X-IG-WWW-Claim": None,
                                    "X-CSRFToken": None, "Sec-Fetch-Mode": "navigate", "Sec-Fetch-Dest": "document",
                                    "Sec-Fetch-Site": "none", "Upgrade-Insecure-Requests": "1"})
        except Exception as e:
            raise CodedError("PROXY_DEAD", f"instagram home: {e}")
        loc = r.headers.get("Location", "")
        uid = str(self.cookies.get("ds_user_id") or "")
        if r.status_code in (301, 302, 303, 307, 308):
            if "challenge" in loc or "checkpoint" in loc:
                raise CodedError("IG_CHALLENGE", f"redirect to {loc}")
            if "/accounts/login" in loc or "/accounts/logout" in loc:
                raise CodedError("IG_COOKIES_EXPIRED", f"redirect to {loc}")
            # any other redirect (language, www, consent) says nothing about the sign-in
            raise CodedError("IG_UNKNOWN", f"home redirected to {loc}")
        if r.status_code == 429:
            raise CodedError("IG_RATE_LIMIT", "429 on home page")
        if r.status_code >= 400:
            raise CodedError("IG_UNKNOWN", f"home {r.status_code}")
        html = r.text or ""
        if "checkpoint_required" in html or "/challenge/" in html:
            raise CodedError("IG_CHALLENGE", "challenge page")
        # signed in: the page knows who we are (the account id from the cookies, or the page token)
        signed = (uid and (f'"{uid}"' in html or f"viewerId\":\"{uid}" in html or f'"viewerId":"{uid}"' in html)) \
            or "fb_dtsg" in html or "DTSGInitialData" in html or '"is_logged_in":true' in html
        # signed out: only when the page clearly says so — never guess
        out = 'id="loginForm"' in html or '"viewerId":null' in html or '"is_logged_in":false' in html \
            or ("/accounts/login" in html and not signed and len(html) < 200_000 and '"username"' not in html)
        if out and not signed:
            raise CodedError("IG_COOKIES_EXPIRED", "home page is not signed in")
        if not signed:
            raise CodedError("IG_UNKNOWN", f"home page did not confirm the account ({len(html)} bytes)")
        m = re.search(r'"username":"([A-Za-z0-9._]{1,30})"', html)
        return {"username": m.group(1) if m else None, "id": uid}

    def profile(self, handle):
        handle = clean_handle(handle)
        data = self.get("/api/v1/users/web_profile_info/", {"username": handle}, handle)
        u = (data.get("data") or {}).get("user")
        if not u:
            raise CodedError("IG_PROFILE_NOT_FOUND", json.dumps(data)[:500], handle=handle)
        return {
            "id": str(u.get("id")), "username": u.get("username") or handle, "full_name": u.get("full_name"),
            "picture": u.get("profile_pic_url"), "is_private": bool(u.get("is_private")),
            "followed": bool(u.get("followed_by_viewer")),
            "posts": (u.get("edge_owner_to_timeline_media") or {}).get("count"),
        }

    def feed(self, user_id, max_id=None, handle=None):
        params = {"count": 12}
        if max_id:
            params["max_id"] = max_id
        data = self.get(f"/api/v1/feed/user/{user_id}/", params, handle)
        return [parse_item(i) for i in data.get("items", [])], (data.get("next_max_id") if data.get("more_available") else None)

    def media(self, pk, handle=None):
        data = self.get(f"/api/v1/media/{pk}/info/", None, handle)
        items = data.get("items") or []
        if not items:
            raise CodedError("IG_MEDIA_GONE", json.dumps(data)[:300])
        return parse_item(items[0])

    def download(self, url, path, on_progress=None):
        try:
            r = self.s.get(url, stream=True, timeout=(20, 300), headers={"Referer": BASE + "/"})
        except Exception as e:
            raise CodedError("PROXY_DEAD", f"download: {e}")
        if r.status_code in (403, 404, 410):
            raise CodedError("IG_MEDIA_GONE", f"cdn {r.status_code}")
        if r.status_code >= 400:
            raise CodedError("IG_UNKNOWN", f"cdn {r.status_code}")
        size = 0
        with open(path, "wb") as f:
            for part in r.iter_content(1 << 20):
                f.write(part)
                size += len(part)
                if on_progress:
                    on_progress(size)
        if size < 1024:
            raise CodedError("IG_MEDIA_GONE", "empty download")
        return size


def clean_handle(h):
    h = (h or "").strip()
    h = re.sub(r"^https?://(www\.)?instagram\.com/", "", h)
    h = h.lstrip("@").split("/")[0].split("?")[0].strip()
    if not re.fullmatch(r"[A-Za-z0-9._]{1,30}", h):
        raise ValueError("That is not an Instagram username or profile link.")
    return h.lower()


def classify(r, handle=None):
    """HTTP answer → data, or the right CodedError."""
    body = r.text[:3000] if r.content else ""
    low = body.lower()
    loc = r.headers.get("Location", "")
    if r.status_code in (301, 302, 303, 307, 308):
        if "login" in loc or "accounts" in loc:
            raise CodedError("IG_COOKIES_EXPIRED", f"redirect to {loc}")
        if "challenge" in loc:
            raise CodedError("IG_CHALLENGE", f"redirect to {loc}")
        raise CodedError("IG_HOME_REDIRECT", f"redirect to {loc}", handle=handle)
    bad = r.status_code >= 400  # words inside a normal 200 answer (a caption!) must never trip these
    if bad and ("checkpoint_required" in low or "challenge_required" in low):
        raise CodedError("IG_CHALLENGE", body)
    if bad and "feedback_required" in low:
        raise CodedError("IG_FEEDBACK", body)
    if r.status_code == 429 or (bad and ("please wait a few minutes" in low or "rate limit" in low)):
        raise CodedError("IG_RATE_LIMIT", body)
    if "login_required" in low or r.status_code == 401:
        raise CodedError("IG_COOKIES_EXPIRED", body)
    if r.status_code == 404:
        raise CodedError("IG_PROFILE_NOT_FOUND" if handle else "IG_MEDIA_GONE", body, handle=handle)
    if r.status_code == 403:
        raise CodedError("IG_COOKIES_EXPIRED" if "csrf" in low or "login" in low else "IG_UNKNOWN", body, handle=handle)
    if r.status_code >= 500:
        raise CodedError("IG_UNKNOWN", f"{r.status_code} {body}", handle=handle)
    if "text/html" in r.headers.get("Content-Type", ""):
        raise CodedError("IG_HOME_REDIRECT", "html instead of data", handle=handle)
    try:
        data = r.json()
    except ValueError:
        raise CodedError("IG_UNKNOWN", body, handle=handle)
    if isinstance(data, dict) and data.get("status") == "fail":
        msg = (data.get("message") or "").lower()
        if "checkpoint" in msg or "challenge" in msg:
            raise CodedError("IG_CHALLENGE", body)
        if "feedback" in msg:
            raise CodedError("IG_FEEDBACK", body)
        if "login" in msg:
            raise CodedError("IG_COOKIES_EXPIRED", body)
        if "wait" in msg:
            raise CodedError("IG_RATE_LIMIT", body)
        raise CodedError("IG_UNKNOWN", body, handle=handle)
    return data


# ------------------------------------------------------------------ items
def _best_image(node):
    cands = ((node.get("image_versions2") or {}).get("candidates") or [])
    if not cands:
        return None
    c = max(cands, key=lambda c: (c.get("width") or 0) * (c.get("height") or 0))
    return {"url": c.get("url"), "width": c.get("width"), "height": c.get("height")}


def _best_progressive(node):
    vs = node.get("video_versions") or []
    if not vs:
        return None
    v = max(vs, key=lambda v: (v.get("width") or 0) * (v.get("height") or 0))
    return {"url": v.get("url"), "width": v.get("width"), "height": v.get("height")}


def parse_dash(manifest):
    """Highest video representation + best audio from Instagram's DASH manifest."""
    if not manifest:
        return None
    try:
        root = ET.fromstring(manifest)
    except ET.ParseError:
        return None
    best_v, best_a = None, None
    for rep in root.iter():
        if not rep.tag.endswith("Representation"):
            continue
        base = next((c.text for c in rep if c.tag.endswith("BaseURL") and c.text), None)
        if not base:
            continue
        mime = rep.get("mimeType") or ""
        parent_mime = ""
        bw = int(rep.get("bandwidth") or 0)
        h, w = int(rep.get("height") or 0), int(rep.get("width") or 0)
        if "video" in mime or "video" in parent_mime or h:
            if not best_v or (h, bw) > (best_v["height"], best_v["bw"]):
                best_v = {"url": base.strip(), "height": h, "width": w, "bw": bw}
        elif "audio" in mime or rep.get("audioSamplingRate"):
            if not best_a or bw > best_a["bw"]:
                best_a = {"url": base.strip(), "bw": bw}
    if not best_v:
        return None
    return {"video": best_v, "audio": best_a}


def parse_item(it):
    mt = it.get("media_type")
    kind = {1: "photo", 2: "video", 8: "carousel"}.get(mt, "photo")
    cap = it.get("caption") or {}
    out = {
        "pk": str(it.get("pk") or it.get("id", "")).split("_")[0], "code": it.get("code"),
        "taken_at": it.get("taken_at"), "caption": cap.get("text") if isinstance(cap, dict) else None,
        "kind": kind, "product": it.get("product_type"), "duration": it.get("video_duration"),
        "has_audio": it.get("has_audio"), "video": None, "dash": None, "images": [],
    }
    if kind == "video":
        out["video"] = _best_progressive(it)
        out["dash"] = parse_dash(it.get("video_dash_manifest"))
    elif kind == "photo":
        img = _best_image(it)
        out["images"] = [img] if img else []
    else:
        kids = it.get("carousel_media") or []
        out["images"] = [i for i in (_best_image(k) for k in kids if k.get("media_type") == 1) if i]
        out["has_video_children"] = any(k.get("media_type") == 2 for k in kids)
    return out


def best_video_plan(item):
    """Choose the highest resolution: DASH (separate video+audio) when it beats the progressive file."""
    prog, dash = item.get("video"), item.get("dash")
    ph = min(prog.get("width") or 0, prog.get("height") or 0) if prog else 0
    if dash and dash.get("audio"):
        dv = dash["video"]
        dh = min(dv.get("width") or dv.get("height") or 0, dv.get("height") or 0) or dv.get("height") or 0
        if dh > ph:
            return {"mode": "dash", "video": dv["url"], "audio": dash["audio"]["url"], "short": dh}
    if prog:
        return {"mode": "progressive", "video": prog["url"], "audio": None, "short": ph}
    if dash:
        return {"mode": "dash", "video": dash["video"]["url"], "audio": (dash.get("audio") or {}).get("url"),
                "short": dash["video"].get("height") or 0}
    return None
