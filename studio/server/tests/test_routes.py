"""v7: YouTube/TikTok sources, routes without YouTube, Instagram destination, loop guard,
Facebook photos, IG home-page check, public media links. Network is faked; ffmpeg is real."""
import json
import os
import shutil
import tempfile
import time
import unittest
from unittest import mock

from drivecast import api, cleaner, config, db, igpost, igsync, instagram, security, store, vidsync, worker
from drivecast.errors import CodedError
from drivecast.net import Uncertain

from test_core import FakeDrive, adapters, fresh_db, job_for, make_world
from test_instagram import FakeDriveAPI, make_video

HAVE_FFMPEG = cleaner.available()


class FakeFB:
    def __init__(self):
        self.videos, self.photos = [], []

    def publish_video(self, s, page_id, tok, path, title, desc, on_progress=None):
        self.videos.append((page_id, os.path.getsize(path)))
        return {"video_id": "FBV1", "post_id": None}

    def publish_photos(self, s, page_id, tok, paths, msg):
        self.photos.append((page_id, len(paths)))
        return {"post_id": "FBP1"}


class FakeIGPost:
    def __init__(self, fail=None):
        self.posts, self.fail = [], fail

    def publish(self, s, dest, token, items, caption, on_progress=None):
        if self.fail:
            raise self.fail
        self.posts.append((dest["username"], [k for _, k in items], token))
        return {"post_id": f"IG{len(self.posts)}"}

    def refresh(self, s, tok):
        return tok + "+", time.time() + 60 * 86400


def route_world():
    """A client WITHOUT YouTube: Facebook page + Instagram destination on Group 1."""
    ws, g, ch = make_world()
    now = time.time()
    db.run("UPDATE channels SET platform='route', external_id='route-x', token_enc=NULL, youtube_on=0 WHERE id=?", (ch,))
    acc = db.run("INSERT INTO fb_accounts(workspace_id,group_id,name,token_enc,created_at) VALUES(?,?,?,?,?)",
                 (ws, g, "Naimat", security.seal("UT"), now))
    db.run("""INSERT INTO fb_pages(workspace_id,fb_account_id,page_id,name,token_enc,channel_id,created_at)
              VALUES(?,?,?,?,?,?,?)""", (ws, acc, "PAGE_A", "Art by Zara", security.seal("PT"), ch, now))
    db.run("UPDATE channels SET fb_page_id='PAGE_A' WHERE id=?", (ch,))
    d = db.run("""INSERT INTO ig_dest(workspace_id,group_id,via,ig_user_id,username,page_id,token_enc,channel_id,created_at)
                  VALUES(?,?,?,?,?,?,?,?,?)""", (ws, g, "facebook", "IGU1", "artbyzara", "PAGE_A", security.seal("PT"), ch, now))
    db.run("UPDATE channels SET ig_dest_id=? WHERE id=?", (d, ch))
    return ws, g, ch, d


class SchemaAndLinks(unittest.TestCase):
    def setUp(self):
        self.dir = fresh_db()

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_v7_media_origins(self):
        ws, g, ch = make_world()
        for origin in ("youtube", "tiktok"):
            db.run("""INSERT INTO media(workspace_id,channel_id,origin,ext_id,created_at) VALUES(?,?,?,?,?)""",
                   (ws, ch, origin, "x", time.time()))
        self.assertEqual(db.one("SELECT COUNT(*) n FROM media")["n"], 2)

    def test_parse_links(self):
        self.assertEqual(vidsync.parse_link("youtube", "https://m.youtube.com/@Ink.Epoch/shorts")[1], "@Ink.Epoch")
        self.assertEqual(vidsync.parse_link("tiktok", "https://www.tiktok.com/@zara_ink?lang=en")[0],
                         "https://www.tiktok.com/@zara_ink")
        with self.assertRaises(ValueError):
            vidsync.parse_link("youtube", "hello")

    def test_ig_home_page_check(self):
        class S:
            def __init__(self, status, text, loc=""):
                self.r = type("R", (), {"status_code": status, "text": text, "headers": {"Location": loc}})()
                import requests
                self.headers, self.cookies = {}, requests.cookies.RequestsCookieJar()

            def get(self, *a, **k):
                return self.r
        jar = {"sessionid": "S", "ds_user_id": "9", "csrftoken": "C"}
        ok = instagram.Client(S(200, '<script>"fb_dtsg":"x" "username":"tester.one"</script>'), jar, min_gap=(0, 0)).whoami()
        self.assertEqual(ok, {"username": "tester.one", "id": "9"})
        with self.assertRaises(CodedError) as cm:
            instagram.Client(S(302, "", "https://www.instagram.com/accounts/login/"), jar, min_gap=(0, 0)).whoami()
        self.assertEqual(cm.exception.code, "IG_COOKIES_EXPIRED")
        with self.assertRaises(CodedError) as cm:
            instagram.Client(S(200, '<form id="loginForm">'), jar, min_gap=(0, 0)).whoami()
        self.assertEqual(cm.exception.code, "IG_COOKIES_EXPIRED")
        ok2 = instagram.Client(S(200, '<script>{"viewerId":"9","username":"tester.two"}</script>'), jar, min_gap=(0, 0)).whoami()
        self.assertEqual(ok2["username"], "tester.two")
        with self.assertRaises(CodedError) as cm:  # unclear page: never called "expired", cookies get saved
            instagram.Client(S(200, '<html>new layout</html>'), jar, min_gap=(0, 0)).whoami()
        self.assertEqual(cm.exception.code, "IG_UNKNOWN")
        with self.assertRaises(CodedError) as cm:
            instagram.Client(S(302, "", "https://www.instagram.com/?hl=en"), jar, min_gap=(0, 0)).whoami()
        self.assertEqual(cm.exception.code, "IG_UNKNOWN")

    def test_caption_words_do_not_trip_rate_limit(self):
        r = type("R", (), {"status_code": 200, "text": '{"items":[{"caption":"please wait a few minutes for part 2"}]}',
                           "headers": {"Content-Type": "application/json"}})()
        r.content = r.text.encode()
        r.json = lambda: json.loads(r.text)
        self.assertIn("items", instagram.classify(r))

    def test_public_media_link(self):
        db.meta_set("public_url", "https://phone.example.ts.net")
        p = os.path.join(self.dir, "x.mp4")
        open(p, "wb").write(b"abc")
        url, tok = igpost.share(p)
        self.assertTrue(url.startswith("https://phone.example.ts.net/media/"))
        self.assertEqual(igpost.resolve_link(url.rsplit("/", 1)[1]), p)
        igpost.unshare(tok)
        self.assertIsNone(igpost.resolve_link(tok))
        db.meta_set("public_url", "")
        with self.assertRaises(CodedError):
            igpost.share(p)


class IGPublishTests(unittest.TestCase):
    """The container → wait → media_publish dance, against a fake Graph."""
    def setUp(self):
        self.dir = fresh_db()
        db.meta_set("public_url", "https://p.example.net")
        self.f = os.path.join(self.dir, "v.mp4")
        open(self.f, "wb").write(b"x")
        igpost.POLL_EVERY = 0

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def graph(self, publish_fail=False):
        calls = []

        class R:
            def __init__(s, p, st=200):
                s.status_code, s._p = st, p

            def json(s):
                return s._p

        class G:
            def get(s, url, params=None, **k):
                calls.append(("GET", url))
                return R({"status_code": "FINISHED"})

            def post(s, url, data=None, **k):
                calls.append(("POST", url, dict(data or {})))
                if url.endswith("/media_publish"):
                    if publish_fail:
                        raise OSError("connection reset")
                    return R({"id": "IGPOST1"})
                return R({"id": f"C{len(calls)}"})
        return G(), calls

    def test_reel_and_carousel(self):
        dest = {"via": "facebook", "ig_user_id": "IGU1", "username": "a"}
        s, calls = self.graph()
        out = igpost.publish(s, dest, "T", [(self.f, "video")], "Hello")
        self.assertEqual(out["post_id"], "IGPOST1")
        first = calls[0][2]
        self.assertEqual(first["media_type"], "REELS")
        self.assertTrue(first["video_url"].startswith("https://p.example.net/media/"))
        self.assertIn("graph.facebook.com", calls[0][1])
        s, calls = self.graph()
        igpost.publish(s, {**dest, "via": "instagram"}, "T", [(self.f, "photo"), (self.f, "photo")], "Two")
        kinds = [c[2].get("media_type") for c in calls if c[0] == "POST"]
        self.assertIn("CAROUSEL", kinds)
        self.assertIn("graph.instagram.com", calls[0][1])
        self.assertEqual(igpost._links, {})  # links are gone after the post

    def test_lost_publish_answer_is_uncertain(self):
        s, _ = self.graph(publish_fail=True)
        with self.assertRaises(Uncertain):
            igpost.publish(s, {"via": "facebook", "ig_user_id": "1", "username": "a"}, "T", [(self.f, "video")], "x")


class RouteWorkerTests(unittest.TestCase):
    def setUp(self):
        self.dir = fresh_db()
        worker.CLEAN_BEFORE_PUBLISH = False
        self.ws, self.g, self.ch, self.dest = route_world()
        self.fb, self.ig = FakeFB(), FakeIGPost()

    def tearDown(self):
        worker.CLEAN_BEFORE_PUBLISH = True
        shutil.rmtree(self.dir, ignore_errors=True)

    def ad(self, files):
        a = adapters(FakeDrive(files), None)
        a.pop("platform", None)
        a["facebook"], a["igpost"] = self.fb, self.ig
        return a

    def test_no_youtube_posts_to_facebook_then_instagram(self):
        job, _ = job_for(self.ch)
        worker.run(job, self.ad([{"id": "f1", "name": "Studio night.mp4"}]))
        ups = {u["destination"]: u["status"] for u in db.all("SELECT * FROM uploads")}
        self.assertEqual(ups, {"facebook:PAGE_A": "published", f"instagram:{self.dest}": "published"})
        self.assertEqual(self.ig.posts[0][1], ["video"])

    def test_instagram_problem_stays_isolated(self):
        self.ig.fail = CodedError("IGD_TOKEN_EXPIRED", "dead")
        job, _ = job_for(self.ch)
        worker.run(job, self.ad([{"id": "f1", "name": "A.mp4"}]))
        self.assertEqual(db.one("SELECT status FROM uploads WHERE destination='facebook:PAGE_A'")["status"], "published")
        self.assertEqual(db.one("SELECT status FROM ig_dest")["status"], "needs_reconnect")
        self.assertIsNone(db.one("SELECT 1 FROM uploads WHERE destination LIKE 'instagram:%'"))

    def test_photo_post_goes_to_facebook_and_instagram(self):
        now = time.time()
        folder = os.path.join(self.dir, "ph")
        os.makedirs(folder)
        for i in (1, 2):
            open(os.path.join(folder, f"Sketch [DC-AAAAAA] {i}.jpg"), "wb").write(b"\xff\xd8\xff\xd9")
        db.run("""INSERT INTO media(workspace_id,channel_id,origin,ext_id,kind,lane,state,ref,title,local_path,drive_file_id,created_at)
                  VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""", (self.ws, self.ch, "instagram", "P9", "carousel", "fresh", "waiting_facebook",
                                                   "DC-AAAAAA", "Sketch", folder, "d1,d2", now))
        job, _ = job_for(self.ch)
        worker.run(job, self.ad([]))
        self.assertEqual(self.fb.photos, [("PAGE_A", 2)])
        self.assertEqual(self.ig.posts[0][1], ["photo", "photo"])
        self.assertEqual(db.one("SELECT state FROM media WHERE ext_id='P9'")["state"], "published")

    def test_loop_guard_never_posts_back_to_the_source_account(self):
        now = time.time()
        sid = db.run("""INSERT INTO sources(channel_id,kind,handle,started_at,created_at) VALUES(?,?,?,?,?)""",
                     (self.ch, "instagram", "ArtByZara", now, now))
        mid = db.run("""INSERT INTO media(workspace_id,channel_id,source_id,origin,ext_id,kind,lane,state,ref,created_at)
                        VALUES(?,?,?,?,?,?,?,?,?,?)""", (self.ws, self.ch, sid, "instagram", "R1", "video", "fresh", "in_drive", "DC-BBBBBB", now))
        job, _ = job_for(self.ch)
        worker.run(job, self.ad([{"id": "f1", "name": "Reel.mp4", "appProperties": {"dc_media": str(mid), "dc_ref": "DC-BBBBBB",
                                                                                     "dc_lane": "fresh"}}]))
        dests = [u["destination"] for u in db.all("SELECT destination FROM uploads")]
        self.assertEqual(dests, ["facebook:PAGE_A"])  # Instagram skipped: it came from that same account
        self.assertEqual(self.ig.posts, [])

    def test_instagram_login_token_is_refreshed_before_expiry(self):
        db.run("UPDATE ig_dest SET via='instagram', token_enc=?, token_expires=? WHERE id=?",
               (security.seal("OLD"), time.time() + 86400, self.dest))
        job, _ = job_for(self.ch)
        worker.run(job, self.ad([{"id": "f1", "name": "A.mp4"}]))
        self.assertEqual(self.ig.posts[0][2], "OLD+")
        self.assertGreater(db.one("SELECT token_expires FROM ig_dest")["token_expires"], time.time() + 50 * 86400)

    def test_no_destination_holds(self):
        db.run("UPDATE channels SET fb_page_id=NULL, ig_dest_id=NULL WHERE id=?", (self.ch,))
        job, sid = job_for(self.ch)
        worker.run(job, self.ad([{"id": "f1", "name": "A.mp4"}]))
        self.assertEqual(db.one("SELECT status FROM slots WHERE id=?", (sid,))["status"], "held")


@unittest.skipUnless(HAVE_FFMPEG, "ffmpeg not installed")
class VideoSourceTests(unittest.TestCase):
    def setUp(self):
        self.dir = fresh_db()
        config.DATA_DIR = self.dir
        config.MEDIA_DIR = os.path.join(self.dir, "Shared with my tool DriveCast")
        self.ws, self.g, self.ch = make_world()
        db.run("UPDATE channels SET plan_json=? WHERE id=?", (json.dumps({"posts_per_day": 2}), self.ch))
        now = time.time()
        self.now = now
        self.src = db.run("""INSERT INTO sources(channel_id,kind,handle,url,content_type,include_old,daily_cap,proxy_mode,started_at,
                             next_check,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                          (self.ch, "youtube", "@zara", "https://www.youtube.com/@zara", "videos", 1, 6, "phone", now - 3600, 0, now))
        db.run("UPDATE sources SET ig_user_id='UCzzzzzzzzzzzzzzzzzzzzzz' WHERE id=?", (self.src,))
        self.vids = tempfile.mkdtemp()
        make_video(os.path.join(self.vids, "v.mp4"), size="1080x1920")
        self.fd = FakeDriveAPI()
        vidsync._next.clear()
        feed = [{"id": "NEWVIDEO001", "taken_at": now - 60, "title": "New upload", "caption": "Fresh one"},
                {"id": "OLDVIDEO001", "taken_at": now - 5 * 86400, "title": "Old upload", "caption": ""}]

        def fake_extract(src, url, tmpdir, flat=True, **kw):
            return {"entries": [{"id": "OLDVIDEO002", "title": "Older"}]}

        def fake_dl(src, url, fmt, out_base, tmpdir, progress=None):
            p = out_base + ".mp4"
            shutil.copy(os.path.join(self.vids, "v.mp4"), p)
            return p, {"title": "New upload", "description": "Fresh one", "timestamp": now - 60}

        self.p = [mock.patch.object(vidsync, "rss_latest", lambda cid, session=None: list(feed)),
                  mock.patch.object(vidsync, "_extract", fake_extract),
                  mock.patch.object(vidsync, "_download_one", fake_dl),
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

    def passes(self, n):
        out = []
        for _ in range(n):
            vidsync._next.clear()
            out.append(vidsync.source_pass(db.one("SELECT * FROM sources WHERE id=?", (self.src,))))
        return out

    def test_found_videos_do_not_count_as_ready(self):
        """15 videos only *found* on the profile must not stop the downloads (the TikTok bug)."""
        self.passes(1)  # first check
        for i in range(15):
            vidsync._insert(db.one("SELECT * FROM sources WHERE id=?", (self.src,)), store.channel(self.ch), f"X{i:08d}A", "backfill", self.now - 9 * 86400 - i)
        self.assertEqual(igsync.ready_count(self.ch), 0)
        steps = self.passes(6)
        self.assertTrue({"fresh", "backfill"} & set(steps), steps)
        self.assertTrue(self.fd.uploaded)

    def test_fast_pass_ignores_daily_cap_and_waits(self):
        db.run("UPDATE sources SET day_count=99, day=? WHERE id=?", (igsync._today(), self.src))
        src = lambda: db.one("SELECT * FROM sources WHERE id=?", (self.src,))  # noqa: E731
        self.assertEqual(vidsync.source_pass(src(), fast=True), "check")
        vidsync._next[self.src] = time.time() + 999  # slow mode would wait; fast does not
        out = [vidsync.source_pass(src(), fast=True) for _ in range(3)]
        self.assertNotIn("waiting", out)
        self.assertTrue(self.fd.uploaded)

    def test_only_new_still_starts_with_the_latest_video(self):
        db.run("UPDATE sources SET include_old=0, started_at=? WHERE id=?", (self.now + 10, self.src))
        self.passes(2)
        top = db.one("SELECT * FROM media WHERE ext_id='NEWVIDEO001'")
        self.assertEqual(top["lane"], "fresh")
        self.assertEqual(top["state"], "in_drive")

    def test_fresh_first_then_buffer_then_ledger(self):
        steps = self.passes(4)
        self.assertEqual(steps[:2], ["check", "fresh"])
        led = {m["ext_id"]: m for m in db.all("SELECT * FROM media")}
        self.assertEqual(led["NEWVIDEO001"]["state"], "in_drive")
        self.assertEqual(led["NEWVIDEO001"]["origin"], "youtube")
        self.assertEqual(led["NEWVIDEO001"]["lane"], "fresh")
        self.assertIn(steps[2], ("backfill", "discover"))
        f = list(self.fd.uploaded.values())[0]
        self.assertEqual(f["appProperties"]["dc_src"], "youtube")
        # same video again is never added twice
        vidsync._insert(db.one("SELECT * FROM sources"), store.channel(self.ch), "NEWVIDEO001", "fresh", time.time())
        self.assertEqual(db.one("SELECT COUNT(*) n FROM media WHERE ext_id='NEWVIDEO001'")["n"], 1)

    def test_bot_check_holds_only_this_source(self):
        def boom(*a, **k):
            raise vidsync._classify(Exception("Sign in to confirm you're not a bot"), db.one("SELECT * FROM sources"))
        with mock.patch.object(vidsync, "rss_latest", boom):
            self.assertEqual(self.passes(1), ["error"])
        s = db.one("SELECT * FROM sources")
        self.assertEqual(s["error_code"], "VID_BOT_CHECK")
        self.assertGreater(s["next_check"], time.time() + 3 * 3600)

    def test_paste_links(self):
        n = vidsync.add_links(db.one("SELECT * FROM sources"), store.channel(self.ch),
                              ["https://youtu.be/abcdefghijk", "https://www.youtube.com/shorts/ABCDEFGHIJK", "junk"])
        self.assertEqual(n, 2)


class ApiRouteTests(unittest.TestCase):
    def setUp(self):
        self.dir = fresh_db()
        self.ws, self.g, self.ch = make_world()

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def req(self, body=None):
        return type("R", (), {"ws": self.ws, "user": {"id": 1, "role": "owner"}, "json": body or {}, "query": {}})()

    def test_route_channel_with_youtube_and_tiktok_sources(self):
        out = api.new_route_channel(self.req({"title": "Ink Epoch", "group_id": self.g}))
        cid = out["channel"]["id"]
        self.assertFalse(out["channel"]["youtube_on"])
        db.run("UPDATE workspaces SET drive_root_id='ROOT' WHERE id=?", (self.ws,))
        with mock.patch.object(api.drive, "child_folder", lambda s, b, p, n: f"{p}/{n}"):
            api.set_route(self.req({"group_id": self.g, "start": False,
                                    "sources": [{"kind": "youtube", "link": "https://www.youtube.com/@inkepoch"},
                                                {"kind": "tiktok", "link": "@ink.epoch"}]}), str(cid))
        kinds = sorted(s["kind"] for s in db.all("SELECT kind FROM sources WHERE channel_id=? AND state='active'", (cid,)))
        self.assertEqual(kinds, ["tiktok", "youtube"])
        self.assertEqual(db.one("SELECT proxy_mode FROM sources WHERE kind='tiktok'")["proxy_mode"], "phone")
        d = api.channel_detail(self.req(), str(cid))
        self.assertEqual(len(d["sources"]), 2)
        self.assertEqual(d["destinations"], [])


if __name__ == "__main__":
    unittest.main()


class OwnDomainTests(unittest.TestCase):
    def setUp(self):
        self.dir = fresh_db()

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_own_domain_and_media_links_stay_on_tailscale(self):
        r = type("R", (), {"json": {"url": "https://s1.drivecast.site"}})()
        api.local_own_domain(r)
        self.assertEqual(db.meta_get("public_url_override"), "https://s1.drivecast.site")
        db.meta_set("public_url", "https://drivecast-2.tail1.ts.net")
        p = os.path.join(self.dir, "v.mp4")
        open(p, "wb").write(b"x")
        url, _ = igpost.share(p)
        self.assertTrue(url.startswith("https://drivecast-2.tail1.ts.net/media/"))
        with self.assertRaises(api.HttpError):
            api.local_own_domain(type("R", (), {"json": {"url": "http://bad"}})())

    def test_public_site_pages(self):
        from drivecast import web
        db.meta_set("public_url_override", "https://s1.drivecast.site")
        h = type("H", (), {})()
        for host, path, want in (("drivecast.site", "/", "/site/index.html"), ("www.drivecast.site", "/", "/site/index.html"),
                                 ("s1.drivecast.site", "/", None), ("s1.drivecast.site", "/privacy", "/site/privacy.html"),
                                 ("anything.ts.net", "/terms", "/site/terms.html")):
            h.headers = {"Host": host}
            self.assertEqual(web.Handler._site_page(h, path), want, (host, path))
        for f in ("index.html", "privacy.html", "terms.html", "site.css"):
            self.assertTrue(os.path.isfile(os.path.join(web.WEB_DIR, "site", f)))


class FleetTests(unittest.TestCase):
    def setUp(self):
        self.dir = fresh_db()
        from drivecast import fleet
        self.f = fleet
        db.meta_set("public_url_override", "https://s1.drivecast.site")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def R(self, body, headers=None, raw=None):
        return type("R", (), {"json": body, "headers": headers or {}, "raw": raw, "secure": True, "user": None})()

    def test_hub_allocates_and_finds_accounts(self):
        f = self.f
        f.make_hub()
        db.meta_set("open_signup", "1")
        f.hello()
        now = time.time()
        f.apply("/fleet/hello", {"host": "s2.drivecast.site", "label": "S2", "users": 1, "max_users": 20, "open_signup": True})
        f.apply("/fleet/hello", {"host": "s3.drivecast.site", "label": "S3", "users": 0, "max_users": 20, "open_signup": False})
        self.assertEqual(f.pick_for_signup()[0], "s1.drivecast.site")  # emptiest open phone (0 users)
        f.apply("/fleet/dir", {"host": "s3.drivecast.site", "kind": "invite", "hash": f.h("DC-AAAA-BBBB")})
        out = api.site_join(self.R({"invite": "dc-aaaa-bbbb"}))
        self.assertEqual(out["url"], "https://s3.drivecast.site/#/register?invite=DC-AAAA-BBBB")
        f.apply("/fleet/dir", {"host": "s2.drivecast.site", "kind": "email", "hash": f.h("Zara@Example.com")})
        self.assertTrue(api.site_where(self.R({"email": "zara@example.com"}))["url"].startswith("https://s2.drivecast.site/#/login"))
        with self.assertRaises(api.HttpError):
            api.site_where(self.R({"email": "nobody@example.com"}))
        live = api.site_fleet(self.R({}))
        self.assertEqual(len(live["devices"]), 3)
        self.assertTrue(live["open"])

    def test_signature_and_open_signup(self):
        f = self.f
        f.make_hub()
        body = json.dumps({"host": "s9.drivecast.site", "label": "S9"}).encode()
        with self.assertRaises(api.HttpError):
            api.fleet_hello(self.R(json.loads(body), {"X-DC-Fleet": "bad"}, body))
        api.fleet_hello(self.R(json.loads(body), {"X-DC-Fleet": f.sign(body)}, body))
        self.assertTrue(db.one("SELECT 1 FROM fleet_devices WHERE host='s9.drivecast.site'"))
        db.meta_set("signup_mode", "invite")
        with self.assertRaises(api.HttpError):  # invite-only when chosen
            api.register(self.R({"name": "A", "email": "a@x.com", "password": "password1"}))
        db.meta_set("open_signup", "1")
        db.meta_set("max_users", "1")
        req = self.R({"name": "A", "email": "a@x.com", "password": "password1"})
        req.set_cookie = None
        with mock.patch.object(api, "_start_session", lambda r, u: {"user": u["email"]}):
            self.assertEqual(api.register(req)["user"], "a@x.com")
            with self.assertRaises(api.HttpError):  # the limit is reached
                api.register(self.R({"name": "B", "email": "b@x.com", "password": "password1"}))
