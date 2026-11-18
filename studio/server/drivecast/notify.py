"""Telegram alerts for the owner: a server offline for an hour, a pool's proxy down for real, serious problems.

Set once in Settings (bot token + chat id). The hub passes the same setting to every phone of the studio,
so one bot hears from all servers. Messages are de-duplicated: the same problem is sent once, then again
only when it is solved ("back online") or after 12 hours if it is still open.
"""
import json
import logging
import threading
import time

from . import db, security

log = logging.getLogger("drivecast.notify")
API = "https://api.telegram.org/bot{token}/{method}"
REPEAT_AFTER = 12 * 3600
SERIOUS = {"YT_AUTH_REVOKED", "YT_TOKEN_EXPIRED", "FB_TOKEN_EXPIRED", "IGD_TOKEN_EXPIRED", "IG_COOKIES_EXPIRED",
           "PHONE_REPLACED", "PHONE_OFFLINE", "BATTERY_LOW", "PHONE_HOT", "BACKUP_TEST_FAILED",
           "DRIVE_AUTH_REVOKED", "DRIVE_FULL", "QUOTA_NEAR", "AI_KEY_BAD", "FB_GRAPH_OLD", "YTDLP_OLD", "YT_REMOVED"}


def config():
    c = db.jload(db.meta_get("telegram_json"), {}) or {}
    return c


def ready():
    c = config()
    return bool(c.get("token_enc") and c.get("chat_id"))


def view():
    c = config()
    return {"ready": ready(), "chat_id": c.get("chat_id") or "", "bot": c.get("bot") or "", "has_token": bool(c.get("token_enc"))}


def _token():
    c = config()
    return security.unseal(c["token_enc"]) if c.get("token_enc") else None


def _post(token, method, payload):
    from .net import session_for
    r = session_for(None).post(API.format(token=token, method=method), json=payload, timeout=20)
    return r.status_code, r.json() if r.headers.get("content-type", "").startswith("application/json") else {}


def save(b):
    c = config()
    if b.get("clear"):
        db.meta_set("telegram_json", "{}")
        return view()
    if b.get("token"):
        tok = b["token"].strip()
        code, out = _post(tok, "getMe", {})
        if code != 200 or not out.get("ok"):
            raise ValueError("Telegram did not accept this bot token")
        c["token_enc"] = security.seal(tok)
        c["bot"] = out["result"].get("username")
    if "chat_id" in b:
        c["chat_id"] = str(b["chat_id"] or "").strip()[:40]
    db.meta_set("telegram_json", json.dumps(c))
    return view()


def find_chat():
    """After the owner sends any message to the bot: read the chat id from the bot's updates."""
    tok = _token()
    if not tok:
        raise ValueError("Save the bot token first")
    code, out = _post(tok, "getUpdates", {"limit": 20})
    if code != 200 or not out.get("ok"):
        raise ValueError("Telegram did not answer")
    chats = [u.get("message", {}).get("chat") for u in out.get("result", []) if u.get("message")]
    chats = [ch for ch in chats if ch]
    if not chats:
        raise ValueError("No message yet — open your bot in Telegram, press Start, send “hi”, then try again")
    ch = chats[-1]
    c = config()
    c["chat_id"] = str(ch["id"])
    db.meta_set("telegram_json", json.dumps(c))
    return {"chat_id": c["chat_id"], "name": ch.get("first_name") or ch.get("title") or ""}


def send_now(text):
    tok, c = _token(), config()
    if not tok or not c.get("chat_id"):
        return False
    code, out = _post(tok, "sendMessage", {"chat_id": c["chat_id"], "text": text[:3900], "disable_web_page_preview": True})
    return code == 200 and out.get("ok")


def _host():
    from . import urls
    try:
        return urls.own_domain() or urls.tailscale() or "DriveCast"
    except Exception:
        return "DriveCast"


def alert(key, text, solved=False):
    """Send once per open problem (key). solved=True sends the 'fixed' message once and closes it."""
    if not ready():
        return False
    sent = db.jload(db.meta_get("telegram_sent"), {}) or {}
    now = time.time()
    if solved:
        if key not in sent:
            return False
        sent.pop(key, None)
        db.meta_set("telegram_sent", json.dumps(sent))
        msg = f"✅ {text}\n— {_host()}"
    else:
        if key in sent and now - sent[key] < REPEAT_AFTER:
            return False
        sent[key] = now
        db.meta_set("telegram_sent", json.dumps(sent))
        msg = f"🔴 {text}\n— {_host()}"

    def go():
        try:
            send_now(msg)
        except Exception as e:
            log.warning("telegram: %s", e)
    threading.Thread(target=go, daemon=True).start()
    return True


def from_event(level, message, code, workspace_id=None):
    """Called by db.event: serious, coded errors go to Telegram (owner's own problems and his clients')."""
    if level != "error" or code not in SERIOUS:
        return
    alert(f"ev:{code}:{workspace_id or 0}", message)


def resolved_codes(open_keys):
    """Close Telegram problems that are no longer open (e.g. alerts that cleared)."""
    sent = db.jload(db.meta_get("telegram_sent"), {}) or {}
    for k in list(sent):
        if k.startswith("al:") and k not in open_keys:
            alert(k, "Solved: " + k[3:].replace("off:", "server back online: "), solved=True)
