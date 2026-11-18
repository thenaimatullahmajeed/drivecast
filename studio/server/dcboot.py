"""Boot loader for the phone app: runs the newest *verified* server code.

Hot updates live in <data>/hot/<version>/drivecast. A new version is tried once;
if it does not confirm a healthy start within a minute, the next start falls back
to the code inside the APK, so a bad update can never lock the phone out.
"""
import json
import os
import sys


def _vt(v):
    out = []
    for p in str(v or "0").split("."):
        try:
            out.append(int(p))
        except ValueError:
            out.append(0)
    return tuple(out)


def _apk_version():
    """VERSION of the code inside the APK, read without importing it."""
    if os.environ.get("DC_APP_VERSION"):
        return os.environ["DC_APP_VERSION"]  # the app tells us (set by the Android service)
    import importlib.util
    import re
    try:
        spec = importlib.util.find_spec("drivecast")
        loader = spec.loader
        src = None
        if spec.origin and os.path.isfile(spec.origin):
            with open(spec.origin, encoding="utf-8") as f:
                src = f.read()
        elif hasattr(loader, "get_source"):
            src = loader.get_source("drivecast")
        m = re.search(r'VERSION\s*=\s*"([^"]+)"', src or "")
        return m.group(1) if m else "0"
    except Exception:
        return "0"


def _pick(data_dir):
    hot = os.path.join(data_dir, "hot")
    try:
        with open(os.path.join(hot, "current.json")) as f:
            cur = json.load(f)
    except Exception:
        return None
    ver = cur.get("version")
    path = os.path.join(hot, ver or "-")
    if not ver or not os.path.isfile(os.path.join(path, "drivecast", "__init__.py")):
        return None
    if int(os.environ.get("DC_SHELL_API", "0") or 0) < int(cur.get("shell_api", 0)):
        return None  # this update needs a newer APK
    if _vt(ver) <= _vt(_apk_version()):
        # a newer APK was installed after this update: the APK's own code wins, the old copy is retired
        try:
            os.replace(os.path.join(hot, "current.json"), os.path.join(hot, f"old-{ver}.json"))
        except OSError:
            pass
        return None
    trying = os.path.join(hot, "trying")
    if os.path.exists(trying):
        with open(trying) as f:
            failed = f.read().strip()
        if failed == ver:  # last start with this version never confirmed: fall back, remember it as bad
            os.remove(trying)
            try:
                os.replace(os.path.join(hot, "current.json"), os.path.join(hot, f"bad-{ver}.json"))
            except OSError:
                pass
            return None
    with open(trying, "w") as f:
        f.write(ver)
    return path


def start(data_dir, host="127.0.0.1", port=8765):
    path = _pick(os.path.abspath(data_dir))
    if path:
        sys.path.insert(0, path)
        for k in [k for k in sys.modules if k == "drivecast" or k.startswith("drivecast.")]:
            del sys.modules[k]
        os.environ["DC_HOT"] = path
    from drivecast import main
    return main.start(data_dir, host, port)
