"""Turns posting plans into slots and due slots into jobs.

Slots are planned per UTC day for today and tomorrow. A slot is only run inside
its peak window; if the server was off for the whole window the slot is marked
missed instead of posting at a bad hour.
"""
import datetime as dt
import random
import time

from . import db
from .timeplan import normalize_plan, plan_day

_rng = random.SystemRandom()


def ensure_slots(ch, now=None, days=2):
    now = now or time.time()
    plan = normalize_plan(db.jload(ch["plan_json"], {}))
    today = dt.datetime.fromtimestamp(now, dt.timezone.utc).date()
    created = 0
    for k in range(days):
        d = today + dt.timedelta(days=k)
        if db.one("SELECT 1 FROM slots WHERE channel_id=? AND day=? LIMIT 1", (ch["id"], d.isoformat())):
            continue
        picks = plan_day(plan, d, _rng)
        with db.tx() as c:
            for i, p in enumerate(picks):
                status = "planned" if p["window_end"] > now else "missed"
                c.execute("""INSERT OR IGNORE INTO slots(channel_id,day,idx,due_at,window_end,market,status,updated_at)
                             VALUES(?,?,?,?,?,?,?,?)""",
                          (ch["id"], d.isoformat(), i, p["due"], p["window_end"], p["market"], status, now))
                created += 1
    return created


def replan_future(ch, now=None):
    """Plan changed: drop future planned slots and plan again."""
    now = now or time.time()
    db.run("DELETE FROM slots WHERE channel_id=? AND status='planned' AND due_at>?", (ch["id"], now))
    today = dt.datetime.fromtimestamp(now, dt.timezone.utc).date()
    for k in range(2):
        d = (today + dt.timedelta(days=k)).isoformat()
        left = db.one("SELECT COUNT(*) n FROM slots WHERE channel_id=? AND day=?", (ch["id"], d))["n"]
        if left == 0:
            continue
        # keep what already happened today; add the remaining posts for the day
        plan = normalize_plan(db.jload(ch["plan_json"], {}))
        need = plan["posts_per_day"] - left
        if need > 0:
            picks = [p for p in plan_day(plan, dt.date.fromisoformat(d), _rng) if p["due"] > now + 300][:need]
            with db.tx() as c:
                base = c.execute("SELECT COALESCE(MAX(idx),-1)+1 FROM slots WHERE channel_id=? AND day=?",
                                 (ch["id"], d)).fetchone()[0]
                for i, p in enumerate(picks):
                    c.execute("""INSERT INTO slots(channel_id,day,idx,due_at,window_end,market,status,updated_at)
                                 VALUES(?,?,?,?,?,?,'planned',?)""",
                              (ch["id"], d, base + i, p["due"], p["window_end"], p["market"], now))
    ensure_slots(ch, now)


def tick(now=None):
    """Plan missing days and queue every slot that is due."""
    now = now or time.time()
    for ch in db.all("SELECT * FROM channels WHERE state IN ('live','attention')"):
        try:
            ensure_slots(ch, now)
        except Exception as e:  # one channel's bad plan must not stop the others
            db.event("warn", "plan", f"{ch['title']}: could not plan posts ({e})", ch["workspace_id"], ch["id"])
    due = db.all("""SELECT s.*, c.state, c.workspace_id FROM slots s JOIN channels c ON c.id=s.channel_id
                    WHERE s.status='planned' AND s.due_at<=?""", (now,))
    queued = 0
    for s in due:
        with db.tx() as c:
            if s["window_end"] < now:
                c.execute("UPDATE slots SET status='missed', note='Server was offline during the peak window', updated_at=? WHERE id=?",
                          (now, s["id"]))
                continue
            if s["state"] not in ("live", "attention"):
                c.execute("UPDATE slots SET status='held', note='Channel paused', updated_at=? WHERE id=?", (now, s["id"]))
                continue
            c.execute("UPDATE slots SET status='queued', updated_at=? WHERE id=? AND status='planned'", (now, s["id"]))
            c.execute("INSERT INTO jobs(channel_id,slot_id,created_at) VALUES(?,?,?)", (s["channel_id"], s["id"], now))
            queued += 1
    return queued


def post_now(ch, now=None):
    """Bring today's next planned slot forward to now. Never adds an extra post."""
    now = now or time.time()
    ensure_slots(ch, now)
    s = db.one("""SELECT * FROM slots WHERE channel_id=? AND status='planned' AND due_at>? ORDER BY due_at LIMIT 1""",
               (ch["id"], now))
    if not s or s["due_at"] - now > 26 * 3600:
        return None
    with db.tx() as c:
        c.execute("UPDATE slots SET status='queued', due_at=?, window_end=?, note='Posted now by you', updated_at=? WHERE id=?",
                  (now, max(s["window_end"], now + 3600), now, s["id"]))
        c.execute("INSERT INTO jobs(channel_id,slot_id,created_at) VALUES(?,?,?)", (ch["id"], s["id"], now))
    return s["id"]
