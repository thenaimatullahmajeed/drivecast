"""Small data helpers shared by the API and the background services."""
import json
import time

from . import db, security
from .google import TokenBox


def workspace(ws_id):
    return db.one("SELECT * FROM workspaces WHERE id=?", (ws_id,))


def google_app(ws):
    cid = ws.get("google_client_id")
    sec = security.unseal(ws.get("google_client_secret_enc")) if ws.get("google_client_secret_enc") else None
    return cid, sec


def drive_app(ws):
    """The Google app that issues Drive access: the separate Drive app when set (owner only,
    so the client-facing app needs only YouTube permissions), else the main Google app."""
    if ws.get("drive_client_id") and ws.get("drive_client_secret_enc"):
        return ws["drive_client_id"], security.unseal(ws["drive_client_secret_enc"])
    return google_app(ws)


def drive_box(ws):
    # refresh with the app that issued the token (switching apps never breaks the current Drive link)
    if ws.get("drive_token_client") and ws.get("drive_token_client") == ws.get("drive_client_id") and ws.get("drive_client_secret_enc"):
        cid, sec = ws["drive_client_id"], security.unseal(ws["drive_client_secret_enc"])
    else:
        cid, sec = google_app(ws)

    def save(sealed):
        db.run("UPDATE workspaces SET drive_token_enc=? WHERE id=?", (sealed, ws["id"]))
    return TokenBox(ws.get("drive_token_enc"), cid, sec, save)


def group_google_app(g):
    if g and g.get("google_client_id") and g.get("google_client_secret_enc"):
        return g["google_client_id"], security.unseal(g["google_client_secret_enc"])
    return None, None


def google_app_for(ws, g):
    """The Google app for a client: its Pool's own app (the client's Cloud project) when set, else yours."""
    cid, sec = group_google_app(g)
    return (cid, sec) if cid else google_app(ws)


def meta_app_for(ws, g):
    if g and g.get("fb_app_id") and g.get("fb_app_secret_enc"):
        return g["fb_app_id"], security.unseal(g["fb_app_secret_enc"])
    return ws.get("fb_app_id"), (security.unseal(ws["fb_app_secret_enc"]) if ws.get("fb_app_secret_enc") else None)


def ig_app_for(ws, g):
    if g and g.get("ig_app_id") and g.get("ig_app_secret_enc"):
        return g["ig_app_id"], security.unseal(g["ig_app_secret_enc"])
    return ws.get("ig_app_id"), (security.unseal(ws["ig_app_secret_enc"]) if ws.get("ig_app_secret_enc") else None)


def channel_box(ch):
    ws = workspace(ch["workspace_id"])
    g = group(ch.get("group_id"))
    gcid, gsec = group_google_app(g)
    # refresh with the app that issued this channel's token
    if ch.get("token_client") and gcid and ch["token_client"] == gcid:
        cid, sec = gcid, gsec
    elif ch.get("token_client") and ch["token_client"] == ws.get("google_client_id"):
        cid, sec = google_app(ws)
    else:
        cid, sec = google_app_for(ws, g)

    def save(sealed):
        db.run("UPDATE channels SET token_enc=? WHERE id=?", (sealed, ch["id"]))
    return TokenBox(ch.get("token_enc"), cid, sec, save)


def group(gid):
    return db.one("SELECT * FROM ip_groups WHERE id=?", (gid,)) if gid else None


def channel(cid, ws_id=None):
    if ws_id is None:
        return db.one("SELECT * FROM channels WHERE id=?", (cid,))
    return db.one("SELECT * FROM channels WHERE id=? AND workspace_id=?", (cid, ws_id))


def limits_for_workspace(ws_id):
    u = db.one("SELECT limits_json, role FROM users WHERE workspace_id=? ORDER BY id LIMIT 1", (ws_id,))
    if not u or u["role"] == "owner":
        return {"max_channels": None, "max_groups": None, "daily_cap": 95}
    lim = db.jload(u["limits_json"], {})
    return {"max_channels": lim.get("max_channels"), "max_groups": lim.get("max_groups"),
            "daily_cap": min(95, int(lim.get("daily_cap") or 95))}


def uploads_today(ws_id, now=None):
    """Uploads started in the current Pacific day (YouTube quota day)."""
    from .timeplan import next_pacific_midnight
    now = now or time.time()
    start = next_pacific_midnight(now) - 86400
    r = db.one("""SELECT COUNT(*) n FROM uploads WHERE workspace_id=? AND reserved_at>=? AND status!='failed'
                  AND destination LIKE 'youtube:%'""",
               (ws_id, start))
    return r["n"]


def set_channel_state(cid, state, reason=None):
    db.run("UPDATE channels SET state=?, state_reason=? WHERE id=?", (state, reason, cid))


def public(row, drop=("token_enc", "password_enc", "google_client_secret_enc", "drive_token_enc", "pw_hash",
                      "drive_client_secret_enc", "fb_app_secret_enc", "ig_app_secret_enc", "cookies_enc",
                      "google_client_secret_enc", "queue_json", "groq_key_enc")):
    if row is None:
        return None
    return {k: v for k, v in row.items() if k not in drop}


def dumps(o):
    return json.dumps(o, separators=(",", ":"))
