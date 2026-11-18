"""Move a whole user (workspace) from one server of the studio to another — the drag and drop on the hub.

What moves: the account (password, Google sign-in), every pool with its proxy and the client's own apps,
YouTube / Facebook / Instagram authorizations, Instagram cookies and the vault, sources, routes, plans,
Drive link, AI settings, the posting history and stats. Tokens move as they are, and each pool keeps its
own proxy, so the platforms see the same IP — nothing needs to be authorized again.

The source server stops posting for that user the moment the move succeeds, and also when it comes back
online later (the hub tells it). Each server also sends the hub an encrypted snapshot of every workspace
a few times a day, so a user can be moved away from a server that does not respond.
"""
import json
import os
import time

from . import db, fleet, security

SNAPSHOT_EVERY = 6 * 3600
TABLES = ("ip_groups", "channels", "sources", "ig_accounts", "fb_accounts", "fb_pages", "ig_dest", "media", "uploads",
          "ig_vault", "channel_points", "stat_points")


def _plain(row):
    return fleet._plain(dict(row))


def workspace_of(email):
    u = db.one("SELECT * FROM users WHERE lower(email)=lower(?)", (email,))
    return (u, db.one("SELECT * FROM workspaces WHERE id=?", (u["workspace_id"],))) if u else (None, None)


def export_workspace(email):
    u, ws = workspace_of(email)
    if not u:
        raise ValueError("User not found on this server")
    if u["role"] == "owner":
        raise ValueError("The owner account stays on its server")
    wid = ws["id"]
    chs = db.all("SELECT * FROM channels WHERE workspace_id=?", (wid,))
    cids = [c["id"] for c in chs] or [-1]
    q = ",".join("?" * len(cids))
    ups = db.all(f"SELECT * FROM uploads WHERE channel_id IN ({q})", cids)
    uids = [x["id"] for x in ups] or [-1]
    return {"v": 1, "from": fleet.my_host(), "at": time.time(), "email": u["email"],
            "workspace": _plain(ws), "users": [_plain(x) for x in db.all("SELECT * FROM users WHERE workspace_id=?", (wid,))],
            "ip_groups": [_plain(r) for r in db.all("SELECT * FROM ip_groups WHERE workspace_id=?", (wid,))],
            "channels": [_plain(c) for c in chs],
            "sources": [_plain(r) for r in db.all(f"SELECT * FROM sources WHERE channel_id IN ({q})", cids)],
            "ig_accounts": [_plain(r) for r in db.all("SELECT * FROM ig_accounts WHERE workspace_id=?", (wid,))],
            "fb_accounts": [_plain(r) for r in db.all("SELECT * FROM fb_accounts WHERE workspace_id=?", (wid,))],
            "fb_pages": [_plain(r) for r in db.all("SELECT * FROM fb_pages WHERE workspace_id=?", (wid,))],
            "ig_dest": [_plain(r) for r in db.all("SELECT * FROM ig_dest WHERE workspace_id=?", (wid,))],
            "ig_vault": [_plain(r) for r in db.all("SELECT * FROM ig_vault WHERE workspace_id=?", (wid,))],
            "media": [dict(r) for r in db.all(f"SELECT * FROM media WHERE channel_id IN ({q})", cids)],
            "uploads": [dict(r) for r in ups],
            "channel_points": [dict(r) for r in db.all(f"SELECT * FROM channel_points WHERE channel_id IN ({q})", cids)],
            "stat_points": [dict(r) for r in db.all(
                f"SELECT * FROM stat_points WHERE upload_id IN ({','.join('?' * len(uids))})", uids)] if len(uids) < 900 else []}


def import_workspace(b):
    """On the target server: a new workspace with new ids; every secret sealed again with this server's key."""
    if db.one("SELECT 1 FROM users WHERE lower(email)=lower(?)", (b["email"],)):
        raise ValueError(f"{b['email']} already has an account on this server")
    ids = {t: {} for t in TABLES + ("workspaces", "users")}

    def put(c, table, row, keep_id=False, **over):
        row = dict(row)
        old = row.pop("id", None)
        for k in [k for k in row if k.endswith("__plain")]:
            v = row.pop(k)
            row[k[:-7] + "_enc"] = security.seal(v) if v else None
        row.update(over)
        cols = fleet._cols(c, table)
        row = {k: v for k, v in row.items() if k in cols}
        cur = c.execute(f"INSERT INTO {table}({','.join(row)}) VALUES({','.join('?' * len(row))})", list(row.values()))
        ids[table][old] = cur.lastrowid
        return cur.lastrowid

    m = lambda t, v: ids[t].get(v) if v is not None else None
    with db.tx() as c:
        wid = put(c, "workspaces", b["workspace"], moved_to=None, paused_until=None)
        for u in b["users"]:
            put(c, "users", u, workspace_id=wid, status="active")
        for g in b["ip_groups"]:
            put(c, "ip_groups", g, workspace_id=wid, moved_to=None, status="unknown", fail_count=0, fail_since=None)
        for ch in b["channels"]:
            put(c, "channels", ch, workspace_id=wid, group_id=m("ip_groups", ch.get("group_id")), ig_dest_id=None,
                queue_count=None, queue_json=None)
        for r in b["sources"]:
            put(c, "sources", r, channel_id=m("channels", r["channel_id"]))
        for r in b["ig_accounts"]:
            put(c, "ig_accounts", r, workspace_id=wid, group_id=m("ip_groups", r.get("group_id")))
        for r in b["fb_accounts"]:
            put(c, "fb_accounts", r, workspace_id=wid, group_id=m("ip_groups", r.get("group_id")))
        for r in b["fb_pages"]:
            put(c, "fb_pages", r, workspace_id=wid, fb_account_id=m("fb_accounts", r.get("fb_account_id")),
                channel_id=m("channels", r.get("channel_id")))
        for r in b["ig_dest"]:
            new = put(c, "ig_dest", r, workspace_id=wid, group_id=m("ip_groups", r.get("group_id")),
                      channel_id=m("channels", r.get("channel_id")))
            if r.get("channel_id") in ids["channels"]:
                c.execute("UPDATE channels SET ig_dest_id=? WHERE id=?", (new, ids["channels"][r["channel_id"]]))
        for r in b.get("ig_vault", []):
            put(c, "ig_vault", r, workspace_id=wid, group_id=m("ip_groups", r.get("group_id")))
        for r in b["media"]:
            put(c, "media", r, workspace_id=wid, channel_id=m("channels", r["channel_id"]),
                source_id=m("sources", r.get("source_id")), local_path=None, local_deleted_at=time.time(),
                state="in_drive" if r.get("state") == "downloading" else r.get("state"))
        for r in b["uploads"]:
            put(c, "uploads", r, workspace_id=wid, channel_id=m("channels", r["channel_id"]), media_id=m("media", r.get("media_id")))
        for r in b.get("channel_points", []):
            if m("channels", r["channel_id"]):
                c.execute("INSERT OR REPLACE INTO channel_points(channel_id,day,views,subscribers) VALUES(?,?,?,?)",
                          (ids["channels"][r["channel_id"]], r["day"], r["views"], r["subscribers"]))
        for r in b.get("stat_points", []):
            if m("uploads", r["upload_id"]):
                c.execute("INSERT OR REPLACE INTO stat_points(upload_id,day,views) VALUES(?,?,?)",
                          (ids["uploads"][r["upload_id"]], r["day"], r["views"]))
    db.event("info", "fleet", f"{b['email']} moved in from {b.get('from')}: {len(b['channels'])} client(s), "
             f"{len(b['ip_groups'])} pool(s) — posting continues here.", wid)
    fleet.report("email", b["email"])
    return {"ok": True, "clients": len(b["channels"]), "pools": len(b["ip_groups"]), "snapshot_at": b.get("at")}


def mark_moved(email, host):
    """On the source server: never post for this user again; their panel points them to the new server."""
    u, ws = workspace_of(email)
    if not u:
        return False
    wid = ws["id"]
    db.run("UPDATE workspaces SET moved_to=? WHERE id=?", (host, wid))
    db.run("UPDATE ip_groups SET moved_to=? WHERE workspace_id=?", (host, wid))
    db.run("UPDATE channels SET state='paused', state_reason=? WHERE workspace_id=?", (f"Moved to {host}", wid))
    db.run("UPDATE sources SET state='paused' WHERE channel_id IN (SELECT id FROM channels WHERE workspace_id=?)", (wid,))
    db.run("UPDATE slots SET status='skipped' WHERE status IN ('planned','queued') AND channel_id IN "
           "(SELECT id FROM channels WHERE workspace_id=?)", (wid,))
    db.run("DELETE FROM sessions WHERE user_id IN (SELECT id FROM users WHERE workspace_id=?)", (wid,))
    return True


# ---------------------------------------------------------------- crypto + transport (fleet key)
def seal_bundle(obj):
    data = json.dumps(obj).encode()
    nonce = os.urandom(12)
    return nonce + fleet._cipher().encrypt(nonce, data, b"dc-ws")


def open_bundle(body):
    return json.loads(fleet._cipher().decrypt(body[:12], body[12:], b"dc-ws"))


def _call(host, path, body, binary=False, timeout=(15, 180)):
    from .net import session_for
    if isinstance(body, (dict, list)):
        body = json.dumps(body).encode()
    r = session_for(None).post(f"https://{host}{path}", data=body, timeout=timeout,
                               headers={"Content-Type": "application/octet-stream", "X-DC-Fleet": fleet.sign(body)})
    if r.status_code >= 400:
        try:
            msg = r.json().get("error")
        except ValueError:
            msg = r.text[:200]
        raise ValueError(f"{host}: {msg}")
    return r.content if binary else r.json()


def local_action(email, action, to=None):
    from . import api
    u, ws = workspace_of(email)
    if not u:
        raise ValueError("User not found on this server")
    if u["role"] == "owner" and action in ("suspend", "delete", "moved"):
        raise ValueError("Not for the owner account")
    if action == "suspend":
        db.run("UPDATE users SET status='suspended' WHERE id=?", (u["id"],))
        db.run("DELETE FROM sessions WHERE user_id=?", (u["id"],))
        db.run("UPDATE channels SET state='paused', state_reason='Account suspended' WHERE workspace_id=? AND state='live'", (ws["id"],))
        return {"ok": True}
    if action == "activate":
        db.run("UPDATE users SET status='active' WHERE id=?", (u["id"],))
        return {"ok": True}
    if action == "delete":
        api.delete_workspace_data(u)
        return {"ok": True}
    if action == "reset_link":
        tok = api._make_reset(u, "owner", minutes=24 * 60)
        from . import urls
        return {"url": urls.public(None) + "/#/reset/" + tok, "email": u["email"]}
    if action == "moved":
        return {"ok": mark_moved(email, to)}
    raise ValueError("Unknown action")


# ---------------------------------------------------------------- hub: the board and the moves
def summary():
    """This server's users, for the hub board (no secrets)."""
    now = time.time()
    out = []
    for u in db.all("SELECT u.*, w.moved_to FROM users u JOIN workspaces w ON w.id=u.workspace_id ORDER BY u.created_at"):
        if u["moved_to"]:
            continue
        wid = u["workspace_id"]
        r = db.one("""SELECT (SELECT COUNT(*) FROM channels WHERE workspace_id=?) clients,
                             (SELECT COUNT(*) FROM channels WHERE workspace_id=? AND state='live') live,
                             (SELECT COUNT(*) FROM ip_groups WHERE workspace_id=? AND moved_to IS NULL) pools,
                             (SELECT COUNT(*) FROM ip_groups WHERE workspace_id=? AND status='down') pools_down,
                             (SELECT COUNT(*) FROM uploads WHERE workspace_id=? AND status='published' AND published_at>?) posts_7d,
                             (SELECT COUNT(*) FROM uploads WHERE workspace_id=? AND status IN ('failed','uncertain') AND reserved_at>?) failed_7d,
                             (SELECT COALESCE(SUM(views),0) FROM uploads WHERE workspace_id=? AND status='published' AND published_at>?) views_7d""",
                   (wid, wid, wid, wid, wid, now - 7 * 86400, wid, now - 7 * 86400, wid, now - 7 * 86400))
        out.append({"name": u["name"], "email": u["email"], "role": u["role"], "status": u["status"],
                    "created_at": u["created_at"], "last_login": u["last_login"], **dict(r)})
    return out


def board():
    devs = fleet.devices()
    me = fleet.my_host()
    for d in devs:
        d["users_list"] = db.jload(d.pop("users_json", None), []) or []
        if d["host"] == me:
            d["users_list"] = summary()
        snaps = _snap_dir(d["host"])
        d["snapshots"] = {}
        if os.path.isdir(snaps):
            for f in os.listdir(snaps):
                d["snapshots"][f[:-4]] = os.path.getmtime(os.path.join(snaps, f))
    return devs


def _snap_dir(host):
    from . import config
    return os.path.join(config.DATA_DIR, "snapshots", host.replace("/", "_"))


def _snap_name(email):
    return fleet.h(email)[:32] + ".bin"


def store_snapshot(host, email, blob):
    d = _snap_dir(host)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, _snap_name(email)), "wb") as f:
        f.write(blob)


def push_snapshots(force=False):
    """Every server: send the hub an encrypted copy of each workspace (for moves off a server that died)."""
    if fleet.role() not in ("node", "hub") or not fleet.key():
        return 0
    last = float(db.meta_get("snap_at") or 0)
    if not force and time.time() - last < SNAPSHOT_EVERY:
        return 0
    n = 0
    for u in db.all("SELECT u.email FROM users u JOIN workspaces w ON w.id=u.workspace_id WHERE u.role!='owner' AND w.moved_to IS NULL"):
        try:
            blob = seal_bundle(export_workspace(u["email"]))
            if fleet.role() == "hub":
                store_snapshot(fleet.my_host(), u["email"], blob)
            else:
                hub = fleet.hub_url().split("://")[-1].strip("/")
                head = json.dumps({"host": fleet.my_host(), "email": u["email"]}).encode()
                _call(hub, "/fleet/ws-snapshot", len(head).to_bytes(4, "big") + head + blob)
            n += 1
        except Exception:
            continue
    db.meta_set("snap_at", str(time.time()))
    return n


def action(host, email, act, to=None):
    if host == fleet.my_host():
        return local_action(email, act, to)
    return _call(host, "/fleet/ws-action", {"email": email, "action": act, "to": to})


def move(email, src, dst):
    """Drag and drop: copy the user to dst (live from src, or from the last snapshot if src does not answer),
    then make src stop for good and point everyone to dst."""
    if src == dst:
        raise ValueError("Pick another server")
    devs = {d["host"]: d for d in fleet.devices()}
    if dst not in devs:
        raise ValueError("Unknown server")
    t = devs[dst]
    if not t["online"]:
        raise ValueError(f"{dst} is offline — pick a healthy server")
    st = t.get("stats") or {}
    if (st.get("temp") or 0) >= 43:
        raise ValueError(f"{dst} is too hot right now")
    used_live = True
    try:
        if src == fleet.my_host():
            if db.one("SELECT 1 FROM jobs j JOIN channels c ON c.id=j.channel_id JOIN users u ON u.workspace_id=c.workspace_id "
                      "WHERE j.status='running' AND lower(u.email)=lower(?)", (email,)):
                raise ValueError("One of this user's posts is uploading — try again in a few minutes")
            blob = seal_bundle(export_workspace(email))
        else:
            if not devs.get(src, {}).get("online"):
                raise ConnectionError("source offline")
            blob = _call(src, "/fleet/ws-export", {"email": email}, binary=True)
    except (ConnectionError, OSError) as e:
        used_live = False
        path = os.path.join(_snap_dir(src), _snap_name(email))
        if not os.path.isfile(path):
            raise ValueError(f"{src} does not answer and there is no saved copy of this user yet") from e
        blob = open(path, "rb").read()
    except ValueError as e:
        if "does not answer" in str(e) or "Max retries" in str(e) or "timed out" in str(e).lower():
            used_live = False
            path = os.path.join(_snap_dir(src), _snap_name(email))
            if not os.path.isfile(path):
                raise ValueError(f"{src} does not answer and there is no saved copy of this user yet") from e
            blob = open(path, "rb").read()
        else:
            raise
    snap_at = open_bundle(blob).get("at")
    if dst == fleet.my_host():
        out = import_workspace(open_bundle(blob))
    else:
        out = _call(dst, "/fleet/ws-import", blob)
    # the source stops for good (now, or the next time it reports to the hub)
    pend = db.jload(db.meta_get("moved_away"), {}) or {}
    pend[fleet.h(email)] = {"email": email, "from": src, "to": dst, "at": time.time()}
    db.meta_set("moved_away", json.dumps(pend))
    try:
        action(src, email, "moved", dst)
        pend.pop(fleet.h(email), None)
        db.meta_set("moved_away", json.dumps(pend))
    except Exception:
        pass  # told at its next check-in
    db.run("""INSERT INTO fleet_dir(kind,hash,host,created_at) VALUES('email',?,?,?)
              ON CONFLICT(kind,hash) DO UPDATE SET host=excluded.host""", (fleet.h(email), dst, time.time()))
    log = db.jload(db.meta_get("move_log"), []) or []
    log.insert(0, {"email": email, "from": src, "to": dst, "at": time.time(), "live": used_live, "snapshot_at": snap_at})
    db.meta_set("move_log", json.dumps(log[:100]))
    return {**out, "live": used_live, "snapshot_at": snap_at}


def pending_for(host):
    """Hub → a server checking in: users that were moved away from it while it was offline."""
    pend = db.jload(db.meta_get("moved_away"), {}) or {}
    return [{"email": v["email"], "to": v["to"]} for v in pend.values() if v["from"] == host]


def clear_pending(host):
    pend = db.jload(db.meta_get("moved_away"), {}) or {}
    db.meta_set("moved_away", json.dumps({k: v for k, v in pend.items() if v["from"] != host}))


def service():
    try:
        push_snapshots()
    except Exception:
        pass
