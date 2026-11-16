"""Periodic jobs: Drive folder inventory, proxy health, YouTube stats, cleanup.
Each loop isolates failures per workspace / group / channel."""
import datetime as dt
import json
import os
import shutil
import time

from . import db, drive, store
from .net import DriveCastError, check_group, session_for
from .platforms import get as platform


def refresh_folders(ws_id=None):
    rows = db.all("SELECT * FROM workspaces WHERE drive_token_enc IS NOT NULL AND drive_root_id IS NOT NULL"
                  + (" AND id=?" if ws_id else ""), (ws_id,) if ws_id else ())
    for ws in rows:
        try:
            box = store.drive_box(ws)
            # Inventory is metadata of the owner's own Drive; it goes direct.
            folders = drive.subfolders(session_for(None), box, ws["drive_root_id"])
            db.run("UPDATE workspaces SET folders_json=?, folders_scanned_at=?, folders_error=NULL WHERE id=?",
                   (json.dumps(folders), time.time(), ws["id"]))
        except Exception as e:
            msg = e.user_message if isinstance(e, DriveCastError) else str(e)
            # keep the previous complete list; only record the error
            db.run("UPDATE workspaces SET folders_error=? WHERE id=?", (msg[:300], ws["id"]))


def check_groups():
    for g in db.all("SELECT * FROM ip_groups"):
        try:
            ip, ms = check_group(g)
            hist = db.jload(g["latency_json"], [])[-23:] + [ms]
            if g["outgoing_ip"] and ip != g["outgoing_ip"]:
                db.run("UPDATE ip_groups SET status='down', last_error=?, last_check=?, latency_json=? WHERE id=?",
                       (f"Outgoing IP changed to {ip}", time.time(), json.dumps(hist), g["id"]))
                db.event("error", "proxy", f"Group {g['number']}: outgoing IP changed to {ip}. Channels held until you confirm.",
                         g["workspace_id"])
                continue
            was_down = g["status"] == "down"
            db.run("""UPDATE ip_groups SET status='ok', outgoing_ip=COALESCE(outgoing_ip,?), latency_ms=?, last_check=?,
                      last_error=NULL, fail_count=0, latency_json=? WHERE id=?""",
                   (ip, ms, time.time(), json.dumps(hist), g["id"]))
            if was_down:
                db.event("info", "proxy", f"Group {g['number']} is back online.", g["workspace_id"])
        except Exception as e:
            hist = db.jload(g["latency_json"], [])[-23:] + [0]
            db.run("UPDATE ip_groups SET status='down', last_error=?, last_check=?, fail_count=fail_count+1, latency_json=? WHERE id=?",
                   (str(getattr(e, "user_message", e))[:200], time.time(), json.dumps(hist), g["id"]))


def sync_stats():
    today = dt.datetime.now(dt.timezone.utc).date().isoformat()
    for ch in db.all("SELECT * FROM channels WHERE state!='setup' AND token_enc IS NOT NULL"):
        g = store.group(ch["group_id"])
        if not g or g["status"] == "down":
            continue  # stats also travel only through the channel's own IP
        try:
            s = session_for(g)
            box = store.channel_box(ch)
            p = platform(ch["platform"])
            cs = p.channel_stats(s, box)
            db.run("UPDATE channels SET subscribers=?, total_views=?, title=?, handle=COALESCE(?,handle), thumbnail=COALESCE(?,thumbnail), stats_at=? WHERE id=?",
                   (cs["subscribers"], cs["total_views"], cs["title"], cs["handle"], cs["thumbnail"], time.time(), ch["id"]))
            db.run("INSERT OR REPLACE INTO channel_points(channel_id,day,views,subscribers) VALUES(?,?,?,?)",
                   (ch["id"], today, cs["total_views"], cs["subscribers"]))
            ups = db.all("SELECT id, external_video_id FROM uploads WHERE channel_id=? AND status='published' AND published_at>?",
                         (ch["id"], time.time() - 90 * 86400))
            ids = [u["external_video_id"] for u in ups if u["external_video_id"]]
            stats = p.video_stats(s, box, ids) if ids else {}
            for u in ups:
                v = stats.get(u["external_video_id"])
                if not v:
                    continue
                db.run("UPDATE uploads SET views=?, likes=?, comments=?, visibility=COALESCE(?,visibility), stats_at=? WHERE id=?",
                       (v["views"], v["likes"], v["comments"], v["visibility"], time.time(), u["id"]))
                db.run("INSERT OR REPLACE INTO stat_points(upload_id,day,views) VALUES(?,?,?)", (u["id"], today, v["views"]))
        except Exception as e:
            msg = getattr(e, "user_message", str(e))
            db.event("warn", "stats", f"{ch['title']}: stats not updated ({msg})", ch["workspace_id"], ch["id"])


def cleanup(tmp_dir):
    now = time.time()
    db.run("DELETE FROM sessions WHERE expires_at<?", (now,))
    db.run("DELETE FROM connect_links WHERE expires_at<?", (now - 86400,))
    db.run("DELETE FROM events WHERE at<?", (now - 60 * 86400,))
    db.run("DELETE FROM jobs WHERE status IN ('done','failed') AND finished_at<?", (now - 30 * 86400,))
    db.run("DELETE FROM slots WHERE due_at<? AND status NOT IN ('done','uncertain')", (now - 120 * 86400,))
    if tmp_dir and os.path.isdir(tmp_dir):
        for name in os.listdir(tmp_dir):
            p = os.path.join(tmp_dir, name)
            try:
                if now - os.path.getmtime(p) > 6 * 3600:
                    shutil.rmtree(p, ignore_errors=True)
            except OSError:
                pass
