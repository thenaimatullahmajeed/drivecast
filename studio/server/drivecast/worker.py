"""Publishing worker. One job = pick the next Drive video for a channel and
publish it through that channel's IP group.

Bulkhead rule: every job runs inside its own try/except. A failure changes only
that channel (and, for proxy problems, that IP group) — never the whole server.
"""
import logging
import os
import shutil
import tempfile
import time

from . import db, drive, store
from .net import (AuthRevoked, DriveCastError, Permanent, ProxyDown, QuotaExceeded, Transient, Uncertain,
                  outgoing_ip, session_for)
from .platforms import get as platform
from .timeplan import next_pacific_midnight

log = logging.getLogger("drivecast.worker")
LEASE = 3 * 3600
BREAKER_AFTER = 3          # consecutive failures before a channel cools down
BREAKER_COOLDOWN = 3600    # first cool-down; doubles each time, max 12 h
TMP_DIR = None
PROGRESS = {}  # channel_id -> live status of the post being made right now


def _stage(ch_id, stage, pct=None, title=None):
    p = PROGRESS.setdefault(ch_id, {"started": time.time()})
    p.update({"stage": stage, "at": time.time()})
    if pct is not None:
        p["pct"] = round(max(0.0, min(1.0, pct)) * 100)
    if title:
        p["title"] = title


def claim(worker_id, now=None):
    """Atomically take the oldest queued job whose channel has nothing running."""
    now = now or time.time()
    with db.tx() as c:
        row = c.execute("""
            SELECT j.id FROM jobs j WHERE j.status='queued'
              AND NOT EXISTS (SELECT 1 FROM jobs r WHERE r.channel_id=j.channel_id AND r.status='running')
            ORDER BY j.created_at LIMIT 1""").fetchone()
        if not row:
            return None
        c.execute("UPDATE jobs SET status='running', lease_owner=?, lease_until=? WHERE id=?",
                  (worker_id, now + LEASE, row[0]))
        return dict(c.execute("SELECT * FROM jobs WHERE id=?", (row[0],)).fetchone())


def finish(job, status, error=None, slot_status=None, note=None, upload_id=None):
    with db.tx() as c:
        c.execute("UPDATE jobs SET status=?, finished_at=?, error=?, lease_owner=NULL WHERE id=?",
                  (status, time.time(), error, job["id"]))
        if job.get("slot_id") and slot_status:
            c.execute("UPDATE slots SET status=?, note=?, upload_id=COALESCE(?,upload_id), updated_at=? WHERE id=?",
                      (slot_status, note, upload_id, time.time(), job["slot_id"]))


def recover_interrupted():
    """After a crash/restart: jobs left 'running' are closed safely."""
    for j in db.all("SELECT * FROM jobs WHERE status='running'"):
        finish(j, "failed", "Interrupted by restart", "failed", "Server restarted during this post")
    for u in db.all("SELECT * FROM uploads WHERE status IN ('reserved','uploading')"):
        if u["status"] == "uploading":
            db.run("UPDATE uploads SET status='uncertain', error=? WHERE id=?",
                   ("Server stopped while uploading — check YouTube Studio before re-posting", u["id"]))
            db.event("error", "uncertain", f"“{u['title']}” may or may not be on YouTube. Check YouTube Studio.",
                     u["workspace_id"], u["channel_id"])
        else:  # nothing was sent yet: release the file
            db.run("DELETE FROM uploads WHERE id=?", (u["id"],))


def _breaker(ch, err_msg):
    n = ch["consecutive_failures"] + 1
    if n >= BREAKER_AFTER:
        cool = min(12 * 3600, BREAKER_COOLDOWN * (2 ** (n - BREAKER_AFTER)))
        db.run("UPDATE channels SET consecutive_failures=?, paused_until=?, state='attention', state_reason=? WHERE id=?",
               (n, time.time() + cool, f"{n} failures in a row — cooling down. Last: {err_msg}", ch["id"]))
    else:
        db.run("UPDATE channels SET consecutive_failures=? WHERE id=?", (n, ch["id"]))


def _group_down(g, why):
    db.run("UPDATE ip_groups SET status='down', last_error=?, fail_count=fail_count+1, last_check=? WHERE id=?",
           (why, time.time(), g["id"]))
    db.event("error", "proxy", f"Group {g['number']} is not responding — its channels are held. {why}",
             g["workspace_id"])


def run(job, adapters=None):
    """Execute one job. ``adapters`` lets tests inject fakes:
    {'session': fn(group), 'ip': fn(session), 'drive': module, 'platform': module}."""
    a = adapters or {}
    make_session = a.get("session", session_for)
    check_ip = a.get("ip", outgoing_ip)
    dmod = a.get("drive", drive)
    ch = store.channel(job["channel_id"])
    if not ch:
        return finish(job, "failed", "Channel removed", "skipped", "Channel removed")
    pmod = a.get("platform") or platform(ch["platform"])
    ws = store.workspace(ch["workspace_id"])
    now = time.time()

    # --- gates: nothing is touched unless the route is fully healthy ---
    # 'attention' only runs again when it came from the cool-down breaker and the cool-down ended
    breaker_over = ch["state"] == "attention" and ch["paused_until"] and ch["paused_until"] <= now
    if not (ch["state"] == "live" or breaker_over) or (ch["paused_until"] or 0) > now:
        return finish(job, "failed", "Channel not live", "held", ch["state_reason"] or "Channel paused")
    g = store.group(ch["group_id"])
    if not g:
        store.set_channel_state(ch["id"], "attention", "No IP group — choose one")
        return finish(job, "failed", "No IP group", "held", "No IP group")
    if g["status"] == "down":
        return finish(job, "failed", "Proxy down", "held", f"Group {g['number']} not responding")
    lim = store.limits_for_workspace(ws["id"])
    if store.uploads_today(ws["id"], now) >= lim["daily_cap"]:
        return finish(job, "failed", "Daily cap", "held", "Daily upload cap reached — resets at midnight Pacific")
    if not ch["folder_id"]:
        return finish(job, "failed", "No folder", "held", "No Drive folder connected")

    session = make_session(g)
    phase, upload_id, tmp = "prepare", None, None
    _stage(ch["id"], "Checking proxy IP", 0)
    try:
        # --- never leave from the wrong IP ---
        try:
            ip, ms = check_ip(session)
        except Exception as e:
            _group_down(g, str(e)[:120])
            return finish(job, "failed", "Proxy down", "held", f"Group {g['number']} not responding")
        if g.get("outgoing_ip") and ip != g["outgoing_ip"]:
            _group_down(g, f"Outgoing IP changed to {ip} (expected {g['outgoing_ip']})")
            return finish(job, "failed", "IP changed", "held", "Proxy IP changed — confirm the group")
        db.run("UPDATE ip_groups SET outgoing_ip=COALESCE(outgoing_ip,?), status='ok', latency_ms=?, last_check=? WHERE id=?",
               (ip, ms, now, g["id"]))

        dbox = store.drive_box(ws)
        cbox = store.channel_box(ch)
        # --- pick the next file that has never been reserved for this destination ---
        dest = f"{ch['platform']}:{ch['external_id']}"
        files = dmod.videos(session, dbox, ch["folder_id"])
        db.run("UPDATE channels SET queue_count=?, queue_checked_at=? WHERE id=?", (len(files), now, ch["id"]))
        chosen = None
        for f in files:
            try:
                with db.tx() as c:
                    cur = c.execute("""INSERT INTO uploads(workspace_id,channel_id,destination,file_id,file_name,title,
                                       status,reserved_at,outgoing_ip) VALUES(?,?,?,?,?,?,'reserved',?,?)""",
                                    (ws["id"], ch["id"], dest, f["id"], f["name"], pmod.title_from_filename(f["name"]),
                                     now, ip))
                    upload_id = cur.lastrowid
                chosen = f
                break
            except Exception:  # UNIQUE(file_id,destination): already handled before
                continue
        if not chosen:
            if not files:
                db.event("warn", "empty", f"{ch['title']}: Drive folder is empty — add videos.", ws["id"], ch["id"])
            return finish(job, "done", None, "skipped", "No new video in the Drive folder")

        # --- stage in Drive, download to a private temp file ---
        title0 = pmod.title_from_filename(chosen["name"])
        _stage(ch["id"], "Downloading from Drive", 0, title0)
        processing = dmod.child_folder(session, dbox, ch["folder_id"], "Processing")
        dmod.move(session, dbox, chosen["id"], ch["folder_id"], processing)
        tmp = tempfile.mkdtemp(prefix="dc-", dir=TMP_DIR)
        path = os.path.join(tmp, "video" + (os.path.splitext(chosen["name"])[1] or ".mp4"))
        size = int(chosen.get("size") or 0)
        try:
            dmod.download(session, dbox, chosen["id"], path,
                          on_progress=(lambda n: _stage(ch["id"], "Downloading from Drive", n / size)) if size else None)
        except TypeError:  # test doubles without progress support
            dmod.download(session, dbox, chosen["id"], path)

        title = pmod.title_from_filename(chosen["name"])
        db.run("UPDATE uploads SET status='uploading' WHERE id=?", (upload_id,))
        phase = "upload"
        _stage(ch["id"], "Uploading to YouTube", 0)
        try:
            res = pmod.publish(session, cbox, path, title, title,
                               on_progress=lambda f: _stage(ch["id"], "Uploading to YouTube", f))
        except TypeError as te:
            if "on_progress" not in str(te):
                raise
            res = pmod.publish(session, cbox, path, title, title)
        phase = "after"
        db.run("UPDATE uploads SET status='published', external_video_id=?, visibility=?, published_at=? WHERE id=?",
               (res["video_id"], res["visibility"], time.time(), upload_id))
        # stats for this channel come back ~5 minutes after the post, then every 20 minutes
        db.run("UPDATE channels SET consecutive_failures=0, paused_until=NULL, state='live', state_reason=NULL, stats_at=? WHERE id=?",
               (time.time() - 15 * 60, ch["id"]))
        try:
            done = dmod.child_folder(session, dbox, ch["folder_id"], "Done")
            dmod.move(session, dbox, chosen["id"], processing, done)
        except Exception as e:  # the post is live; a failed tidy-up must not undo that
            db.event("warn", "drive", f"Published, but could not move the file to Done: {e}", ws["id"], ch["id"])
        if res["visibility"] != "public":
            db.event("warn", "visibility", f"“{title}” was accepted but YouTube shows it as {res['visibility']}.",
                     ws["id"], ch["id"])
        return finish(job, "done", None, "done", res["video_id"], upload_id)

    except Uncertain as e:
        db.run("UPDATE uploads SET status='uncertain', error=? WHERE id=?", (e.user_message, upload_id))
        db.event("error", "uncertain", f"{ch['title']}: {e.user_message}. Check YouTube Studio — it will not be retried.",
                 ws["id"], ch["id"])
        return finish(job, "failed", str(e), "uncertain", e.user_message, upload_id)
    except Exception as e:
        err = e.user_message if isinstance(e, DriveCastError) else f"Unexpected error: {e}"
        if phase == "upload" and not isinstance(e, DriveCastError):
            # an unknown crash while bytes may have been sent is treated as uncertain
            db.run("UPDATE uploads SET status='uncertain', error=? WHERE id=?", (err, upload_id))
            db.event("error", "uncertain", f"{ch['title']}: upload interrupted. Check YouTube Studio.", ws["id"], ch["id"])
            return finish(job, "failed", err, "uncertain", err, upload_id)
        _release(upload_id, session, dmod, ch, permanent=isinstance(e, Permanent))
        if isinstance(e, AuthRevoked):
            store.set_channel_state(ch["id"], "attention", "Reconnect this channel — Google access ended")
            db.event("error", "auth", f"{ch['title']}: {err}", ws["id"], ch["id"])
            return finish(job, "failed", err, "held", "Reconnect channel")
        if isinstance(e, QuotaExceeded):
            db.run("UPDATE channels SET paused_until=? WHERE id=?", (next_pacific_midnight(time.time()), ch["id"]))
            db.event("warn", "quota", f"{ch['title']}: {err}. Posting resumes after midnight Pacific.", ws["id"], ch["id"])
            return finish(job, "failed", err, "held", "Quota reached")
        if isinstance(e, ProxyDown):
            _group_down(g, err)
            return finish(job, "failed", err, "held", "Proxy down")
        if isinstance(e, Permanent):
            db.event("warn", "rejected", f"{ch['title']}: {err}. File moved to Failed.", ws["id"], ch["id"])
            return finish(job, "failed", err, "failed", err)
        _breaker(ch, err)
        log.warning("job %s transient failure: %s", job["id"], e)
        return finish(job, "failed", err, "failed", err)
    finally:
        PROGRESS.pop(ch["id"], None)
        if tmp:
            shutil.rmtree(tmp, ignore_errors=True)


def _release(upload_id, session, dmod, ch, permanent=False):
    """Nothing was published: free the reservation and put the file back
    (or into Failed when the file itself is the problem)."""
    if not upload_id:
        return
    u = db.one("SELECT * FROM uploads WHERE id=?", (upload_id,))
    if not u:
        return
    try:
        dbox = store.drive_box(store.workspace(ch["workspace_id"]))
        processing = dmod.child_folder(session, dbox, ch["folder_id"], "Processing")
        if permanent:
            failed = dmod.child_folder(session, dbox, ch["folder_id"], "Failed")
            dmod.move(session, dbox, u["file_id"], processing, failed)
        else:
            dmod.move(session, dbox, u["file_id"], processing, ch["folder_id"])
    except Exception:
        pass  # file may still be in the source folder; that is fine
    if permanent:
        db.run("UPDATE uploads SET status='failed', error='Rejected' WHERE id=?", (upload_id,))
    else:
        db.run("DELETE FROM uploads WHERE id=?", (upload_id,))
