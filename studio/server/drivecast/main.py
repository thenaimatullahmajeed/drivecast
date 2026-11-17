"""Entry point. The Android app calls ``start(data_dir)``; on a computer run
``python -m drivecast --data ./data``."""
import argparse
import logging
import logging.handlers
import os
import threading

from . import config, db, maintenance, scheduler, security, supervisor, web, worker

_httpd = None


def start(data_dir, host="127.0.0.1", port=8765, block=False, workers=2):
    global _httpd
    data_dir = os.path.abspath(data_dir)
    os.makedirs(data_dir, exist_ok=True)
    os.makedirs(os.path.join(data_dir, "logs"), exist_ok=True)
    tmp = os.path.join(data_dir, "tmp")
    os.makedirs(tmp, exist_ok=True)
    config.DATA_DIR, config.TMP_DIR = data_dir, tmp
    worker.TMP_DIR = tmp

    handler = logging.handlers.RotatingFileHandler(os.path.join(data_dir, "logs", "drivecast.log"),
                                                   maxBytes=2_000_000, backupCount=3)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger("drivecast")
    root.setLevel(logging.INFO)
    if not root.handlers:
        root.addHandler(handler)

    security.load_key(os.path.join(data_dir, "master.key"))
    db.init(os.path.join(data_dir, "drivecast.db"))
    tok_path = os.path.join(data_dir, "local.token")
    if not os.path.exists(tok_path):
        fd = os.open(tok_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(security.token(24))
    with open(tok_path) as f:
        config.LOCAL_TOKEN = f.read().strip()
    if not db.meta_get("setup_code"):
        db.meta_set("setup_code", security.code("SETUP"))

    supervisor.start([
        ("scheduler", scheduler.tick, 30),
        ("folders", maintenance.refresh_folders, 300),
        ("proxies", maintenance.check_groups, 300),
        ("stats", maintenance.sync_stats, 5 * 60),
        ("cleanup", lambda: maintenance.cleanup(tmp), 6 * 3600),
    ], workers=workers)

    _httpd = web.serve(host, port)
    if block:
        _httpd.serve_forever()
    else:
        threading.Thread(target=_httpd.serve_forever, name="dc-web", daemon=True).start()
    return port


def stop():
    supervisor.stop()
    if _httpd:
        _httpd.shutdown()


def setup_code():
    return db.meta_get("setup_code")


def cli():
    ap = argparse.ArgumentParser(description="DriveCast Studio server")
    ap.add_argument("--data", default="./data")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO)
    start(a.data, a.host, a.port, block=False)
    print(f"DriveCast running on http://{a.host}:{a.port}  setup code: {setup_code()}")
    threading.Event().wait()


if __name__ == "__main__":
    cli()
