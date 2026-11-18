"""Updates without reinstalling the APK.

Every release on GitHub carries dc-server-<version>.zip (the Python server + panel)
and dc-server-<version>.json (its SHA-256 and the APK level it needs). The phone:
  1. asks GitHub for the newest release of this repository (HTTPS, this repo only);
  2. downloads the bundle, checks size, SHA-256, version and APK level;
  3. unpacks it to <data>/hot/<version> and restarts the server.
dcboot.py runs it once on trial: if it does not report a healthy start within a
minute, the phone goes back to the code inside the APK by itself.
Automatic mode applies updates at night (02:00–05:00 local), never during a post.
"""
import hashlib
import io
import json
import os
import shutil
import threading
import time
import zipfile

from . import VERSION, config, db

REPO = "thenaimatullahmajeed/drivecast"
API = f"https://api.github.com/repos/{REPO}/releases?per_page=10"
SHELL_API = int(os.environ.get("DC_SHELL_API", "0") or 0)
_cache = {"at": 0, "data": None}


def vtuple(v):
    try:
        return tuple(int(x) for x in str(v).split(".")[:3])
    except ValueError:
        return (0, 0, 0)


def running_from():
    return "update" if os.environ.get("DC_HOT") else "app"


def latest(force=False):
    """Newest release that has a server bundle. Cached for an hour."""
    from .net import session_for
    if not force and _cache["data"] and time.time() - _cache["at"] < 3600:
        return _cache["data"]
    r = session_for(None).get(API, timeout=(15, 30), headers={"Accept": "application/vnd.github+json"})
    r.raise_for_status()
    best = None
    for rel in r.json():
        assets = {a["name"]: a for a in rel.get("assets", [])}
        ver = (rel.get("tag_name") or "").replace("server-", "")
        z, m = assets.get(f"dc-server-{ver}.zip"), assets.get(f"dc-server-{ver}.json")
        apk = next((a for n, a in assets.items() if n.endswith(".apk")), None)
        if z and m and (not best or vtuple(ver) > vtuple(best["version"])):
            best = {"version": ver, "zip": z["browser_download_url"], "manifest": m["browser_download_url"],
                    "size": z["size"], "apk": apk and apk["browser_download_url"], "notes": (rel.get("body") or "")[:600],
                    "published": rel.get("published_at")}
    _cache.update(at=time.time(), data=best)
    return best


def state():
    try:
        lt = latest()
        err = None
    except Exception as e:
        lt, err = None, "Could not reach GitHub right now — it tries again by itself." + (f" ({type(e).__name__})" if e else "")
    newer = bool(lt and vtuple(lt["version"]) > vtuple(VERSION))
    return {"version": VERSION, "running_from": running_from(), "shell_api": SHELL_API, "latest": lt, "newer": newer,
            "auto": db.meta_get("auto_update") != "0", "error": err or db.meta_get("update_error") or None,
            "last": db.meta_get("update_last") or None}


def apply(force=False):
    """Download, check and stage the newest bundle, then restart the server."""
    from .net import session_for
    lt = latest(force=True)
    if not lt:
        raise ValueError("No update bundle found on GitHub yet.")
    if not force and vtuple(lt["version"]) <= vtuple(VERSION):
        return {"ok": True, "message": "Already up to date", "version": VERSION}
    s = session_for(None)
    man = s.get(lt["manifest"], timeout=(15, 60)).json()
    if man.get("version") != lt["version"]:
        raise ValueError("Update manifest does not match the release.")
    if int(man.get("shell_api", 0)) > SHELL_API:
        raise ValueError("This update needs the new APK — install it from GitHub once (Server page shows the link).")
    blob = s.get(lt["zip"], timeout=(15, 300)).content
    if len(blob) != int(man.get("size", -1)) or hashlib.sha256(blob).hexdigest() != man.get("sha256"):
        raise ValueError("Downloaded update failed its checksum — not installed.")
    zf = zipfile.ZipFile(io.BytesIO(blob))
    names = zf.namelist()
    if "drivecast/__init__.py" not in names or any(n.startswith("/") or ".." in n.split("/") for n in names):
        raise ValueError("Update bundle is not valid.")
    if f'VERSION = "{lt["version"]}"' not in zf.read("drivecast/__init__.py").decode():
        raise ValueError("Update bundle version mismatch.")
    hot = os.path.join(config.DATA_DIR, "hot")
    dest = os.path.join(hot, lt["version"])
    shutil.rmtree(dest, ignore_errors=True)
    os.makedirs(dest, exist_ok=True)
    zf.extractall(dest)
    with open(os.path.join(hot, "current.json"), "w") as f:
        json.dump({"version": lt["version"], "shell_api": int(man.get("shell_api", 0)), "at": time.time()}, f)
    # keep only the two newest versions on disk
    for name in sorted((n for n in os.listdir(hot) if os.path.isdir(os.path.join(hot, n))), key=vtuple)[:-2]:
        shutil.rmtree(os.path.join(hot, name), ignore_errors=True)
    db.meta_set("update_last", json.dumps({"from": VERSION, "to": lt["version"], "at": time.time()}))
    db.meta_set("update_error", "")
    db.event("info", "update", f"Updating DriveCast {VERSION} → {lt['version']}. The server restarts in a few seconds.")
    threading.Thread(target=_restart, daemon=True).start()
    return {"ok": True, "version": lt["version"], "restarting": True}


def _restart():
    time.sleep(2)
    os._exit(3)  # the Android service starts the server again


def back_to_app():
    """Forget hot updates; run the code inside the APK."""
    hot = os.path.join(config.DATA_DIR, "hot")
    try:
        os.remove(os.path.join(hot, "current.json"))
    except OSError:
        pass
    threading.Thread(target=_restart, daemon=True).start()


def confirm_healthy():
    """Called a minute after a good start: this version stays."""
    try:
        os.remove(os.path.join(config.DATA_DIR, "hot", "trying"))
    except OSError:
        pass


def service():
    """Nightly: install a newer bundle automatically (owner can switch this off)."""
    if db.meta_get("auto_update") == "0" or not SHELL_API:
        return
    h = time.localtime().tm_hour
    if not 2 <= h < 5:
        return
    if db.one("SELECT 1 FROM jobs WHERE status='running'"):
        return
    try:
        st = state()
        if st["newer"]:
            apply()
    except Exception as e:
        db.meta_set("update_error", str(e)[:200])
