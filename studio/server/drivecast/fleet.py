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
    return (db.meta_get("public_url_override") or "").rstrip("/")


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
def _status():
    return {"host": my_host(), "label": my_host().split(".")[0].upper(), "users": users_count(), "max_users": max_users(),
            "open_signup": open_signup(), "version": VERSION, "at": time.time()}


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
            db.meta_set("fleet_error", f"Hub answered {r.status_code}: {r.text[:120]}")
            return False
        db.meta_set("fleet_error", "")
        db.meta_set("fleet_seen", str(time.time()))
        return True
    except Exception as e:
        db.meta_set("fleet_error", f"Hub not reachable: {e}"[:200])
        return False


def hello():
    if not role() or not my_host():
        return False
    return _send("/fleet/hello", _status())


def report(kind, value):
    """kind: 'email' (a new account here) or 'invite' (an invite code issued here)."""
    if not role() or not my_host():
        return False
    return _send("/fleet/dir", {"host": my_host(), "kind": kind, "hash": h(value)})


def tick():
    """Every few minutes: tell the hub this device is alive (and, once, every account it holds)."""
    if not role():
        return
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
        db.run("""INSERT INTO fleet_devices(host,label,users,max_users,open_signup,version,last_seen,created_at)
                  VALUES(?,?,?,?,?,?,?,?)
                  ON CONFLICT(host) DO UPDATE SET label=excluded.label, users=excluded.users, max_users=excluded.max_users,
                  open_signup=excluded.open_signup, version=excluded.version, last_seen=excluded.last_seen""",
               (host, (p.get("label") or host.split(".")[0])[:20], int(p.get("users") or 0), int(p.get("max_users") or 0),
                1 if p.get("open_signup") else 0, str(p.get("version") or "")[:20], now, now))
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
    return rows


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
