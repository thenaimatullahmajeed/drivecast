"""Fleet: several DriveCast phones behind one domain.

One phone is the HUB — it serves drivecast.site (the landing page). Every phone,
the hub included, is a DEVICE with its own address (s1.drivecast.site, s2.…).
Devices tell the hub every few minutes who they are and how full they are, and
report new accounts and invite codes as one-way hashes (never emails or codes in
clear). The landing page then:
  • Get started → the device that issued the invite, or a device with open sign-ups
    and free room (fewest accounts first);
  • Sign in → the device that holds that email.
A new phone appears on the landing page by itself as soon as it joins the fleet.

Phones talk with a shared fleet key: every message is signed (HMAC-SHA256).
"""
import hashlib
import hmac
import json
import logging
import time

from . import VERSION, db, security
from .net import session_for

log = logging.getLogger("drivecast.fleet")
ONLINE_FOR = 15 * 60


# ------------------------------------------------------------------ settings
def role():
    return db.meta_get("fleet_role") or ""


def key():
    return db.meta_get("fleet_key") or ""


def my_url():
    from . import urls
    return urls.own_domain()


def my_host():
    return my_url().split("://")[-1].lower()


def hub_url():
    return my_url() if role() == "hub" else (db.meta_get("fleet_hub") or "").rstrip("/")


def make_hub():
    db.meta_set("fleet_role", "hub")
    if not key():
        db.meta_set("fleet_key", security.token(24))
    db.meta_set("fleet_hub", "")
    hello()
    return key()


def join(hub, fleet_key):
    hub = hub.strip().rstrip("/")
    if not hub.startswith("https://"):
        hub = "https://" + hub.split("://")[-1]
    db.meta_set("fleet_role", "node")
    db.meta_set("fleet_hub", hub)
    db.meta_set("fleet_key", fleet_key.strip())
    return hello()


def leave():
    for k in ("fleet_role", "fleet_hub", "fleet_key"):
        db.meta_set(k, "")


def open_signup():
    return db.meta_get("open_signup") == "1"


def max_users():
    try:
        return int(db.meta_get("max_users") or 25)
    except ValueError:
        return 25


def users_count():
    return db.one("SELECT COUNT(*) n FROM users WHERE status='active'")["n"]


# ------------------------------------------------------------------ crypto
def h(value, k=None):
    """One-way: the hub learns that an email/invite exists on a device, never the value itself."""
    return hashlib.sha256(((k or key()) + "|" + (value or "").strip().lower()).encode()).hexdigest()


def sign(body, k=None):
    return hmac.new((k or key()).encode(), body, hashlib.sha256).hexdigest()


def verify(body, sig):
    k = key()
    return bool(k and sig) and hmac.compare_digest(sign(body, k), sig)


# ------------------------------------------------------------------ device → hub
def _stats():
    now = time.time()
    day = now - 86400
    d = db.jload(db.meta_get("device_info"), {}) or {}
    v7 = db.one("SELECT COALESCE(SUM(views),0) v FROM uploads WHERE status='published' AND published_at>?", (now - 7 * 86400,))
    return {"channels": db.one("SELECT COUNT(*) n FROM channels")["n"],
            "live": db.one("SELECT COUNT(*) n FROM channels WHERE state='live'")["n"],
            "posts_24h": db.one("SELECT COUNT(*) n FROM uploads WHERE status='published' AND published_at>?", (day,))["n"],
            "failed_24h": db.one("SELECT COUNT(*) n FROM uploads WHERE status IN ('failed','uncertain') AND reserved_at>?", (day,))["n"],
            "groups": db.one("SELECT COUNT(*) n FROM ip_groups WHERE moved_to IS NULL")["n"],
            "views_7d": int(v7["v"] or 0), "battery": d.get("battery"), "charging": d.get("charging"), "temp": d.get("temp"),
            "replaced": bool(db.meta_get("superseded_by"))}


def _status():
    try:
        from . import usermove
        people = usermove.summary()
    except Exception:
        people = []
    return {"host": my_host(), "label": my_host().split(".")[0].upper(), "users": users_count(), "max_users": max_users(),
            "open_signup": open_signup(), "version": VERSION, "at": time.time(), "stats": _stats(), "people": people}


def _send(path, payload):
    if role() == "hub":
        return apply(path, payload)
    hub = hub_url()
    if not (role() == "node" and hub and key()):
        return False
    body = json.dumps(payload).encode()
    try:
        r = session_for(None).post(hub + path, data=body, timeout=(10, 20),
                                   headers={"Content-Type": "application/json", "X-DC-Fleet": sign(body)})
        if r.status_code >= 400:
            db.meta_set("fleet_error", f"Hub not reachable (HTTP {r.status_code}). Check the hub address in Server → Studios and that the hub phone is on.")
            return False
        db.meta_set("fleet_error", "")
        db.meta_set("fleet_seen", str(time.time()))
        try:
            out = r.json()
            return out if isinstance(out, dict) and out else True
        except ValueError:
            return True
    except Exception as e:
        db.meta_set("fleet_error", f"Hub not reachable: {e}"[:200])
        return False


def hello():
    if not role() or not my_host():
        return False
    res = _send("/fleet/hello", _status())
    if isinstance(res, dict):
        follow_update(res.get("update_now"))
        take_telegram(res.get("telegram"))
        if res.get("moved_away"):
            from . import usermove
            for m in res["moved_away"]:
                usermove.mark_moved(m["email"], m["to"])  # moved off this server while it was offline
    return res


def take_telegram(t):
    """Phone: use the hub's Telegram bot, so one bot hears from every server."""
    if not isinstance(t, dict) or not t.get("token") or not t.get("chat_id"):
        return
    from . import notify, security
    cur = notify.config()
    if cur.get("chat_id") == t["chat_id"] and cur.get("token_enc") and notify._token() == t["token"]:
        return
    db.meta_set("telegram_json", json.dumps({"token_enc": security.seal(t["token"]), "chat_id": t["chat_id"], "bot": t.get("bot")}))


def telegram_for_fleet():
    from . import notify
    if not notify.ready():
        return None
    c = notify.config()
    return {"token": notify._token(), "chat_id": c.get("chat_id"), "bot": c.get("bot")}


def push_update():
    """Hub: ask every phone of the studio to install the newest update (they do it at their next check-in)."""
    db.meta_set("fleet_update_now", str(time.time()))


def follow_update(ts):
    """Phone: the hub asked for an update — install it once, when nothing is uploading."""
    try:
        ts = float(ts or 0)
    except (TypeError, ValueError):
        return
    if not ts or ts <= float(db.meta_get("fleet_update_done") or 0):
        return
    if db.one("SELECT 1 FROM jobs WHERE status='running'"):
        return  # next check-in
    db.meta_set("fleet_update_done", str(ts))
    from . import hotupdate

    def go():
        try:
            hotupdate.apply()
        except Exception as e:
            db.meta_set("update_error", f"Update from the hub failed: {e}"[:300])
    import threading
    threading.Thread(target=go, daemon=True).start()


def report(kind, value):
    """kind: 'email' (a new account here) or 'invite' (an invite code issued here)."""
    if not role() or not my_host():
        return False
    return _send("/fleet/dir", {"host": my_host(), "kind": kind, "hash": h(value)})


def tick():
    """Every few minutes: tell the hub this device is alive (and, once, every account it holds)."""
    if not role():
        return
    if role() == "hub":
        follow_update(db.meta_get("fleet_update_now"))  # the hub follows its own request too (e.g. it was busy)
    ok = hello()
    if ok and db.meta_get("fleet_synced") != my_host() + "|" + (hub_url() or "self"):
        for u in db.all("SELECT email FROM users"):
            report("email", u["email"])
        for i in db.all("SELECT code FROM invites WHERE used_by IS NULL AND revoked=0 AND expires_at>?", (time.time(),)):
            report("invite", i["code"])
        db.meta_set("fleet_synced", my_host() + "|" + (hub_url() or "self"))


# ------------------------------------------------------------------ hub side
def apply(path, p):
    now = time.time()
    host = (p.get("host") or "").lower()
    if not host:
        return False
    if path == "/fleet/hello":
        db.run("""INSERT INTO fleet_devices(host,label,users,max_users,open_signup,version,last_seen,created_at,stats_json,users_json)
                  VALUES(?,?,?,?,?,?,?,?,?,?)
                  ON CONFLICT(host) DO UPDATE SET label=excluded.label, users=excluded.users, max_users=excluded.max_users,
                  open_signup=excluded.open_signup, version=excluded.version, last_seen=excluded.last_seen,
                  stats_json=excluded.stats_json, users_json=excluded.users_json, hidden=0""",
               (host, (p.get("label") or host.split(".")[0])[:20], int(p.get("users") or 0), int(p.get("max_users") or 0),
                1 if p.get("open_signup") else 0, str(p.get("version") or "")[:20], now, now, json.dumps(p.get("stats") or {})[:2000],
                json.dumps(p.get("people") or [])[:200000]))
        return True
    if path == "/fleet/dir" and p.get("kind") in ("email", "invite") and len(p.get("hash") or "") == 64:
        db.run("""INSERT INTO fleet_dir(kind,hash,host,created_at) VALUES(?,?,?,?)
                  ON CONFLICT(kind,hash) DO UPDATE SET host=excluded.host""", (p["kind"], p["hash"], host, now))
        return True
    return False


def devices():
    now = time.time()
    rows = db.all("SELECT * FROM fleet_devices WHERE hidden=0 ORDER BY host")
    for r in rows:
        r["online"] = (r["last_seen"] or 0) > now - ONLINE_FOR
        r["stats"] = db.jload(r.pop("stats_json", None), {}) or {}
    return rows


def totals(devs):
    t = {"phones": len(devs), "online": sum(1 for d in devs if d["online"]), "users": 0, "channels": 0, "live": 0,
         "posts_24h": 0, "failed_24h": 0, "views_7d": 0, "groups": 0}
    for d in devs:
        t["users"] += d["users"] or 0
        for k in ("channels", "live", "posts_24h", "failed_24h", "views_7d", "groups"):
            t[k] += int(d["stats"].get(k) or 0)
    return t


# ------------------------------------------------------------------ move an IP group (with all its clients) to another phone
# One IP group = one identity, so a group always moves whole: proxy, Instagram/Facebook connections,
# clients, sources and the full ledger (nothing posts twice after the move).
MOVE_TABLES = ("ip_groups", "channels", "sources", "ig_accounts", "fb_accounts", "fb_pages", "ig_dest", "media", "uploads")


def _cipher(k=None):
    from . import security
    return security.AESGCM(hashlib.sha256(((k or key()) + "|move").encode()).digest())


def _plain(row):
    from . import security
    out = {}
    for k, v in row.items():
        if k.endswith("_enc") and v:
            out[k[:-4] + "__plain"] = security.unseal(v)
        else:
            out[k] = v
    return out


def export_group(gid):
    g = db.one("SELECT * FROM ip_groups WHERE id=?", (gid,))
    if not g:
        raise ValueError("Pool not found")
    chs = db.all("SELECT * FROM channels WHERE group_id=?", (gid,))
    cids = [c["id"] for c in chs] or [-1]
    q = ",".join("?" * len(cids))
    fb = db.all("SELECT * FROM fb_accounts WHERE group_id=?", (gid,))
    fbids = [a["id"] for a in fb] or [-1]
    ws = db.one("SELECT google_client_id FROM workspaces WHERE id=?", (g["workspace_id"],))
    return {"v": 1, "from": my_host(), "google_client_id": ws and ws["google_client_id"],
            "ip_groups": [_plain(g)], "channels": [_plain(c) for c in chs],
            "sources": [_plain(r) for r in db.all(f"SELECT * FROM sources WHERE channel_id IN ({q})", cids)],
            "ig_accounts": [_plain(r) for r in db.all("SELECT * FROM ig_accounts WHERE group_id=?", (gid,))],
            "fb_accounts": [_plain(a) for a in fb],
            "fb_pages": [_plain(r) for r in db.all(f"SELECT * FROM fb_pages WHERE fb_account_id IN ({','.join('?' * len(fbids))})", fbids)],
            "ig_dest": [_plain(r) for r in db.all("SELECT * FROM ig_dest WHERE group_id=?", (gid,))],
            "media": [dict(r) for r in db.all(f"SELECT * FROM media WHERE channel_id IN ({q})", cids)],
            "uploads": [dict(r) for r in db.all(f"SELECT * FROM uploads WHERE channel_id IN ({q})", cids)]}


def _cols(c, table):
    return {r[1] for r in c.execute(f"PRAGMA table_info({table})").fetchall()}


def import_group(b):
    """On the receiving phone: everything lands in the owner's workspace, with new ids, secrets sealed again."""
    from . import security
    owner = db.one("SELECT workspace_id FROM users WHERE role='owner' ORDER BY id LIMIT 1")
    if not owner:
        raise ValueError("This phone has no owner yet")
    ws = owner["workspace_id"]
    now = time.time()
    ids = {t: {} for t in MOVE_TABLES}

    def put(c, table, row, **over):
        row = dict(row)
        old = row.pop("id", None)
        for k in [k for k in row if k.endswith("__plain")]:
            v = row.pop(k)
            row[k[:-7] + "_enc"] = security.seal(v) if v else None
        row.update(over)
        cols = _cols(c, table)
        row = {k: v for k, v in row.items() if k in cols}
        cur = c.execute(f"INSERT INTO {table}({','.join(row)}) VALUES({','.join('?' * len(row))})", list(row.values()))
        ids[table][old] = cur.lastrowid
        return cur.lastrowid

    with db.tx() as c:
        g = b["ip_groups"][0]
        if c.execute("SELECT 1 FROM ip_groups WHERE workspace_id=? AND host=? AND port=? AND username IS ?",
                     (ws, g.get("host"), g.get("port"), g.get("username"))).fetchone():
            raise ValueError("This proxy is already on this phone")
        num = (c.execute("SELECT COALESCE(MAX(number),0)+1 FROM ip_groups WHERE workspace_id=?", (ws,)).fetchone()[0])
        gid = put(c, "ip_groups", g, workspace_id=ws, number=num, moved_to=None, status="unknown")
        for ch in b["channels"]:
            if c.execute("SELECT 1 FROM channels WHERE workspace_id=? AND platform=? AND external_id=?",
                         (ws, ch["platform"], ch["external_id"])).fetchone():
                raise ValueError(f"{ch['title']} is already on this phone")
            put(c, "channels", ch, workspace_id=ws, group_id=gid, ig_dest_id=None, queue_count=None)
        for r in b["sources"]:
            put(c, "sources", r, channel_id=ids["channels"].get(r["channel_id"]))
        for r in b["ig_accounts"]:
            put(c, "ig_accounts", r, workspace_id=ws, group_id=gid)
        for r in b["fb_accounts"]:
            put(c, "fb_accounts", r, workspace_id=ws, group_id=gid)
        for r in b["fb_pages"]:
            put(c, "fb_pages", r, workspace_id=ws, fb_account_id=ids["fb_accounts"].get(r["fb_account_id"]),
                channel_id=ids["channels"].get(r.get("channel_id")))
        for r in b["ig_dest"]:
            new = put(c, "ig_dest", r, workspace_id=ws, group_id=gid, channel_id=ids["channels"].get(r.get("channel_id")))
            if r.get("channel_id") in ids["channels"]:
                c.execute("UPDATE channels SET ig_dest_id=? WHERE id=?", (new, ids["channels"][r["channel_id"]]))
        for r in b["media"]:
            put(c, "media", r, workspace_id=ws, channel_id=ids["channels"][r["channel_id"]],
                source_id=ids["sources"].get(r.get("source_id")), local_path=None, local_deleted_at=now,
                ref=r.get("ref"), state="in_drive" if r.get("state") == "downloading" else r.get("state"))
        for r in b["uploads"]:
            put(c, "uploads", r, workspace_id=ws, channel_id=ids["channels"][r["channel_id"]], media_id=ids["media"].get(r.get("media_id")))
    mine = db.one("SELECT google_client_id FROM workspaces WHERE id=?", (ws,))
    same_google = bool(mine and mine["google_client_id"] and mine["google_client_id"] == b.get("google_client_id"))
    db.event("info", "fleet", f"Group from {b.get('from')} moved in as Pool {num}: {len(b['channels'])} client(s)."
             + ("" if same_google else " This phone uses a different Google app — reconnect YouTube on these clients."), ws)
    return {"group_number": num, "clients": len(b["channels"]), "same_google_app": same_google}


def send_group(gid, host):
    """From this phone to another phone in the fleet. The bundle is encrypted with the fleet key."""
    import os
    host = host.strip().lower().split("://")[-1].strip("/")
    if not key():
        raise ValueError("Join the fleet first")
    if host == my_host():
        raise ValueError("Choose another phone")
    if db.one("SELECT 1 FROM jobs WHERE status='running'"):
        raise ValueError("A post is uploading right now. Try again in a few minutes.")
    bundle = json.dumps(export_group(gid)).encode()
    nonce = os.urandom(12)
    body = nonce + _cipher().encrypt(nonce, bundle, b"dc-move")
    r = session_for(None).post(f"https://{host}/fleet/import", data=body, timeout=(15, 120),
                               headers={"Content-Type": "application/octet-stream", "X-DC-Fleet": sign(body)})
    if r.status_code >= 400:
        try:
            msg = r.json().get("error")
        except ValueError:
            msg = r.text[:200]
        raise ValueError(f"{host} refused: {msg}")
    out = r.json()
    # this phone stops serving the group at once; nothing is deleted (the owner can remove it later)
    db.run("UPDATE ip_groups SET moved_to=? WHERE id=?", (host, gid))
    db.run("UPDATE channels SET state='paused', state_reason=? WHERE group_id=?", (f"Moved to {host}", gid))
    db.run("UPDATE sources SET state='paused' WHERE channel_id IN (SELECT id FROM channels WHERE group_id=?)", (gid,))
    return out


def receive_group(body):
    nonce, ct = body[:12], body[12:]
    data = json.loads(_cipher().decrypt(nonce, ct, b"dc-move"))
    return import_group(data)


def where(email):
    r = db.one("SELECT host FROM fleet_dir WHERE kind='email' AND hash=?", (h(email),))
    return r["host"] if r else None


def pick_for_signup(invite=None):
    """The device for a new account: the one that issued the invite, else the emptiest open one."""
    if invite:
        r = db.one("SELECT host FROM fleet_dir WHERE kind='invite' AND hash=?", (h(invite),))
        return (r["host"], True) if r else (None, True)
    cands = [d for d in devices() if d["online"] and d["open_signup"] and d["users"] < (d["max_users"] or 0)]
    if not cands:
        return None, False
    best = min(cands, key=lambda d: (d["users"] / max(1, d["max_users"]), d["users"]))
    return best["host"], False
