"""Instagram source pipeline: discovery, priorities, cleaning, Drive, worker, Encore, error scopes.
Instagram and Google are faked; ffmpeg cleaning is real."""
import json
import os
import shutil
import subprocess
import tempfile
import time
import unittest
from unittest import mock

from drivecast import cleaner, config, db, igsync, instagram, security, store, titles, worker
from drivecast.errors import CodedError

from test_core import FakeYT, adapters, fresh_db, job_for, make_world

HAVE_FFMPEG = cleaner.available()


def make_video(path, size="720x1280", audio=True, secs=4):
    args = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i", f"testsrc=size={size}:rate=25"]
    if audio:
        args += ["-f", "lavfi", "-i", "sine=frequency=300"]
    args += ["-t", str(secs), "-c:v", "libx264"]
    if audio:
        args += ["-c:a", "aac"]
    args += ["-metadata", "location=+40.7128-074.0060/", "-metadata", "comment=Instagram", path]
    subprocess.run(args, check=True)


class FakeIG:
    def __init__(self, items, videos_dir, fail=None):
        self.items, self.dir, self.fail, self.calls = items, videos_dir, fail, []

    def _maybe_fail(self):
        if self.fail:
            raise self.fail

    def profile(self, handle):
        self.calls.append("profile")
        self._maybe_fail()
        return {"id": "42", "username": handle, "full_name": "Ink", "picture": None, "is_private": False, "followed": False, "posts": 3}

    def feed(self, uid, max_id=None, handle=None):
        self.calls.append(("feed", max_id))
        self._maybe_fail()
        if max_id == "p2":
            return [self.items[-1]], None
        return self.items[:-1], "p2"

    def media(self, pk, handle=None):
        self.calls.append(("media", pk))
        self._maybe_fail()
        return next(i for i in self.items if i["pk"] == pk)

    def download(self, url, path, on_progress=None):
        shutil.copyfile(os.path.join(self.dir, url), path)
        return os.path.getsize(path)


class FakeDriveAPI:
    def __init__(self):
        self.uploaded = {}
        self.n = 0

    def upload(self, s, b, path, name, parent, mime="video/mp4", app_props=None, on_progress=None):
        self.n += 1
        fid = f"d{self.n}"
        self.uploaded[fid] = {"id": fid, "name": name, "parent": parent, "appProperties": app_props or {},
                              "createdTime": f"2026-01-01T00:00:{self.n:02d}Z", "size": os.path.getsize(path)}
        return fid

    def child_folder(self, s, b, parent, name):
        return parent + "/" + name

    def videos(self, s, b, folder):
        return [f for f in self.uploaded.values() if f["parent"] == folder]

    def move(self, s, b, fid, a, z):
        self.uploaded[fid]["parent"] = z

    def download(self, s, b, fid, path):
        raise AssertionError("should use the phone copy")


def item(pk, taken, caption, kind="video", url=None):
    return {"pk": pk, "code": "C" + pk, "taken_at": taken, "caption": caption, "kind": kind, "product": "clips",
            "duration": 4, "has_audio": True, "video": {"url": url or "ok.mp4", "width": 720, "height": 1280} if kind == "video" else None,
            "dash": None, "images": [] if kind == "video" else [{"url": "p.jpg", "width": 1080, "height": 1080}]}


class TitleTests(unittest.TestCase):
    def test_rules(self):
        self.assertEqual(titles.from_caption("Night shift in the studio 🖤\n#tattoo #ink @friend"), "Night shift in the studio")
        self.assertIsNone(titles.from_caption("#tattoo #ink"))
        for bad in ("Untitled.mp4", "IMG_1234.MOV", "VID-20240101-WA0001.mp4", "20240101_123000.mp4", "video (3).mp4"):
            self.assertIsNone(titles.from_filename(bad), bad)
        self.assertEqual(titles.from_filename("Cover up before and after (1).mp4"), "Cover up before and after")
        t = titles.choose(name="IMG_0001.mp4", caption="", channel_title="Ink Epoch", bank=["Fine line magic"], used=[], seed="a")
        self.assertEqual(t, "Fine line magic")
        t2 = titles.choose(name="IMG_0001.mp4", caption="", channel_title="Ink Epoch", bank=["Fine line magic"], used=["Fine line magic"], seed="a")
        self.assertTrue(t2.startswith("Ink Epoch — "))
        self.assertIn("#Shorts", titles.description("Nice one #ink", "Nice one"))


class InstagramParseTests(unittest.TestCase):
    def test_cookies_formats(self):
        net = ".instagram.com\tTRUE\t/\tTRUE\t0\tsessionid\tS\n.instagram.com\tTRUE\t/\tTRUE\t0\tds_user_id\t9\n.instagram.com\tTRUE\t/\tTRUE\t0\tcsrftoken\tC"
        self.assertEqual(instagram.parse_cookies(net)["sessionid"], "S")
        js = json.dumps([{"domain": ".instagram.com", "name": n, "value": v} for n, v in (("sessionid", "S"), ("ds_user_id", "9"), ("csrftoken", "C"))])
        self.assertEqual(instagram.parse_cookies(js)["ds_user_id"], "9")
        self.assertEqual(instagram.parse_cookies("sessionid=S; ds_user_id=9; csrftoken=C")["csrftoken"], "C")
        with self.assertRaises(ValueError):
            instagram.parse_cookies("sessionid=S")

    def test_handles(self):
        self.assertEqual(instagram.clean_handle("https://www.instagram.com/Ink.Epoch/?hl=en"), "ink.epoch")
        self.assertEqual(instagram.clean_handle("@inkepoch"), "inkepoch")
        with self.assertRaises(ValueError):
            instagram.clean_handle("not a handle!")

    def test_classify(self):
        class R:
            def __init__(self, code, text="{}", headers=None):
                self.status_code, self.text, self.content, self.headers = code, text, text.encode(), headers or {"Content-Type": "application/json"}

            def json(self):
                return json.loads(self.text)
        cases = [(R(302, "", {"Location": "https://www.instagram.com/accounts/login/"}), "IG_COOKIES_EXPIRED"),
                 (R(400, '{"message":"checkpoint_required","status":"fail"}'), "IG_CHALLENGE"),
                 (R(429, '{"message":"Please wait a few minutes","status":"fail"}'), "IG_RATE_LIMIT"),
                 (R(400, '{"message":"feedback_required","status":"fail"}'), "IG_FEEDBACK"),
                 (R(200, "<html>", {"Content-Type": "text/html"}), "IG_HOME_REDIRECT")]
        for r, code in cases:
            with self.assertRaises(CodedError) as cm:
                instagram.classify(r, "x")
            self.assertEqual(cm.exception.code, code)

    def test_dash_beats_progressive(self):
        mpd = ('<MPD><Period><AdaptationSet><Representation mimeType="video/mp4" width="1080" height="1920" bandwidth="3000">'
               '<BaseURL>v1080</BaseURL></Representation><Representation mimeType="video/mp4" width="720" height="1280" bandwidth="1000">'
               '<BaseURL>v720</BaseURL></Representation></AdaptationSet><AdaptationSet><Representation mimeType="audio/mp4" bandwidth="128">'
               '<BaseURL>a</BaseURL></Representation></AdaptationSet></Period></MPD>')
        it = instagram.parse_item({"pk": "1", "media_type": 2, "video_versions": [{"url": "p720", "width": 720, "height": 1280}],
                                   "video_dash_manifest": mpd})
        plan = instagram.best_video_plan(it)
        self.assertEqual((plan["mode"], plan["video"], plan["audio"]), ("dash", "v1080", "a"))


@unittest.skipUnless(HAVE_FFMPEG, "ffmpeg not installed")
class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.dir = fresh_db()
        config.DATA_DIR = self.dir
        config.MEDIA_DIR = os.path.join(self.dir, "Shared with my tool DriveCast")
        worker.CLEAN_BEFORE_PUBLISH = True
        self.ws, self.g, self.ch = make_world()
        db.run("UPDATE channels SET plan_json=? WHERE id=?", (json.dumps({"posts_per_day": 2}), self.ch))
        self.vids = tempfile.mkdtemp()
        make_video(os.path.join(self.vids, "ok.mp4"))
        make_video(os.path.join(self.vids, "low.mp4"), size="360x640")
        now = time.time()
        self.src = db.run("""INSERT INTO sources(channel_id,handle,content_type,include_old,daily_cap,started_at,next_check,created_at)
                             VALUES(?,?,?,?,?,?,?,?)""", (self.ch, "inkepoch", "videos", 1, 6, now - 3600, 0, now))
        self.acc = db.run("INSERT INTO ig_accounts(workspace_id,group_id,cookies_enc,created_at) VALUES(?,?,?,?)",
                          (self.ws, self.g, security.seal("sessionid=S; ds_user_id=9; csrftoken=C"), now))
        self.items = [item("100", now - 600, "Night shift in the studio 🖤 #ink"),           # fresh
                      item("90", now - 86400, ""),                                            # old, no caption
                      item("80", now - 2 * 86400, "Low res one", url="low.mp4"),              # old, too low
                      item("70", now - 3 * 86400, "A photo", kind="photo"),                   # photo: not wanted (videos only)
                      item("60", now - 4 * 86400, "Oldest reel")]                             # page 2
        self.fig = FakeIG(self.items, self.vids)
        self.fd = FakeDriveAPI()
        igsync._next_action.clear()
        self.p = [mock.patch.object(igsync, "client_for", lambda acc, g: self.fig),
                  mock.patch.object(igsync, "session_for", lambda g: object()),
                  mock.patch.object(igsync.drive, "upload", self.fd.upload),
                  mock.patch.object(igsync.drive, "child_folder", self.fd.child_folder)]
        for p in self.p:
            p.start()

    def tearDown(self):
        for p in self.p:
            p.stop()
        shutil.rmtree(self.dir, ignore_errors=True)
        shutil.rmtree(self.vids, ignore_errors=True)

    def run_passes(self, n):
        out = []
        for _ in range(n):
            igsync._next_action.clear()
            out.append(igsync.account_pass(db.one("SELECT * FROM ig_accounts")))
        return out

    def test_end_to_end(self):
        steps = self.run_passes(8)
        self.assertEqual(steps[0], "check")
        self.assertEqual(steps[1], "fresh")       # the new reel is downloaded before anything old
        led = {m["ext_id"]: m for m in db.all("SELECT * FROM media")}
        self.assertNotIn("70", led)               # photo skipped for a videos-only client
        self.assertEqual(led["100"]["lane"], "fresh")
        self.assertEqual(led["100"]["state"], "in_drive")
        self.assertEqual(led["100"]["title"], "Night shift in the studio")
        self.assertEqual(led["80"]["state"], "skipped")
        self.assertEqual(led["80"]["error_code"], "LOW_QUALITY")
        self.assertTrue(led["90"]["title"] and "Untitled" not in led["90"]["title"])
        self.assertIn("60", led)                  # page 2 discovered while the buffer was low
        # the phone folder exists, hidden from the Gallery, file cleaned + tagged
        self.assertTrue(os.path.exists(os.path.join(config.MEDIA_DIR, ".nomedia")))
        local = led["100"]["local_path"]
        data = open(local, "rb").read()
        self.assertNotIn(b"Instagram", data)
        self.assertNotIn(b"x264", data)
        self.assertIn(b"DriveCast Studio", data)
        # nothing is ever inserted twice
        before = db.one("SELECT COUNT(*) n FROM media")["n"]
        db.run("UPDATE sources SET next_check=0")
        self.run_passes(1)
        self.assertEqual(db.one("SELECT COUNT(*) n FROM media")["n"], before)

        # worker: fresh first, from the phone copy, real title + description
        yt = FakeYT()
        seen = {}
        yt.publish = lambda s, box, path, title, desc: (seen.update(title=title, desc=desc, size=os.path.getsize(path)) or
                                                       {"video_id": "v1", "visibility": "public"})
        job, _ = job_for(self.ch)
        worker.run(job, adapters(self.fd, yt))
        u = db.one("SELECT * FROM uploads")
        self.assertEqual(u["status"], "published")
        self.assertEqual(seen["title"], "Night shift in the studio")
        self.assertIn("#Shorts", seen["desc"])
        self.assertEqual(db.one("SELECT state FROM media WHERE ext_id='100'")["state"], "published")

    def test_cookie_expiry_stops_only_instagram(self):
        self.fig.fail = CodedError("IG_COOKIES_EXPIRED", "login_required")
        self.assertEqual(self.run_passes(1), ["check"])
        acc = db.one("SELECT * FROM ig_accounts")
        self.assertEqual(acc["status"], "needs_cookies")
        ev = db.one("SELECT * FROM events WHERE code='IG_COOKIES_EXPIRED'")
        self.assertIsNotNone(ev)
        self.assertEqual(self.run_passes(1), ["blocked"])
        self.assertEqual(store.channel(self.ch)["state"], "live")   # publishing is untouched

    def test_rate_limit_rests_and_resumes(self):
        self.fig.fail = CodedError("IG_RATE_LIMIT", "wait")
        self.run_passes(1)
        acc = db.one("SELECT * FROM ig_accounts")
        self.assertEqual(acc["status"], "resting")
        self.assertGreater(acc["paused_until"], time.time() + 1000)
        self.assertEqual(self.run_passes(1), ["waiting"])

    def test_private_profile_pauses_only_that_source(self):
        self.fig.profile = lambda h: {"id": "42", "username": h, "is_private": True, "followed": False, "full_name": "", "picture": None}
        self.run_passes(1)
        s = db.one("SELECT * FROM sources")
        self.assertEqual(s["error_code"], "IG_PROFILE_PRIVATE")
        self.assertEqual(db.one("SELECT status FROM ig_accounts")["status"], "ok")

    def test_local_copy_removed_after_post(self):
        self.run_passes(2)
        m = db.one("SELECT * FROM media WHERE ext_id='100'")
        db.run("UPDATE media SET state='published', published_at=? WHERE id=?", (time.time() - 7 * 3600, m["id"]))
        igsync.cleanup_local()
        self.assertFalse(os.path.exists(m["local_path"]))


class EncoreTests(unittest.TestCase):
    def setUp(self):
        self.dir = fresh_db()
        self.ws, self.g, self.ch = make_world()

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def add(self, fid, views, days_ago, encore_of=None, title="T"):
        t = time.time() - days_ago * 86400
        return db.run("""INSERT INTO uploads(workspace_id,channel_id,destination,file_id,file_name,title,status,reserved_at,published_at,views,encore_of)
                         VALUES(?,?,?,?,?,?,'published',?,?,?,?)""", (self.ws, self.ch, "youtube:UC1", fid, fid + ".mp4", title, t, t, views, encore_of))

    def test_ranking_cooldown_and_cap(self):
        ch = store.channel(self.ch)
        a = self.add("a", 5000, 90, title="Big one")
        self.add("b", 9000, 10)          # best, but inside the 60-day cooldown
        self.add("c", 100, 100)
        pick = worker.encore_pick(ch, "youtube:UC1", time.time())
        self.assertEqual(pick["file_id"], "a")
        self.assertEqual(pick["key"], "a#e1")
        self.assertNotEqual(pick["title"], "Big one")
        for n in range(3):
            self.add(f"a#e{n + 1}", 10, 70, encore_of=a)
        self.assertEqual(worker.encore_pick(ch, "youtube:UC1", time.time())["file_id"], "c")  # a reached max 3

    def test_one_encore_a_day(self):
        ch = store.channel(self.ch)
        a = self.add("a", 5000, 90)
        self.add("x#e1", 1, 0.1, encore_of=self.add("x", 1, 200))
        self.assertIsNone(worker.encore_pick(ch, "youtube:UC1", time.time()))

    def test_queue_order(self):
        files = [{"id": "back_old", "appProperties": {"dc_lane": "backfill", "dc_taken": "100"}, "createdTime": "1"},
                 {"id": "mine", "appProperties": {}, "createdTime": "3"},
                 {"id": "back_new", "appProperties": {"dc_lane": "backfill", "dc_taken": "200"}, "createdTime": "2"},
                 {"id": "fresh", "appProperties": {"dc_lane": "fresh"}, "createdTime": "9"}]
        self.assertEqual([f["id"] for f in worker.order_queue(files)], ["fresh", "mine", "back_new", "back_old"])


if __name__ == "__main__":
    unittest.main()
