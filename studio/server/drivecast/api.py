"""JSON API and the public sign-in link pages."""
import html
import json
import re
import time

from . import VERSION, db, drive, insights, maintenance, scheduler, security, store, supervisor
from .google import DRIVE_SCOPES, TokenBox, consent_url, exchange
from .net import DriveCastError, check_group, parse_proxy, probe, session_for
from .platforms import get as platform
from .timeplan import AUDIENCES, normalize_plan

ROUTES = []
SESSION_DAYS = 30
LINK_MINUTES = 30
STARTED = time.time()
_login_fail = {}


class FileOut:
    def __init__(self, data, name, ctype="application/octet-stream"):
        self.data, self.name, self.ctype = data, name, ctype


class HttpError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status, self.message = status, message


def route(method, pattern, auth="user"):
    def deco(fn):
        ROUTES.append((method, re.compile("^" + pattern + "$"), auth, fn))
        return fn
    return deco


def need(body, *keys):
    missing = [k for k in keys if body.get(k) in (None, "")]
    if missing:
        raise HttpError(400, "Missing: " + ", ".join(missing))


def public_base(req):
    url = db.meta_get("public_url_override") or db.meta_get("public_url")
    if url:
        return url.rstrip("/")
    proto = req.headers.get("X-Forwarded-Proto") or ("https" if req.secure else "http")
    return f"{proto}://{req.headers.get('Host', 'localhost')}"


def user_view(u):
    return {"id": u["id"], "name": u["name"], "email": u["email"], "role": u["role"], "timezone": u["timezone"],
            "workspace_id": u["workspace_id"], "status": u["status"]}


# ---------------------------------------------------------------- accounts
def _create_user(name, email, password, role, tz, limits=None, invite=None):
    if len(password or "") < 8:
        raise HttpError(400, "Password must be at least 8 characters")
    if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email or ""):
        raise HttpError(400, "Enter a valid email")
    if db.one("SELECT 1 FROM users WHERE email=?", (email,)):
        raise HttpError(409, "An account with this email already exists")
    now = time.time()
    with db.tx() as c:
        ws = c.execute("INSERT INTO workspaces(name,created_at) VALUES(?,?)", (name, now)).lastrowid
        uid = c.execute("""INSERT INTO users(workspace_id,email,name,pw_hash,role,timezone,limits_json,invite_code,created_at)
                           VALUES(?,?,?,?,?,?,?,?,?)""",
                        (ws, email.strip(), name.strip(), security.hash_password(password), role, tz or "Asia/Karachi",
                         json.dumps(limits or {}), invite, now)).lastrowid
        if invite:
            c.execute("UPDATE invites SET used_by=?, used_at=? WHERE code=?", (uid, now, invite))
    return db.one("SELECT * FROM users WHERE id=?", (uid,))


def _start_session(req, u):
    tok = security.token()
    db.run("INSERT INTO sessions(token_hash,user_id,created_at,expires_at,user_agent) VALUES(?,?,?,?,?)",
           (security.token_hash(tok), u["id"], time.time(), time.time() + SESSION_DAYS * 86400,
            (req.headers.get("User-Agent") or "")[:200]))
    db.run("UPDATE users SET last_login=? WHERE id=?", (time.time(), u["id"]))
    req.set_cookie = tok
    return {"user": user_view(u)}


@route("GET", "/api/session", auth=None)
def session_info(req):
    owner = db.one("SELECT 1 FROM users WHERE role='owner'")
    return {"user": user_view(req.user) if req.user else None, "needs_setup": not owner, "version": VERSION}


@route("POST", "/api/setup", auth=None)
def setup_owner(req):
    if db.one("SELECT 1 FROM users WHERE role='owner'"):
        raise HttpError(409, "This server already has an owner")
    b = req.json
    need(b, "setup_code", "name", "email", "password")
    if b["setup_code"].strip().upper() != (db.meta_get("setup_code") or "").upper():
        raise HttpError(403, "Setup code is wrong. It is shown in the phone app.")
    u = _create_user(b["name"], b["email"], b["password"], "owner", b.get("timezone"))
    return _start_session(req, u)


@route("POST", "/api/login", auth=None)
def login(req):
    b = req.json
    need(b, "email", "password")
    key = req.ip
    fails = [t for t in _login_fail.get(key, []) if t > time.time() - 900]
    if len(fails) >= 8:
        raise HttpError(429, "Too many attempts. Wait 15 minutes.")
    u = db.one("SELECT * FROM users WHERE email=?", (b["email"].strip(),))
    if not u or not security.check_password(b["password"], u["pw_hash"]):
        _login_fail[key] = fails + [time.time()]
        raise HttpError(401, "Email or password is wrong")
    if u["status"] != "active":
        raise HttpError(403, "This account is suspended. Contact the server owner.")
    _login_fail.pop(key, None)
    return _start_session(req, u)


@route("POST", "/api/logout")
def logout(req):
    db.run("DELETE FROM sessions WHERE token_hash=?", (security.token_hash(req.session_token),))
    req.clear_cookie = True
    return {"ok": True}


@route("POST", "/api/register", auth=None)
def register(req):
    b = req.json
    need(b, "invite_code", "name", "email", "password")
    code = b["invite_code"].strip().upper()
    inv = db.one("SELECT * FROM invites WHERE code=?", (code,))
    if not inv or inv["revoked"] or inv["used_by"]:
        raise HttpError(403, "This invite code is not valid or was already used")
    if inv["expires_at"] < time.time():
        raise HttpError(403, "This invite code has expired. Ask for a new one.")
    u = _create_user(b["name"], b["email"], b["password"], "student", b.get("timezone"),
                     db.jload(inv["limits_json"], {}), code)
    return _start_session(req, u)


# ---------------------------------------------------------------- settings
@route("GET", "/api/settings")
def get_settings(req):
    ws = store.workspace(req.ws)
    return {"timezone": req.user["timezone"], "google_client_id": ws["google_client_id"],
            "has_secret": bool(ws["google_client_secret_enc"]), "redirect_uri": public_base(req) + "/oauth/callback",
            "public_url": public_base(req), "audiences": list(AUDIENCES)}


@route("POST", "/api/settings")
def save_settings(req):
    b = req.json
    if b.get("timezone"):
        db.run("UPDATE users SET timezone=? WHERE id=?", (b["timezone"], req.user["id"]))
    if b.get("google_client_id"):
        cid = b["google_client_id"].strip()
        if not cid.endswith(".apps.googleusercontent.com"):
            raise HttpError(400, "Client ID should end with .apps.googleusercontent.com")
        db.run("UPDATE workspaces SET google_client_id=? WHERE id=?", (cid, req.ws))
    if b.get("google_client_secret"):
        db.run("UPDATE workspaces SET google_client_secret_enc=? WHERE id=?",
               (security.seal(b["google_client_secret"].strip()), req.ws))
    if b.get("new_password"):
        if not security.check_password(b.get("current_password") or "", req.user["pw_hash"]):
            raise HttpError(403, "Current password is wrong")
        if len(b["new_password"]) < 8:
            raise HttpError(400, "Password must be at least 8 characters")
        db.run("UPDATE users SET pw_hash=? WHERE id=?", (security.hash_password(b["new_password"]), req.user["id"]))
    return get_settings(req)


# ---------------------------------------------------------------- dashboard
@route("GET", "/api/dashboard")
def dashboard(req):
    try:
        since = float(req.query.get("since") or 0) or None
    except ValueError:
        since = None
    return insights.dashboard(req.ws, req.user["timezone"], since=since)


_REFRESHING = {}


@route("POST", "/api/stats/refresh")
def stats_refresh(req):
    """Fetch fresh YouTube numbers for this workspace now (through each channel's own proxy)."""
    import threading
    from . import maintenance
    last = _REFRESHING.get(req.ws, 0)
    if time.time() - last < 60:
        return {"ok": True, "started": False}
    _REFRESHING[req.ws] = time.time()
    threading.Thread(target=maintenance.sync_stats, kwargs={"ws_id": req.ws, "force": True}, daemon=True).start()
    return {"ok": True, "started": True}


@route("GET", "/api/device", auth="owner")
def device(req):
    try:
        d = json.loads(db.meta_get("device_info") or "{}")
    except ValueError:
        d = {}
    d["at"] = float(db.meta_get("device_at") or 0) or None
    return d


@route("GET", "/api/events/(\\d+)")
def event_detail(req, eid):
    e = db.one("SELECT id, level, kind, message, at, code, detail FROM events WHERE id=? AND workspace_id=?", (int(eid), req.ws))
    if not e:
        raise HttpError(404, "Not found")
    from . import errors
    if e["code"]:
        e["info"] = errors.info(e["code"])
    return {"event": e}


@route("POST", "/api/events/(\\d+)/dismiss")
def dismiss(req, eid):
    db.run("UPDATE events SET resolved=1 WHERE id=? AND workspace_id=?", (int(eid), req.ws))
    return {"ok": True}


# ---------------------------------------------------------------- IP groups
def _group_out(g):
    n = db.one("SELECT COUNT(*) n FROM channels WHERE group_id=?", (g["id"],))["n"]
    chans = db.all("SELECT id,title,handle,thumbnail,state FROM channels WHERE group_id=? ORDER BY title", (g["id"],))
    out = store.public(g)
    out.update({"channel_count": n, "channels": chans, "over_recommended": n > g["max_channels"],
                "latency": db.jload(g["latency_json"], []), "has_password": bool(g["password_enc"])})
    acc = db.one("SELECT * FROM ig_accounts WHERE group_id=?", (g["id"],))
    out["instagram"] = None if not acc else {
        "username": acc["username"], "status": acc["status"], "error_code": acc["error_code"],
        "checked_at": acc["checked_at"], "paused_until": acc["paused_until"], "today": acc["day_count"] if acc["day"] == time.strftime("%Y-%m-%d", time.gmtime()) else 0,
        "sources": db.one("SELECT COUNT(*) n FROM sources s JOIN channels c ON c.id=s.channel_id WHERE c.group_id=? AND s.state='active'", (g["id"],))["n"],
    }
    lat = out.get("latency_ms")
    out["health"] = "down" if g["status"] == "down" else ("slow" if lat and lat > 2500 else "good" if lat else "unknown")
    return out


def _proxy_fields(b, partial=False):
    """Proxy parts from a pasted string ('proxy') or separate fields."""
    if b.get("proxy"):
        try:
            p = parse_proxy(b["proxy"])
        except ValueError as e:
            raise HttpError(400, str(e))
    else:
        if partial and not b.get("host"):
            return None
        need(b, "host", "port")
        try:
            port = int(b["port"])
            assert 0 < port < 65536
        except (ValueError, AssertionError):
            raise HttpError(400, "Port must be a number between 1 and 65535")
        p = {"host": b["host"].strip(), "port": port, "username": (b.get("username") or "").strip() or None,
             "password": b.get("password") or None}
    return p


def _probe(p):
    g = {"host": p["host"], "port": p["port"], "username": p["username"],
         "password_enc": security.seal(p["password"]) if p.get("password") else None}
    try:
        return probe(g)
    except DriveCastError as e:
        raise HttpError(400, f"Proxy test failed: {e.user_message}. Check host, port and login.")


def _save_probe(gid, r):
    hist = db.jload(db.one("SELECT latency_json FROM ip_groups WHERE id=?", (gid,))["latency_json"], [])[-23:] + [r["ms"]]
    db.run("""UPDATE ip_groups SET status='ok', latency_ms=?, last_check=?, last_error=NULL, fail_count=0, latency_json=?,
              country=COALESCE(?,country), country_name=COALESCE(?,country_name), city=COALESCE(?,city), isp=COALESCE(?,isp)
              WHERE id=?""", (r["ms"], time.time(), json.dumps(hist), r["country"], r["country_name"], r["city"], r["isp"], gid))


@route("GET", "/api/groups")
def groups(req):
    return {"groups": [_group_out(g) for g in db.all("SELECT * FROM ip_groups WHERE workspace_id=? ORDER BY number", (req.ws,))]}


@route("POST", "/api/groups")
def add_group(req):
    b = req.json
    lim = store.limits_for_workspace(req.ws)
    have = db.one("SELECT COUNT(*) n FROM ip_groups WHERE workspace_id=?", (req.ws,))["n"]
    if lim["max_groups"] and have >= int(lim["max_groups"]):
        raise HttpError(403, f"Your account allows {lim['max_groups']} IP groups")
    p = _proxy_fields(b)
    r = _probe(p)
    num = db.one("SELECT COALESCE(MAX(number),0)+1 n FROM ip_groups WHERE workspace_id=?", (req.ws,))["n"]
    gid = db.run("""INSERT INTO ip_groups(workspace_id,number,host,port,username,password_enc,max_channels,priority,status,
                    outgoing_ip,latency_ms,last_check,latency_json,created_at,country,country_name,city,isp)
                    VALUES(?,?,?,?,?,?,?,?,'ok',?,?,?,?,?,?,?,?,?)""",
                 (req.ws, num, p["host"], p["port"], p["username"], security.seal(p["password"]) if p.get("password") else None,
                  max(1, int(b.get("max_channels") or 5)), b.get("priority") or "normal", r["ip"], r["ms"], time.time(),
                  json.dumps([r["ms"]]), time.time(), r["country"], r["country_name"], r["city"], r["isp"]))
    return {"group": _group_out(store.group(gid))}


@route("POST", "/api/groups/test", auth="user")
def test_group_form(req):
    try:
        p = _proxy_fields(req.json)
        r = _probe(p)
        return {"ok": True, "ip": r["ip"], "latency_ms": r["ms"], "country": r["country"], "country_name": r["country_name"],
                "city": r["city"], "isp": r["isp"], "parsed": {k: v for k, v in p.items() if k != "password"}}
    except HttpError as e:
        return {"ok": False, "error": e.message}


def _own_group(req, gid):
    g = db.one("SELECT * FROM ip_groups WHERE id=? AND workspace_id=?", (int(gid), req.ws))
    if not g:
        raise HttpError(404, "IP group not found")
    return g


@route("POST", "/api/groups/(\\d+)/test")
def test_group(req, gid):
    _own_group(req, gid)
    maintenance_one_group(int(gid))
    return {"group": _group_out(store.group(int(gid)))}


def maintenance_one_group(gid):
    g = store.group(gid)
    try:
        r = probe(g)
        if g["outgoing_ip"] and r["ip"] != g["outgoing_ip"]:
            db.run("UPDATE ip_groups SET status='down', last_error=?, last_check=? WHERE id=?",
                   (f"Outgoing IP changed to {r['ip']}. Confirm to accept it.", time.time(), gid))
        else:
            db.run("UPDATE ip_groups SET outgoing_ip=COALESCE(outgoing_ip,?) WHERE id=?", (r["ip"], gid))
            _save_probe(gid, r)
    except DriveCastError as e:
        db.run("UPDATE ip_groups SET status='down', last_error=?, last_check=? WHERE id=?", (e.user_message, time.time(), gid))


@route("POST", "/api/groups/(\\d+)/accept-ip")
def accept_ip(req, gid):
    g = _own_group(req, gid)
    try:
        r = probe(g)
    except DriveCastError as e:
        raise HttpError(400, e.user_message)
    db.run("UPDATE ip_groups SET outgoing_ip=? WHERE id=?", (r["ip"], g["id"]))
    _save_probe(g["id"], r)
    return {"group": _group_out(store.group(g["id"]))}


@route("PATCH", "/api/groups/(\\d+)")
def edit_group(req, gid):
    g = _own_group(req, gid)
    b = req.json
    p = _proxy_fields(b, partial=True)
    if p:
        if not p.get("password") and g["password_enc"] and p.get("username") == g["username"]:
            p["password"] = security.unseal(g["password_enc"])  # keep the saved password
        r = _probe(p)
        n = db.one("SELECT COUNT(*) n FROM channels WHERE group_id=?", (g["id"],))["n"]
        if g["outgoing_ip"] and r["ip"] != g["outgoing_ip"] and n and not b.get("accept_new_ip"):
            loc = ", ".join(x for x in (r["city"], r["country"]) if x)
            raise HttpError(409, f"The new proxy leaves from {r['ip']}{' (' + loc + ')' if loc else ''}, not {g['outgoing_ip']}. "
                                 f"The {n} channel(s) in this group will post from the new IP. Confirm to continue.")
        db.run("UPDATE ip_groups SET host=?, port=?, username=?, password_enc=?, outgoing_ip=? WHERE id=?",
               (p["host"], p["port"], p["username"], security.seal(p["password"]) if p.get("password") else None, r["ip"], g["id"]))
        _save_probe(g["id"], r)
    if "max_channels" in b:
        db.run("UPDATE ip_groups SET max_channels=? WHERE id=?", (max(1, int(b["max_channels"])), g["id"]))
    if b.get("priority") in ("high", "normal", "low"):
        db.run("UPDATE ip_groups SET priority=? WHERE id=?", (b["priority"], g["id"]))
    if b.get("password"):
        db.run("UPDATE ip_groups SET password_enc=? WHERE id=?", (security.seal(b["password"]), g["id"]))
    return {"group": _group_out(store.group(g["id"]))}


@route("DELETE", "/api/groups/(\\d+)")
def delete_group(req, gid):
    g = _own_group(req, gid)
    if db.one("SELECT 1 FROM channels WHERE group_id=?", (g["id"],)):
        raise HttpError(409, "Move or remove this group's channels first")
    db.run("DELETE FROM ip_groups WHERE id=?", (g["id"],))
    return {"ok": True}


# ---------------------------------------------------------------- Drive
@route("GET", "/api/drive")
def drive_state(req):
    ws = store.workspace(req.ws)
    used = {r["folder_id"] for r in db.all("SELECT folder_id FROM channels WHERE workspace_id=? AND folder_id IS NOT NULL", (req.ws,))}
    folders = db.jload(ws["folders_json"], [])
    for f in folders:
        f["used"] = f["id"] in used
    return {"connected": bool(ws["drive_token_enc"]), "email": ws["drive_email"], "root_id": ws["drive_root_id"],
            "root_name": ws["drive_root_name"], "folders": folders, "scanned_at": ws["folders_scanned_at"],
            "error": ws["folders_error"], "app_ready": bool(ws["google_client_id"] and ws["google_client_secret_enc"])}


@route("POST", "/api/drive/root")
def drive_root(req):
    b = req.json
    need(b, "folder")
    m = re.search(r"folders/([A-Za-z0-9_-]{10,})", b["folder"]) or re.search(r"^([A-Za-z0-9_-]{10,})$", b["folder"].strip())
    if not m:
        raise HttpError(400, "Paste the main folder link or ID")
    ws = store.workspace(req.ws)
    if not ws["drive_token_enc"]:
        raise HttpError(400, "Connect Google Drive first")
    try:
        info = drive.folder_info(session_for(None), store.drive_box(ws), m.group(1))
    except DriveCastError as e:
        raise HttpError(400, e.user_message)
    if info.get("mimeType") != drive.FOLDER:
        raise HttpError(400, "That link is not a folder")
    db.run("UPDATE workspaces SET drive_root_id=?, drive_root_name=? WHERE id=?", (info["id"], info["name"], req.ws))
    maintenance.refresh_folders(req.ws)
    return drive_state(req)


@route("POST", "/api/drive/refresh")
def drive_refresh(req):
    maintenance.refresh_folders(req.ws)
    return drive_state(req)


# ---------------------------------------------------------------- sign-in links
@route("POST", "/api/connect")
def new_link(req):
    b = req.json
    purpose = b.get("purpose")
    ws = store.workspace(req.ws)
    if not (ws["google_client_id"] and ws["google_client_secret_enc"]):
        raise HttpError(400, "Add your Google API app in Settings first")
    gid = None
    if purpose == "youtube":
        g = _own_group(req, b.get("group_id") or 0)
        if g["status"] == "down":
            raise HttpError(400, f"Group {g['number']} is not responding. Fix it before connecting a channel.")
        gid = g["id"]
    elif purpose != "drive":
        raise HttpError(400, "Unknown link type")
    code = security.token(18)
    exp = time.time() + LINK_MINUTES * 60
    db.run("INSERT INTO connect_links(code,workspace_id,purpose,group_id,created_at,expires_at) VALUES(?,?,?,?,?,?)",
           (code, req.ws, purpose, gid, time.time(), exp))
    return {"code": code, "url": f"{public_base(req)}/connect/{code}", "expires_at": exp}


@route("GET", "/api/connect/([A-Za-z0-9_-]+)")
def link_status(req, code):
    l = db.one("SELECT * FROM connect_links WHERE code=? AND workspace_id=?", (code, req.ws))
    if not l:
        raise HttpError(404, "Link not found")
    res = db.jload(l["result_json"], None)
    ch = store.public(store.channel(res["channel_id"], req.ws)) if res and res.get("channel_id") else None
    state = "done" if l["used_at"] and not l["error"] else ("error" if l["error"] else
                                                            ("expired" if l["expires_at"] < time.time() else "waiting"))
    return {"status": state, "error": l["error"], "channel": ch, "expires_at": l["expires_at"]}


def _page(title, body, ok=True):
    color = "#0B5E57" if ok else "#B42318"
    return ("<!doctype html><html lang=en><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
            f"<title>{html.escape(title)}</title><body style='margin:0;font-family:system-ui,sans-serif;background:#F4F3EF;"
            "color:#0E1116;display:flex;min-height:100vh;align-items:center;justify-content:center'>"
            "<div style='max-width:440px;margin:24px;background:#fff;border:1px solid #E3E1DA;border-radius:18px;padding:28px'>"
            "<div style='font:600 12px monospace;letter-spacing:.14em;color:#5B606B'>DRIVECAST</div>"
            f"<h1 style='font-size:24px;margin:10px 0;color:{color}'>{html.escape(title)}</h1>"
            f"<p style='font-size:15px;line-height:1.5;color:#3F434B'>{body}</p></div></body></html>")


@route("GET", "/connect/([A-Za-z0-9_-]+)", auth=None)
def open_link(req, code):
    l = db.one("SELECT * FROM connect_links WHERE code=?", (code,))
    if not l or l["used_at"]:
        return 404, _page("Link not valid", "This sign-in link was already used or does not exist. Ask for a new one.", False)
    if l["expires_at"] < time.time():
        return 410, _page("Link expired", "This sign-in link expired. Ask for a new one.", False)
    ws = store.workspace(l["workspace_id"])
    scopes = platform("youtube").SCOPES if l["purpose"] == "youtube" else DRIVE_SCOPES
    url = consent_url(ws["google_client_id"], public_base(req) + "/oauth/callback", scopes, code)
    return 302, {"Location": url}


@route("GET", "/oauth/callback", auth=None)
def oauth_callback(req):
    code, err = req.query.get("state", ""), req.query.get("error")
    l = db.one("SELECT * FROM connect_links WHERE code=?", (code,))
    if not l or l["used_at"] or l["expires_at"] < time.time():
        return 400, _page("Link not valid", "Start again from a new sign-in link.", False)
    if err:
        db.run("UPDATE connect_links SET error=? WHERE code=?", ("Google sign-in was cancelled", code))
        return 400, _page("Not connected", "Sign-in was cancelled. You can open the link again.", False)
    ws = store.workspace(l["workspace_id"])
    cid, sec = store.google_app(ws)
    try:
        if l["purpose"] == "drive":
            s = session_for(None)
            tok = exchange(s, cid, sec, req.query.get("code", ""), public_base(req) + "/oauth/callback")
            sealed = security.seal(json.dumps(tok))
            box = TokenBox(sealed, cid, sec, lambda x: None)
            email = drive.whoami(s, box)
            db.run("UPDATE workspaces SET drive_token_enc=?, drive_email=? WHERE id=?", (sealed, email, ws["id"]))
            db.run("UPDATE connect_links SET used_at=?, result_json=? WHERE code=?", (time.time(), json.dumps({"email": email}), code))
            return 200, _page("Google Drive connected", f"Connected as <b>{html.escape(email or '')}</b>. You can close this tab.")
        g = store.group(l["group_id"])
        s = session_for(g)  # the channel's very first call already leaves from its group IP
        tok = exchange(s, cid, sec, req.query.get("code", ""), public_base(req) + "/oauth/callback")
        sealed = security.seal(json.dumps(tok))
        p = platform("youtube")
        me = p.whoami(s, TokenBox(sealed, cid, sec, lambda x: None))
        existing = db.one("SELECT * FROM channels WHERE workspace_id=? AND platform='youtube' AND external_id=?",
                          (ws["id"], me["external_id"]))
        if existing:
            db.run("""UPDATE channels SET token_enc=?, title=?, handle=?, thumbnail=?, subscribers=?, total_views=?,
                      state=CASE WHEN state='attention' THEN 'live' ELSE state END, state_reason=NULL,
                      consecutive_failures=0, paused_until=NULL WHERE id=?""",
                   (sealed, me["title"], me["handle"], me["thumbnail"], me["subscribers"], me["total_views"], existing["id"]))
            chid = existing["id"]
        else:
            chid = db.run("""INSERT INTO channels(workspace_id,platform,external_id,title,handle,thumbnail,token_enc,group_id,
                             subscribers,total_views,state,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,'setup',?)""",
                          (ws["id"], "youtube", me["external_id"], me["title"], me["handle"], me["thumbnail"], sealed, g["id"],
                           me["subscribers"], me["total_views"], time.time()))
        db.run("UPDATE connect_links SET used_at=?, result_json=? WHERE code=?", (time.time(), json.dumps({"channel_id": chid}), code))
        return 200, _page("Channel connected", f"<b>{html.escape(me['title'])}</b> is connected. You can close this tab.")
    except DriveCastError as e:
        db.run("UPDATE connect_links SET error=? WHERE code=?", (e.user_message, code))
        return 400, _page("Not connected", html.escape(e.user_message), False)
    except Exception as e:
        db.run("UPDATE connect_links SET error=? WHERE code=?", (f"Unexpected error: {e}"[:300], code))
        return 500, _page("Not connected", "Something went wrong. Ask for a new link and try again.", False)


# ---------------------------------------------------------------- channels
def _channel_out(c):
    out = store.public(c)
    out["plan"] = normalize_plan(db.jload(c["plan_json"], {}))
    g = store.group(c["group_id"])
    out["group"] = {"id": g["id"], "number": g["number"], "status": g["status"], "outgoing_ip": g["outgoing_ip"]} if g else None
    now = time.time()
    out["today"] = db.one("""SELECT SUM(status='done') done, COUNT(*) total FROM slots WHERE channel_id=? AND due_at BETWEEN ? AND ?""",
                          (c["id"], now - 12 * 3600, now + 12 * 3600))
    out["next"] = db.one("SELECT due_at, status FROM slots WHERE channel_id=? AND status IN ('planned','queued') AND due_at>=? ORDER BY due_at LIMIT 1",
                         (c["id"], now - 60))
    out["views_7d"] = None
    pts = db.all("SELECT day, views FROM channel_points WHERE channel_id=? ORDER BY day DESC LIMIT 15", (c["id"],))
    if len(pts) >= 2:
        pts.reverse()
        diffs = [max(0, pts[i]["views"] - pts[i - 1]["views"]) for i in range(1, len(pts)) if pts[i]["views"] is not None and pts[i - 1]["views"] is not None]
        out["series"] = diffs[-10:]
        out["views_7d"] = sum(diffs[-7:])
    from . import worker
    out["live"] = worker.PROGRESS.get(c["id"])
    out["published_total"] = db.one("SELECT COUNT(*) n FROM uploads WHERE channel_id=? AND status='published'", (c["id"],))["n"]
    src = db.one("SELECT handle, state, error_code, content_type FROM sources WHERE channel_id=? AND state!='removed' ORDER BY id DESC LIMIT 1", (c["id"],))
    out["source"] = src
    out["ready"] = db.one("SELECT COUNT(*) n FROM media WHERE channel_id=? AND state='in_drive'", (c["id"],))["n"]
    return out


@route("GET", "/api/channels")
def channels(req):
    rows = db.all("SELECT * FROM channels WHERE workspace_id=? ORDER BY title COLLATE NOCASE", (req.ws,))
    return {"channels": [_channel_out(c) for c in rows]}


def _own_channel(req, cid):
    c = store.channel(int(cid), req.ws)
    if not c:
        raise HttpError(404, "Channel not found")
    return c


@route("POST", "/api/channels/(\\d+)/route")
def set_route(req, cid):
    """Finish the wizard: IP group, content source (Drive folder and/or Instagram), posting plan."""
    c = _own_channel(req, cid)
    b = req.json
    igb = b.get("instagram") or None
    if not b.get("folder_id") and not igb:
        raise HttpError(400, "Choose a Drive folder or add the client's Instagram")
    ws = store.workspace(req.ws)
    folders = {f["id"]: f for f in db.jload(ws["folders_json"], [])}
    if b.get("folder_id"):
        if b["folder_id"] not in folders:
            raise HttpError(400, "Choose a folder from your main Drive folder")
        other = db.one("SELECT title FROM channels WHERE workspace_id=? AND folder_id=? AND id!=?", (req.ws, b["folder_id"], c["id"]))
        if other:
            raise HttpError(409, f"This folder already feeds “{other['title']}”")
    lim = store.limits_for_workspace(req.ws)
    live = db.one("SELECT COUNT(*) n FROM channels WHERE workspace_id=? AND state!='setup' AND id!=?", (req.ws, c["id"]))["n"]
    if lim["max_channels"] and live >= int(lim["max_channels"]):
        raise HttpError(403, f"Your account allows {lim['max_channels']} channels")
    if b.get("group_id"):
        _own_group(req, b["group_id"])
        db.run("UPDATE channels SET group_id=? WHERE id=?", (int(b["group_id"]), c["id"]))
    if b.get("folder_id"):
        db.run("UPDATE channels SET folder_id=?, folder_name=? WHERE id=?", (b["folder_id"], folders[b["folder_id"]]["name"], c["id"]))
    c = store.channel(c["id"])
    if igb:
        configure_source(c, igb)
    elif b.get("inbox"):
        _ensure_folder(c, c["folder_name"] or c["title"])
    if "encore" in b:
        db.run("UPDATE channels SET encore=? WHERE id=?", (1 if b["encore"] else 0, c["id"]))
    if b.get("inbox") is False:
        db.run("UPDATE channels SET inbox_id=NULL WHERE id=?", (c["id"],))
    plan = normalize_plan(b.get("plan"))
    start = bool(b.get("start", True))
    db.run("""UPDATE channels SET plan_json=?, state=?, state_reason=NULL, live_since=COALESCE(live_since,?) WHERE id=?""",
           (json.dumps(plan), "live" if start else "paused", time.time(), c["id"]))
    c = store.channel(c["id"])
    if start:
        scheduler.replan_future(c)
    return {"channel": _channel_out(c)}


@route("PATCH", "/api/channels/(\\d+)")
def edit_channel(req, cid):
    c = _own_channel(req, cid)
    b = req.json
    if "plan" in b:
        db.run("UPDATE channels SET plan_json=? WHERE id=?", (json.dumps(normalize_plan(b["plan"])), c["id"]))
        c = store.channel(c["id"])
        if c["state"] == "live":
            scheduler.replan_future(c)
    if b.get("group_id") and int(b["group_id"]) != c["group_id"]:
        if c["state"] == "live":
            raise HttpError(409, "Pause the channel before moving it to another IP group")
        _own_group(req, b["group_id"])
        db.run("UPDATE channels SET group_id=? WHERE id=?", (int(b["group_id"]), c["id"]))
    return {"channel": _channel_out(store.channel(c["id"]))}


@route("POST", "/api/channels/(\\d+)/(pause|resume|post-now|delete)")
def channel_action(req, cid, action):
    c = _own_channel(req, cid)
    if action == "pause":
        store.set_channel_state(c["id"], "paused", "Paused by you")
        db.run("UPDATE slots SET status='held', note='Channel paused' WHERE channel_id=? AND status='planned'", (c["id"],))
    elif action == "resume":
        if not c["folder_id"]:
            raise HttpError(400, "Connect a Drive folder first")
        db.run("UPDATE channels SET state='live', state_reason=NULL, consecutive_failures=0, paused_until=NULL WHERE id=?", (c["id"],))
        db.run("UPDATE slots SET status='planned', note=NULL WHERE channel_id=? AND status='held' AND window_end>?", (c["id"], time.time()))
        scheduler.ensure_slots(store.channel(c["id"]))
    elif action == "post-now":
        if c["state"] != "live":
            raise HttpError(400, "Resume the channel first")
        if not scheduler.post_now(c):
            raise HttpError(409, "Today's posts are all done — nothing left to bring forward")
    elif action == "delete":
        if db.one("SELECT 1 FROM jobs WHERE channel_id=? AND status='running'", (c["id"],)):
            raise HttpError(409, "A post is uploading right now. Try again in a few minutes.")
        db.run("DELETE FROM jobs WHERE channel_id=?", (c["id"],))
        db.run("DELETE FROM channels WHERE id=?", (c["id"],))
        return {"ok": True}
    return {"channel": _channel_out(store.channel(c["id"]))}


@route("GET", "/api/schedule")
def schedule(req):
    from . import worker
    days = min(7, int(req.query.get("days", 3)))
    now = time.time()
    rows = db.all("""SELECT s.*, c.title, c.handle, c.thumbnail, c.state channel_state, g.number group_number, g.status group_status,
                     g.country group_country, g.city group_city, u.external_video_id, u.title video_title
                     FROM slots s JOIN channels c ON c.id=s.channel_id LEFT JOIN ip_groups g ON g.id=c.group_id
                     LEFT JOIN uploads u ON u.id=s.upload_id
                     WHERE c.workspace_id=? AND s.due_at BETWEEN ? AND ? ORDER BY s.due_at""",
                  (req.ws, now - 86400, now + days * 86400))
    live = {cid: p for cid, p in worker.PROGRESS.items()}
    return {"slots": rows, "live": live, "now": now}


@route("GET", "/api/videos")
def videos(req):
    lim = min(500, int(req.query.get("limit", 100)))
    rows = db.all("""SELECT u.*, c.title channel_title, g.number group_number, g.outgoing_ip group_ip FROM uploads u
                     JOIN channels c ON c.id=u.channel_id LEFT JOIN ip_groups g ON g.id=c.group_id
                     WHERE u.workspace_id=? ORDER BY u.reserved_at DESC LIMIT ?""", (req.ws, lim))
    return {"videos": rows}


@route("POST", "/api/uploads/(\\d+)/resolve")
def resolve_uncertain(req, uid):
    u = db.one("SELECT * FROM uploads WHERE id=? AND workspace_id=? AND status='uncertain'", (int(uid), req.ws))
    if not u:
        raise HttpError(404, "Nothing to resolve")
    b = req.json
    if b.get("video_id"):
        db.run("UPDATE uploads SET status='published', external_video_id=?, published_at=?, error=NULL WHERE id=?",
               (b["video_id"].strip(), u["reserved_at"], u["id"]))
    else:
        db.run("UPDATE uploads SET status='failed', error='You confirmed it was not published' WHERE id=?", (u["id"],))
    return {"ok": True}


# ---------------------------------------------------------------- server
@route("GET", "/api/server")
def server(req):
    import shutil
    from . import config
    du = shutil.disk_usage(config.DATA_DIR)
    return {"version": VERSION, "uptime": time.time() - STARTED, "services": supervisor.HEARTBEATS,
            "disk_free": du.free, "public_url": public_base(req), "device": db.meta_get("device_info"),
            "jobs": db.one("SELECT SUM(status='queued') queued, SUM(status='running') running FROM jobs")}


@route("GET", "/healthz", auth=None)
def healthz(req):
    stale = [k for k, v in supervisor.HEARTBEATS.items() if time.time() - v["at"] > 900]
    return {"status": "ok" if not stale else "degraded", "version": VERSION, "stale": stale}


# local-only endpoints used by the phone app
@route("GET", "/local/status", auth="local")
def local_status(req):
    owner = db.one("SELECT 1 FROM users WHERE role='owner'")
    oe = db.one("SELECT email FROM users WHERE role='owner'")
    return {"setup_code": None if owner else db.meta_get("setup_code"), "has_owner": bool(owner),
            "owner_email": oe["email"] if oe else None,
            "public_url": db.meta_get("public_url"), "version": VERSION,
            "accounts": db.one("SELECT COUNT(*) n FROM users")["n"],
            "channels_live": db.one("SELECT COUNT(*) n FROM channels WHERE state='live'")["n"],
            "published_today": db.one("SELECT COUNT(*) n FROM uploads WHERE status='published' AND published_at>?",
                                      (time.time() - 86400,))["n"]}


@route("POST", "/local/public-url", auth="local")
def local_public_url(req):
    url = (req.json.get("url") or "").strip().rstrip("/")
    if url and not url.startswith("https://"):
        raise HttpError(400, "Public URL must start with https://")
    db.meta_set("public_url", url)
    if req.json.get("device"):
        db.meta_set("device_info", json.dumps(req.json["device"])[:500])
    return {"ok": True}


@route("POST", "/local/media-dir", auth="local")
def local_media_dir(req):
    """The phone allowed storage: use the shared folder from now on (no restart)."""
    import os
    from . import config
    path = (req.json.get("path") or "").strip()
    if not path:
        raise HttpError(400, "path required")
    try:
        os.makedirs(path, exist_ok=True)
        open(os.path.join(path, ".nomedia"), "a").close()
    except OSError as e:
        raise HttpError(400, f"Cannot write there: {e}")
    config.MEDIA_DIR = path
    return {"ok": True, "media_dir": path}


@route("POST", "/local/device", auth="local")
def local_device(req):
    db.meta_set("device_info", json.dumps(req.json)[:500])
    db.meta_set("device_at", str(time.time()))
    return {"ok": True}


# ---------------------------------------------------------------- admin
@route("GET", "/api/admin/users", auth="owner")
def admin_users(req):
    since = time.time() - 86400
    rows = db.all("""SELECT u.id,u.name,u.email,u.role,u.status,u.created_at,u.last_login,u.limits_json,u.invite_code,u.workspace_id,
                     (SELECT COUNT(*) FROM channels c WHERE c.workspace_id=u.workspace_id AND c.state!='setup') channels,
                     (SELECT COUNT(*) FROM ip_groups g WHERE g.workspace_id=u.workspace_id) groups_n,
                     (SELECT COUNT(*) FROM uploads x WHERE x.workspace_id=u.workspace_id AND x.status='published' AND x.published_at>?) today,
                     (SELECT COUNT(*) FROM ip_groups g WHERE g.workspace_id=u.workspace_id AND g.status='down') groups_down
                     FROM users u ORDER BY u.role='owner' DESC, u.created_at""", (since,))
    for r in rows:
        r["limits"] = db.jload(r.pop("limits_json"), {})
    return {"users": rows}


@route("GET", "/api/admin/users/(\\d+)/report", auth="owner")
def admin_report(req, uid):
    u = db.one("SELECT * FROM users WHERE id=?", (int(uid),))
    if not u:
        raise HttpError(404, "User not found")
    days = min(90, int(req.query.get("days", 30)))
    rep = insights.user_report(u["workspace_id"], req.user["timezone"], days)
    rep["user"] = {k: u[k] for k in ("id", "name", "email", "role", "status", "created_at", "last_login", "invite_code")}
    rep["user"]["limits"] = db.jload(u["limits_json"], {})
    return rep


@route("POST", "/api/admin/users/(\\d+)", auth="owner")
def admin_user_update(req, uid):
    u = db.one("SELECT * FROM users WHERE id=?", (int(uid),))
    if not u or u["role"] == "owner":
        raise HttpError(404, "User not found")
    b = req.json
    if b.get("status") in ("active", "suspended"):
        db.run("UPDATE users SET status=? WHERE id=?", (b["status"], u["id"]))
        if b["status"] == "suspended":
            db.run("DELETE FROM sessions WHERE user_id=?", (u["id"],))
            db.run("UPDATE channels SET state='paused', state_reason='Account suspended by the server owner' WHERE workspace_id=? AND state='live'",
                   (u["workspace_id"],))
    if isinstance(b.get("limits"), dict):
        db.run("UPDATE users SET limits_json=? WHERE id=?", (json.dumps(_clean_limits(b["limits"])), u["id"]))
    return {"ok": True}


def _clean_limits(l):
    out = {}
    for k in ("max_channels", "max_groups", "daily_cap"):
        if l.get(k) not in (None, ""):
            out[k] = max(1, int(l[k]))
    return out


@route("POST", "/api/admin/users/(\\d+)/reset-password", auth="owner")
def admin_reset_password(req, uid):
    u = db.one("SELECT * FROM users WHERE id=?", (int(uid),))
    if not u or u["role"] == "owner":
        raise HttpError(404, "User not found")
    temp = security.code("PW").replace("PW-", "")  # e.g. 7KQ2-M9XD
    db.run("UPDATE users SET pw_hash=? WHERE id=?", (security.hash_password(temp), u["id"]))
    db.run("DELETE FROM sessions WHERE user_id=?", (u["id"],))
    db.event("info", "account", f"Password reset by the server owner for {u['name']}", u["workspace_id"])
    return {"temporary_password": temp}


@route("POST", "/api/admin/users/(\\d+)/delete", auth="owner")
def admin_delete_user(req, uid):
    """Remove a student and everything in their workspace. Videos already on
    YouTube and files in Drive are not touched."""
    u = db.one("SELECT * FROM users WHERE id=?", (int(uid),))
    if not u or u["role"] == "owner":
        raise HttpError(404, "User not found")
    ws = u["workspace_id"]
    if db.one("SELECT 1 FROM jobs j JOIN channels c ON c.id=j.channel_id WHERE c.workspace_id=? AND j.status='running'", (ws,)):
        raise HttpError(409, "A post is uploading for this user right now. Try again in a few minutes.")
    with db.tx() as c:
        c.execute("DELETE FROM jobs WHERE channel_id IN (SELECT id FROM channels WHERE workspace_id=?)", (ws,))
        c.execute("DELETE FROM uploads WHERE workspace_id=?", (ws,))
        c.execute("DELETE FROM media WHERE workspace_id=?", (ws,))
        c.execute("DELETE FROM ig_accounts WHERE workspace_id=?", (ws,))
        c.execute("DELETE FROM channels WHERE workspace_id=?", (ws,))
        c.execute("DELETE FROM ip_groups WHERE workspace_id=?", (ws,))
        c.execute("DELETE FROM connect_links WHERE workspace_id=?", (ws,))
        c.execute("DELETE FROM events WHERE workspace_id=?", (ws,))
        c.execute("DELETE FROM sessions WHERE user_id=?", (u["id"],))
        c.execute("UPDATE invites SET used_by=NULL WHERE used_by=?", (u["id"],))
        c.execute("DELETE FROM users WHERE id=?", (u["id"],))
        c.execute("DELETE FROM workspaces WHERE id=?", (ws,))
    db.event("info", "account", f"Account {u['name']} ({u['email']}) was deleted by the owner")
    return {"ok": True}


@route("POST", "/local/reset-owner", auth="local")
def local_reset_owner(req):
    """Owner forgot the password: only possible from the server phone itself."""
    pw = req.json.get("password") or ""
    if len(pw) < 8:
        raise HttpError(400, "Password must be at least 8 characters")
    o = db.one("SELECT * FROM users WHERE role='owner'")
    if not o:
        raise HttpError(404, "No owner yet — use the setup code")
    db.run("UPDATE users SET pw_hash=?, status='active' WHERE id=?", (security.hash_password(pw), o["id"]))
    db.run("DELETE FROM sessions WHERE user_id=?", (o["id"],))
    _login_fail.clear()
    return {"ok": True, "email": o["email"]}


@route("GET", "/api/admin/invites", auth="owner")
def admin_invites(req):
    rows = db.all("""SELECT i.*, u.name used_name FROM invites i LEFT JOIN users u ON u.id=i.used_by
                     WHERE i.revoked=0 ORDER BY i.created_at DESC LIMIT 100""")
    for r in rows:
        if r["used_at"] and not r["used_by"]:
            r["used_by"], r["used_name"] = -1, "a deleted account"
    for r in rows:
        r["limits"] = db.jload(r.pop("limits_json"), {})
    return {"invites": rows}


@route("POST", "/api/admin/invites", auth="owner")
def admin_new_invite(req):
    b = req.json
    need(b, "label")
    code = security.code()
    while db.one("SELECT 1 FROM invites WHERE code=?", (code,)):
        code = security.code()
    db.run("INSERT INTO invites(code,label,limits_json,created_at,expires_at) VALUES(?,?,?,?,?)",
           (code, b["label"].strip()[:80], json.dumps(_clean_limits(b.get("limits") or {})), time.time(),
            time.time() + int(b.get("days") or 7) * 86400))
    return {"code": code, "register_url": public_base(req) + "/#register"}


@route("POST", "/api/admin/invites/([A-Z0-9-]+)/revoke", auth="owner")
def admin_revoke(req, code):
    db.run("UPDATE invites SET revoked=1 WHERE code=? AND used_by IS NULL", (code,))
    return {"ok": True}


@route("GET", "/api/admin/load", auth="owner")
def admin_load(req):
    since = time.time() - 86400
    rows = db.all("""SELECT u.name, u.workspace_id,
                     (SELECT COUNT(*) FROM uploads x WHERE x.workspace_id=u.workspace_id AND x.reserved_at>?) uploads
                     FROM users u""", (since,))
    return {"accounts": rows}


@route("GET", "/phone", auth=None)
def phone_page(req):
    import os
    with open(os.path.join(os.path.dirname(__file__), "web", "phone.html"), encoding="utf-8") as f:
        return 200, f.read()


# ---------------------------------------------------------------- Instagram source (v2)
from . import errors as _errors  # noqa: E402
from . import igsync, instagram as _ig  # noqa: E402


def _ig_error(e, **ctx):
    if isinstance(e, _errors.CodedError):
        i = e.info(**ctx)
        return HttpError(400, f"{i['title']}. {i['message']}")
    if isinstance(e, ValueError):
        return HttpError(400, str(e))
    if isinstance(e, DriveCastError):
        return HttpError(400, e.user_message)
    return HttpError(500, f"Unexpected: {e}")


@route("POST", "/api/groups/(\\d+)/instagram")
def set_ig_account(req, gid):
    g = _own_group(req, gid)
    text = req.json.get("cookies") or ""
    # the browser the cookies came from: Instagram keeps seeing that same browser
    ua = (req.json.get("user_agent") or req.headers.get("User-Agent") or "")[:300] or None
    if ua and ("Mobile" in ua and "Instagram" in ua):
        ua = None
    lang = igsync.lang_for(g)
    try:
        cookies = _ig.parse_cookies(text)
        who = _ig.Client(session_for(g), cookies, min_gap=(0, 0), user_agent=ua, language=lang).whoami()
    except Exception as e:
        raise _ig_error(e, group=g["number"])
    sealed = security.seal(text.strip())
    now = time.time()
    old = db.one("SELECT id FROM ig_accounts WHERE group_id=?", (g["id"],))
    if old:
        db.run("""UPDATE ig_accounts SET cookies_enc=?, username=?, ig_user_id=?, status='ok', error_code=NULL, error_detail=NULL,
                  paused_until=NULL, backoff=0, checked_at=?, updated_at=?, user_agent=?, language=? WHERE id=?""",
               (sealed, who["username"], who["id"], now, now, ua, lang, old["id"]))
        igsync.forget_client(old["id"])
    else:
        db.run("""INSERT INTO ig_accounts(workspace_id,group_id,username,ig_user_id,cookies_enc,checked_at,created_at,updated_at,user_agent,language)
                  VALUES(?,?,?,?,?,?,?,?,?,?)""", (req.ws, g["id"], who["username"], who["id"], sealed, now, now, now, ua, lang))
    for code in ("IG_COOKIES_EXPIRED", "IG_CHALLENGE", "IG_NO_ACCOUNT", "IG_FEEDBACK"):
        db.resolve(code, req.ws)
    db.run("UPDATE sources SET next_check=MIN(IFNULL(next_check,0),?) WHERE channel_id IN (SELECT id FROM channels WHERE group_id=?)",
           (now + 60, g["id"]))
    return {"ok": True, "username": who["username"], "group": _group_out(store.group(g["id"]))}


@route("DELETE", "/api/groups/(\\d+)/instagram")
def del_ig_account(req, gid):
    g = _own_group(req, gid)
    db.run("DELETE FROM ig_accounts WHERE group_id=?", (g["id"],))
    return {"ok": True}


@route("POST", "/api/ig/lookup")
def ig_lookup(req):
    b = req.json
    need(b, "group_id", "handle")
    g = _own_group(req, b["group_id"])
    acc = db.one("SELECT * FROM ig_accounts WHERE group_id=?", (g["id"],))
    if not acc:
        raise HttpError(400, f"Group {g['number']} has no Instagram account yet. Add its cookies on the IP groups page.")
    try:
        handle = _ig.clean_handle(b["handle"])
        cl = igsync.client_for(acc, g)
        cl.min_gap = (1, 2)
        p = cl.profile(handle)
    except Exception as e:
        raise _ig_error(e, handle=b.get("handle"), group=g["number"])
    finally:
        try:
            cl.min_gap = (7, 14)
        except Exception:
            pass
    return {"profile": p}


def _ensure_folder(c, name):
    """Give the channel its own folder (Root/<name>) and an Inbox, creating them in the Library Drive."""
    ws = store.workspace(c["workspace_id"])
    if not ws["drive_token_enc"]:
        raise HttpError(400, "Connect Google Drive first (Google Drive page).")
    if not ws["drive_root_id"]:
        raise HttpError(400, "Choose your main Drive folder first (Google Drive page).")
    g = store.group(c["group_id"])
    sess, box = session_for(g), store.drive_box(ws)
    try:
        folder_id = c["folder_id"]
        folder_name = c["folder_name"]
        if not folder_id:
            folder_id = drive.child_folder(sess, box, ws["drive_root_id"], name)
            folder_name = name
        inbox = c.get("inbox_id") or drive.child_folder(sess, box, folder_id, "Inbox")
    except DriveCastError as e:
        raise HttpError(400, e.user_message)
    db.run("UPDATE channels SET folder_id=?, folder_name=?, inbox_id=? WHERE id=?", (folder_id, folder_name, inbox, c["id"]))
    return store.channel(c["id"])


def _active_source(cid):
    return db.one("SELECT * FROM sources WHERE channel_id=? AND state!='removed' ORDER BY id DESC LIMIT 1", (cid,))


def configure_source(c, b):
    """Add / change / pause / remove the Instagram source. The ledger is never deleted."""
    now = time.time()
    cur = _active_source(c["id"])
    if not b.get("enabled", True):
        if cur:
            db.run("UPDATE sources SET state='removed', updated_at=? WHERE id=?", (now, cur["id"]))
        return None
    if b.get("paused") is not None and cur and not b.get("handle"):
        db.run("UPDATE sources SET state=?, updated_at=? WHERE id=?", ("paused" if b["paused"] else "active", now, cur["id"]))
        return _active_source(c["id"])
    try:
        handle = _ig.clean_handle(b.get("handle") or (cur and cur["handle"]) or "")
    except ValueError as e:
        raise HttpError(400, str(e))
    ctype = b.get("content_type") or (cur and cur["content_type"]) or "videos"
    if ctype not in ("videos", "mix", "photos"):
        raise HttpError(400, "Content type must be videos, mix or photos")
    include_old = 1 if b.get("include_old", cur["include_old"] if cur else True) else 0
    cap = max(1, min(30, int(b.get("daily_cap") or (cur and cur["daily_cap"]) or 6)))
    if not c["folder_id"] or not c.get("inbox_id"):
        c = _ensure_folder(c, handle)
    if cur and cur["handle"] == handle:
        db.run("UPDATE sources SET content_type=?, include_old=?, daily_cap=?, state=?, updated_at=? WHERE id=?",
               (ctype, include_old, cap, "paused" if b.get("paused") else "active", now, cur["id"]))
        if include_old and not cur["include_old"]:
            db.run("UPDATE sources SET backfill_done=0 WHERE id=?", (cur["id"],))
        return _active_source(c["id"])
    if cur:  # a different handle: the old lane stops, its records stay
        db.run("UPDATE sources SET state='removed', updated_at=? WHERE id=?", (now, cur["id"]))
    old = db.one("SELECT * FROM sources WHERE channel_id=? AND handle=? ORDER BY id DESC LIMIT 1", (c["id"], handle))
    if old:  # the same account again: resume from its ledger, nothing posts twice
        db.run("""UPDATE sources SET state='active', content_type=?, include_old=?, daily_cap=?, next_check=?, error_code=NULL, updated_at=?
                  WHERE id=?""", (ctype, include_old, cap, now, now, old["id"]))
        return _active_source(c["id"])
    db.run("""INSERT INTO sources(channel_id,handle,ig_user_id,full_name,picture,content_type,include_old,daily_cap,started_at,next_check,created_at,updated_at)
              VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
           (c["id"], handle, b.get("ig_user_id"), b.get("full_name"), b.get("picture"), ctype, include_old, cap, now, now, now, now))
    return _active_source(c["id"])


@route("PUT", "/api/channels/(\\d+)/source")
def put_source(req, cid):
    c = _own_channel(req, cid)
    if not c["group_id"]:
        raise HttpError(400, "Choose an IP group for this channel first")
    configure_source(c, req.json or {})
    return channel_detail(req, cid)


@route("POST", "/api/channels/(\\d+)/source/check")
def source_check_now(req, cid):
    c = _own_channel(req, cid)
    src = _active_source(c["id"])
    if not src:
        raise HttpError(400, "This channel has no Instagram source")
    db.run("UPDATE sources SET next_check=0, error_code=NULL WHERE id=?", (src["id"],))
    acc = db.one("SELECT id FROM ig_accounts WHERE group_id=?", (c["group_id"],))
    if acc:
        igsync._next_action.pop(acc["id"], None)
    return {"ok": True}


@route("POST", "/api/channels/(\\d+)/inbox")
def channel_inbox(req, cid):
    c = _own_channel(req, cid)
    if req.json.get("enabled", True):
        c = _ensure_folder(c, c["folder_name"] or c["title"])
    else:
        db.run("UPDATE channels SET inbox_id=NULL WHERE id=?", (c["id"],))
    return channel_detail(req, cid)


@route("PUT", "/api/channels/(\\d+)/settings")
def channel_settings(req, cid):
    c = _own_channel(req, cid)
    b = req.json
    if "encore" in b:
        db.run("UPDATE channels SET encore=? WHERE id=?", (1 if b["encore"] else 0, c["id"]))
    if "titles" in b:
        lines = [t.strip()[:95] for t in (b["titles"] if isinstance(b["titles"], list) else str(b["titles"]).splitlines()) if t.strip()]
        db.run("UPDATE channels SET titles_json=? WHERE id=?", (json.dumps(lines[:200]), c["id"]))
    return channel_detail(req, cid)


@route("POST", "/api/media/(\\d+)/retry")
def media_retry(req, mid):
    m = db.one("SELECT * FROM media WHERE id=? AND workspace_id=?", (int(mid), req.ws))
    if not m:
        raise HttpError(404, "Not found")
    if m["state"] not in ("skipped", "failed"):
        raise HttpError(400, "Only skipped or failed items can be retried")
    db.run("UPDATE media SET state='found', next_try=0, error_code=NULL, skip_reason=NULL, updated_at=? WHERE id=?", (time.time(), m["id"]))
    return {"ok": True}


@route("GET", "/api/channels/(\\d+)/detail")
def channel_detail(req, cid):
    c = _own_channel(req, cid)
    now = time.time()
    src = _active_source(c["id"])
    acc = db.one("SELECT * FROM ig_accounts WHERE group_id=?", (c["group_id"],)) if c["group_id"] else None
    counts = {r["state"]: r["n"] for r in db.all("SELECT state, COUNT(*) n FROM media WHERE channel_id=? GROUP BY state", (c["id"],))}
    lanes = {r["lane"]: r["n"] for r in db.all("SELECT lane, COUNT(*) n FROM media WHERE channel_id=? AND state IN ('found','in_drive') GROUP BY lane", (c["id"],))}
    ready = db.all("""SELECT id, title, caption, lane, kind, taken_at, state, width, height, ref, origin, code FROM media WHERE channel_id=?
                      AND state IN ('in_drive','found','downloading')
                      ORDER BY CASE lane WHEN 'fresh' THEN 0 WHEN 'drive' THEN 1 ELSE 2 END,
                               CASE WHEN lane='backfill' THEN -taken_at ELSE taken_at END LIMIT 12""", (c["id"],))
    slots = db.all("SELECT due_at, status FROM slots WHERE channel_id=? AND status IN ('planned','queued','running') AND due_at>=? ORDER BY due_at LIMIT 12",
                   (c["id"], now - 300))
    enc = db.all("""SELECT u.id, u.title, u.file_id, u.published_at,
                       (SELECT COUNT(*) FROM uploads e WHERE e.encore_of=u.id AND e.status!='failed') reposts,
                       (SELECT MAX(published_at) FROM uploads e WHERE (e.encore_of=u.id OR e.id=u.id) AND e.status='published') last_at,
                       (SELECT COALESCE(SUM(views),0) FROM uploads e WHERE e.encore_of=u.id OR e.id=u.id) views
                    FROM uploads u WHERE u.channel_id=? AND u.encore_of IS NULL AND u.status='published' ORDER BY views DESC LIMIT 8""", (c["id"],))
    from . import worker
    for e in enc:
        e["eligible_at"] = (e["last_at"] or 0) + worker.ENCORE_COOLDOWN
        e["status"] = "retired" if e["reposts"] >= worker.ENCORE_MAX else ("eligible" if e["eligible_at"] <= now else "cooldown")
    skipped = db.all("""SELECT id, title, caption, code, kind, skip_reason, error_code, state, taken_at, updated_at FROM media
                        WHERE channel_id=? AND state IN ('skipped','failed') ORDER BY updated_at DESC LIMIT 12""", (c["id"],))
    events = db.all("SELECT id, level, message, at, code FROM events WHERE channel_id=? ORDER BY at DESC LIMIT 15", (c["id"],))
    srco = None
    if src:
        srco = {k: src[k] for k in ("id", "handle", "full_name", "picture", "content_type", "include_old", "daily_cap", "state",
                                     "last_check", "next_check", "error_code", "total_found", "backfill_done", "started_at")}
        srco["today"] = src["day_count"] if src["day"] == time.strftime("%Y-%m-%d", time.gmtime()) else 0
        if src["error_code"]:
            srco["error"] = _errors.info(src["error_code"], handle=src["handle"])
    acco = None
    if acc:
        acco = {"username": acc["username"], "status": acc["status"], "paused_until": acc["paused_until"], "error_code": acc["error_code"]}
        if acc["error_code"] and acc["status"] != "ok":
            g = store.group(c["group_id"])
            acco["error"] = _errors.info(acc["error_code"], group=g["number"] if g else "?")
    return {"channel": _channel_out(store.channel(c["id"])), "source": srco, "ig_account": acco,
            "titles": db.jload(c.get("titles_json"), []), "encore_on": bool(c.get("encore", 1)),
            "pipeline": {"found": sum(counts.values()), "waiting": counts.get("found", 0), "downloading": counts.get("downloading", 0),
                         "ready": counts.get("in_drive", 0), "published": counts.get("published", 0),
                         "skipped": counts.get("skipped", 0) + counts.get("failed", 0), "photos": counts.get("waiting_facebook", 0),
                         "lanes": lanes},
            "queue": ready, "slots": slots, "encore": enc, "skipped": skipped, "events": events,
            "has_inbox": bool(c.get("inbox_id")), "ready_target": igsync._ready_target(c)}


@route("GET", "/api/settings/advanced", auth="owner")
def adv_get(req):
    return {"public_url_override": db.meta_get("public_url_override") or "", "tunnel_url": db.meta_get("public_url") or ""}


@route("POST", "/api/settings/advanced", auth="owner")
def adv_set(req):
    url = (req.json.get("public_url_override") or "").strip().rstrip("/")
    if url and not re.match(r"^https://[A-Za-z0-9.-]+(:\d+)?$", url):
        raise HttpError(400, "Use a full https:// address, e.g. https://studio.example.com")
    db.meta_set("public_url_override", url)
    return adv_get(req)


# ---------------------------------------------------------------- backup / restore / pause everything
from . import backup as _backup  # noqa: E402


@route("GET", "/api/backup", auth="owner")
def backup_status(req):
    return {"has_password": bool(db.meta_get("backup_password")), "last_at": float(db.meta_get("backup_at") or 0) or None,
            "last_name": db.meta_get("backup_name"), "error": db.meta_get("backup_error") or None, "summary": _backup.summary()}


@route("POST", "/api/backup/password", auth="owner")
def backup_password(req):
    pw = req.json.get("password") or ""
    if len(pw) < 8:
        raise HttpError(400, "Use at least 8 characters. Write it down — without it a backup cannot be opened.")
    db.meta_set("backup_password", security.seal(pw))
    _bg(lambda: _backup.to_drive(force=True))
    return backup_status(req)


def _bg(fn):
    import threading

    def run():
        try:
            fn()
        except Exception as e:
            db.meta_set("backup_error", str(e)[:300])
    threading.Thread(target=run, daemon=True).start()


@route("POST", "/api/backup/now", auth="owner")
def backup_now(req):
    if not db.meta_get("backup_password"):
        raise HttpError(400, "Set a backup password first")
    _bg(lambda: _backup.to_drive(force=True))
    return {"ok": True}


@route("POST", "/api/backup/export", auth="owner")
def backup_export(req):
    pw = req.json.get("password") or _backup.password_get()
    try:
        blob = _backup.create(pw)
    except ValueError as e:
        raise HttpError(400, str(e))
    return FileOut(blob, time.strftime("DriveCast-backup-%Y-%m-%d-%H%M.dcbackup"))


@route("POST", "/api/restore", auth=None)
def restore(req):
    """Fresh phone: the setup code from the phone proves you hold it. Existing server: only the owner."""
    import base64
    import os
    import threading
    b = req.json
    has_owner = bool(db.one("SELECT 1 FROM users WHERE role='owner'"))
    from . import web as _web
    if has_owner:
        tok = None
        from http.cookies import SimpleCookie
        c = SimpleCookie(req.headers.get("Cookie") or "")
        if _web.COOKIE in c:
            tok = c[_web.COOKIE].value
        row = tok and db.one("""SELECT u.role FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token_hash=? AND s.expires_at>?""",
                             (security.token_hash(tok), time.time()))
        if not row or row["role"] != "owner":
            raise HttpError(403, "This server already has an owner. Sign in as the owner to restore.")
    elif (b.get("setup_code") or "").strip().upper() != (db.meta_get("setup_code") or "").upper():
        raise HttpError(403, "Setup code is wrong. It is shown in the phone app.")
    if db.one("SELECT 1 FROM jobs WHERE status='running'"):
        raise HttpError(409, "A post is uploading right now. Try again in a few minutes.")
    try:
        if b.get("link"):
            blob = _backup.fetch_link(b["link"].strip())
        else:
            blob = base64.b64decode(b.get("file_b64") or "")
        summ, created = _backup.stage(blob, b.get("password") or "")
    except ValueError as e:
        raise HttpError(400, str(e))
    except Exception as e:
        raise HttpError(400, f"Could not read the backup: {e}")

    def restart():
        time.sleep(1.5)
        os._exit(3)  # Android restarts the server service; it opens the restored data
    threading.Thread(target=restart, daemon=True).start()
    return {"ok": True, "summary": summ, "created": created, "restarting": True}


def _ws_pause_view(ws_id):
    ws = store.workspace(ws_id)
    until = ws.get("paused_until")
    return {"paused": bool(until and until > time.time()), "until": until, "forever": bool(until and until >= 9e9),
            "keep_collecting": bool(ws.get("pause_collect", 1)), "since": ws.get("paused_at")}


@route("GET", "/api/pause")
def pause_get(req):
    return _ws_pause_view(req.ws)


@route("POST", "/api/pause")
def pause_set(req):
    """Pause everything in this workspace (vacation). Resumes by itself at 'until', or when you press Resume."""
    b = req.json
    now = time.time()
    if b.get("resume"):
        db.run("UPDATE workspaces SET paused_until=NULL, paused_at=NULL WHERE id=?", (req.ws,))
        for c in db.all("SELECT * FROM channels WHERE workspace_id=? AND state='live'", (req.ws,)):
            db.run("UPDATE slots SET status='planned', note=NULL WHERE channel_id=? AND status='held' AND note LIKE 'Paused%' AND window_end>?",
                   (c["id"], now))
            scheduler.ensure_slots(store.channel(c["id"]))
        db.event("info", "pause", "Posting resumed.", req.ws)
        return _ws_pause_view(req.ws)
    until = b.get("until")
    until = 9.9e9 if not until else float(until)
    if until <= now + 60:
        raise HttpError(400, "Choose a time in the future")
    db.run("UPDATE workspaces SET paused_until=?, paused_at=?, pause_collect=? WHERE id=?",
           (until, now, 1 if b.get("keep_collecting", True) else 0, req.ws))
    db.run("""UPDATE slots SET status='held', note='Paused — everything is on hold' WHERE status='planned' AND due_at<?
              AND channel_id IN (SELECT id FROM channels WHERE workspace_id=?)""", (until, req.ws))
    db.event("info", "pause", "All posting paused" + ("" if until >= 9e9 else f" until {time.strftime('%d %b %H:%M UTC', time.gmtime(until))}") + ".", req.ws)
    return _ws_pause_view(req.ws)
