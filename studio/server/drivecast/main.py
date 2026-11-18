"""Entry point. The Android app calls ``start(data_dir)``; on a computer run
``python -m drivecast --data ./data``."""
import argparse
import logging
import logging.handlers
import os
import threading

from . import ai, usermove, alerts, backup, config, db, fleet, hotupdate, igsync, lease, maintenance, scheduler, security, supervisor, vidsync, web, worker

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
    # the Android app passes the shared phone folder and the bundled ffmpeg; on a computer both have defaults
    config.MEDIA_DIR = os.environ.get("DC_MEDIA_DIR") or os.path.join(data_dir, "media")
    config.FFMPEG = os.environ.get("DC_FFMPEG") or None
    try:
        os.makedirs(config.MEDIA_DIR, exist_ok=True)
        open(os.path.join(config.MEDIA_DIR, ".nomedia"), "a").close()
    except OSError:  # no storage permission yet: fall back to the app's private folder
        config.MEDIA_DIR = os.path.join(data_dir, "media")
        os.makedirs(config.MEDIA_DIR, exist_ok=True)

    handler = logging.handlers.RotatingFileHandler(os.path.join(data_dir, "logs", "drivecast.log"),
                                                   maxBytes=2_000_000, backupCount=3)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger("drivecast")
    root.setLevel(logging.INFO)
    if not root.handlers:
        root.addHandler(handler)

    if backup.apply_staged(data_dir):
        logging.getLogger("drivecast").warning("restored a backup before start")
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
        ("instagram", igsync.tick, 60),
        ("video-sources", vidsync.tick, 60),
        ("fleet", fleet.tick, 300),
        ("updates", hotupdate.service, 1800),
        ("guard", lease.tick, 300),
        ("alerts", alerts.tick, 300),
        ("queues", worker.refresh_queues, 600),
        ("ai-reports", ai.service, 6 * 3600),
        ("snapshots", usermove.service, 1800),
        ("inbox", igsync.inbox_pass, 120),
        ("backup", backup.service, 3600),
    ], workers=workers)

    _httpd = web.serve(host, port)
    # a hot update that runs for a minute is kept; one that crashes earlier is rolled back by dcboot
    threading.Timer(60, hotupdate.confirm_healthy).start()
    lease.on_start()
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
