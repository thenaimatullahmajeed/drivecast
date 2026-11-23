"""JSON API and the public sign-in link pages."""
import html
import json
import re
import time

from . import VERSION, db, drive, insights, maintenance, scheduler, security, store, supervisor
from .google import DRIVE_SCOPES, TokenBox, consent_url, exchange
from .net import DriveCastError, parse_proxy, probe, session_for
from .platforms import get as platform
from .timeplan import AUDIENCES, local_day_bounds, normalize_plan

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


def public_base(req=None):
    """The address people see (panel, sign-in links, redirect URIs). See urls.py for the rule."""
    from . import urls
    return urls.public(req)


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
    _fleet_bg("email", email.strip())
    return db.one("SELECT * FROM users WHERE id=?", (uid,))


def _fleet_bg(kind, value):
    """Tell the fleet hub (in the background) that this phone now holds this account / invite."""
    import threading
    from . import fleet

    def go():
        try:
            fleet.report(kind, value)
            fleet.hello()
        except Exception:
            pass
    threading.Thread(target=go, daemon=True).start()


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
    return {"user": user_view(req.user) if req.user else None, "needs_setup": not owner, "version": VERSION,
            "open_signup": db.meta_get("open_signup") == "1", "google_login": _google_login_ready(), "email_reset": _email_ready(),
            "signup_mode": signup_mode()}


def _google_login_ready():
    o = db.one("SELECT workspace_id FROM users WHERE role='owner' ORDER BY id LIMIT 1")
    return bool(o and store.google_app(store.workspace(o["workspace_id"]))[0])


def _email_ready():
    from . import mail
    return mail.ready()


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
    _moved_check(u)
    if u.get("approval") == "pending":
        raise HttpError(403, PENDING_MSG)
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
    from . import fleet
    if not (b.get("invite_code") or "").strip():
        need(b, "name", "email", "password")
        if not fleet.open_signup() and signup_mode() == "approval":
            # no invite: the account waits until the owner approves it
            u = _create_user(b["name"], b["email"], b["password"], "student", b.get("timezone"),
                             db.jload(db.meta_get("open_limits"), {}) or {"max_channels": 3}, None)
            _mark_pending(u)
            return {"pending": True, "message": PENDING_MSG}
        # open sign-ups on this phone (owner's choice, with a cap)
        if not fleet.open_signup():
            raise HttpError(403, "You need an invite code from the owner")
        if fleet.users_count() >= fleet.max_users():
            raise HttpError(403, "This studio is full right now. Try again later or ask for an invite.")
        lim = db.jload(db.meta_get("open_limits"), {}) or {"max_channels": 3}
        u = _create_user(b["name"], b["email"], b["password"], "student", b.get("timezone"), lim, None)
        return _start_session(req, u)
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
    from . import facebook as _fb, ai as _ai
    return {"timezone": req.user["timezone"], "google_client_id": ws["google_client_id"],
            "has_secret": bool(ws["google_client_secret_enc"]), "redirect_uri": public_base(req) + "/oauth/callback",
            "public_url": public_base(req), "audiences": list(AUDIENCES),
            "fb_app_id": ws["fb_app_id"], "has_fb_secret": bool(ws["fb_app_secret_enc"]),
            "fb_redirect_uri": public_base(req) + "/oauth/fb/callback", "fb_graph_version": db.meta_get("fb_graph_version") or _fb.DEFAULT_VERSION,
            "ig_app_id": ws.get("ig_app_id"), "has_ig_secret": bool(ws.get("ig_app_secret_enc")),
            "ig_redirect_uri": public_base(req) + "/oauth/ig/callback",
            "drive_client_id": ws.get("drive_client_id"), "has_drive_secret": bool(ws.get("drive_client_secret_enc")),
            "drive_uses_own_app": bool(ws.get("drive_token_client") and ws.get("drive_token_client") == ws.get("drive_client_id")),
            "has_groq": bool(ws.get("groq_key_enc")), "ai_model": ws.get("ai_model") or _ai.AUTO, "ai_models": _ai.known().get("chat") or []}


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
    if "fb_app_id" in b:
        db.run("UPDATE workspaces SET fb_app_id=? WHERE id=?", ((b["fb_app_id"] or "").strip() or None, req.ws))
    if b.get("fb_app_secret"):
        db.run("UPDATE workspaces SET fb_app_secret_enc=? WHERE id=?", (security.seal(b["fb_app_secret"].strip()), req.ws))
    if "drive_client_id" in b:
        db.run("UPDATE workspaces SET drive_client_id=? WHERE id=?", ((b["drive_client_id"] or "").strip() or None, req.ws))
    if b.get("drive_client_secret"):
        db.run("UPDATE workspaces SET drive_client_secret_enc=? WHERE id=?", (security.seal(b["drive_client_secret"].strip()), req.ws))
    if "ig_app_id" in b:
        db.run("UPDATE workspaces SET ig_app_id=? WHERE id=?", ((b["ig_app_id"] or "").strip() or None, req.ws))
    if b.get("ig_app_secret"):
        db.run("UPDATE workspaces SET ig_app_secret_enc=? WHERE id=?", (security.seal(b["ig_app_secret"].strip()), req.ws))
    if b.get("groq_key"):
        from . import ai as _ai
        k = b["groq_key"].strip()
        try:
            _ai.test_key(k, store.workspace(req.ws))
        except _ai.AiError as e:
            raise HttpError(400, str(e))
        db.run("UPDATE workspaces SET groq_key_enc=? WHERE id=?", (security.seal(k), req.ws))
        db.meta_set(f"ai_bad:ws{req.ws}", "0")
    if b.get("groq_key_clear"):
        db.run("UPDATE workspaces SET groq_key_enc=NULL WHERE id=?", (req.ws,))
    if b.get("ai_model"):
        from . import ai as _ai
        if b["ai_model"] == _ai.AUTO or b["ai_model"] in (_ai.known().get("chat") or []):
            db.run("UPDATE workspaces SET ai_model=? WHERE id=?", (b["ai_model"], req.ws))
    if b.get("fb_graph_version"):
        v = b["fb_graph_version"].strip()
        if re.match(r"^v\d+\.\d+$", v):
            db.meta_set("fb_graph_version", v)
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
SLOW_MS, STALE_S = 2000, 6 * 3600


def pool_health(g, now=None):
    """Healthy only when the proxy answers fast AND was checked recently; thresholds are shown in the panel."""
    now = now or time.time()
    if g.get("status") == "down":
        return {"state": "down", "why": "Not responding"}
    ms, last = g.get("latency_ms"), g.get("last_check") or 0
    if last and now - last > STALE_S:
        return {"state": "stale", "why": f"Last checked {int((now - last) // 3600)} h ago (limit {STALE_S // 3600} h)"}
    if ms and ms > SLOW_MS:
        return {"state": "slow", "why": f"Answers in {ms} ms (slow above {SLOW_MS} ms)"}
    return {"state": "healthy", "why": f"{ms} ms" if ms else "OK"}


def _group_out(g):
    n = db.one("SELECT COUNT(*) n FROM channels WHERE group_id=?", (g["id"],))["n"]
    chans = db.all("SELECT id,title,handle,thumbnail,state FROM channels WHERE group_id=? ORDER BY title", (g["id"],))
    out = store.public(g)
    out["health"] = pool_health(g)
    out.update({"channel_count": n, "channels": chans, "over_recommended": n > g["max_channels"],
                "latency": db.jload(g["latency_json"], []), "has_password": bool(g["password_enc"]),
                "apps": {"google": bool(g.get("google_client_id") and g.get("google_client_secret_enc")),
                         "google_client_id": g.get("google_client_id"),
                         "meta": bool(g.get("fb_app_id") and g.get("fb_app_secret_enc")), "fb_app_id": g.get("fb_app_id"),
                         "instagram": bool(g.get("ig_app_id") and g.get("ig_app_secret_enc")), "ig_app_id": g.get("ig_app_id"),
                         "groq": bool(g.get("groq_key_enc"))}})
    # how ready this group is for a new client (prepared in advance)
    fb = db.one("SELECT status FROM fb_accounts WHERE group_id=?", (g["id"],))
    igd = db.one("SELECT COUNT(*) n FROM ig_dest WHERE group_id=? AND status='ok'", (g["id"],))["n"]
    ig = db.one("SELECT status FROM ig_accounts WHERE group_id=?", (g["id"],))
    steps = [("Proxy", g["status"] == "ok"), ("Google app", out["apps"]["google"]), ("Meta app", out["apps"]["meta"]),
             ("Instagram reader", bool(ig and ig["status"] != "needs_cookies")), ("Facebook", bool(fb and fb["status"] == "ok")),
             ("Instagram posting", igd > 0)]
    out["readiness"] = {"steps": [{"name": k, "ok": v} for k, v in steps], "score": sum(1 for _, v in steps if v), "of": len(steps)}
    acc = db.one("SELECT * FROM ig_accounts WHERE group_id=?", (g["id"],))
    out["instagram"] = None if not acc else {
        "health": igsync.health(acc),
        "username": acc["username"] or f"ID {acc['ig_user_id']}", "status": acc["status"], "error_code": acc["error_code"],
        "proxy_mode": acc.get("proxy_mode") or "group",
        "checked_at": acc["checked_at"], "paused_until": acc["paused_until"], "today": acc["day_count"] if acc["day"] == time.strftime("%Y-%m-%d", time.gmtime()) else 0,
        "sources": db.one("SELECT COUNT(*) n FROM sources s JOIN channels c ON c.id=s.channel_id WHERE c.group_id=? AND s.state='active'", (g["id"],))["n"],
        "shared_with": [r["number"] for r in db.all("""SELECT g2.number FROM ig_accounts a JOIN ip_groups g2 ON g2.id=a.group_id
                        WHERE a.id!=? AND a.workspace_id=? AND ((a.ig_user_id=? AND a.ig_user_id IS NOT NULL) OR
                        (a.username=? AND a.username IS NOT NULL)) ORDER BY g2.number""",
                        (acc["id"], acc["workspace_id"], acc["ig_user_id"], acc["username"]))],
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
    db.run("""UPDATE ip_groups SET status='ok', latency_ms=?, last_check=?, last_error=NULL, fail_count=0, fail_since=NULL, latency_json=?,
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
        raise HttpError(403, f"Your account allows {lim['max_groups']} Pools")
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
        raise HttpError(404, "Pool not found")
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
        maintenance.proxy_failed(g, e.user_message)  # patient: one failed test does not call it down


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
                                 f"The {n} channel(s) in this pool will post from the new IP. Confirm to continue.")
        db.run("UPDATE ip_groups SET host=?, port=?, username=?, password_enc=?, outgoing_ip=? WHERE id=?",
               (p["host"], p["port"], p["username"], security.seal(p["password"]) if p.get("password") else None, r["ip"], g["id"]))
        _save_probe(g["id"], r)
    if "max_channels" in b:
        db.run("UPDATE ip_groups SET max_channels=? WHERE id=?", (max(1, int(b["max_channels"])), g["id"]))
    if b.get("priority") in ("high", "normal", "low"):
        db.run("UPDATE ip_groups SET priority=? WHERE id=?", (b["priority"], g["id"]))
    if "label" in b:
        db.run("UPDATE ip_groups SET label=? WHERE id=?", ((b["label"] or "").strip()[:40] or None, g["id"]))
    if b.get("password"):
        db.run("UPDATE ip_groups SET password_enc=? WHERE id=?", (security.seal(b["password"]), g["id"]))
    return {"group": _group_out(store.group(g["id"]))}


@route("DELETE", "/api/groups/(\\d+)")
def delete_group(req, gid):
    g = _own_group(req, gid)
    if db.one("SELECT 1 FROM channels WHERE group_id=?", (g["id"],)):
        raise HttpError(409, "Move or remove this pool's channels first")
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
    gx = store.group(int(b.get("group_id") or 0)) if b.get("group_id") else None
    if gx and gx["workspace_id"] != req.ws:
        gx = None
    if purpose == "youtube" and not all(store.google_app_for(ws, gx)):
        raise HttpError(400, "Add a Google app first — the client's own (Pool → Apps) or yours (Settings)")
    if purpose == "drive" and not all(store.drive_app(ws)):
        raise HttpError(400, "Add your Google API app (or the separate Drive app) in Settings first")
    gid = None
    if purpose == "youtube":
        g = _own_group(req, b.get("group_id") or 0)
        if g["status"] == "down":
            raise HttpError(400, f"Pool {g['number']} is not responding. Fix it before connecting a channel.")
        gid = g["id"]
    elif purpose == "facebook":
        if not all(store.meta_app_for(ws, gx)):
            raise HttpError(400, "Add a Meta app first — the client's own (Pool → Apps) or yours (Settings)")
        g = _own_group(req, b.get("group_id") or 0)
        gid = g["id"]
    elif purpose == "instagram":
        if not all(store.ig_app_for(ws, gx)):
            raise HttpError(400, "Add an Instagram app ID and secret first — the client's own (Pool → Apps) or yours (Settings)")
        g = _own_group(req, b.get("group_id") or 0)
        gid = g["id"]
    elif purpose != "drive":
        raise HttpError(400, "Unknown link type")
    code = security.token(18)
    exp = time.time() + LINK_MINUTES * 60
    if purpose == "youtube" and b.get("subtitles"):
        purpose = "youtube_subs"  # also asks for permission to add subtitles
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
    return {"status": state, "error": l["error"], "channel": ch, "expires_at": l["expires_at"], "result": res}


def _page(title, body, ok=True):
    color = "#0B5E57" if ok else "#B42318"
    return ("<!doctype html><html lang=en><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
            f"<title>{html.escape(title)}</title><body style='margin:0;font-family:system-ui,sans-serif;background:#F4F3EF;"
            "color:#0E1116;display:flex;min-height:100vh;align-items:center;justify-content:center'>"
            "<div style='max-width:440px;margin:24px;background:#fff;border:1px solid #E3E1DA;border-radius:18px;padding:28px'>"
            "<div style='font:700 20px system-ui,sans-serif;letter-spacing:-.01em'>DriveCast</div>"
            "<div style='font:500 10px monospace;letter-spacing:.14em;color:#5B606B;margin-top:2px'>STUDIO <span style=\"color:#E8492A\">|</span> BY NAIMAT</div>"
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
    if l["purpose"] == "facebook":
        from . import facebook as _fb
        url = _fb.consent_url(store.meta_app_for(ws, store.group(l["group_id"]))[0], public_base(req) + "/oauth/fb/callback", code)
        return 302, {"Location": url}
    if l["purpose"] == "instagram":
        from . import igpost
        return 302, {"Location": igpost.consent_url(store.ig_app_for(ws, store.group(l["group_id"]))[0],
                                                    public_base(req) + "/oauth/ig/callback", code)}
    if l["purpose"] in ("youtube", "youtube_subs"):
        from .ai import FORCE_SSL
        scopes = list(platform("youtube").SCOPES) + ([FORCE_SSL] if l["purpose"] == "youtube_subs" else [])
        url = consent_url(store.google_app_for(ws, store.group(l["group_id"]))[0], public_base(req) + "/oauth/callback",
                          scopes, code)
    else:
        url = consent_url(store.drive_app(ws)[0], public_base(req) + "/oauth/callback", DRIVE_SCOPES, code)
    return 302, {"Location": url}


@route("GET", "/oauth/callback", auth=None)
def oauth_callback(req):
    code, err = req.query.get("state", ""), req.query.get("error")
    l = db.one("SELECT * FROM connect_links WHERE code=?", (code,))
    if not l or l["used_at"] or l["expires_at"] < time.time():
        return 400, _page("Link not valid", "Start again from a new sign-in link.", False)
    if l["purpose"].startswith(("glogin", "gsignup")):
        return google_login_callback(req, l)
    if err:
        db.run("UPDATE connect_links SET error=? WHERE code=?", ("Google sign-in was cancelled", code))
        return 400, _page("Not connected", "Sign-in was cancelled. You can open the link again.", False)
    ws = store.workspace(l["workspace_id"])
    cid, sec = store.google_app(ws)
    try:
        if l["purpose"] == "drive":
            s = session_for(None)
            dcid, dsec = store.drive_app(ws)
            tok = exchange(s, dcid, dsec, req.query.get("code", ""), public_base(req) + "/oauth/callback")
            sealed = security.seal(json.dumps(tok))
            box = TokenBox(sealed, dcid, dsec, lambda x: None)
            email = drive.whoami(s, box)
            db.run("UPDATE workspaces SET drive_token_enc=?, drive_email=?, drive_token_client=? WHERE id=?",
                   (sealed, email, dcid, ws["id"]))
            db.run("UPDATE connect_links SET used_at=?, result_json=? WHERE code=?", (time.time(), json.dumps({"email": email}), code))
            return 200, _page("Google Drive connected", f"Connected as <b>{html.escape(email or '')}</b>. You can close this tab.")
        g = store.group(l["group_id"])
        cid, sec = store.google_app_for(ws, g)  # the client's own Google app when its pool has one
        s = session_for(g)  # the channel's very first call already leaves from its pool IP
        tok = exchange(s, cid, sec, req.query.get("code", ""), public_base(req) + "/oauth/callback")
        sealed = security.seal(json.dumps(tok))
        p = platform("youtube")
        me = p.whoami(s, TokenBox(sealed, cid, sec, lambda x: None))
        auth_email = None
        try:
            ur = s.get("https://www.googleapis.com/oauth2/v2/userinfo", headers={"Authorization": "Bearer " + tok["access_token"]}, timeout=20)
            auth_email = (ur.json() or {}).get("email") if ur.status_code < 400 else None
        except Exception:
            auth_email = None
        existing = db.one("SELECT * FROM channels WHERE workspace_id=? AND platform='youtube' AND external_id=?",
                          (ws["id"], me["external_id"]))
        if existing:
            db.run("""UPDATE channels SET token_enc=?, token_client=?, title=?, handle=?, thumbnail=?, subscribers=?, total_views=?,
                      state=CASE WHEN state='attention' THEN 'live' ELSE state END, state_reason=NULL,
                      consecutive_failures=0, paused_until=NULL, yt_scope=?, auth_email=COALESCE(?,auth_email) WHERE id=?""",
                   (sealed, cid, me["title"], me["handle"], me["thumbnail"], me["subscribers"], me["total_views"],
                    tok.get("scope"), auth_email, existing["id"]))
            chid = existing["id"]
        else:
            chid = db.run("""INSERT INTO channels(workspace_id,platform,external_id,title,handle,thumbnail,token_enc,group_id,
                             subscribers,total_views,state,created_at,token_client,yt_scope,auth_email) VALUES(?,?,?,?,?,?,?,?,?,?,'setup',?,?,?,?)""",
                          (ws["id"], "youtube", me["external_id"], me["title"], me["handle"], me["thumbnail"], sealed, g["id"],
                           me["subscribers"], me["total_views"], time.time(), cid, tok.get("scope"), auth_email))
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
    srcs = db.all("SELECT id, kind, handle, state, error_code, content_type FROM sources WHERE channel_id=? AND state!='removed' ORDER BY id", (c["id"],))
    out["sources"] = srcs
    out["source"] = srcs[0] if srcs else None
    out["youtube_on"] = bool(c.get("youtube_on", 1)) and c["platform"] == "youtube"
    out["instagram"] = None
    if c.get("ig_dest_id"):
        d = db.one("SELECT username, status, via FROM ig_dest WHERE id=?", (c["ig_dest_id"],))
        out["instagram"] = {"username": d["username"], "ok": d["status"] == "ok", "via": d["via"]} if d else None
    out["ready"] = db.one("SELECT COUNT(*) n FROM media WHERE channel_id=? AND state='in_drive'", (c["id"],))["n"]
    if c.get("fb_page_id"):
        pg = db.one("SELECT p.name, p.can_post, a.status FROM fb_pages p JOIN fb_accounts a ON a.id=p.fb_account_id WHERE p.page_id=? AND p.channel_id=?", (c["fb_page_id"], c["id"]))
        out["facebook"] = {"page": pg["name"], "ok": bool(pg["can_post"]) and pg["status"] == "ok"} if pg else None
    else:
        out["facebook"] = None
    return out


@route("GET", "/api/channels")
def channels(req):
    rows = db.all("SELECT * FROM channels WHERE workspace_id=? ORDER BY title COLLATE NOCASE", (req.ws,))
    return {"channels": [_channel_out(c) for c in rows]}


@route("POST", "/api/channels")
def new_route_channel(req):
    """A client route without YouTube (e.g. TikTok/Instagram → Facebook + Instagram)."""
    b = req.json
    title = (b.get("title") or "").strip()[:80]
    if not title:
        raise HttpError(400, "Give the client a name")
    g = _own_group(req, b.get("group_id") or 0)
    lim = store.limits_for_workspace(req.ws)
    n = db.one("SELECT COUNT(*) n FROM channels WHERE workspace_id=?", (req.ws,))["n"]
    if lim["max_channels"] and n >= int(lim["max_channels"]):
        raise HttpError(403, f"Your account allows {lim['max_channels']} channels")
    cid = db.run("""INSERT INTO channels(workspace_id,platform,external_id,title,group_id,created_at,youtube_on)
                    VALUES(?,?,?,?,?,?,0)""", (req.ws, "route", "route-" + security.token(8), title, g["id"], time.time()))
    return {"channel": _channel_out(store.channel(cid))}


def _own_channel(req, cid):
    c = store.channel(int(cid), req.ws)
    if not c:
        raise HttpError(404, "Channel not found")
    return c


@route("POST", "/api/channels/(\\d+)/route")
def set_route(req, cid):
    """Finish the wizard: Pool, content source (Drive folder and/or Instagram), posting plan."""
    c = _own_channel(req, cid)
    b = req.json
    igb = b.get("instagram") or None
    srcs = [x for x in (b.get("sources") or []) if x.get("kind") in ("instagram", "youtube", "tiktok")]
    if igb:
        srcs.append({**igb, "kind": "instagram"})
    if not b.get("folder_id") and not srcs and not b.get("inbox"):
        raise HttpError(400, "Choose a Drive folder or add a source (Instagram, YouTube or TikTok)")
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
    rerouting = c["state"] != "setup"
    for sb in srcs:
        configure_source(store.channel(c["id"]), sb)
    if rerouting:  # starting the route again: sources not chosen this time stop (their ledger stays); sign-ins are untouched
        chosen = {x.get("kind", "instagram") for x in srcs}
        for old in db.all("SELECT id, kind FROM sources WHERE channel_id=? AND state!='removed'", (c["id"],)):
            if old["kind"] not in chosen:
                db.run("UPDATE sources SET state='removed', updated_at=? WHERE id=?", (time.time(), old["id"]))
    if not c["folder_id"] or (not srcs and b.get("inbox")):
        _ensure_folder(c, (c["folder_name"] or c["title"]).strip())  # Drive folder named after the client
    c = store.channel(c["id"])
    if "youtube_on" in b and c["platform"] == "youtube":
        db.run("UPDATE channels SET youtube_on=? WHERE id=?", (1 if b["youtube_on"] else 0, c["id"]))
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
        if any(x.get("kind", "instagram") == "instagram" for x in srcs):
            igsync.kick(c["id"])  # recent video → Drive straight away, with a live bar
        if any(x.get("kind") in ("youtube", "tiktok") for x in srcs):
            vidsync.kick(c["id"])  # about a week of videos → Drive straight away
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
    if b.get("title"):
        db.run("UPDATE channels SET title=? WHERE id=?", (b["title"].strip()[:80], c["id"]))
    if "folder_id" in b and b["folder_id"] and b["folder_id"] != c["folder_id"]:
        ws = store.workspace(req.ws)
        f = {x["id"]: x for x in db.jload(ws["folders_json"], [])}.get(b["folder_id"])
        if not f:
            raise HttpError(400, "Choose a folder inside your main Drive folder")
        if db.one("SELECT 1 FROM channels WHERE folder_id=? AND id!=?", (b["folder_id"], c["id"])):
            raise HttpError(409, "Another client already uses this folder")
        db.run("UPDATE channels SET folder_id=?, folder_name=?, queue_json=NULL WHERE id=?", (f["id"], f["name"], c["id"]))
    if isinstance(b.get("ai"), dict):
        from . import ai as _ai
        db.run("UPDATE channels SET ai_json=? WHERE id=?", (json.dumps(_ai.normalize(b["ai"], _ai.settings(c))), c["id"]))
    if b.get("group_id") and int(b["group_id"]) != c["group_id"]:
        if c["state"] == "live":
            raise HttpError(409, "Pause the client before moving it to another Pool")
        _own_group(req, b["group_id"])
        # Facebook pages and Instagram accounts belong to the old pool's identity: unlink them
        db.run("UPDATE fb_pages SET channel_id=NULL WHERE channel_id=?", (c["id"],))
        db.run("UPDATE ig_dest SET channel_id=NULL WHERE channel_id=?", (c["id"],))
        db.run("UPDATE channels SET group_id=?, fb_page_id=NULL, ig_dest_id=NULL WHERE id=?", (int(b["group_id"]), c["id"]))
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
        ready = (c.get("queue_count") or 0) > 0 or db.one("SELECT 1 FROM media WHERE channel_id=? AND state='in_drive'", (c["id"],))
        fetching = db.one("SELECT 1 FROM sources WHERE channel_id=? AND state='active' AND kind IN ('instagram','youtube','tiktok')", (c["id"],))
        if not ready and fetching:
            igsync.want_post(c["id"])
            igsync.kick(c["id"])
            vidsync.kick(c["id"])
            return {"channel": _channel_out(store.channel(c["id"])), "waiting": True}
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
    nxt, taken = {}, {}
    for r in rows:
        if r["status"] in ("planned", "queued") and not r.get("video_title"):
            cid = r["channel_id"]
            if cid not in nxt:
                q = db.one("SELECT queue_json FROM channels WHERE id=?", (cid,))
                nxt[cid] = db.jload((q or {}).get("queue_json"), None) or []
            i = taken.get(cid, 0); taken[cid] = i + 1
            if i < len(nxt[cid]):
                r["next_title"] = nxt[cid][i].get("name")
    return {"slots": rows, "live": live, "now": now}


def _why(r):
    """One plain line for a slot that did not go out. Reads only what the scheduler already recorded."""
    st, note = r["status"], (r.get("note") or "").strip()
    low = note.lower()
    if st == "missed":
        if "offline" in low:
            return "Missed — server was down"
        if "more than" in low and "days" in low:
            return "Missed — server was off for days, skipped (the queue carries on)"
        if "already went out" in low:
            return "Skipped — the daily safety limit was already reached"
        return f"Missed — {note}" if note else "Missed — no reason was recorded"
    if st == "held":
        return f"Held — {note or r.get('state_reason') or 'client paused'}"
    if st == "failed":
        return f"Failed — {note or 'the upload did not finish'}"
    if st == "uncertain":
        return "Not sure it went out — check YouTube"
    return ""


@route("GET", "/api/pipeline")
def pipeline(req):
    """One daily screen: Today, Ready -> Posting -> Done, one row per client, plain reasons, one line per pool."""
    now = time.time()
    s0, e0, _d = local_day_bounds(req.user["timezone"], now)
    rows = db.all("""SELECT s.id, s.channel_id, s.due_at, s.status, s.note, c.title, c.state channel_state, c.state_reason,
                     g.number gn, u.title video_title
                     FROM slots s JOIN channels c ON c.id=s.channel_id LEFT JOIN ip_groups g ON g.id=c.group_id
                     LEFT JOIN uploads u ON u.id=s.upload_id
                     WHERE c.workspace_id=? AND s.due_at>=? AND s.due_at<? AND c.state!='setup' ORDER BY s.due_at""", (req.ws, s0, e0))
    clients, today = {}, {"done": 0, "missed": 0, "left": 0, "total": 0, "next_at": None}
    cols = {"ready": [], "posting": [], "done": []}
    for r in rows:
        if r["status"] == "skipped":
            continue
        c = clients.setdefault(r["channel_id"], {"id": r["channel_id"], "title": r["title"], "pool": r["gn"], "done": 0, "total": 0,
                                                "next_at": None, "problems": [], "channel_state": r["channel_state"],
                                                "channel_reason": r["state_reason"]})
        c["total"] += 1
        today["total"] += 1
        item = {"channel_id": r["channel_id"], "client": r["title"], "at": r["due_at"], "title": r["video_title"]}
        if r["status"] == "done":
            c["done"] += 1
            today["done"] += 1
            cols["done"].append(item)
        elif r["status"] in ("queued", "running"):
            cols["posting"].append(item)
            today["left"] += 1
        elif r["status"] == "planned":
            cols["ready"].append(item)
            today["left"] += 1
            if r["due_at"] >= now - 600 and (c["next_at"] is None or r["due_at"] < c["next_at"]):
                c["next_at"] = r["due_at"]
            if r["due_at"] >= now - 600 and (today["next_at"] is None or r["due_at"] < today["next_at"]):
                today["next_at"] = r["due_at"]
        else:
            why = _why({**r})
            if r["status"] == "missed":
                today["missed"] += 1
            c["problems"].append({"at": r["due_at"], "why": why})
    for c in clients.values():
        bad = bool(c["problems"]) or c["channel_state"] in ("attention", "paused")
        c["dot"] = "red" if bad else "green"
        if not c["problems"] and c["channel_state"] != "live":
            c["problems"].append({"at": None, "why": (c["channel_reason"] or "Client is paused")})
    pools = []
    for g in db.all("SELECT * FROM ip_groups WHERE workspace_id=? ORDER BY number", (req.ws,)):
        h = pool_health(g)
        pools.append({"id": g["id"], "number": g["number"], "ok": h["state"] == "healthy",
                      "line": f"Pool {g['number']} — {'OK' if h['state'] == 'healthy' else {'down': 'Not responding', 'slow': 'Slow', 'stale': 'Not checked lately'}.get(h['state'], h['state'])}",
                      "why": h["why"]})
    return {"today": today, "clients": sorted(clients.values(), key=lambda x: (x["dot"] != "red", x["title"].lower())),
            "columns": cols, "pools": pools, "now": now}


@route("GET", "/api/videos")
def videos(req):
    lim = min(500, int(req.query.get("limit", 100)))
    rows = db.all("""SELECT u.*, c.title channel_title, g.number group_number, g.outgoing_ip group_ip FROM uploads u
                     JOIN channels c ON c.id=u.channel_id LEFT JOIN ip_groups g ON g.id=c.group_id
                     WHERE u.workspace_id=? ORDER BY u.reserved_at DESC LIMIT ?""", (req.ws, lim))
    live = {(r["channel_id"], r.get("file_id") or r["title"]) for r in rows if r.get("yt_state") not in ("removed", "rejected") and r["status"] == "published"}
    rows = [r for r in rows if not (r.get("yt_state") in ("removed", "rejected") and (r["channel_id"], r.get("file_id") or r["title"]) in live)]
    tot = db.one("""SELECT COUNT(*) n, COALESCE(SUM(views),0) v FROM uploads WHERE workspace_id=? AND status='published'
                    AND IFNULL(yt_state,'') NOT IN ('removed','rejected')""", (req.ws,))
    return {"videos": rows, "published_count": tot["n"], "published_views": tot["v"]}


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
            "disk_free": du.free, "public_url": public_base(req),
            "device": db.meta_get("device_info") if req.user["role"] == "owner" else json.dumps(_device_for(req) or {}),
            "jobs": db.one("SELECT SUM(status='queued') queued, SUM(status='running') running FROM jobs")}


@route("GET", "/healthz", auth=None)
def healthz(req):
    stale = [k for k, v in supervisor.HEARTBEATS.items() if not k.startswith("worker")
             and time.time() - v["at"] > 2 * (v.get("every") or 900) + 600]
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


@route("POST", "/local/own-domain", auth="local")
def local_own_domain(req):
    """The phone's Cloudflare tunnel is up: the panel's public address becomes the own domain
    (sign-in links and redirect URIs). An empty url goes back to the built-in Tailscale link."""
    url = (req.json.get("url") or "").strip().rstrip("/")
    if url and not re.match(r"^https://[a-z0-9.-]+\.[a-z]{2,}$", url):
        raise HttpError(400, "Use https://name.domain")
    db.meta_set("public_url_override", url)
    return {"ok": True, "public_url": url or db.meta_get("public_url")}


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


def delete_workspace_data(u):
    ws = u["workspace_id"]
    if db.one("SELECT 1 FROM jobs j JOIN channels c ON c.id=j.channel_id WHERE c.workspace_id=? AND j.status='running'", (ws,)):
        raise HttpError(409, "A post is uploading for this user right now. Try again in a few minutes.")
    with db.tx() as c:
        c.execute("DELETE FROM jobs WHERE channel_id IN (SELECT id FROM channels WHERE workspace_id=?)", (ws,))
        c.execute("DELETE FROM sources WHERE channel_id IN (SELECT id FROM channels WHERE workspace_id=?)", (ws,))
        for t in ("uploads", "media", "ig_dest", "fb_pages", "fb_accounts", "ig_accounts", "ig_vault", "channels", "ip_groups",
                  "connect_links", "events", "ai_log"):
            c.execute(f"DELETE FROM {t} WHERE workspace_id=?", (ws,))
        c.execute("DELETE FROM sessions WHERE user_id IN (SELECT id FROM users WHERE workspace_id=?)", (ws,))
        c.execute("DELETE FROM password_resets WHERE user_id IN (SELECT id FROM users WHERE workspace_id=?)", (ws,))
        c.execute("UPDATE invites SET used_by=NULL WHERE used_by IN (SELECT id FROM users WHERE workspace_id=?)", (ws,))
        c.execute("DELETE FROM users WHERE workspace_id=?", (ws,))
        c.execute("DELETE FROM workspaces WHERE id=?", (ws,))


@route("POST", "/api/admin/users/(\\d+)/delete", auth="owner")
def admin_delete_user(req, uid):
    """Remove a student and everything in their workspace. Videos already on
    YouTube and files in Drive are not touched."""
    u = db.one("SELECT * FROM users WHERE id=?", (int(uid),))
    if not u or u["role"] == "owner":
        raise HttpError(404, "User not found")
    delete_workspace_data(u)
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
    _fleet_bg("invite", code)
    return {"code": code, "register_url": public_base(req) + f"/#/register?invite={code}"}


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
from . import igsync, vidsync, instagram as _ig  # noqa: E402


def _ig_error(e, **ctx):
    if isinstance(e, _errors.CodedError) and e.code == "IG_RATE_LIMIT" and "until" not in ctx:
        # a one-off check from the panel: nothing is resting, just say what to do
        return HttpError(429, "Instagram asked to slow down for this check. Try again in a few minutes — or continue "
                              "without the check; the profile is checked automatically once the client is saved.")
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
    mode = "phone" if req.json.get("proxy_mode") == "phone" else "group"
    try:
        cookies = _ig.parse_cookies(text)
    except ValueError as e:
        raise HttpError(400, str(e))
    verified, note = True, None
    try:
        who = _ig.Client(session_for(None if mode == "phone" else g), cookies, min_gap=(0, 0), user_agent=ua, language=lang).whoami()
    except _errors.CodedError as e:
        if e.code in ("IG_COOKIES_EXPIRED", "IG_CHALLENGE"):
            raise _ig_error(e, group=g["number"])
        # Instagram was busy or the proxy hiccupped: keep the cookies, check again on first use
        verified, note = False, e.info(group=g["number"], until="later")["title"]
        who = {"username": None, "id": cookies.get("ds_user_id")}
    sealed = security.seal(text.strip())
    now = time.time()
    old = db.one("SELECT id FROM ig_accounts WHERE group_id=?", (g["id"],))
    if old:
        db.run("""UPDATE ig_accounts SET cookies_enc=?, username=?, ig_user_id=?, status='ok', error_code=NULL, error_detail=NULL,
                  paused_until=NULL, backoff=0, checked_at=?, updated_at=?, user_agent=?, language=?, proxy_mode=? WHERE id=?""",
               (sealed, who["username"], who["id"], now, now, ua, lang, mode, old["id"]))
        igsync.forget_client(old["id"])
    else:
        db.run("""INSERT INTO ig_accounts(workspace_id,group_id,username,ig_user_id,cookies_enc,checked_at,created_at,updated_at,user_agent,language,proxy_mode)
                  VALUES(?,?,?,?,?,?,?,?,?,?,?)""", (req.ws, g["id"], who["username"], who["id"], sealed, now, now, now, ua, lang, mode))
    for code in ("IG_COOKIES_EXPIRED", "IG_CHALLENGE", "IG_NO_ACCOUNT", "IG_FEEDBACK"):
        db.resolve(code, req.ws)
    db.run("UPDATE sources SET next_check=MIN(IFNULL(next_check,0),?) WHERE channel_id IN (SELECT id FROM channels WHERE group_id=?)",
           (now + 60, g["id"]))
    return {"ok": True, "username": who["username"] or f"ID {who['id']}", "verified": verified, "note": note,
            "group": _group_out(store.group(g["id"]))}


@route("POST", "/api/groups/(\\d+)/instagram/test")
def test_ig_account(req, gid):
    g = _own_group(req, gid)
    acc = db.one("SELECT * FROM ig_accounts WHERE group_id=?", (g["id"],))
    if not acc:
        raise HttpError(404, "No Instagram test account on this Pool yet")
    r = igsync.test_account(acc, g)
    acc = db.one("SELECT * FROM ig_accounts WHERE id=?", (acc["id"],))
    r["health"] = igsync.health(acc)
    if r["code"]:
        r["message"] = _errors.info(r["code"]).get("message")
    return r


@route("POST", "/api/groups/(\\d+)/instagram/proxy")
def set_ig_proxy_mode(req, gid):
    g = _own_group(req, gid)
    mode = "phone" if req.json.get("proxy_mode") == "phone" else "group"
    db.run("UPDATE ig_accounts SET proxy_mode=? WHERE group_id=?", (mode, g["id"]))
    acc = db.one("SELECT id FROM ig_accounts WHERE group_id=?", (g["id"],))
    if acc:
        igsync.forget_client(acc["id"])
    return {"ok": True, "group": _group_out(store.group(g["id"]))}


@route("DELETE", "/api/groups/(\\d+)/instagram")
def del_ig_account(req, gid):
    g = _own_group(req, gid)
    db.run("DELETE FROM ig_accounts WHERE group_id=?", (g["id"],))
    return {"ok": True}


@route("POST", "/api/ig/lookup")
def ig_lookup(req):
    """Check a client's profile: every test account in turn, then the public page. Instagram being busy never
    stops the wizard — the client is accepted and checked automatically as soon as Instagram answers."""
    b = req.json
    need(b, "group_id", "handle")
    g = _own_group(req, b["group_id"])
    try:
        p = igsync.lookup(req.ws, g, b["handle"])
    except ValueError as e:
        raise HttpError(400, str(e))
    except Exception as e:
        raise _ig_error(e, handle=b.get("handle"), group=g["number"])
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


def _active_source(cid, kind=None):
    if kind:
        return db.one("SELECT * FROM sources WHERE channel_id=? AND kind=? AND state!='removed' ORDER BY id DESC LIMIT 1", (cid, kind))
    return db.one("SELECT * FROM sources WHERE channel_id=? AND state!='removed' ORDER BY id DESC LIMIT 1", (cid,))


def _configure_video_source(c, b, kind):
    from . import vidsync
    now = time.time()
    cur = _active_source(c["id"], kind)
    if not b.get("enabled", True):
        if cur:
            db.run("UPDATE sources SET state='removed', updated_at=? WHERE id=?", (now, cur["id"]))
        return None
    if b.get("paused") is not None and cur and not b.get("link"):
        db.run("UPDATE sources SET state=?, updated_at=? WHERE id=?", ("paused" if b["paused"] else "active", now, cur["id"]))
    link = b.get("link") or b.get("handle")
    if link:
        try:
            url, handle = vidsync.parse_link(kind, link)
        except ValueError as e:
            raise HttpError(400, str(e))
    elif cur:
        url, handle = cur["url"], cur["handle"]
    else:
        raise HttpError(400, f"Paste the client's {vidsync.SITES[kind]} link")
    include_old = 1 if b.get("include_old", cur["include_old"] if cur else True) else 0
    cap = max(1, min(30, int(b.get("daily_cap") or (cur and cur["daily_cap"]) or 6)))
    mode = b.get("proxy_mode") or (cur and cur.get("proxy_mode")) or "phone"
    mode = "group" if mode == "group" else "phone"
    if not c["folder_id"] or not c.get("inbox_id"):
        c = _ensure_folder(c, handle.lstrip("@"))
    if cur and cur["url"] == url:
        db.run("UPDATE sources SET include_old=?, daily_cap=?, proxy_mode=?, state=?, updated_at=? WHERE id=?",
               (include_old, cap, mode, "paused" if b.get("paused") else "active", now, cur["id"]))
        sid = cur["id"]
    else:
        if cur:
            db.run("UPDATE sources SET state='removed', updated_at=? WHERE id=?", (now, cur["id"]))
        old = db.one("SELECT * FROM sources WHERE channel_id=? AND kind=? AND url=? ORDER BY id DESC LIMIT 1", (c["id"], kind, url))
        if old:  # same profile again: continue its ledger, nothing posts twice
            db.run("""UPDATE sources SET state='active', include_old=?, daily_cap=?, proxy_mode=?, next_check=?, error_code=NULL,
                      updated_at=? WHERE id=?""", (include_old, cap, mode, now, now, old["id"]))
            sid = old["id"]
        else:
            sid = db.run("""INSERT INTO sources(channel_id,kind,handle,url,content_type,include_old,daily_cap,proxy_mode,started_at,
                            next_check,created_at,updated_at) VALUES(?,?,?,?,'videos',?,?,?,?,?,?,?)""",
                         (c["id"], kind, handle, url, include_old, cap, mode, now, now, now, now))
    if b.get("cookies"):
        db.run("UPDATE sources SET cookies_enc=? WHERE id=?", (security.seal(b["cookies"].strip()), sid))
    if b.get("clear_cookies"):
        db.run("UPDATE sources SET cookies_enc=NULL WHERE id=?", (sid,))
    vidsync._next.pop(sid, None)
    return db.one("SELECT * FROM sources WHERE id=?", (sid,))


KEEP_DAYS = (7, 14, 30, 90, 0)  # 0 = the whole account, a few a day


def configure_source(c, b):
    """Add / change / pause / remove one source (Instagram, YouTube or TikTok). The ledger is never deleted."""
    out = _configure_source(c, b)
    if "keep_days" in b:
        try:
            kd = int(b["keep_days"])
        except (TypeError, ValueError):
            kd = 7
        kd = kd if kd in KEEP_DAYS else 7
        db.run("UPDATE sources SET keep_days=? WHERE channel_id=? AND kind=? AND state!='removed'",
               (kd, c["id"], b.get("kind") or "instagram"))
    return out


def _configure_source(c, b):
    kind = b.get("kind") or "instagram"
    if kind in ("youtube", "tiktok"):
        return _configure_video_source(c, b, kind)
    now = time.time()
    cur = _active_source(c["id"], "instagram")
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
        c = _ensure_folder(c, (c["title"] or handle).strip())
    if cur and cur["handle"] == handle:
        db.run("UPDATE sources SET content_type=?, include_old=?, daily_cap=?, state=?, updated_at=? WHERE id=?",
               (ctype, include_old, cap, "paused" if b.get("paused") else "active", now, cur["id"]))
        if include_old and not cur["include_old"]:
            db.run("UPDATE sources SET backfill_done=0 WHERE id=?", (cur["id"],))
        return _active_source(c["id"], "instagram")
    if cur:  # a different handle: the old lane stops, its records stay
        db.run("UPDATE sources SET state='removed', updated_at=? WHERE id=?", (now, cur["id"]))
    old = db.one("SELECT * FROM sources WHERE channel_id=? AND kind='instagram' AND handle=? ORDER BY id DESC LIMIT 1", (c["id"], handle))
    if old:  # the same account again: resume from its ledger, nothing posts twice
        db.run("""UPDATE sources SET state='active', content_type=?, include_old=?, daily_cap=?, next_check=?, error_code=NULL, updated_at=?
                  WHERE id=?""", (ctype, include_old, cap, now, now, old["id"]))
        return _active_source(c["id"], "instagram")
    db.run("""INSERT INTO sources(channel_id,handle,ig_user_id,full_name,picture,content_type,include_old,daily_cap,started_at,next_check,created_at,updated_at)
              VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
           (c["id"], handle, b.get("ig_user_id"), b.get("full_name"), b.get("picture"), ctype, include_old, cap, now, now, now, now))
    return _active_source(c["id"], "instagram")


@route("PUT", "/api/channels/(\\d+)/source")
def put_source(req, cid):
    c = _own_channel(req, cid)
    if not c["group_id"]:
        raise HttpError(400, "Choose a pool for this channel first")
    configure_source(c, req.json or {})
    return channel_detail(req, cid)


@route("POST", "/api/channels/(\\d+)/source/check")
def source_check_now(req, cid):
    c = _own_channel(req, cid)
    from . import vidsync
    srcs = db.all("SELECT * FROM sources WHERE channel_id=? AND state='active'", (c["id"],))
    if not srcs:
        raise HttpError(400, "This client has no source")
    for src in srcs:
        db.run("UPDATE sources SET next_check=0, error_code=NULL WHERE id=?", (src["id"],))
        vidsync._next.pop(src["id"], None)
    acc = db.one("SELECT id FROM ig_accounts WHERE group_id=?", (c["group_id"],))
    if acc:
        igsync._next_action.pop(acc["id"], None)
    vidsync.kick(c["id"])  # YouTube / TikTok: check now and fill the week of backup without waiting
    return {"ok": True}


@route("POST", "/api/channels/(\\d+)/source/(\\d+)/links")
def source_links(req, cid, sid):
    """Paste-a-link fallback: video links typed by hand join the ledger as fresh."""
    from . import vidsync
    c = _own_channel(req, cid)
    here = db.one("SELECT * FROM sources WHERE id=? AND channel_id=? AND state!='removed'", (int(sid), c["id"]))
    if not here:
        raise HttpError(404, "Source not found")
    links = req.json.get("links")
    links = links if isinstance(links, list) else str(links or "").split()
    links = [str(x).strip() for x in links if str(x).strip()]
    # each link goes to the client's source for that site (the box works from any of them)
    mine = {r["kind"]: r for r in db.all("SELECT * FROM sources WHERE channel_id=? AND state!='removed'", (c["id"],))}
    buckets = {"instagram": [], "tiktok": [], "youtube": []}
    for x in links:
        low = x.lower()
        k = "instagram" if "instagram.com" in low else "tiktok" if "tiktok.com" in low else "youtube" if ("youtu" in low) else None
        if k:
            buckets[k].append(x)
    added = known = 0
    missing = []
    for k, group in buckets.items():
        if not group:
            continue
        src = mine.get(k)
        if not src:
            missing.append(k)
            continue
        if k == "instagram":
            a, kn = igsync.add_links(src, c, group)
            added += a
            known += kn
            igsync.kick(c["id"])
        else:
            a = vidsync.add_links(src, c, group)
            added += a
            known += len(group) - a
            vidsync._next.pop(src["id"], None)
            vidsync.kick(c["id"])
    return {"added": added, "already": known, "missing": missing}


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
    if "youtube_on" in b:
        if c["platform"] != "youtube" and b["youtube_on"]:
            raise HttpError(400, "Connect a YouTube channel for this client first")
        db.run("UPDATE channels SET youtube_on=? WHERE id=?", (1 if b["youtube_on"] else 0, c["id"]))
    if "titles" in b:
        lines = [t.strip()[:95] for t in (b["titles"] if isinstance(b["titles"], list) else str(b["titles"]).splitlines()) if t.strip()]
        db.run("UPDATE channels SET titles_json=? WHERE id=?", (json.dumps(lines[:200]), c["id"]))
    return channel_detail(req, cid)


def _queue_row(c, now):
    """One client's download queue, for the dashboard and the client page: how far ahead it is, what is
    happening right now, and a plain reason when it is cooling down."""
    from . import vidsync, worker
    srcs = db.all("SELECT * FROM sources WHERE channel_id=? AND state='active'", (c["id"],))
    if not srcs:
        return None
    ppd = max(1, int((db.jload(c.get("plan_json"), {}) or {}).get("posts_per_day") or 2))
    n = {r["state"]: r["k"] for r in db.all("SELECT state, COUNT(*) k FROM media WHERE channel_id=? AND kind='video' GROUP BY state", (c["id"],))}
    ready = n.get("in_drive", 0) + n.get("cleaned", 0) + n.get("downloading", 0)
    waiting = n.get("found", 0)
    until = 0
    for s_ in srcs:
        if s_["kind"] == "instagram":
            acc = db.one("SELECT * FROM ig_accounts WHERE group_id=?", (c["group_id"],))
            if acc:
                until = max(until, float(db.meta_get("ig_route_rest:" + igsync.route_of(acc)) or 0), acc.get("paused_until") or 0)
        elif s_["error_code"] and (s_["next_check"] or 0) > now:
            until = max(until, s_["next_check"])
    prog = worker.PROGRESS.get(c["id"]) or {}
    scanning = any(s_["include_old"] and not s_["backfill_done"] for s_ in srcs)
    cap = sum(min(s_["daily_cap"] or vidsync.SOURCE_DAILY, vidsync.SOURCE_DAILY) for s_ in srcs if s_["include_old"])
    done_today = sum(s_["day_count"] for s_ in srcs if s_["include_old"] and s_["day"] == time.strftime("%Y-%m-%d", time.gmtime()))
    targets = [igsync._ready_target(c, s_) for s_ in srcs]
    state = "cooling" if until > now else "downloading" if prog.get("by") == "sync" else "scanning" if scanning else \
        "full" if ready >= min(targets) else "queued"
    return {"id": c["id"], "title": c["title"], "ready": ready, "waiting": waiting, "state": state, "until": until if until > now else None,
            "stage": prog.get("stage") if prog.get("by") == "sync" else None, "pct": prog.get("pct") if prog.get("by") == "sync" else None,
            "runway_days": round((ready + waiting) / ppd), "ready_days": round(ready / ppd), "scan_done": not scanning,
            "today": done_today, "daily": cap, "ppd": ppd}


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
    sources_out = []
    for sx in db.all("SELECT * FROM sources WHERE channel_id=? AND state!='removed' ORDER BY id", (c["id"],)):
        site = {"youtube": "YouTube", "tiktok": "TikTok"}.get(sx["kind"], "Instagram")
        o = {k: sx.get(k) for k in ("id", "kind", "handle", "url", "full_name", "picture", "content_type", "include_old", "daily_cap", "keep_days",
                                     "state", "last_check", "next_check", "error_code", "total_found", "backfill_done", "started_at")}
        o["proxy_mode"] = sx.get("proxy_mode") or ("phone" if sx["kind"] != "instagram" else "group")
        o["has_cookies"] = bool(sx.get("cookies_enc"))
        o["today"] = sx["day_count"] if sx["day"] == time.strftime("%Y-%m-%d", time.gmtime()) else 0
        if sx["error_code"]:
            o["error"] = _errors.info(sx["error_code"], handle=sx["handle"], site=site)
        sources_out.append(o)
    src = _active_source(c["id"], "instagram")
    srco = None
    if src:
        srco = {k: src[k] for k in ("id", "handle", "full_name", "picture", "content_type", "include_old", "daily_cap", "keep_days", "state",
                                     "last_check", "next_check", "error_code", "total_found", "backfill_done", "started_at")}
        srco["today"] = src["day_count"] if src["day"] == time.strftime("%Y-%m-%d", time.gmtime()) else 0
        if src["error_code"]:
            srco["error"] = _errors.info(src["error_code"], handle=src["handle"])
    acco = None
    if acc:
        acco = {"username": acc["username"], "status": acc["status"], "paused_until": acc["paused_until"], "error_code": acc["error_code"]}
        still = not (acc["status"] == "resting" and (acc["paused_until"] or 0) <= now)  # rest is over: no banner
        if acc["error_code"] and acc["status"] != "ok" and still:
            g = store.group(c["group_id"])
            until = time.strftime("%H:%M UTC", time.gmtime(acc["paused_until"])) if (acc["paused_until"] or 0) > now else "a little later"
            acco["error"] = _errors.info(acc["error_code"], group=g["number"] if g else "?", until=until)
    fb = None
    if c["group_id"]:
        fa = db.one("SELECT * FROM fb_accounts WHERE group_id=?", (c["group_id"],))
        linked = db.one("SELECT * FROM fb_pages WHERE channel_id=? AND page_id=?", (c["id"], c.get("fb_page_id") or "")) if c.get("fb_page_id") else None
        avail = db.all("SELECT id,page_id,name,picture,can_post FROM fb_pages WHERE fb_account_id=? AND (channel_id IS NULL OR channel_id=?)",
                       (fa["id"], c["id"])) if fa else []
        fb = {"connected": bool(fa), "account": fa["name"] if fa else None, "status": fa["status"] if fa else None,
              "linked": {"id": linked["id"], "name": linked["name"], "page_id": linked["page_id"]} if linked else None,
              "available": avail, "app_ready": all(store.meta_app_for(store.workspace(c["workspace_id"]), store.group(c["group_id"])))}
    igd = None
    if c["group_id"]:
        rows = db.all("SELECT id, via, ig_user_id, username, picture, status, error_code, channel_id FROM ig_dest WHERE group_id=? ORDER BY username",
                      (c["group_id"],))
        linked = next((r for r in rows if r["id"] == c.get("ig_dest_id")), None)
        ws = store.workspace(c["workspace_id"])
        igd = {"linked": linked, "available": [r for r in rows if not r["channel_id"] or r["channel_id"] == c["id"]],
               "app_ready": all(store.ig_app_for(ws, store.group(c["group_id"]))),
               "fb_ready": bool(db.one("SELECT 1 FROM fb_accounts WHERE group_id=?", (c["group_id"],)))}
        if linked and linked["error_code"]:
            linked["error"] = _errors.info(linked["error_code"])
    from . import worker as _w
    return {"channel": _channel_out(store.channel(c["id"])), "source": srco, "sources": sources_out, "ig_account": acco, "facebook": fb,
            "instagram_dest": igd, "destinations": [d["kind"] for d in _w.destinations(store.channel(c["id"]))],
            "titles": db.jload(c.get("titles_json"), []), "encore_on": bool(c.get("encore", 1)),
            "pipeline": {"found": sum(counts.values()), "waiting": counts.get("found", 0), "downloading": counts.get("downloading", 0),
                         "ready": max(counts.get("in_drive", 0), c.get("queue_count") or 0), "published": db.one("SELECT COUNT(*) n FROM uploads WHERE channel_id=? AND status='published'", (c["id"],))["n"],
                         "skipped": counts.get("skipped", 0) + counts.get("failed", 0), "photos": counts.get("waiting_facebook", 0),
                         "lanes": lanes},
            "queue": ready, "ai": _ai_channel(c), "upnext": db.jload(c.get("queue_json"), None), "upnext_at": c.get("queue_at"), "slots": slots, "encore": enc, "skipped": skipped, "events": events,
            "has_inbox": bool(c.get("inbox_id")), "ready_target": igsync._ready_target(c), "runway": _queue_row(c, time.time())}


@route("GET", "/api/settings/advanced", auth="owner")
def adv_get(req):
    from . import urls
    return {"public_url_override": urls.own_domain(), "tunnel_url": urls.tailscale(), "public": urls.public(req),
            "media": urls.media(), "backup": urls.backup()}


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


# ---------------------------------------------------------------- Facebook Pages (Graph API)
from . import facebook as _fb  # noqa: E402
from .errors import CodedError  # noqa: E402


def _fb_store_account(ws, g, user_tok, expires, s):
    """Save the connected Facebook identity for a pool and its pages' tokens."""
    who = _fb.me(s, user_tok)
    now = time.time()
    sealed = security.seal(user_tok)
    acc = db.one("SELECT id FROM fb_accounts WHERE group_id=?", (g["id"],))
    if acc:
        db.run("""UPDATE fb_accounts SET fb_user_id=?, name=?, token_enc=?, token_expires=?, status='ok',
                  error_code=NULL, error_detail=NULL, checked_at=?, updated_at=? WHERE id=?""",
               (who.get("id"), who.get("name"), sealed, expires, now, now, acc["id"]))
        acc_id = acc["id"]
    else:
        acc_id = db.run("""INSERT INTO fb_accounts(workspace_id,group_id,fb_user_id,name,token_enc,token_expires,checked_at,created_at,updated_at)
                           VALUES(?,?,?,?,?,?,?,?,?)""", (ws["id"], g["id"], who.get("id"), who.get("name"), sealed, expires, now, now, now))
    db.run("UPDATE fb_accounts SET app_id=? WHERE id=?", (store.meta_app_for(ws, g)[0], acc_id))
    n = 0
    for pg in _fb.pages(s, user_tok):
        if not pg.get("token"):
            continue
        db.run("""INSERT INTO fb_pages(workspace_id,fb_account_id,page_id,name,category,picture,token_enc,can_post,created_at,updated_at)
                  VALUES(?,?,?,?,?,?,?,?,?,?)
                  ON CONFLICT(fb_account_id,page_id) DO UPDATE SET name=excluded.name, category=excluded.category,
                  picture=excluded.picture, token_enc=excluded.token_enc, can_post=excluded.can_post, updated_at=excluded.updated_at""",
               (ws["id"], acc_id, pg["page_id"], pg["name"], pg["category"], pg["picture"],
                security.seal(pg["token"]), 1 if pg["can_post"] else 0, now, now))
        n += 1
    for code in ("FB_TOKEN_EXPIRED", "FB_NO_ACCOUNT"):
        db.resolve(code, ws["id"])
    _discover_ig_dest(ws, g, s)
    return who, n


def _discover_ig_dest(ws, g, s):
    """Every Instagram Professional account linked to this pool's pages becomes a ready destination."""
    from . import igpost
    acc = db.one("SELECT * FROM fb_accounts WHERE group_id=?", (g["id"],))
    if not acc:
        return 0
    n = 0
    for pg in db.all("SELECT * FROM fb_pages WHERE fb_account_id=?", (acc["id"],)):
        try:
            a = igpost.linked_account(s, pg["page_id"], security.unseal(pg["token_enc"]))
        except Exception:  # a page without Instagram (or without permission) is simply skipped
            continue
        if not a:
            continue
        db.run("UPDATE fb_pages SET ig_user_id=?, ig_username=? WHERE id=?", (a["ig_user_id"], a["username"], pg["id"]))
        _ig_dest_upsert(ws["id"], g["id"], "facebook", a, pg["token_enc"], None, pg["page_id"])
        n += 1
    return n


def _ig_dest_upsert(ws_id, gid, via, a, token_enc, expires, page_id=None):
    now = time.time()
    row = db.one("SELECT id FROM ig_dest WHERE group_id=? AND ig_user_id=?", (gid, a["ig_user_id"]))
    if row:
        db.run("""UPDATE ig_dest SET via=?, username=?, picture=?, page_id=?, token_enc=?, token_expires=?, status='ok',
                  error_code=NULL, error_detail=NULL, updated_at=? WHERE id=?""",
               (via, a["username"], a["picture"], page_id, token_enc, expires, now, row["id"]))
        return row["id"]
    other = db.one("SELECT g.number FROM ig_dest d JOIN ip_groups g ON g.id=d.group_id WHERE d.ig_user_id=? AND d.group_id!=?",
                   (a["ig_user_id"], gid))
    if other:  # one IP group = one identity
        db.event("warn", "instagram", f"@{a['username']} is already a destination on Pool {other['number']} — kept there.", ws_id)
        return None
    return db.run("""INSERT INTO ig_dest(workspace_id,group_id,via,ig_user_id,username,picture,page_id,token_enc,token_expires,created_at,updated_at)
                     VALUES(?,?,?,?,?,?,?,?,?,?,?)""", (ws_id, gid, via, a["ig_user_id"], a["username"], a["picture"], page_id,
                                                         token_enc, expires, now, now))


@route("GET", "/oauth/fb/callback", auth=None)
def fb_callback(req):
    code, err = req.query.get("state", ""), req.query.get("error")
    l = db.one("SELECT * FROM connect_links WHERE code=?", (code,))
    if not l or l["used_at"] or l["expires_at"] < time.time() or l["purpose"] != "facebook":
        return 400, _page("Link not valid", "Start again from a new Connect Facebook link.", False)
    if err:
        db.run("UPDATE connect_links SET error=? WHERE code=?", ("Facebook sign-in was cancelled", code))
        return 400, _page("Not connected", "Sign-in was cancelled. You can open the link again.", False)
    ws = store.workspace(l["workspace_id"])
    g = store.group(l["group_id"])
    try:
        s = session_for(g)  # Facebook sees this pool's proxy IP, like its YouTube traffic
        app_id, app_secret = store.meta_app_for(ws, g)
        tok, exp = _fb.exchange_code(s, app_id, app_secret, req.query.get("code", ""), public_base(req) + "/oauth/fb/callback")
        who, n = _fb_store_account(ws, g, tok, exp, s)
        db.run("UPDATE connect_links SET used_at=?, result_json=? WHERE code=?",
               (time.time(), json.dumps({"fb": who.get("name"), "pages": n}), code))
        return 200, _page("Facebook connected",
                          f"Connected as <b>{html.escape(who.get('name') or '')}</b> on Pool {g['number']}. "
                          f"<b>{n}</b> page(s) are ready. Go back to DriveCast and link each page to a channel. You can close this tab.")
    except CodedError as e:
        db.run("UPDATE connect_links SET error=? WHERE code=?", (e.info()["message"][:300], code))
        return 400, _page("Not connected", html.escape(e.info()["message"]), False)
    except Exception as e:
        db.run("UPDATE connect_links SET error=? WHERE code=?", (f"Unexpected error: {e}"[:300], code))
        return 500, _page("Not connected", "Something went wrong. Ask for a new link and try again.", False)


@route("GET", "/api/facebook")
def fb_state(req):
    ws = store.workspace(req.ws)
    groups = db.all("SELECT * FROM ip_groups WHERE workspace_id=? ORDER BY number", (req.ws,))
    out = []
    for g in groups:
        acc = db.one("SELECT * FROM fb_accounts WHERE group_id=?", (g["id"],))
        pages = []
        if acc:
            for p in db.all("SELECT * FROM fb_pages WHERE fb_account_id=? ORDER BY name", (acc["id"],)):
                ch = store.channel(p["channel_id"]) if p["channel_id"] else None
                pages.append({"id": p["id"], "page_id": p["page_id"], "name": p["name"], "category": p["category"],
                              "picture": p["picture"], "can_post": bool(p["can_post"]),
                              "channel": {"id": ch["id"], "title": ch["title"]} if ch else None})
        out.append({"group_id": g["id"], "number": g["number"], "group_status": g["status"],
                    "account": None if not acc else {"name": acc["name"], "status": acc["status"], "error_code": acc["error_code"],
                                                     "expires": acc["token_expires"]},
                    "pages": pages, "own_meta": bool(g.get("fb_app_id") and g.get("fb_app_secret_enc")),
                    "app_ready": all(store.meta_app_for(ws, g))})
    return {"app_ready": bool(ws["fb_app_id"] and ws["fb_app_secret_enc"]) or any(r.get("own_meta") for r in out), "groups": out}


@route("DELETE", "/api/groups/(\\d+)/facebook")
def fb_disconnect(req, gid):
    g = _own_group(req, gid)
    db.run("UPDATE channels SET fb_page_id=NULL WHERE fb_page_id IN (SELECT page_id FROM fb_pages p JOIN fb_accounts a ON a.id=p.fb_account_id WHERE a.group_id=?)", (g["id"],))
    db.run("DELETE FROM fb_accounts WHERE group_id=?", (g["id"],))
    return {"ok": True}


@route("POST", "/api/facebook/refresh/(\\d+)")
def fb_refresh(req, gid):
    g = _own_group(req, gid)
    acc = db.one("SELECT * FROM fb_accounts WHERE group_id=?", (g["id"],))
    if not acc:
        raise HttpError(400, "No Facebook connected on this pool")
    ws = store.workspace(req.ws)
    try:
        s = session_for(g)
        _fb_store_account(ws, g, security.unseal(acc["token_enc"]), acc["token_expires"], s)
    except CodedError as e:
        if e.code == "FB_TOKEN_EXPIRED":
            db.run("UPDATE fb_accounts SET status='needs_reconnect', error_code=? WHERE id=?", (e.code, acc["id"]))
        raise _ig_error(e, group=g["number"])
    return fb_state(req)


@route("POST", "/api/facebook/pages/(\\d+)/assign")
def fb_assign(req, pid):
    p = db.one("SELECT * FROM fb_pages WHERE id=? AND workspace_id=?", (int(pid), req.ws))
    if not p:
        raise HttpError(404, "Page not found")
    ch_id = req.json.get("channel_id")
    if ch_id in (None, "", 0):
        if p["channel_id"]:
            db.run("UPDATE channels SET fb_page_id=NULL WHERE id=? AND fb_page_id=?", (p["channel_id"], p["page_id"]))
        db.run("UPDATE fb_pages SET channel_id=NULL WHERE id=?", (p["id"],))
        return {"ok": True, "channel": None}
    c = _own_channel(req, ch_id)
    if c["group_id"] != db.one("SELECT group_id FROM fb_accounts WHERE id=?", (p["fb_account_id"],))["group_id"]:
        raise HttpError(400, "This page and channel are on different Pools. Keep each client on one group (one IP = one ID).")
    # one page per channel, one channel per page
    db.run("UPDATE fb_pages SET channel_id=NULL WHERE channel_id=? AND id!=?", (c["id"], p["id"]))
    db.run("UPDATE channels SET fb_page_id=NULL WHERE fb_page_id IN (SELECT page_id FROM fb_pages WHERE id=? ) AND id!=?", (p["id"], c["id"]))
    db.run("UPDATE fb_pages SET channel_id=? WHERE id=?", (c["id"], p["id"]))
    db.run("UPDATE channels SET fb_page_id=? WHERE id=?", (p["page_id"], c["id"]))
    return {"ok": True, "channel": {"id": c["id"], "title": c["title"]}}


# ---------------------------------------------------------------- Instagram as a destination
@route("GET", "/oauth/ig/callback", auth=None)
def ig_callback(req):
    from . import igpost
    code, err = req.query.get("state", ""), req.query.get("error")
    l = db.one("SELECT * FROM connect_links WHERE code=?", (code,))
    if not l or l["used_at"] or l["expires_at"] < time.time() or l["purpose"] != "instagram":
        return 400, _page("Link not valid", "Start again from a new Connect Instagram link.", False)
    if err:
        db.run("UPDATE connect_links SET error=? WHERE code=?", ("Instagram sign-in was cancelled", code))
        return 400, _page("Not connected", "Sign-in was cancelled. You can open the link again.", False)
    ws = store.workspace(l["workspace_id"])
    g = store.group(l["group_id"])
    try:
        s = session_for(g)  # Instagram sees this pool's proxy IP, like everything else of this client
        ig_id, ig_secret = store.ig_app_for(ws, g)
        tok, exp = igpost.exchange_code(s, ig_id, ig_secret, req.query.get("code", "").split("#")[0], public_base(req) + "/oauth/ig/callback")
        a = igpost.me(s, tok)
        did = _ig_dest_upsert(ws["id"], g["id"], "instagram", a, security.seal(tok), exp)
        if not did:
            raise CodedError("IGD_NOT_PRO", "already on another group")
        db.run("UPDATE connect_links SET used_at=?, result_json=? WHERE code=?",
               (time.time(), json.dumps({"instagram": a["username"], "dest_id": did}), code))
        return 200, _page("Instagram connected",
                          f"<b>@{html.escape(a['username'] or '')}</b> is ready on Pool {g['number']}. "
                          "Go back to DriveCast and choose it as this client's Instagram destination. You can close this tab.")
    except CodedError as e:
        db.run("UPDATE connect_links SET error=? WHERE code=?", (e.info()["message"][:300], code))
        return 400, _page("Not connected", html.escape(e.info()["message"]), False)
    except Exception as e:
        db.run("UPDATE connect_links SET error=? WHERE code=?", (f"Unexpected error: {e}"[:300], code))
        return 500, _page("Not connected", "Something went wrong. Ask for a new link and try again.", False)


@route("GET", "/api/instagram/destinations")
def ig_dest_list(req):
    rows = db.all("""SELECT d.id, d.group_id, g.number, d.via, d.ig_user_id, d.username, d.picture, d.status, d.error_code, d.channel_id,
                            c.title channel_title FROM ig_dest d JOIN ip_groups g ON g.id=d.group_id
                     LEFT JOIN channels c ON c.id=d.channel_id WHERE d.workspace_id=? ORDER BY g.number, d.username""", (req.ws,))
    from . import igpost
    for r in rows:
        r["today"] = igpost.published_today(r["id"])
        r["limit"] = igpost.DAILY_LIMIT
    ws = store.workspace(req.ws)
    return {"destinations": rows, "app_ready": bool(ws.get("ig_app_id") and ws.get("ig_app_secret_enc"))}


@route("POST", "/api/instagram/destinations/discover/(\\d+)")
def ig_dest_discover(req, gid):
    g = _own_group(req, gid)
    if not db.one("SELECT 1 FROM fb_accounts WHERE group_id=?", (g["id"],)):
        raise HttpError(400, f"Connect Facebook on Pool {g['number']} first — its pages carry the linked Instagram accounts.")
    n = _discover_ig_dest(store.workspace(req.ws), g, session_for(g))
    out = ig_dest_list(req)
    out["found"] = n
    return out


@route("POST", "/api/instagram/destinations/(\\d+)/assign")
def ig_dest_assign(req, did):
    d = db.one("SELECT * FROM ig_dest WHERE id=? AND workspace_id=?", (int(did), req.ws))
    if not d:
        raise HttpError(404, "Instagram destination not found")
    ch_id = req.json.get("channel_id")
    if ch_id in (None, "", 0):
        db.run("UPDATE channels SET ig_dest_id=NULL WHERE ig_dest_id=?", (d["id"],))
        db.run("UPDATE ig_dest SET channel_id=NULL WHERE id=?", (d["id"],))
        return {"ok": True, "channel": None}
    c = _own_channel(req, ch_id)
    if c["group_id"] != d["group_id"]:
        raise HttpError(400, "This Instagram account and client are on different Pools. Keep each client on one group (one IP = one ID).")
    db.run("UPDATE ig_dest SET channel_id=NULL WHERE channel_id=? AND id!=?", (c["id"], d["id"]))
    db.run("UPDATE channels SET ig_dest_id=NULL WHERE ig_dest_id=? AND id!=?", (d["id"], c["id"]))
    db.run("UPDATE ig_dest SET channel_id=? WHERE id=?", (c["id"], d["id"]))
    db.run("UPDATE channels SET ig_dest_id=? WHERE id=?", (d["id"], c["id"]))
    return {"ok": True, "channel": {"id": c["id"], "title": c["title"]}}


@route("DELETE", "/api/instagram/destinations/(\\d+)")
def ig_dest_delete(req, did):
    d = db.one("SELECT * FROM ig_dest WHERE id=? AND workspace_id=?", (int(did), req.ws))
    if not d:
        raise HttpError(404, "Not found")
    db.run("UPDATE channels SET ig_dest_id=NULL WHERE ig_dest_id=?", (d["id"],))
    db.run("DELETE FROM ig_dest WHERE id=?", (d["id"],))
    return {"ok": True}


# ---------------------------------------------------------------- live heartbeat (blinking dot)
_STARTED = time.time()


@route("GET", "/api/live")
def live(req):
    """Small and cheap: the panel polls this every few seconds."""
    from . import worker
    now = time.time()
    hb = supervisor.HEARTBEATS
    services = {k: {"age": round(now - v.get("at", now)), "state": v.get("state"), "errors": v.get("errors", 0),
                    "every": v.get("every")} for k, v in hb.items()}
    # slow = has not checked in for well over its own schedule (cleanup runs every 6 h, backup hourly…),
    # or keeps failing. Workers only wake up for posts, so they are never "slow".
    stale = [k for k, v in services.items() if not k.startswith("worker")
             and (v["age"] > 2 * (v["every"] or 900) + 600 or v["errors"] >= 3)]
    mine = {c["id"]: c["title"] for c in db.all("SELECT id, title FROM channels WHERE workspace_id=?", (req.ws,))}
    running = [{"channel_id": cid, "client": mine[cid], "stage": p.get("stage"), "pct": p.get("pct"),
                "video": p.get("title"), "started": p.get("started")}
               for cid, p in list(worker.PROGRESS.items()) if cid in mine]
    last = db.one("SELECT MAX(id) id FROM events WHERE workspace_id=? OR workspace_id IS NULL", (req.ws,))["id"]
    pub = db.one("SELECT MAX(id) id FROM uploads WHERE workspace_id=?", (req.ws,))["id"]
    ws = store.workspace(req.ws)
    queue = [q for q in (_queue_row(c, now) for c in db.all(
        "SELECT * FROM channels WHERE workspace_id=? AND state='live'", (req.ws,))) if q]
    return {"now": now, "up": round(now - _STARTED), "version": VERSION, "running": running, "queue": queue,
            "services": services, "healthy": not stale, "stale": stale,
            "paused": bool((ws.get("paused_until") or 0) > now),
            "replaced": bool(db.meta_get("superseded_by")),
            "attention": db.one("""SELECT COUNT(*) n FROM events WHERE workspace_id=? AND resolved=0 AND level='error' AND at>?""",
                                (ws["id"], now - 86400))["n"],
            "mark": f"{last or 0}.{pub or 0}.{len(running)}.{sum(q['ready'] for q in queue)}.{sum(q['waiting'] for q in queue)}",
            "device": _device_for(req)}


def _device_for(req):
    """Battery and heat for everyone; the phone's make and Android version only for the owner."""
    d = db.jload(db.meta_get("device_info"), None)
    if not d or req.user["role"] == "owner":
        return d
    out = {k: d.get(k) for k in ("battery", "charging", "temp") if k in d}
    out["at"] = float(db.meta_get("device_at") or 0) or None
    return out


# ---------------------------------------------------------------- downloader (YouTube / TikTok sources)
@route("GET", "/api/downloader")
def downloader_state(req):
    from . import vidsync
    at = db.meta_get("ytdlp_updated_at")
    return {"version": vidsync.downloader_version(), "updated_at": float(at) if at else None}


@route("POST", "/api/downloader/update", auth="owner")
def downloader_update(req):
    from . import vidsync
    try:
        v = vidsync.update_downloader()
    except CodedError as e:
        raise HttpError(400, e.info()["message"])
    except Exception as e:
        raise HttpError(502, f"Could not download the update: {e}")
    return {"version": v, "updated_at": time.time()}


# ---------------------------------------------------------------- restore from the phone screen
@route("POST", "/local/restore", auth="local")
def local_restore(req):
    """The phone app itself restores (fresh install): no panel sign-in needed, the request never leaves the phone."""
    import base64
    import os
    import threading
    b = req.json
    if db.one("SELECT 1 FROM jobs WHERE status='running'"):
        raise HttpError(409, "A post is uploading right now. Try again in a few minutes.")
    try:
        blob = _backup.fetch_link(b["link"].strip()) if b.get("link") else base64.b64decode(b.get("file_b64") or "")
        summ, created = _backup.stage(blob, b.get("password") or "")
    except ValueError as e:
        raise HttpError(400, str(e))
    except Exception as e:
        raise HttpError(400, f"Could not read the backup: {e}")

    def restart():
        time.sleep(1.5)
        os._exit(3)
    threading.Thread(target=restart, daemon=True).start()
    return {"ok": True, "summary": summ, "created": created, "restarting": True}


# ---------------------------------------------------------------- fleet (several phones, one domain)
def _fleet_body(req):
    from . import fleet
    if not fleet.verify(req.raw or b"", req.headers.get("X-DC-Fleet") or ""):
        raise HttpError(403, "Bad fleet signature")
    if fleet.role() != "hub":
        raise HttpError(400, "This phone is not the hub")
    return fleet


@route("POST", "/fleet/hello", auth=None)
def fleet_hello(req):
    fleet = _fleet_body(req)
    from . import usermove
    host = (req.json.get("host") or "").lower()
    moved = usermove.pending_for(host)
    if moved:
        usermove.clear_pending(host)  # it applies them on this answer
    return {"ok": fleet.apply("/fleet/hello", req.json), "update_now": db.meta_get("fleet_update_now") or None,
            "telegram": fleet.telegram_for_fleet(), "moved_away": moved}


@route("POST", "/fleet/dir", auth=None)
def fleet_dir(req):
    fleet = _fleet_body(req)
    return {"ok": fleet.apply("/fleet/dir", req.json)}


def _panel(host):
    return f"https://{host}"


@route("GET", "/site/api/fleet", auth=None)
def site_fleet(req):
    """What the landing page shows: the studios (phones) online and whether sign-ups are open."""
    from . import fleet
    if fleet.role() != "hub":
        me = fleet.my_host() or (req.headers.get("Host") or "")
        return {"devices": [{"label": (me.split(".")[0] or "S1").upper(), "online": True}], "open": fleet.open_signup(),
                "single": True}
    devs = fleet.devices()
    return {"devices": [{"label": d["label"], "online": d["online"]} for d in devs],
            "open": any(d["online"] and d["open_signup"] and d["users"] < (d["max_users"] or 0) for d in devs), "single": False}


@route("POST", "/site/api/where", auth=None)
def site_where(req):
    """Sign in: which phone holds this email."""
    from . import fleet
    email = (req.json.get("email") or "").strip().lower()
    if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
        raise HttpError(400, "Enter a valid email")
    if fleet.role() != "hub":
        return {"url": public_base(req) + "/#/login?email=" + email}
    host = fleet.where(email)
    if not host:
        raise HttpError(404, "No studio account uses this email. Check it, or use Get started with your invite code.")
    return {"url": _panel(host) + "/#/login?email=" + email}


@route("POST", "/site/api/join", auth=None)
def site_join(req):
    """Get started: the phone that issued the invite, or the emptiest one with open sign-ups."""
    from . import fleet
    code = (req.json.get("invite") or "").strip().upper()
    if fleet.role() != "hub":
        if not code and not fleet.open_signup():
            raise HttpError(403, "Sign-ups are by invite. Enter your invite code.")
        return {"url": public_base(req) + "/#/register" + (f"?invite={code}" if code else "")}
    host, by_invite = fleet.pick_for_signup(code or None)
    if not host:
        raise HttpError(404 if by_invite else 403, "This invite code was not found." if by_invite
                        else "New sign-ups are closed right now. Enter an invite code, or write to support@drivecast.site.")
    return {"url": _panel(host) + "/#/register" + (f"?invite={code}" if code else "")}


@route("GET", "/api/fleet", auth="owner")
def fleet_state(req):
    from . import fleet
    return {"role": fleet.role(), "host": fleet.my_host(), "hub": fleet.hub_url(),
            "key": fleet.key() if fleet.role() == "hub" else None,
            "error": db.meta_get("fleet_error") or None, "seen": float(db.meta_get("fleet_seen") or 0) or None,
            "devices": fleet.devices() if fleet.role() == "hub" else [],
            "totals": fleet.totals(fleet.devices()) if fleet.role() == "hub" else None,
            "targets": [d["host"] for d in fleet.devices() if d["online"] and d["host"] != fleet.my_host()] if fleet.role() == "hub" else [],
            "groups": db.all("SELECT id, number, outgoing_ip, moved_to FROM ip_groups WHERE workspace_id=? ORDER BY number", (req.ws,)),
            "open_signup": fleet.open_signup(), "max_users": fleet.max_users(), "users": fleet.users_count()}


@route("POST", "/api/fleet", auth="owner")
def fleet_save(req):
    from . import fleet
    b = req.json
    if "open_signup" in b:
        db.meta_set("open_signup", "1" if b["open_signup"] else "0")
    if b.get("max_users"):
        db.meta_set("max_users", str(max(1, min(500, int(b["max_users"])))))
    act = b.get("action")
    if act in ("hub", "join") and not fleet.my_host():
        raise HttpError(400, "Turn on this phone's own domain first (phone screen → Own domain).")
    if act == "hub":
        fleet.make_hub()
    elif act == "join":
        if not b.get("hub") or not b.get("key"):
            raise HttpError(400, "Paste the hub address and the fleet key")
        fleet.join(b["hub"], b["key"])
    elif act == "leave":
        fleet.leave()
    elif act == "hide" and fleet.role() == "hub":
        db.run("UPDATE fleet_devices SET hidden=1 WHERE host=?", ((b.get("host") or "").lower(),))
    if fleet.role():
        fleet.tick()
    return fleet_state(req)


# ---------------------------------------------------------------- alerts (panel + phone notifications)
@route("GET", "/local/alerts", auth="local")
def local_alerts(req):
    return {"alerts": db.jload(db.meta_get("alerts_json"), []) or []}


@route("GET", "/api/alerts", auth="owner")
def owner_alerts(req):
    from . import alerts
    return {"alerts": alerts.tick()}


# ---------------------------------------------------------------- updates without reinstalling
@route("GET", "/api/update", auth="owner")
def update_state(req):
    from . import hotupdate, fleet
    st = hotupdate.state()
    st["fleet_role"] = fleet.role()
    st["phones"] = len(fleet.devices()) if fleet.role() == "hub" else 1
    return st


@route("POST", "/api/update", auth="owner")
def update_apply(req):
    from . import hotupdate
    b = req.json
    if "auto" in b:
        db.meta_set("auto_update", "1" if b["auto"] else "0")
        return hotupdate.state()
    if b.get("action") == "back_to_app":
        hotupdate.back_to_app()
        return {"ok": True, "restarting": True}
    if b.get("all"):
        from . import fleet
        if fleet.role() == "hub":
            fleet.push_update()  # every phone installs it at its next check-in (within 5 minutes)
    if db.one("SELECT 1 FROM jobs WHERE status='running'"):
        if b.get("all"):
            return {"ok": True, "message": "Other phones update within 5 minutes; this one updates when its upload ends.", "later": True}
        raise HttpError(409, "A post is uploading right now. Try again in a few minutes.")
    try:
        return hotupdate.apply(force=bool(b.get("force")))
    except ValueError as e:
        raise HttpError(400, str(e))
    except Exception as e:
        raise HttpError(502, f"Update failed: {e}")


# ---------------------------------------------------------------- backup test (restore drill)
@route("POST", "/api/backup/test", auth="owner")
def backup_test(req):
    try:
        return _backup.test_latest()
    except Exception as e:
        raise HttpError(400, str(e))


@route("POST", "/fleet/import", auth=None)
def fleet_import(req):
    from . import fleet
    if not fleet.verify(req.raw or b"", req.headers.get("X-DC-Fleet") or ""):
        raise HttpError(403, "Bad fleet signature")
    try:
        return fleet.receive_group(req.raw)
    except ValueError as e:
        raise HttpError(409, str(e))


@route("POST", "/api/fleet/move", auth="owner")
def fleet_move(req):
    from . import fleet
    g = _own_group(req, req.json.get("group_id") or 0)
    try:
        return fleet.send_group(g["id"], req.json.get("host") or "")
    except ValueError as e:
        raise HttpError(400, str(e))


# ---------------------------------------------------------------- the client's own apps, per Pool
@route("PUT", "/api/groups/(\\d+)/apps")
def group_apps(req, gid):
    """Google / Meta / Instagram apps that belong to the client of this pool (their own Cloud project and
    Meta app). Empty = use yours from Settings. Existing sign-ins keep working with the app that issued them."""
    g = _own_group(req, gid)
    b = req.json
    sets = []
    for idk, seck, col_id, col_sec in (("google_client_id", "google_client_secret", "google_client_id", "google_client_secret_enc"),
                                       ("fb_app_id", "fb_app_secret", "fb_app_id", "fb_app_secret_enc"),
                                       ("ig_app_id", "ig_app_secret", "ig_app_id", "ig_app_secret_enc")):
        if idk in b:
            v = (b[idk] or "").strip() or None
            if idk == "google_client_id" and v and not v.endswith(".apps.googleusercontent.com"):
                raise HttpError(400, "A Google Client ID ends with .apps.googleusercontent.com")
            sets.append((col_id, v))
            if not v:
                sets.append((col_sec, None))
        if b.get(seck):
            sets.append((col_sec, security.seal(b[seck].strip())))
    if b.get("groq_key"):
        from . import ai as _ai
        k = b["groq_key"].strip()
        try:
            _ai.test_key(k, store.workspace(req.ws))
        except _ai.AiError as e:
            raise HttpError(400, str(e))
        sets.append(("groq_key_enc", security.seal(k)))
        db.meta_set(f"ai_bad:g{g['id']}", "0")
    if b.get("groq_key_clear"):
        sets.append(("groq_key_enc", None))
    if "label" in b:
        sets.append(("label", (b["label"] or "").strip()[:60] or None))
    for col, val in sets:
        db.run(f"UPDATE ip_groups SET {col}=? WHERE id=?", (val, g["id"]))
    return {"group": _group_out(store.group(g["id"]))}


# ---------------------------------------------------------------- Instagram cookie vault (test accounts loaded in advance)
@route("GET", "/api/ig/vault")
def vault_list(req):
    rows = db.all("""SELECT v.id, v.label, v.username, v.status, v.error, v.checked_at, v.group_id, g.number group_number, v.created_at
                     FROM ig_vault v LEFT JOIN ip_groups g ON g.id=v.group_id WHERE v.workspace_id=? ORDER BY v.group_id IS NOT NULL, v.id""",
                  (req.ws,))
    return {"vault": rows}


@route("POST", "/api/ig/vault")
def vault_add(req):
    text = (req.json.get("cookies") or "").strip()
    try:
        jar = _ig.parse_cookies(text)
    except ValueError as e:
        raise HttpError(400, str(e))
    if not jar.get("sessionid"):
        raise HttpError(400, "These cookies have no sessionid — export them while signed in to instagram.com")
    uid = str(jar.get("ds_user_id") or "")
    if uid and db.one("SELECT 1 FROM ig_vault WHERE workspace_id=? AND ig_user_id=? AND group_id IS NULL", (req.ws, uid)):
        raise HttpError(409, "This account is already in the vault")
    vid = db.run("""INSERT INTO ig_vault(workspace_id,label,ig_user_id,cookies_enc,user_agent,created_at) VALUES(?,?,?,?,?,?)""",
                 (req.ws, (req.json.get("label") or "").strip()[:60] or None, uid, security.seal(text),
                  (req.json.get("user_agent") or "")[:300] or None, time.time()))
    _vault_check(db.one("SELECT * FROM ig_vault WHERE id=?", (vid,)))
    return vault_list(req)


def _vault_check(v):
    """Light check through the phone's own internet (the vault account has no group yet)."""
    try:
        who = _ig.Client(session_for(None), _ig.parse_cookies(security.unseal(v["cookies_enc"])), min_gap=(0, 0),
                         user_agent=v.get("user_agent")).whoami()
        db.run("UPDATE ig_vault SET status='ready', username=COALESCE(?,username), error=NULL, checked_at=? WHERE id=?",
               (who.get("username"), time.time(), v["id"]))
    except CodedError as e:
        bad = e.code in ("IG_COOKIES_EXPIRED", "IG_CHALLENGE")
        db.run("UPDATE ig_vault SET status=?, error=?, checked_at=? WHERE id=?",
               ("bad" if bad else "unchecked", e.info()["title"], time.time(), v["id"]))
    except Exception as e:
        db.run("UPDATE ig_vault SET status='unchecked', error=?, checked_at=? WHERE id=?", (str(e)[:120], time.time(), v["id"]))


@route("POST", "/api/ig/vault/(\\d+)/check")
def vault_check(req, vid):
    v = db.one("SELECT * FROM ig_vault WHERE id=? AND workspace_id=?", (int(vid), req.ws))
    if not v:
        raise HttpError(404, "Not found")
    _vault_check(v)
    return vault_list(req)


@route("DELETE", "/api/ig/vault/(\\d+)")
def vault_delete(req, vid):
    db.run("DELETE FROM ig_vault WHERE id=? AND workspace_id=?", (int(vid), req.ws))
    return vault_list(req)


@route("POST", "/api/groups/(\\d+)/instagram/from-vault")
def group_from_vault(req, gid):
    """Give this pool one of the prepared test accounts."""
    g = _own_group(req, gid)
    v = db.one("SELECT * FROM ig_vault WHERE id=? AND workspace_id=?", (int(req.json.get("vault_id") or 0), req.ws))
    if not v:
        raise HttpError(404, "Vault account not found")
    if v["group_id"] and v["group_id"] != g["id"]:
        raise HttpError(409, "This account already belongs to another group (one account per group)")
    sub = type("R", (), {"ws": req.ws, "user": req.user, "query": {}, "headers": req.headers,
                         "json": {"cookies": security.unseal(v["cookies_enc"]), "user_agent": v.get("user_agent"),
                                  "proxy_mode": req.json.get("proxy_mode") or "group"}})()
    out = set_ig_account(sub, str(g["id"]))
    db.run("UPDATE ig_vault SET group_id=NULL WHERE group_id=? AND id!=?", (g["id"], v["id"]))
    db.run("UPDATE ig_vault SET group_id=?, status='used' WHERE id=?", (g["id"], v["id"]))
    return out


# ---------------------------------------------------------------- Devices page (admin): what each phone needs to stay on air
@route("GET", "/api/devices", auth="owner")
def devices_page(req):
    import shutil
    from . import config, fleet, hotupdate, lease, urls
    du = shutil.disk_usage(config.DATA_DIR)
    now = time.time()
    services = []
    for k, v in sorted(supervisor.HEARTBEATS.items()):
        if k.startswith("worker"):
            continue
        age = now - v.get("at", now)
        every = v.get("every") or 900
        services.append({"name": k, "age": round(age), "every": every, "state": v.get("state"), "errors": v.get("errors", 0),
                         "ok": age <= 2 * every + 600 and (v.get("errors") or 0) < 3, "last_error": v.get("last_error")})
    me = {"host": fleet.my_host() or "this phone", "version": VERSION, "running_from": hotupdate.running_from(),
          "uptime": round(now - STARTED), "device": db.jload(db.meta_get("device_info"), {}) or {},
          "disk_free": du.free, "disk_total": du.total, "own_domain": urls.own_domain(), "tailscale": urls.tailscale(),
          "replaced": bool(lease.superseded()), "instance": lease.instance_id()[:6],
          "backup_at": float(db.meta_get("backup_at") or 0) or None, "backup_test_at": float(db.meta_get("backup_test_at") or 0) or None,
          "jobs": db.one("SELECT COALESCE(SUM(status='queued'),0) queued, COALESCE(SUM(status='running'),0) running FROM jobs"),
          "services": services, "alerts": db.jload(db.meta_get("alerts_json"), []) or [],
          "stats": fleet._stats(), "role": fleet.role()}
    return {"this": me, "fleet": fleet.devices() if fleet.role() == "hub" else [],
            "totals": fleet.totals(fleet.devices()) if fleet.role() == "hub" else None}


# ---------------------------------------------------------------- AI captions (Groq)
def _ai_channel(c):
    from . import ai as _ai
    k = _ai.key_for(c)
    rep = _ai.latest_report(c["id"])
    return {"settings": _ai.settings(c), "key": k[2] if k else None, "usage": _ai.usage(k[1]) if k else None,
            "subtitles_allowed": _ai.can_subtitle(c), "report": rep, "languages": _ai.LANGUAGES}


@route("GET", "/api/ai")
def ai_overview(req):
    """Every AI key in this workspace, how much of today's free limit it used, and each client's switches."""
    from . import ai as _ai
    ws = store.workspace(req.ws)
    keys = []
    if ws.get("groq_key_enc"):
        keys.append({"scope": f"ws{ws['id']}", "where": "Yours (Settings)", "usage": _ai.usage(f"ws{ws['id']}")})
    for g in db.all("SELECT * FROM ip_groups WHERE workspace_id=? AND groq_key_enc IS NOT NULL ORDER BY number", (req.ws,)):
        keys.append({"scope": f"g{g['id']}", "where": f"Pool {g['number']}" + (f" · {g['label']}" if g.get("label") else ""),
                     "group_id": g["id"], "usage": _ai.usage(f"g{g['id']}")})
    chans = []
    for c in db.all("SELECT * FROM channels WHERE workspace_id=? ORDER BY title", (req.ws,)):
        k = _ai.key_for(c)
        chans.append({"id": c["id"], "title": c["title"], "thumbnail": c["thumbnail"], "settings": _ai.settings(c),
                      "key": k[2] if k else None, "subtitles_allowed": _ai.can_subtitle(c)})
    since = time.time() - 7 * 86400
    week = db.one("""SELECT COUNT(*) n, COALESCE(SUM(CASE WHEN kind='text' AND ok=1 THEN 1 ELSE 0 END),0) captions,
                     COALESCE(SUM(CASE WHEN kind='subs' AND ok=1 THEN 1 ELSE 0 END),0) subs,
                     COALESCE(SUM(CASE WHEN kind='flag' THEN 1 ELSE 0 END),0) held,
                     COALESCE(SUM(audio_s),0) audio FROM ai_log WHERE workspace_id=? AND at>=?""", (req.ws, since))
    return {"has_owner_key": bool(ws.get("groq_key_enc")), "model": _ai.model(ws), "choice": ws.get("ai_model") or _ai.AUTO,
            "models": _ai.known().get("chat") or [], "keys": keys,
            "channels": chans, "week": week, "limits": {"text": _ai.TEXT_PER_DAY, "audio": _ai.AUDIO_PER_DAY},
            "languages": _ai.LANGUAGES}


@route("GET", "/api/channels/(\\d+)/ai")
def ai_channel(req, cid):
    return _ai_channel(_own_channel(req, cid))


@route("POST", "/api/channels/(\\d+)/ai/report")
def ai_report_now(req, cid):
    from . import ai as _ai
    c = _own_channel(req, cid)
    if not _ai.key_for(c):
        raise HttpError(400, "Add a Groq key first — in Settings, or on this client's Pool")
    _ai.weekly(force_channel=c["id"])
    return _ai_channel(store.channel(c["id"]))


@route("POST", "/api/ai/preview")
def ai_preview(req):
    """Try the captions on a sample text, without posting anything."""
    from . import ai as _ai
    b = req.json
    c = _own_channel(req, int(b.get("channel_id") or 0))
    k = _ai.key_for(c)
    if not k:
        raise HttpError(400, "Add a Groq key first — in Settings, or on this client's Pool")
    cfg = _ai.normalize(b.get("ai") or {}, _ai.settings(c))
    tmp = dict(c, ai_json=json.dumps({**cfg, "captions": True, "subtitles": False}))
    title = (b.get("title") or "").strip() or c["title"]
    try:
        pack = _ai.prepare(tmp, {"id": "preview"}, {"caption": (b.get("caption") or "").strip()}, title, title,
                           ["youtube", "facebook", "instagram"], file_key=None)
    except CodedError as e:
        return {"held": e.ctx.get("why") or e.detail}
    if not pack or not pack["texts"]:
        raise HttpError(502, "Groq did not answer — the key may be resting. Try again in a minute.")
    t = pack["texts"]
    return {"youtube": {"title": t["youtube"][0], "description": t["youtube"][1]},
            "facebook": t["facebook"][1], "instagram": t["instagram"][1]}


# ---------------------------------------------------------------- forgotten password
RESET_MINUTES = 30
_forgot_hits = {}


def _owner():
    return db.one("SELECT * FROM users WHERE role='owner' ORDER BY id LIMIT 1")


def _make_reset(u, made_by, minutes=RESET_MINUTES):
    tok = security.token(24)
    now = time.time()
    db.run("DELETE FROM password_resets WHERE user_id=? AND used_at IS NULL", (u["id"],))
    db.run("INSERT INTO password_resets(token_hash,user_id,created_at,expires_at,made_by) VALUES(?,?,?,?,?)",
           (security.token_hash(tok), u["id"], now, now + minutes * 60, made_by))
    return tok


@route("POST", "/api/auth/forgot", auth=None)
def forgot_password(req):
    """Always the same answer, so nobody can test which emails have an account."""
    from . import mail
    email = (req.json.get("email") or "").strip()
    if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
        raise HttpError(400, "Enter a valid email")
    hits = [t for t in _forgot_hits.get(req.ip, []) if t > time.time() - 900]
    if len(hits) >= 5:
        raise HttpError(429, "Too many requests. Wait 15 minutes.")
    _forgot_hits[req.ip] = hits + [time.time()]
    u = db.one("SELECT * FROM users WHERE lower(email)=lower(?)", (email,))
    if u and u["status"] == "active":
        if mail.ready():
            tok = _make_reset(u, "email")
            link = public_base(req) + "/#/reset/" + tok
            try:
                text, body = mail.reset_email(u["name"], link)
                mail.send(u["email"], "Reset your DriveCast password", text, body)
            except Exception as e:
                o = _owner()
                db.event("error", "account", f"The password reset email to {u['email']} could not be sent ({str(e)[:120]}). "
                         "Check Settings → Email, or make a reset link on the Users page.", o and o["workspace_id"])
        else:
            o = _owner()
            if o and u["id"] != o["id"]:
                db.event("warn", "account", f"{u['name']} ({u['email']}) asked to reset the password. Users → open the account → "
                         "Send reset link.", o["workspace_id"], None, "PW_RESET_ASK")
    from . import mail as _m
    return {"ok": True, "emailed": _m.ready()}


@route("POST", "/api/auth/reset", auth=None)
def reset_password(req):
    b = req.json
    need(b, "token", "password")
    if len(b["password"]) < 8:
        raise HttpError(400, "Password must be at least 8 characters")
    r = db.one("SELECT * FROM password_resets WHERE token_hash=?", (security.token_hash(b["token"].strip()),))
    if not r or r["used_at"] or r["expires_at"] < time.time():
        raise HttpError(400, "This reset link is not valid any more. Ask for a new one.")
    u = db.one("SELECT * FROM users WHERE id=?", (r["user_id"],))
    if not u or u["status"] != "active":
        raise HttpError(403, "This account is suspended. Contact the server owner.")
    db.run("UPDATE users SET pw_hash=? WHERE id=?", (security.hash_password(b["password"]), u["id"]))
    db.run("UPDATE password_resets SET used_at=? WHERE token_hash=?", (time.time(), r["token_hash"]))
    db.run("DELETE FROM sessions WHERE user_id=?", (u["id"],))
    db.event("info", "account", f"{u['name']} chose a new password", u["workspace_id"])
    return _start_session(req, u)


@route("POST", "/api/admin/users/(\\d+)/reset-link", auth="owner")
def admin_reset_link(req, uid):
    from . import mail
    u = db.one("SELECT * FROM users WHERE id=?", (int(uid),))
    if not u:
        raise HttpError(404, "User not found")
    tok = _make_reset(u, "owner", minutes=24 * 60)
    link = public_base(req) + "/#/reset/" + tok
    emailed = False
    if mail.ready():
        try:
            text, body = mail.reset_email(u["name"], link)
            mail.send(u["email"], "Reset your DriveCast password", text.replace("30 minutes", "24 hours"), body.replace("30 minutes", "24 hours"))
            emailed = True
        except Exception:
            emailed = False
    return {"url": link, "emailed": emailed, "email": u["email"]}


@route("GET", "/api/mail", auth="owner")
def mail_get(req):
    from . import mail
    return mail.view()


@route("POST", "/api/mail", auth="owner")
def mail_save(req):
    from . import mail
    return mail.save(req.json)


@route("POST", "/api/mail/test", auth="owner")
def mail_test(req):
    from . import mail
    try:
        mail.send(req.user["email"], "DriveCast email works", "This is a test from DriveCast Studio. Password reset emails will look like they come from this address.")
    except Exception as e:
        raise HttpError(400, f"Could not send: {str(e)[:200]}")
    return {"ok": True, "to": req.user["email"]}


# ---------------------------------------------------------------- Sign in / sign up with Google
LOGIN_SCOPES = ["openid", "email", "profile"]  # basic scopes: no Google review needed


@route("GET", "/oauth/google", auth=None)
def google_start(req):
    from .google import AUTH_URL
    from urllib.parse import urlencode
    o = _owner()
    if not o:
        return 400, _page("Not ready", "This server has no owner yet.", False)
    ws = store.workspace(o["workspace_id"])
    cid, _sec = store.google_app(ws)
    if not cid:
        return 400, _page("Google sign-in is not set up yet", "Use your email and password for now.", False)
    mode = "gsignup" if req.query.get("mode") == "signup" else "glogin"
    inv = re.sub(r"[^A-Z0-9-]", "", (req.query.get("invite") or "").upper())[:40]
    code = security.token(18)
    db.run("INSERT INTO connect_links(code,workspace_id,purpose,group_id,created_at,expires_at) VALUES(?,?,?,?,?,?)",
           (code, ws["id"], mode + (":" + inv if inv else ""), None, time.time(), time.time() + 600))
    q = {"client_id": cid, "redirect_uri": public_base(req) + "/oauth/callback", "response_type": "code",
         "scope": " ".join(LOGIN_SCOPES), "prompt": "select_account", "state": code}
    hint = (req.query.get("hint") or "").strip()
    if hint and "@" in hint:
        q["login_hint"] = hint[:200]
    return 302, {"Location": AUTH_URL + "?" + urlencode(q)}


def _google_identity(req, l):
    ws = store.workspace(l["workspace_id"])
    cid, sec = store.google_app(ws)
    s = session_for(None)
    tok = exchange(s, cid, sec, req.query.get("code", ""), public_base(req) + "/oauth/callback", offline=False)
    r = s.get("https://openidconnect.googleapis.com/v1/userinfo", headers={"Authorization": "Bearer " + tok["access_token"]}, timeout=20)
    info = r.json() if r.status_code < 400 else {}
    return info


def google_login_callback(req, l):
    from . import fleet
    db.run("UPDATE connect_links SET used_at=? WHERE code=?", (time.time(), l["code"]))
    if req.query.get("error"):
        return 302, {"Location": "/#/login"}
    try:
        info = _google_identity(req, l)
    except Exception as e:
        return 400, _page("Google sign-in failed", f"Try again. ({html.escape(str(e)[:160])})", False)
    email, sub = (info.get("email") or "").strip(), info.get("sub")
    if not email or not sub or not info.get("email_verified", False):
        return 400, _page("Email not verified", "Google did not confirm this email address. Use another Google account.", False)
    u = db.one("SELECT * FROM users WHERE google_sub=?", (sub,)) or db.one("SELECT * FROM users WHERE lower(email)=lower(?)", (email,))
    if u:
        w = store.workspace(u["workspace_id"])
        if w and w.get("moved_to"):
            return 302, {"Location": _panel(w["moved_to"]) + "/oauth/google?mode=login&hint=" + email}
        if u.get("approval") == "pending":
            if not u.get("google_sub"):
                db.run("UPDATE users SET google_sub=? WHERE id=?", (sub, u["id"]))
            return 200, _page("Waiting for approval", PENDING_MSG, False)
        if u["status"] != "active":
            return 403, _page("Account suspended", "Contact the studio owner.", False)
        if not u.get("google_sub"):
            db.run("UPDATE users SET google_sub=? WHERE id=?", (sub, u["id"]))
        _start_session(req, u)
        return 302, {"Location": "/#/dashboard"}
    mode, _, invite = l["purpose"].partition(":")
    if fleet.role() == "hub":  # the account may live on another phone of the studio
        host = fleet.where(email)
        if host and host != fleet.my_host():
            return 302, {"Location": _panel(host) + "/oauth/google?mode=login&hint=" + email}
    # no account yet: Google sign-in also creates one, with the same rules as the Create account form
    name = (info.get("name") or email.split("@")[0]).strip()[:80]
    tz = "Asia/Karachi"
    if invite:
        inv = db.one("SELECT * FROM invites WHERE code=?", (invite,))
        if not inv and fleet.role() == "hub":
            host, _ = fleet.pick_for_signup(invite)  # the phone that issued this invite
            if host and host != fleet.my_host():
                return 302, {"Location": _panel(host) + f"/oauth/google?mode=signup&invite={invite}&hint={email}"}
        if not inv or inv["revoked"] or inv["used_by"] or inv["expires_at"] < time.time():
            return 403, _page("Invite not valid", "This invite code is not valid or was already used. Ask for a new one.", False)
        u = _create_user(name, email, security.token(24), "student", tz, db.jload(inv["limits_json"], {}), invite)
    elif fleet.open_signup() and fleet.users_count() < fleet.max_users():
        u = _create_user(name, email, security.token(24), "student", tz, db.jload(db.meta_get("open_limits"), {}) or {"max_channels": 3}, None)
    else:
        if fleet.role() == "hub":
            host, _ = fleet.pick_for_signup(None)
            if host and host != fleet.my_host():
                return 302, {"Location": _panel(host) + "/oauth/google?mode=signup&hint=" + email}
        if signup_mode() != "approval":
            return 403, _page("No account for this email", "Sign-ups are by invite. Open Create account, enter your invite code, then use Google.", False)
        u = _create_user(name, email, security.token(24), "student", tz, db.jload(db.meta_get("open_limits"), {}) or {"max_channels": 3}, None)
        db.run("UPDATE users SET google_sub=? WHERE id=?", (sub, u["id"]))
        _mark_pending(u)
        return 200, _page("Waiting for approval", PENDING_MSG, False)
    db.run("UPDATE users SET google_sub=? WHERE id=?", (sub, u["id"]))
    _start_session(req, u)
    return 302, {"Location": "/#/settings"}


# ---------------------------------------------------------------- Telegram alerts (owner)
@route("GET", "/api/telegram", auth="owner")
def telegram_get(req):
    from . import notify
    return notify.view()


@route("POST", "/api/telegram", auth="owner")
def telegram_save(req):
    from . import notify
    try:
        return notify.save(req.json)
    except ValueError as e:
        raise HttpError(400, str(e))


@route("POST", "/api/telegram/find", auth="owner")
def telegram_find(req):
    from . import notify
    try:
        return notify.find_chat()
    except ValueError as e:
        raise HttpError(400, str(e))


@route("POST", "/api/telegram/test", auth="owner")
def telegram_test(req):
    from . import notify
    if not notify.ready():
        raise HttpError(400, "Save the bot token and chat id first")
    if not notify.send_now(f"✅ DriveCast alerts work. You will hear here when a server is offline for an hour, a pool's proxy is down for real, or something serious needs you.\n— {notify._host()}"):
        raise HttpError(502, "Telegram did not deliver — check the chat id (press Start in your bot first)")
    return {"ok": True}


# ---------------------------------------------------------------- Accounts (every connected account) and deep stats
@route("GET", "/api/accounts")
def accounts_list(req):
    return insights.accounts(req.ws)


@route("GET", "/api/accounts/(youtube|facebook|instagram)/(\\d+)")
def account_detail(req, kind, aid):
    d = insights.account_deep(req.ws, kind, int(aid), req.user["timezone"])
    if not d:
        raise HttpError(404, "Account not found")
    from . import ai as _ai
    if d["channel_id"]:
        c = store.channel(d["channel_id"])
        d["ai_pick"] = _ai.last_pick(c["id"]) if c else None
        d["ai_ready"] = bool(c and _ai.key_for(c))
    return d


@route("POST", "/api/accounts/(youtube|facebook|instagram)/(\\d+)/analyse")
def account_analyse(req, kind, aid):
    from . import ai as _ai
    d = insights.account_deep(req.ws, kind, int(aid), req.user["timezone"])
    if not d or not d["channel_id"]:
        raise HttpError(404, "This account is not linked to a client yet")
    c = store.channel(d["channel_id"])
    facts = {"platform": kind, "account": d["head"]["name"], "summary": d["summary"], "origins": d["origins"],
             "best_posts": d["top"], "recent": [{k: p[k] for k in ("title", "views", "first_day", "status")} for p in d["posts"][:25]],
             "growth_last_14_days": d["growth"][-14:]}
    try:
        return {"text": _ai.analyse(c, facts)}
    except _ai.AiError as e:
        raise HttpError(400, str(e))


def _moved_check(u):
    w = store.workspace(u["workspace_id"])
    if w and w.get("moved_to"):
        raise HttpError(409, f"Your studio moved to a new server. Sign in at https://{w['moved_to']}")


# ---------------------------------------------------------------- whole-user moves between servers (hub board)
def _fleet_signed(req):
    from . import fleet
    if not fleet.verify(req.raw or b"", req.headers.get("X-DC-Fleet") or ""):
        raise HttpError(403, "Bad fleet signature")
    return fleet


@route("POST", "/fleet/ws-export", auth=None)
def fleet_ws_export(req):
    _fleet_signed(req)
    from . import usermove
    b = json.loads(req.raw or b"{}")
    if db.one("SELECT 1 FROM jobs j JOIN channels c ON c.id=j.channel_id JOIN users u ON u.workspace_id=c.workspace_id "
              "WHERE j.status='running' AND lower(u.email)=lower(?)", (b.get("email") or "",)):
        raise HttpError(409, "One of this user's posts is uploading — try again in a few minutes")
    try:
        return FileOut(usermove.seal_bundle(usermove.export_workspace(b.get("email") or "")), "ws.bin")
    except ValueError as e:
        raise HttpError(404, str(e))


@route("POST", "/fleet/ws-import", auth=None)
def fleet_ws_import(req):
    _fleet_signed(req)
    from . import usermove
    try:
        return usermove.import_workspace(usermove.open_bundle(req.raw))
    except ValueError as e:
        raise HttpError(409, str(e))


@route("POST", "/fleet/ws-action", auth=None)
def fleet_ws_action(req):
    _fleet_signed(req)
    from . import usermove
    b = json.loads(req.raw or b"{}")
    try:
        return usermove.local_action(b.get("email") or "", b.get("action"), b.get("to"))
    except ValueError as e:
        raise HttpError(400, str(e))


@route("POST", "/fleet/ws-snapshot", auth=None)
def fleet_ws_snapshot(req):
    fleet = _fleet_signed(req)
    if fleet.role() != "hub":
        raise HttpError(400, "This phone is not the hub")
    from . import usermove
    raw = req.raw or b""
    n = int.from_bytes(raw[:4], "big")
    head = json.loads(raw[4:4 + n])
    usermove.store_snapshot(head["host"], head["email"], raw[4 + n:])
    return {"ok": True}


@route("GET", "/api/fleet/users", auth="owner")
def fleet_users(req):
    from . import fleet, usermove
    if fleet.role() != "hub":
        return {"hub": False, "servers": [{"host": fleet.my_host() or (req.headers.get("Host") or "this server"), "online": True,
                                           "label": "This server", "users_list": usermove.summary(), "stats": fleet._stats(),
                                           "users": fleet.users_count(), "max_users": fleet.max_users(), "snapshots": {}}],
                "log": []}
    return {"hub": True, "servers": usermove.board(), "log": db.jload(db.meta_get("move_log"), []) or []}


@route("POST", "/api/fleet/users/action", auth="owner")
def fleet_users_action(req):
    from . import usermove
    b = req.json
    need(b, "host", "email", "action")
    if b["action"] not in ("suspend", "activate", "delete", "reset_link"):
        raise HttpError(400, "Unknown action")
    try:
        out = usermove.action(b["host"], b["email"], b["action"])
    except ValueError as e:
        raise HttpError(400, str(e))
    from . import fleet
    if fleet.role() == "hub":
        threading_hello()
    return out


def threading_hello():
    import threading
    from . import fleet
    threading.Thread(target=lambda: fleet.hello(), daemon=True).start()


@route("POST", "/api/fleet/users/move", auth="owner")
def fleet_users_move(req):
    from . import usermove
    b = req.json
    need(b, "email", "from", "to")
    try:
        return usermove.move(b["email"], b["from"], b["to"])
    except ValueError as e:
        raise HttpError(400, str(e))


# ---------------------------------------------------------------- sign-ups that wait for the owner's approval
PENDING_MSG = ("Your account is waiting for the studio owner's approval. You will be able to sign in as soon as it is approved — "
               "usually within a day.")


def signup_mode():
    """invite = only with a code · approval = without a code the account waits for the owner · open (fleet setting)."""
    return db.meta_get("signup_mode") or "approval"


def _mark_pending(u):
    db.run("UPDATE users SET approval='pending' WHERE id=?", (u["id"],))
    db.run("DELETE FROM sessions WHERE user_id=?", (u["id"],))
    o = _owner()
    msg = f"New sign-up waiting for your approval: {u['name']} ({u['email']}). Users → Waiting for approval."
    db.event("warn", "account", msg, o and o["workspace_id"], None, "SIGNUP_PENDING")
    try:
        from . import notify
        notify.alert(f"su:{u['id']}", msg)
    except Exception:
        pass


@route("GET", "/api/admin/pending", auth="owner")
def pending_list(req):
    return {"mode": signup_mode(), "users": [dict(id=u["id"], name=u["name"], email=u["email"], created_at=u["created_at"],
                                                  google=bool(u.get("google_sub")))
                                             for u in db.all("SELECT * FROM users WHERE approval='pending' ORDER BY created_at")]}


@route("POST", "/api/admin/pending", auth="owner")
def pending_mode(req):
    m = req.json.get("mode")
    if m not in ("invite", "approval"):
        raise HttpError(400, "Choose invite or approval")
    db.meta_set("signup_mode", m)
    return {"mode": m}


@route("POST", "/api/admin/users/(\\d+)/approve", auth="owner")
def approve_user(req, uid):
    from . import mail
    u = db.one("SELECT * FROM users WHERE id=?", (int(uid),))
    if not u or u.get("approval") != "pending":
        raise HttpError(404, "No waiting sign-up with this id")
    lim = req.json.get("limits") if isinstance(req.json.get("limits"), dict) else None
    db.run("UPDATE users SET approval=NULL, status='active' WHERE id=?", (u["id"],))
    if lim:
        db.run("UPDATE users SET limits_json=? WHERE id=?", (json.dumps({k: int(v) for k, v in lim.items() if str(v).isdigit()}), u["id"]))
    db.run("UPDATE events SET resolved=1 WHERE code='SIGNUP_PENDING' AND message LIKE ?", (f"%{u['email']}%",))
    try:
        from . import notify
        notify.alert(f"su:{u['id']}", f"Approved: {u['name']} ({u['email']})", solved=True)
    except Exception:
        pass
    emailed = False
    if mail.ready():
        try:
            link = public_base(req)
            mail.send(u["email"], "Your DriveCast account is ready",
                      f"Hi {u['name']},\n\nYour DriveCast Studio account was approved. Sign in here:\n{link}\n\nDriveCast Studio")
            emailed = True
        except Exception:
            emailed = False
    return {"ok": True, "emailed": emailed}


@route("POST", "/api/admin/users/(\\d+)/reject", auth="owner")
def reject_user(req, uid):
    u = db.one("SELECT * FROM users WHERE id=?", (int(uid),))
    if not u or u.get("approval") != "pending":
        raise HttpError(404, "No waiting sign-up with this id")
    delete_workspace_data(u)
    db.run("UPDATE events SET resolved=1 WHERE code='SIGNUP_PENDING' AND message LIKE ?", (f"%{u['email']}%",))
    return {"ok": True}
