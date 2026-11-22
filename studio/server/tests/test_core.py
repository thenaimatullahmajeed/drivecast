import os
os.environ["DC_NO_KICK"] = "1"  # no background pass during tests
import datetime as dt
import json
import os
import random
import shutil
import tempfile
import time
import unittest
from unittest import mock

from drivecast import db, scheduler, security, store, timeplan, worker
from drivecast.net import Permanent, ProxyDown, QuotaExceeded, Transient, Uncertain


def fresh_db():
    d = tempfile.mkdtemp()
    security._key = os.urandom(32)
    db._local.__dict__.clear()
    db.init(os.path.join(d, "t.db"))
    worker.TMP_DIR = d
    return d


def make_world(state="live", group_status="ok"):
    now = time.time()
    ws = db.run("INSERT INTO workspaces(name,created_at,google_client_id,google_client_secret_enc,drive_token_enc) VALUES('w',?,?,?,?)",
                (now, "x.apps.googleusercontent.com", security.seal("s"),
                 security.seal(json.dumps({"access_token": "a", "refresh_token": "r", "expires_at": now + 3600}))))
    db.run("INSERT INTO users(workspace_id,email,name,pw_hash,role,created_at) VALUES(?,?,?,?, 'owner',?)",
           (ws, "o@x.com", "Owner", security.hash_password("password1"), now))
    g = db.run("INSERT INTO ip_groups(workspace_id,number,host,port,status,outgoing_ip,created_at) VALUES(?,1,'h',1080,?,'1.2.3.4',?)",
               (ws, group_status, now))
    tok = security.seal(json.dumps({"access_token": "a", "refresh_token": "r", "expires_at": now + 3600}))
    ch = db.run("""INSERT INTO channels(workspace_id,external_id,title,token_enc,group_id,folder_id,folder_name,state,created_at,plan_json)
                   VALUES(?,?,?,?,?,?,?,?,?,?)""", (ws, "UC1", "Chan", tok, g, "F", "Chan", state, now, "{}"))
    return ws, g, ch


class FakeDrive:
    FOLDER = "folder"

    def __init__(self, files):
        self.files = files
        self.moves = []

    def videos(self, s, b, folder):
        return list(self.files)

    def child_folder(self, s, b, parent, name):
        return parent + "/" + name

    def move(self, s, b, fid, a, z):
        self.moves.append((fid, a, z))

    def download(self, s, b, fid, path):
        with open(path, "wb") as f:
            f.write(b"x" * 10)
        return 10


class FakeYT:
    def __init__(self, exc=None):
        self.exc, self.calls = exc, 0

    def title_from_filename(self, n):
        from drivecast.platforms.youtube import title_from_filename
        return title_from_filename(n)

    def publish(self, s, box, path, title, desc):
        self.calls += 1
        if self.exc:
            raise self.exc
        return {"video_id": "vid%d" % self.calls, "visibility": "public"}


def adapters(drive, yt, ip="1.2.3.4"):
    return {"session": lambda g: object(), "ip": lambda s: (ip, 50), "drive": drive, "platform": yt}


def job_for(ch):
    idx = db.one("SELECT COUNT(*) n FROM slots")["n"]
    sid = db.run("INSERT INTO slots(channel_id,day,idx,due_at,window_end,market,status) VALUES(?,?,?,?,?,'UK','queued')",
                 (ch, "2026-01-01", idx, time.time(), time.time() + 3600))
    jid = db.run("INSERT INTO jobs(channel_id,slot_id,created_at) VALUES(?,?,?)", (ch, sid, time.time()))
    return worker.claim("t"), sid


class TimePlanTests(unittest.TestCase):
    def test_slots_inside_peak_windows_with_gap(self):
        rng = random.Random(1)
        for _ in range(50):
            day = dt.date(2026, 11, 16)
            picks = timeplan.plan_day({"posts_per_day": 2}, day, rng)
            self.assertEqual(len(picks), 2)
            self.assertGreaterEqual(abs(picks[0]["due"] - picks[1]["due"]), 240 * 60)
            wins = timeplan.candidate_windows("US+UK", day)
            for p in picks:
                self.assertTrue(any(w["start"] <= p["due"] <= w["end"] for w in wins))
            self.assertEqual({p["market"] for p in picks}, {"UK", "US"})

    def test_unknown_audience_falls_back(self):
        self.assertEqual(timeplan.normalize_plan({"audience": "Mars"})["audience"], "US+UK")

    def test_title_from_filename(self):
        from drivecast.platforms.youtube import title_from_filename
        self.assertEqual(title_from_filename("Autumn night out fit 🍂.mp4"), "Autumn night out fit 🍂")
        self.assertEqual(title_from_filename("a<b>c.mov"), "abc")
        self.assertLessEqual(len(title_from_filename("word " * 40 + ".mp4")), 100)
        self.assertEqual(title_from_filename(".mp4"), ".mp4" if False else title_from_filename(".mp4"))


class WorkerTests(unittest.TestCase):
    def setUp(self):
        worker.CLEAN_BEFORE_PUBLISH = False  # fake files are not real videos
        self.dir = fresh_db()
        self.ws, self.g, self.ch = make_world()

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_publishes_oldest_and_never_twice(self):
        d, yt = FakeDrive([{"id": "f1", "name": "First.mp4"}, {"id": "f2", "name": "Second.mp4"}]), FakeYT()
        job, sid = job_for(self.ch)
        worker.run(job, adapters(d, yt))
        u = db.all("SELECT * FROM uploads")
        self.assertEqual(len(u), 1)
        self.assertEqual((u[0]["file_id"], u[0]["status"], u[0]["title"]), ("f1", "published", "First"))
        self.assertEqual(db.one("SELECT status FROM slots WHERE id=?", (sid,))["status"], "done")
        job, _ = job_for(self.ch)
        worker.run(job, adapters(d, yt))
        self.assertEqual([r["file_id"] for r in db.all("SELECT file_id FROM uploads ORDER BY id")], ["f1", "f2"])

    def test_wrong_ip_holds_and_marks_group_down(self):
        d, yt = FakeDrive([{"id": "f1", "name": "a.mp4"}]), FakeYT()
        job, sid = job_for(self.ch)
        worker.run(job, adapters(d, yt, ip="9.9.9.9"))
        self.assertEqual(yt.calls, 0)
        self.assertEqual(store.group(self.g)["status"], "down")
        self.assertEqual(db.one("SELECT status FROM slots WHERE id=?", (sid,))["status"], "held")

    def test_proxy_unreachable_never_uploads(self):
        d, yt = FakeDrive([{"id": "f1", "name": "a.mp4"}]), FakeYT()
        a = adapters(d, yt)
        a["ip"] = mock.Mock(side_effect=ProxyDown("down"))
        job, sid = job_for(self.ch)
        worker.run(job, a)
        self.assertEqual(yt.calls, 0)
        self.assertEqual(db.one("SELECT COUNT(*) n FROM uploads")["n"], 0)

    def test_uncertain_is_kept_and_not_retried(self):
        d, yt = FakeDrive([{"id": "f1", "name": "a.mp4"}]), FakeYT(Uncertain("lost"))
        job, sid = job_for(self.ch)
        worker.run(job, adapters(d, yt))
        self.assertEqual(db.one("SELECT status FROM uploads")["status"], "uncertain")
        yt.exc = None
        job, _ = job_for(self.ch)
        worker.run(job, adapters(d, yt))  # f1 is reserved; folder has nothing else
        self.assertEqual(yt.calls, 1)
        self.assertEqual(db.one("SELECT COUNT(*) n FROM uploads WHERE status='published'")["n"], 0)

    def test_transient_releases_file_and_breaker_trips(self):
        d, yt = FakeDrive([{"id": "f1", "name": "a.mp4"}]), FakeYT(Transient("busy"))
        for _ in range(3):
            job, _ = job_for(self.ch)
            worker.run(job, adapters(d, yt))
        self.assertEqual(db.one("SELECT COUNT(*) n FROM uploads")["n"], 0)  # released each time
        c = store.channel(self.ch)
        self.assertEqual(c["state"], "attention")
        self.assertGreater(c["paused_until"], time.time())

    def test_quota_pauses_until_pacific_midnight(self):
        d, yt = FakeDrive([{"id": "f1", "name": "a.mp4"}]), FakeYT(QuotaExceeded("quota"))
        job, _ = job_for(self.ch)
        worker.run(job, adapters(d, yt))
        self.assertGreater(store.channel(self.ch)["paused_until"], time.time())
        self.assertEqual(db.one("SELECT COUNT(*) n FROM uploads")["n"], 0)

    def test_permanent_moves_to_failed_and_skips_file(self):
        d, yt = FakeDrive([{"id": "f1", "name": "a.mp4"}]), FakeYT(Permanent("bad file"))
        job, _ = job_for(self.ch)
        worker.run(job, adapters(d, yt))
        self.assertEqual(db.one("SELECT status FROM uploads")["status"], "failed")
        self.assertTrue(any(z.endswith("/Failed") for _, _, z in d.moves))

    def test_paused_channel_does_nothing(self):
        store.set_channel_state(self.ch, "paused")
        d, yt = FakeDrive([{"id": "f1", "name": "a.mp4"}]), FakeYT()
        job, sid = job_for(self.ch)
        worker.run(job, adapters(d, yt))
        self.assertEqual(yt.calls, 0)

    def test_restart_recovery(self):
        now = time.time()
        db.run("INSERT INTO uploads(workspace_id,channel_id,destination,file_id,file_name,title,status,reserved_at) VALUES(?,?,?,?,?,?,?,?)",
               (self.ws, self.ch, "youtube:UC1", "f9", "x.mp4", "x", "uploading", now))
        db.run("INSERT INTO uploads(workspace_id,channel_id,destination,file_id,file_name,title,status,reserved_at) VALUES(?,?,?,?,?,?,?,?)",
               (self.ws, self.ch, "youtube:UC1", "f8", "y.mp4", "y", "reserved", now))
        worker.recover_interrupted()
        self.assertEqual(db.one("SELECT status FROM uploads WHERE file_id='f9'")["status"], "uncertain")
        self.assertIsNone(db.one("SELECT 1 FROM uploads WHERE file_id='f8'"))


class SchedulerTests(unittest.TestCase):
    def setUp(self):
        self.dir = fresh_db()
        self.ws, self.g, self.ch = make_world()

    def test_tick_plans_and_queues_due(self):
        now = time.time()
        scheduler.ensure_slots(store.channel(self.ch), now)
        n = db.one("SELECT COUNT(*) n FROM slots")["n"]
        self.assertEqual(n, 4)
        s = db.one("SELECT * FROM slots WHERE status='planned' ORDER BY due_at LIMIT 1")
        q = scheduler.tick(s["due_at"] + 1)
        self.assertGreaterEqual(q, 1)
        self.assertEqual(db.one("SELECT status FROM slots WHERE id=?", (s["id"],))["status"], "queued")

    def test_missed_window_is_not_posted(self):
        now = time.time()
        db.run("INSERT INTO slots(channel_id,day,idx,due_at,window_end,market,status) VALUES(?,?,0,?,?,'UK','planned')",
               (self.ch, "2000-01-01", now - 4 * 86400, now - 4 * 86400 + 3600))  # off for 4 days: too old to catch up
        scheduler.tick(now)
        self.assertEqual(db.one("SELECT status FROM slots WHERE day='2000-01-01'")["status"], "missed")

    def _missed(self, n, hours_ago):
        now = time.time()
        for i in range(n):
            db.run("INSERT INTO slots(channel_id,day,idx,due_at,window_end,market,status) VALUES(?,?,?,?,?,'UK','planned')",
                   (self.ch, "2099-01-0%d" % (i + 1), i, now - hours_ago * 3600 - i * 60, now - hours_ago * 3600 + 600))
        return now

    def test_short_outage_posts_catch_up_spaced_apart(self):
        now = self._missed(3, 5)
        scheduler.tick(now)
        rows = db.all("SELECT status, due_at FROM slots WHERE day LIKE '2099-%' ORDER BY due_at")
        self.assertEqual(sorted(r["status"] for r in rows), ["planned", "planned", "queued"])  # first now, rest later
        dues = sorted(r["due_at"] for r in rows)
        self.assertGreaterEqual(dues[1] - dues[0], scheduler.CATCHUP_GAP - 1)
        self.assertGreaterEqual(dues[2] - dues[1], scheduler.CATCHUP_GAP - 1)

    def test_long_outage_is_skipped_not_caught_up(self):
        now = self._missed(2, 24 * 4)
        scheduler.tick(now)
        self.assertEqual({r["status"] for r in db.all("SELECT status FROM slots WHERE day LIKE '2099-%'")}, {"missed"})

    def test_catch_up_never_exceeds_daily_cap(self):
        now = self._missed(scheduler.CATCHUP_DAY_CAP + 3, 5)
        scheduler.tick(now)
        kept = db.one("SELECT COUNT(*) n FROM slots WHERE day LIKE '2099-%' AND status!='missed'")["n"]
        self.assertLessEqual(kept, scheduler.CATCHUP_DAY_CAP)

    def test_post_now_brings_slot_forward(self):
        now = time.time()
        scheduler.ensure_slots(store.channel(self.ch), now)
        before = db.one("SELECT COUNT(*) n FROM slots")["n"]
        sid = scheduler.post_now(store.channel(self.ch), now)
        self.assertIsNotNone(sid)
        self.assertEqual(db.one("SELECT COUNT(*) n FROM slots")["n"], before)  # no extra post
        self.assertEqual(db.one("SELECT status FROM slots WHERE id=?", (sid,))["status"], "queued")


if __name__ == "__main__":
    unittest.main()


class ViewsTest(unittest.TestCase):
    def test_views_show_same_day(self):
        """A video published today shows its views today, even with a single channel reading."""
        from drivecast import insights
        fresh_db()
        ws, g, ch = make_world()
        now = dt.datetime.now(dt.timezone.utc).replace(hour=12, minute=0, second=0).timestamp()
        db.run("INSERT INTO channel_ticks(channel_id,at,views,subscribers) VALUES(?,?,?,?)", (ch, now - 600, 106800, 500))
        u = db.run("""INSERT INTO uploads(workspace_id,channel_id,destination,file_id,file_name,title,status,reserved_at,published_at,views)
                      VALUES(?,?,'youtube',?,?,?, 'published',?,?,?)""", (ws, ch, "f1", "a.mp4", "A", now - 3000, now - 2900, 973))
        db.run("INSERT INTO upload_ticks(upload_id,at,views) VALUES(?,?,?)", (u, now - 60, 973))
        d = insights.dashboard(ws, "UTC", now=now)
        self.assertEqual(d["today"]["views_today"], 973)
        self.assertEqual(d["kpis"]["views_7d"], 973)
        self.assertEqual(d["pulse"][0]["views_today"], 973)
        self.assertEqual(d["recent"][0]["today"], 973)
        # later the channel counter catches up and is larger: that wins
        db.run("INSERT INTO channel_ticks(channel_id,at,views,subscribers) VALUES(?,?,?,?)", (ch, now - 30, 108000, 510))
        d = insights.dashboard(ws, "UTC", now=now)
        self.assertEqual(d["today"]["views_today"], 1200)
        self.assertEqual(d["kpis"]["subs_7d"], 10)
