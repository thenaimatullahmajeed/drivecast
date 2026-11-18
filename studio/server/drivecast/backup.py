"""Backup and restore of the whole server: every account, channel, proxy, Instagram
cookie, Google sign-in and the full post ledger.

File format (.dcbackup):  b"DCBK1" | salt(16) | nonce(12) | AES-GCM( zlib(JSON) )
The key comes from the backup password (scrypt), so the file is useless without it.
Google/Instagram secrets inside stay sealed with the server key, which travels
inside the encrypted file — a restored phone can use them without re-authorizing.

Restore never swaps a live database: it stages the files and the server restarts
into them (main.start applies staged files before opening anything).
"""
import base64
import hashlib
import json
import os
import sqlite3
import tempfile
import time
import zlib

from . import config, db, security

MAGIC = b"DCBK1"
AAD = b"drivecast-backup-v1"
KEEP_ON_DRIVE = 7
FOLDER = "_DriveCast backups"


def _key(password, salt):
    if len(password or "") < 8:
        raise ValueError("The backup password must be at least 8 characters.")
    return hashlib.scrypt(password.encode("utf-8"), salt=salt, n=2 ** 14, r=8, p=1, dklen=32)


def _db_bytes():
    """A consistent copy of the live database (SQLite online backup)."""
    fd, tmp = tempfile.mkstemp(suffix=".db", dir=config.TMP_DIR if os.path.isdir(config.TMP_DIR) else None)
    os.close(fd)
    try:
        dst = sqlite3.connect(tmp)
        db.conn().backup(dst)
        dst.close()
        with open(tmp, "rb") as f:
            return f.read()
    finally:
        os.remove(tmp)


def create(password):
    from . import VERSION
    with open(os.path.join(config.DATA_DIR, "master.key"), "rb") as f:
        key = f.read()
    payload = {"v": 1, "created": time.time(), "version": VERSION,
               "key": base64.b64encode(key).decode(), "db": base64.b64encode(_db_bytes()).decode(),
               "summary": summary()}
    raw = zlib.compress(json.dumps(payload).encode(), 6)
    salt, nonce = os.urandom(16), os.urandom(12)
    ct = security.AESGCM(_key(password, salt)).encrypt(nonce, raw, AAD)
    return MAGIC + salt + nonce + ct


def summary():
    one = lambda q: db.one(q)["n"]  # noqa: E731
    return {"accounts": one("SELECT COUNT(*) n FROM users"), "channels": one("SELECT COUNT(*) n FROM channels WHERE state!='setup'"),
            "groups": one("SELECT COUNT(*) n FROM ip_groups"), "posts": one("SELECT COUNT(*) n FROM uploads WHERE status='published'")}


def open_file(blob, password):
    if not blob or blob[:5] != MAGIC or len(blob) < 5 + 16 + 12 + 16:
        raise ValueError("This is not a DriveCast backup file.")
    salt, nonce, ct = blob[5:21], blob[21:33], blob[33:]
    try:
        raw = security.AESGCM(_key(password, salt)).decrypt(nonce, ct, AAD)
    except ValueError:
        raise
    except Exception:
        raise ValueError("Wrong backup password (or the file is damaged).")
    data = json.loads(zlib.decompress(raw))
    if data.get("v") != 1:
        raise ValueError("This backup was made by a newer DriveCast. Update the app first.")
    return data


def stage(blob, password):
    """Check the file and put it next to the live data; applied on the next start."""
    data = open_file(blob, password)
    key, dbb = base64.b64decode(data["key"]), base64.b64decode(data["db"])
    if len(key) != 32 or dbb[:16] != b"SQLite format 3\x00":
        raise ValueError("The backup file is damaged.")
    with open(os.path.join(config.DATA_DIR, "drivecast.db.restore"), "wb") as f:
        f.write(dbb)
    with open(os.path.join(config.DATA_DIR, "master.key.restore"), "wb") as f:
        f.write(key)
    return data.get("summary") or {}, data.get("created")


def apply_staged(data_dir):
    """Called by main.start before anything is opened."""
    dbr, keyr = os.path.join(data_dir, "drivecast.db.restore"), os.path.join(data_dir, "master.key.restore")
    if not (os.path.exists(dbr) and os.path.exists(keyr)):
        return False
    stamp = time.strftime("%Y%m%d-%H%M%S")
    old = os.path.join(data_dir, f"before-restore-{stamp}")
    os.makedirs(old, exist_ok=True)
    for name in ("drivecast.db", "drivecast.db-wal", "drivecast.db-shm", "master.key"):
        p = os.path.join(data_dir, name)
        if os.path.exists(p):
            os.replace(p, os.path.join(old, name))
    os.replace(dbr, os.path.join(data_dir, "drivecast.db"))
    os.replace(keyr, os.path.join(data_dir, "master.key"))
    # restoring a backup = this phone takes over that phone's address (see lease.py)
    with open(os.path.join(data_dir, "takeover.flag"), "w") as f:
        f.write(stamp)
    return True


def fetch_link(url, session=None):
    """Download a backup from a Google Drive 'Anyone with the link' URL or any https link."""
    import re
    import requests
    m = re.search(r"/d/([A-Za-z0-9_-]{10,})", url) or re.search(r"[?&]id=([A-Za-z0-9_-]{10,})", url)
    s = session or requests.Session()
    if "drive.google.com" in url or "docs.google.com" in url:
        if not m:
            raise ValueError("Could not read the file ID from that Drive link.")
        url = f"https://drive.usercontent.google.com/download?id={m.group(1)}&export=download&confirm=t"
    elif not url.startswith("https://"):
        raise ValueError("Use an https link.")
    r = s.get(url, timeout=(20, 120))
    if r.status_code != 200:
        raise ValueError(f"Could not download the file ({r.status_code}). Share it as “Anyone with the link”.")
    if r.content[:5] != MAGIC:
        raise ValueError("The link did not return a DriveCast backup. In Drive, share the file as “Anyone with the link”.")
    return r.content


# ------------------------------------------------------------------ nightly Drive backup
def password_get():
    sealed = db.meta_get("backup_password")
    return security.unseal(sealed) if sealed else None


def to_drive(force=False):
    """Once a day: encrypted backup into <main folder>/_DriveCast backups, keep the last 7."""
    from . import drive, store
    from .net import session_for
    pw = password_get()
    if not pw:
        return "no_password"
    last = float(db.meta_get("backup_at") or 0)
    if not force and time.time() - last < 23 * 3600:
        return "recent"
    owner = db.one("SELECT workspace_id FROM users WHERE role='owner' ORDER BY id LIMIT 1")
    ws = store.workspace(owner["workspace_id"]) if owner else None
    if not ws or not ws.get("drive_token_enc") or not ws.get("drive_root_id"):
        db.meta_set("backup_error", "Connect Google Drive and choose the main folder first.")
        return "no_drive"
    blob = create(pw)
    fd, tmp = tempfile.mkstemp(suffix=".dcbackup", dir=config.TMP_DIR)
    os.close(fd)
    try:
        with open(tmp, "wb") as f:
            f.write(blob)
        s, box = session_for(None), store.drive_box(ws)
        folder = drive.child_folder(s, box, ws["drive_root_id"], FOLDER)
        name = time.strftime("DriveCast-backup-%Y-%m-%d-%H%M.dcbackup")
        drive.upload(s, box, tmp, name, folder, "application/octet-stream", {"dc_backup": "1"})
        olds = sorted(drive.files(s, box, folder, limit=100), key=lambda f: f.get("createdTime") or "", reverse=True)
        for f in olds[KEEP_ON_DRIVE:]:
            try:
                drive.remove(s, box, f["id"], folder)
            except Exception:
                pass
    finally:
        os.remove(tmp)
    db.meta_set("backup_at", str(time.time()))
    db.meta_set("backup_name", name)
    db.meta_set("backup_error", "")
    return "done"


def test_latest():
    """Restore drill: download the newest backup from Drive and open it with the saved password."""
    from . import drive, store
    from .net import session_for
    pw = password_get()
    owner = db.one("SELECT workspace_id FROM users WHERE role='owner' ORDER BY id LIMIT 1")
    ws = store.workspace(owner["workspace_id"]) if owner else None
    if not pw or not ws or not ws.get("drive_token_enc") or not ws.get("drive_root_id"):
        raise ValueError("Set a backup password and connect Drive first.")
    s, box = session_for(None), store.drive_box(ws)
    folder = drive.child_folder(s, box, ws["drive_root_id"], FOLDER)
    files = sorted(drive.files(s, box, folder, limit=50), key=lambda f: f.get("createdTime") or "", reverse=True)
    if not files:
        raise ValueError("No backup in Drive yet. Press Back up now first.")
    fd, tmp = tempfile.mkstemp(suffix=".dcbackup", dir=config.TMP_DIR)
    os.close(fd)
    try:
        drive.download(s, box, files[0]["id"], tmp)
        with open(tmp, "rb") as f:
            data = open_file(f.read(), pw)
        db.meta_set("backup_test_at", str(time.time()))
        db.meta_set("backup_test_error", "")
        return {"ok": True, "name": files[0]["name"], "tables": {k: len(v) for k, v in (data.get("tables") or {}).items()} if isinstance(data, dict) else None}
    except ValueError as e:
        db.meta_set("backup_test_error", str(e)[:200])
        raise
    finally:
        os.remove(tmp)


def service():
    # weekly restore drill, quietly, after the nightly backup exists
    if time.time() - float(db.meta_get("backup_test_at") or 0) > 7 * 86400 and db.meta_get("backup_at"):
        try:
            test_latest()
        except Exception:
            pass
    try:
        to_drive()
    except Exception as e:
        db.meta_set("backup_error", str(e)[:300])
        db.event("warn", "backup", f"Nightly backup to Drive failed: {e}", None, None, "BACKUP_FAILED", str(e))
