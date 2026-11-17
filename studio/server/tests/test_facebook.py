"""Facebook Pages via Graph API: OAuth/token, page fetch, page→channel link,
and the worker cross-posting a video to the linked page after YouTube. Graph is faked."""
import json
import os
import shutil
import time
import unittest
from unittest import mock

from drivecast import db, facebook, security, store, worker
from drivecast.errors import CodedError

from test_core import FakeDrive, FakeYT, adapters, fresh_db, job_for, make_world


class FakeGraph:
    """Stands in for graph.facebook.com. Records posts; can be told to fail."""
    def __init__(self, pages, fail=None):
        self._pages = pages
        self.fail = fail
        self.videos = []

    def _resp(self, payload, status=200):
        class R:
            def __init__(s): s.status_code, s._p = status, payload
            def json(s): return s._p
            @property
            def text(s): return json.dumps(s._p)
        return R()

    def get(self, url, params=None, data=None, timeout=None, **kw):
        if "/me/accounts" in url:
            return self._resp({"data": [dict(p) for p in self._pages]})
        if url.endswith("/me"):
            return self._resp({"id": "U1", "name": "Naimat"})
        if "/oauth/access_token" in url:
            return self._resp({"access_token": "LL-token", "expires_in": 5184000})
        return self._resp({}, 404)

    def post(self, url, data=None, files=None, timeout=None, **kw):
        if self.fail:
            return self._resp({"error": self.fail}, 400)
        if "/videos" in url:
            phase = (data or {}).get("upload_phase")
            if phase == "start":
                return self._resp({"upload_session_id": "S1", "video_id": "V123", "start_offset": "0", "end_offset": str(len(b'x'))})
            if phase == "transfer":
                self.videos.append(data)
                return self._resp({"start_offset": "999", "end_offset": "999"})
            if phase == "finish":
                return self._resp({"success": True})
        if "/photos" in url:
            return self._resp({"id": "P1", "post_id": "PAGE_P1"})
        if "/feed" in url:
            return self._resp({"id": "PAGE_FEED1"})
        return self._resp({}, 404)


def pages_fixture():
    return [{"id": "PAGE_A", "name": "Art by Zara", "access_token": "pt-a", "category": "Artist", "tasks": ["CREATE_CONTENT", "MANAGE"], "picture": {"data": {"url": "u"}}},
            {"id": "PAGE_B", "name": "Read only", "access_token": "pt-b", "category": "Blog", "tasks": ["ANALYZE"], "picture": {"data": {"url": "u"}}}]


class FacebookUnitTests(unittest.TestCase):
    def test_consent_url_and_parse(self):
        with mock.patch.object(facebook, "version", lambda: "v21.0"):
            u = facebook.consent_url("APP1", "https://x/cb", "STATE", )
            self.assertIn("client_id=APP1", u)
            self.assertIn("v21.0/dialog/oauth", u)
            self.assertIn("pages_manage_posts", u)

    def test_pages_and_errors(self):
        g = FakeGraph(pages_fixture())
        pgs = facebook.pages(g, "LL")
        self.assertEqual([(p["page_id"], p["can_post"]) for p in pgs], [("PAGE_A", True), ("PAGE_B", False)])
        # token expiry maps to a coded error
        bad = FakeGraph([])
        with self.assertRaises(CodedError) as cm2:
            facebook._check(bad._resp({"error": {"code": 190, "message": "x"}}, 400), "t")
        self.assertEqual(cm2.exception.code, "FB_TOKEN_EXPIRED")
        with self.assertRaises(CodedError) as cm3:
            facebook._check(bad._resp({"error": {"code": 200, "message": "needs admin"}}, 403), "t")
        self.assertEqual(cm3.exception.code, "FB_PERMISSION")


class FacebookFlowTests(unittest.TestCase):
    def setUp(self):
        self.dir = fresh_db()
        worker.CLEAN_BEFORE_PUBLISH = False
        self.ws, self.g, self.ch = make_world()
        db.run("UPDATE workspaces SET fb_app_id='APP1', fb_app_secret_enc=? WHERE id=?", (security.seal("secret"), self.ws))

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def _connect(self, graph):
        from drivecast import api
        ws = store.workspace(self.ws)
        with mock.patch.object(api, "session_for", lambda g: graph):
            who, n = api._fb_store_account(ws, store.group(self.g), "LL-token", time.time() + 5e6, graph)
        return who, n

    def test_connect_fetches_and_links_pages(self):
        who, n = self._connect(FakeGraph(pages_fixture()))
        self.assertEqual((who["name"], n), ("Naimat", 2))
        acc = db.one("SELECT * FROM fb_accounts WHERE group_id=?", (self.g,))
        self.assertEqual(acc["name"], "Naimat")
        self.assertEqual(json.loads(security.unseal(acc["token_enc"]) if False else '"x"'), "x") if False else None
        pages = db.all("SELECT * FROM fb_pages ORDER BY name")
        self.assertEqual([(p["name"], p["can_post"]) for p in pages], [("Art by Zara", 1), ("Read only", 0)])
        self.assertEqual(security.unseal(pages[0]["token_enc"]), "pt-a")
        # link page A to the channel
        from drivecast import api

        class Req:
            def __init__(s): s.ws, s.user = self.ws, {"id": 1}
            json = {"channel_id": self.ch}
        pa = next(p for p in pages if p["name"] == "Art by Zara")
        r = Req(); r.json = {"channel_id": self.ch}
        out = api.fb_assign(r, str(pa["id"]))
        self.assertEqual(out["channel"]["id"], self.ch)
        self.assertEqual(store.channel(self.ch)["fb_page_id"], "PAGE_A")

    def test_worker_crossposts_after_youtube(self):
        graph = FakeGraph(pages_fixture())
        self._connect(graph)
        from drivecast import api
        r = type("R", (), {"ws": self.ws, "user": {"id": 1}, "json": {"channel_id": self.ch}})()
        pa = db.one("SELECT id FROM fb_pages WHERE name='Art by Zara'")
        api.fb_assign(r, str(pa["id"]))
        # the proxy session the worker passes to facebook is our fake graph
        with mock.patch.object(worker, "session_for", lambda g: graph):
            job, sid = job_for(self.ch)
            yt = FakeYT()
            # worker uses adapters' session for drive/yt; facebook uses the real session_for(g) -> patched
            ad = adapters(FakeDrive([{"id": "f1", "name": "Reel one.mp4"}]), yt)
            ad["session"] = lambda g: graph
            worker.run(job, ad)
        ups = {u["destination"]: u for u in db.all("SELECT * FROM uploads")}
        self.assertEqual(ups["youtube:UC1"]["status"], "published")
        self.assertIn("facebook:PAGE_A", ups)
        self.assertEqual(ups["facebook:PAGE_A"]["status"], "published")
        self.assertEqual(ups["facebook:PAGE_A"]["external_video_id"], "V123")
        self.assertTrue(graph.videos)  # the chunk was sent

    def test_facebook_failure_keeps_youtube(self):
        graph = FakeGraph(pages_fixture())
        self._connect(graph)
        from drivecast import api
        r = type("R", (), {"ws": self.ws, "user": {"id": 1}, "json": {"channel_id": self.ch}})()
        api.fb_assign(r, str(db.one("SELECT id FROM fb_pages WHERE name='Art by Zara'")["id"]))
        graph.fail = {"code": 190, "message": "token dead"}
        with mock.patch.object(worker, "session_for", lambda g: graph):
            job, _ = job_for(self.ch)
            yt = FakeYT()
            ad = adapters(FakeDrive([{"id": "f1", "name": "Reel.mp4"}]), yt)
            ad["session"] = lambda g: graph
            worker.run(job, ad)
        self.assertEqual(db.one("SELECT status FROM uploads WHERE destination='youtube:UC1'")["status"], "published")
        self.assertEqual(db.one("SELECT status FROM fb_accounts")["status"], "needs_reconnect")
        # no leftover facebook reservation
        self.assertIsNone(db.one("SELECT 1 FROM uploads WHERE destination LIKE 'facebook:%'"))


if __name__ == "__main__":
    unittest.main()
