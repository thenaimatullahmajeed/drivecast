"""JSON API and the public sign-in link pages."""
import html
import json
import re
import time

from . import VERSION, db, drive, insights, maintenance, scheduler, security, store, supervisor
from .google import DRIVE_SCOPES, TokenBox, consent_url, exchange
from .net import DriveCastError, check_group, session_for
from .platforms import get as platform
from .timeplan import AUDIENCES, normalize_plan

ROUTES = []
SESSION_DAYS = 30
LINK_MINUTES = 30
STARTED = time.time()
_login_fail = {}


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
    url = db.meta_get("public_url")
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
    return insights.dashboard(req.ws, req.user["timezone"])


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
    return out


@route("GET", "/api/groups")
def groups(req):
    return {"groups": [_group_out(g) for g in db.all("SELECT * FROM ip_groups WHERE workspace_id=? ORDER BY number", (req.ws,))]}


@route("POST", "/api/groups")
def add_group(req):
    b = req.json
    need(b, "host", "port")
    lim = store.limits_for_workspace(req.ws)
    have = db.one("SELECT COUNT(*) n FROM ip_groups WHERE workspace_id=?", (req.ws,))["n"]
    if lim["max_groups"] and have >= int(lim["max_groups"]):
        raise HttpError(403, f"Your account allows {lim['max_groups']} IP groups")
    try:
        port = int(b["port"])
        assert 0 < port < 65536
    except (ValueError, AssertionError):
        raise HttpError(400, "Port must be a number between 1 and 65535")
    g = {"host": b["host"].strip(), "port": port, "username": (b.get("username") or "").strip() or None,
         "password_enc": security.seal(b["password"]) if b.get("password") else None}
    try:
        ip, ms = check_group(g)
    except DriveCastError as e:
        raise HttpError(400, f"Proxy test failed: {e.user_message}. Check host, port and login.")
    num = db.one("SELECT COALESCE(MAX(number),0)+1 n FROM ip_groups WHERE workspace_id=?", (req.ws,))["n"]
    gid = db.run("""INSERT INTO ip_groups(workspace_id,number,host,port,username,password_enc,max_channels,priority,status,
                    outgoing_ip,latency_ms,last_check,latency_json,created_at) VALUES(?,?,?,?,?,?,?,?, 'ok',?,?,?,?,?)""",
                 (req.ws, num, g["host"], port, g["username"], g["password_enc"], max(1, int(b.get("max_channels") or 5)),
                  b.get("priority") or "normal", ip, ms, time.time(), json.dumps([ms]), time.time()))
    return {"group": _group_out(store.group(gid))}


@route("POST", "/api/groups/test", auth="user")
def test_group_form(req):
    b = req.json
    need(b, "host", "port")
    g = {"host": b["host"].strip(), "port": int(b["port"]), "username": b.get("username") or None,
         "password_enc": security.seal(b["password"]) if b.get("password") else None}
    try:
        ip, ms = check_group(g)
        return {"ok": True, "ip": ip, "latency_ms": ms}
    except DriveCastError as e:
        return {"ok": False, "error": e.user_message}


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
        ip, ms = check_group(g)
        hist = db.jload(g["latency_json"], [])[-23:] + [ms]
        if g["outgoing_ip"] and ip != g["outgoing_ip"]:
            db.run("UPDATE ip_groups SET status='down', last_error=?, last_check=?, latency_json=? WHERE id=?",
                   (f"Outgoing IP changed to {ip}. Confirm to accept it.", time.time(), json.dumps(hist), gid))
        else:
            db.run("UPDATE ip_groups SET status='ok', outgoing_ip=COALESCE(outgoing_ip,?), latency_ms=?, last_check=?, last_error=NULL, fail_count=0, latency_json=? WHERE id=?",
                   (ip, ms, time.time(), json.dumps(hist), gid))
    except DriveCastError as e:
        db.run("UPDATE ip_groups SET status='down', last_error=?, last_check=? WHERE id=?", (e.user_message, time.time(), gid))


@route("POST", "/api/groups/(\\d+)/accept-ip")
def accept_ip(req, gid):
    g = _own_group(req, gid)
    ip, ms = check_group(g)
    db.run("UPDATE ip_groups SET outgoing_ip=?, status='ok', last_error=NULL, latency_ms=? WHERE id=?", (ip, ms, g["id"]))
    return {"group": _group_out(store.group(g["id"]))}


@route("PATCH", "/api/groups/(\\d+)")
def edit_group(req, gid):
    g = _own_group(req, gid)
    b = req.json
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
    out["published_total"] = db.one("SELECT COUNT(*) n FROM uploads WHERE channel_id=? AND status='published'", (c["id"],))["n"]
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
    c = _own_channel(req, cid)
    b = req.json
    need(b, "folder_id")
    ws = store.workspace(req.ws)
    folders = {f["id"]: f for f in db.jload(ws["folders_json"], [])}
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
    plan = normalize_plan(b.get("plan"))
    start = bool(b.get("start", True))
    db.run("""UPDATE channels SET folder_id=?, folder_name=?, plan_json=?, group_id=COALESCE(?,group_id), state=?, state_reason=NULL,
              live_since=COALESCE(live_since,?) WHERE id=?""",
           (b["folder_id"], folders[b["folder_id"]]["name"], json.dumps(plan), b.get("group_id"),
            "live" if start else "paused", time.time(), c["id"]))
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
    days = min(7, int(req.query.get("days", 2)))
    now = time.time()
    rows = db.all("""SELECT s.*, c.title, g.number group_number FROM slots s JOIN channels c ON c.id=s.channel_id
                     LEFT JOIN ip_groups g ON g.id=c.group_id WHERE c.workspace_id=? AND s.due_at BETWEEN ? AND ?
                     ORDER BY s.due_at""", (req.ws, now - 86400, now + days * 86400))
    return {"slots": rows}


@route("GET", "/api/videos")
def videos(req):
    lim = min(500, int(req.query.get("limit", 100)))
    rows = db.all("""SELECT u.*, c.title channel_title FROM uploads u JOIN channels c ON c.id=u.channel_id
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


@route("POST", "/local/device", auth="local")
def local_device(req):
    db.meta_set("device_info", json.dumps(req.json)[:500])
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
