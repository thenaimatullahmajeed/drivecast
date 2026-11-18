"""AI captions with Groq: per-platform text, group key before owner key, limits, the check's hold,
subtitles, and 'AI never stops a post'. Groq is faked."""
import json
import os
import shutil
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from drivecast import ai, api, db, security, store, worker  # noqa: E402

from test_core import FakeDrive, adapters, fresh_db, job_for, make_world  # noqa: E402
from test_routes import FakeFB, FakeIGPost, route_world  # noqa: E402


def groq_reply(payload, tokens=300):
    return 200, {}, json.dumps({"choices": [{"message": {"content": json.dumps(payload)}}], "usage": {"total_tokens": tokens}})


GOOD = {"youtube": {"title": "Late night painting session", "description": "Soft colours.\n#art #painting"},
        "facebook": {"caption": "A calm evening at the easel."},
        "instagram": {"caption": "Night colours", "hashtags": ["art", "#painting", "art", "night studio"]},
        "check": {"ok": True, "reason": ""}}


class FakeIG(FakeIGPost):
    def publish(self, s, dest, token, items, caption, on_progress=None):
        self.caption = caption
        return super().publish(s, dest, token, items, caption, on_progress)


class FakeFBc(FakeFB):
    def publish_video(self, s, page_id, tok, path, title, desc, on_progress=None):
        self.desc = desc
        return super().publish_video(s, page_id, tok, path, title, desc, on_progress)


class AiCaptions(unittest.TestCase):
    def setUp(self):
        self.dir = fresh_db()
        self.ws, self.g, self.ch, self.dest = route_world()
        worker.CLEAN_BEFORE_PUBLISH = False
        self.fb, self.ig = FakeFBc(), FakeIG()
        db.run("UPDATE workspaces SET groq_key_enc=? WHERE id=?", (security.seal("owner-key"), self.ws))
        db.run("UPDATE channels SET ai_json=? WHERE id=?", (json.dumps({"captions": True, "language": "English"}), self.ch))
        self.calls = []

    def tearDown(self):
        worker.CLEAN_BEFORE_PUBLISH = True
        shutil.rmtree(self.dir, ignore_errors=True)

    def ad(self, files):
        a = adapters(FakeDrive(files), None)
        a.pop("platform", None)
        a["facebook"], a["igpost"] = self.fb, self.ig
        return a

    def fake(self, reply):
        def _post(key, path, **kw):
            self.calls.append((key, path, kw))
            return reply(key, path, kw) if callable(reply) else reply
        return mock.patch.object(ai, "_post", _post)

    def test_each_platform_gets_its_own_text(self):
        job, _ = job_for(self.ch)
        with self.fake(groq_reply(GOOD)):
            worker.run(job, self.ad([{"id": "f1", "name": "Studio night.mp4"}]))
        self.assertEqual(self.fb.desc, "A calm evening at the easel.")
        self.assertTrue(self.ig.caption.startswith("Night colours"))
        self.assertIn("#art #painting #nightstudio", self.ig.caption)  # cleaned, no duplicates
        self.assertEqual(self.calls[0][0], "owner-key")
        self.assertEqual(ai.usage(f"ws{self.ws}")["text"], 1)

    def test_group_key_comes_first(self):
        db.run("UPDATE ip_groups SET groq_key_enc=? WHERE id=?", (security.seal("client-key"), self.g))
        self.assertEqual(ai.key_for(store.channel(self.ch))[:2], ("client-key", f"g{self.g}"))

    def test_ai_problem_never_stops_the_post(self):
        job, _ = job_for(self.ch)
        with self.fake((500, {}, "boom")):
            worker.run(job, self.ad([{"id": "f1", "name": "Studio night.mp4"}]))
        self.assertEqual(db.one("SELECT COUNT(*) n FROM uploads WHERE status='published'")["n"], 2)
        self.assertIn("Studio night", self.fb.desc)  # the normal text

    def test_rate_limit_rests_the_key(self):
        with self.fake((429, {"retry-after": "120"}, "{}")):
            pack = ai.prepare(store.channel(self.ch), {"id": "x"}, None, "T", "D", ["facebook"])
        self.assertEqual(pack["texts"], {})
        self.assertIsNotNone(ai.usage(f"ws{self.ws}")["resting_until"])
        with self.fake(groq_reply(GOOD)):
            ai.prepare(store.channel(self.ch), {"id": "x"}, None, "T", "D", ["facebook"])
        self.assertEqual(len(self.calls), 1)  # resting: no second call

    def test_daily_limit(self):
        for _ in range(ai.TEXT_PER_DAY):
            ai._log(f"ws{self.ws}", store.channel(self.ch), "text")
        with self.fake(groq_reply(GOOD)):
            pack = ai.prepare(store.channel(self.ch), {"id": "x"}, None, "T", "D", ["facebook"])
        self.assertEqual(self.calls, [])
        self.assertEqual(pack["texts"], {})

    def test_refused_key_raises_one_alert(self):
        with self.fake((401, {}, "{}")):
            ai.prepare(store.channel(self.ch), {"id": "x"}, None, "T", "D", ["facebook"])
            ai.prepare(store.channel(self.ch), {"id": "y"}, None, "T", "D", ["facebook"])
        self.assertEqual(db.one("SELECT COUNT(*) n FROM events WHERE code='AI_KEY_BAD'")["n"], 1)
        self.assertTrue(ai.usage(f"ws{self.ws}")["key_refused"])

    def test_check_holds_then_your_ok_lets_it_through(self):
        db.run("UPDATE channels SET ai_json=? WHERE id=?", (json.dumps({"check": True}), self.ch))
        bad = dict(GOOD, check={"ok": False, "reason": "uses another brand's name"})
        job, _ = job_for(self.ch)
        drive = FakeDrive([{"id": "f1", "name": "A.mp4"}])
        a = self.ad([])
        a["drive"] = drive
        with self.fake(groq_reply(bad)):
            worker.run(job, a)
        self.assertEqual(db.one("SELECT COUNT(*) n FROM uploads WHERE status='published'")["n"], 0)
        ev = db.one("SELECT * FROM events WHERE code='AI_HOLD'")
        self.assertIn("another brand", ev["message"])
        # moved back by you → the check does not run again for this file
        self.assertTrue(ai.approved(store.channel(self.ch), "f1"))
        with self.fake(groq_reply(bad)):
            pack = ai.prepare(store.channel(self.ch), {"id": "f1"}, None, "T", "D", ["facebook"], file_key="f1")
        self.assertIsNotNone(pack)

    def test_title_never_repeats_and_is_cleaned(self):
        db.run("""INSERT INTO uploads(workspace_id,channel_id,destination,file_id,file_name,title,status,reserved_at)
                  VALUES(?,?,?,?,?,?,'published',0)""", (self.ws, self.ch, "facebook:PAGE_A", "old", "o.mp4", "Late night painting session"))
        s = ai.sanitize(GOOD, "Base", "Desc", ["Late night painting session"])
        self.assertEqual(s["youtube"][0], "Base")
        s = ai.sanitize({"youtube": {"title": "<b>Hi</b>" + "x" * 200}}, "Base", "Desc", [])
        self.assertNotIn("<", s["youtube"][0])
        self.assertLessEqual(len(s["youtube"][0]), 100)

    def test_subtitles_need_permission(self):
        c = store.channel(self.ch)
        self.assertFalse(ai.can_subtitle(c))
        db.run("UPDATE channels SET yt_scope=? WHERE id=?", ("a " + ai.FORCE_SSL, self.ch))
        self.assertTrue(ai.can_subtitle(store.channel(self.ch)))
        self.assertIn("00:00:01,500 --> 00:00:03,000", ai.srt([{"start": 1.5, "end": 3.0, "text": "Hello"}]))
        self.assertEqual(ai.lang_code("urdu"), "ur")

    def test_api_switches_overview_and_secrets_hidden(self):
        R = type("R", (), {"ws": self.ws, "user": {"id": 1, "role": "owner", "timezone": "UTC"}, "json": {}, "query": {}, "headers": {}})
        r = R()
        r.json = {"ai": {"captions": True, "subtitles": True, "language": "Klingon", "voice": "  calm   and kind "}}
        api.edit_channel(r, str(self.ch))
        cfg = ai.settings(store.channel(self.ch))
        self.assertEqual(cfg["language"], "auto")
        self.assertEqual(cfg["voice"], "calm and kind")
        out = api.ai_overview(R())
        self.assertTrue(out["has_owner_key"])
        self.assertEqual(out["channels"][0]["key"], "your Settings")
        with self.fake(groq_reply({"x": 1})):
            g = R()
            g.json = {"groq_key": "client-key"}
            api.group_apps(g, str(self.g))
        self.assertTrue(api._group_out(store.group(self.g))["apps"]["groq"])
        self.assertNotIn("groq_key_enc", store.public(store.group(self.g)))
        with self.fake((401, {}, "{}")):
            g.json = {"groq_key": "wrong"}
            with self.assertRaises(api.HttpError):
                api.group_apps(g, str(self.g))

    def test_weekly_report(self):
        db.run("UPDATE channels SET ai_json=? WHERE id=?", (json.dumps({"captions": True}), self.ch))

        def reply(key, path, kw):
            return 200, {}, json.dumps({"choices": [{"message": {"content": "Good week: 4 posts went out."}}], "usage": {}})
        with self.fake(reply):
            self.assertEqual(ai.weekly(), 1)
            self.assertEqual(ai.weekly(), 0)  # once a week
        self.assertIn("Good week", ai.latest_report(self.ch)["text"])


if __name__ == "__main__":
    unittest.main()
