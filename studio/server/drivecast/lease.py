"""Spare-phone takeover without double posts.

Each install has its own instance id (a file in the data folder, never inside a
backup). A small lease file in the owner's Drive says which instance runs this
phone's address. Restoring a backup on a spare phone = taking over: the spare
writes its id into the lease. The old phone, if it ever comes back, sees that the
lease is no longer its own and stops posting at once (posting is held, the panel
and the phone screen say "this phone was replaced").
"""
import logging
import os
import secrets
import time

from . import config, db

log = logging.getLogger("drivecast.lease")
FOLDER = "_DriveCast backups"
_state = {"ok": True, "checked": 0, "superseded_by": None}


def instance_id():
    p = os.path.join(config.DATA_DIR, "instance.id")
    try:
        with open(p) as f:
            v = f.read().strip()
            if v:
                return v
    except OSError:
        pass
    v = secrets.token_hex(8)
    with open(p, "w") as f:
        f.write(v)
    return v


def _key():
    from . import urls
    host = urls.public().split("://")[-1].strip("/")
    return host or "this-phone"


def _ctx():
    from . import drive, store
    from .net import session_for
    owner = db.one("SELECT workspace_id FROM users WHERE role='owner' ORDER BY id LIMIT 1")
    ws = store.workspace(owner["workspace_id"]) if owner else None
    if not ws or not ws.get("drive_token_enc") or not ws.get("drive_root_id"):
        return None
    s, box = session_for(None), store.drive_box(ws)
    return drive, s, box, drive.child_folder(s, box, ws["drive_root_id"], FOLDER)


def superseded():
    return db.meta_get("superseded_by") or None


def claim():
    """This install now runs the address (fresh install, or takeover after a restore)."""
    ctx = _ctx()
    if not ctx:
        return False
    drive, s, box, folder = ctx
    me = instance_id()
    props = {"dc_lease": _key(), "dc_instance": me, "dc_at": int(time.time())}
    f = drive.find_by_prop(s, box, folder, "dc_lease", _key())
    if f:
        drive.set_props(s, box, f["id"], props)
    else:
        drive.create_meta(s, box, folder, f"lease-{_key()}.json", props)
    db.meta_set("superseded_by", "")
    db.meta_set("lease_claimed", str(time.time()))
    return True


def check():
    ctx = _ctx()
    if not ctx:
        return True
    drive, s, box, folder = ctx
    f = drive.find_by_prop(s, box, folder, "dc_lease", _key())
    me = instance_id()
    if not f:
        return claim()
    other = (f.get("appProperties") or {}).get("dc_instance")
    if other and other != me:
        if not superseded():
            db.meta_set("superseded_by", other)
            db.event("error", "takeover", "This phone was replaced by another phone (a backup was restored there). "
                     "Posting is stopped here so nothing is posted twice. Erase this phone, or restore it to take over again.",
                     None, None, "PHONE_REPLACED")
        return False
    if superseded():
        db.meta_set("superseded_by", "")
    return True


def on_start():
    flag = os.path.join(config.DATA_DIR, "takeover.flag")
    if os.path.exists(flag):
        try:
            if claim():
                os.remove(flag)
                db.event("info", "takeover", "This phone took over its address from the backup. The old phone stops posting if it comes back.")
        except Exception:
            log.exception("takeover claim failed; will retry")


def tick():
    on_start()
    try:
        check()
    except Exception:
        log.exception("lease check failed")
