"""Final build: whole-user moves, patient proxies, Telegram, Accounts page, AI picks the next post."""
import json
import os
import shutil
import sys
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from drivecast import ai, api, db, fleet, insights, maintenance, notify, security, store, usermove  # noqa: E402

from test_core import fresh_db, make_world  # noqa: E402
from test_routes import route_world  # noqa: E402


class UserMove(unittest.TestCase):
    def setUp(self):
        self.dirs = [fresh_db()]
        db.meta_set("fleet_key", "k" * 40)
        self.ws, self.g, self.ch, self.dest = route_world()
        db.run("UPDATE workspaces SET name='Ali studio', groq_key_enc=? WHERE id=?", (security.seal("gsk-1"), self.ws))
        db.run("""INSERT INTO users(workspace_id,email,name,pw_hash,role,timezone,created_at,google_sub)
                  VALUES(?,?,?,?,?,?,?,?)""", (self.ws, "ali@x.com", "Ali", security.hash_password("password1"), "student", "UTC", time.time(), "g-9"))
        db.run("INSERT INTO uploads(workspace_id,channel_id,destination,file_id,file_name,title,status,reserved_at,published_at,views) "
               "VALUES(?,?,?,?,?,?,'published',?,?,50)", (self.ws, self.ch, "facebook:PAGE_A", "f1", "a.mp4", "A", time.time(), time.time()))

    def tearDown(self):
        for d in self.dirs:
            shutil.rmtree(d, ignore_errors=True)

    def test_export_import_keeps_everything_and_source_stops(self):
        with mock.patch.object(fleet, "key", lambda: "k" * 40), mock.patch.object(fleet, "report", lambda *a: None):
            blob = usermove.seal_bundle(usermove.export_workspace("ALI@x.com"))
            usermove.mark_moved("ali@x.com", "s2.drivecast.site")
            self.assertEqual(store.workspace(self.ws)["moved_to"], "s2.drivecast.site")
            self.assertEqual(db.one("SELECT state FROM channels WHERE id=?", (self.ch,))["state"], "paused")
            with self.assertRaises(api.HttpError):
                api._moved_check(db.one("SELECT * FROM users WHERE email='ali@x.com'"))
            # the other server: a different database and a different secret key
            self.dirs.append(fresh_db())
            out = usermove.import_workspace(usermove.open_bundle(blob))
        self.assertEqual((out["clients"], out["pools"]), (1, 1))
        u = db.one("SELECT * FROM users WHERE email='ali@x.com'")
        self.assertTrue(security.check_password("password1", u["pw_hash"]))
        self.assertEqual(u["google_sub"], "g-9")
        ws = store.workspace(u["workspace_id"])
        self.assertEqual(security.unseal(ws["groq_key_enc"]), "gsk-1")
        p = db.one("SELECT * FROM fb_pages WHERE workspace_id=?", (ws["id"],))
        self.assertEqual(security.unseal(p["token_enc"]), "PT")
        ch = db.one("SELECT * FROM channels WHERE workspace_id=?", (ws["id"],))
        self.assertEqual(p["channel_id"], ch["id"])
        self.assertEqual(db.one("SELECT ig_dest_id FROM channels WHERE id=?", (ch["id"],))["ig_dest_id"],
                         db.one("SELECT id FROM ig_dest WHERE workspace_id=?", (ws["id"],))["id"])
        g = db.one("SELECT * FROM ip_groups WHERE workspace_id=?", (ws["id"],))
        self.assertEqual(ch["group_id"], g["id"])
        self.assertEqual(db.one("SELECT COUNT(*) n FROM uploads WHERE workspace_id=?", (ws["id"],))["n"], 1)
        with mock.patch.object(fleet, "key", lambda: "k" * 40), self.assertRaises(ValueError):  # twice: refused
            usermove.import_workspace(usermove.open_bundle(blob))

    def test_owner_never_moves(self):
        with self.assertRaises(ValueError):
            usermove.export_workspace(db.one("SELECT email FROM users WHERE role='owner'")["email"]) \
                if db.one("SELECT 1 FROM users WHERE role='owner'") else (_ for _ in ()).throw(ValueError())

    def test_board_summary_has_no_secrets(self):
        s = usermove.summary()
        self.assertIn("ali@x.com", [x["email"] for x in s])
        self.assertNotIn("pw_hash", json.dumps(s))


class PatientProxy(unittest.TestCase):
    def setUp(self):
        self.dir = fresh_db()
        self.ws, self.g, self.ch = make_world()
        maintenance.QUICK_WAIT = 0

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_retry_before_down_and_back_by_itself(self):
        sent = []
        with mock.patch.object(maintenance, "probe", side_effect=OSError("timeout")), \
                mock.patch.object(notify, "alert", lambda *a, **k: sent.append(a)):
            maintenance.check_groups()
            self.assertEqual(store.group(self.g)["status"], "unstable")  # not down after one bad check
            maintenance.check_groups()
            maintenance.check_groups()
            self.assertEqual(store.group(self.g)["status"], "unstable")  # 3 checks but not 10 minutes yet
            db.run("UPDATE ip_groups SET fail_since=? WHERE id=?", (time.time() - 11 * 60, self.g))
            maintenance.check_groups()
            self.assertEqual(store.group(self.g)["status"], "down")
        self.assertEqual(len(sent), 1)
        ip = store.group(self.g)["outgoing_ip"]
        with mock.patch.object(maintenance, "probe", return_value={"ip": ip, "ms": 90, "country": None}), \
                mock.patch.object(notify, "alert", lambda *a, **k: sent.append((a, k))):
            maintenance.check_groups()
        g = store.group(self.g)
        self.assertEqual((g["status"], g["fail_count"], g["fail_since"]), ("ok", 0, None))
        self.assertTrue(sent[-1][1].get("solved"))


class Telegram(unittest.TestCase):
    def setUp(self):
        self.dir = fresh_db()

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_once_per_problem_then_solved(self):
        out = []
        db.meta_set("telegram_json", json.dumps({"token_enc": security.seal("T"), "chat_id": "42"}))
        with mock.patch.object(notify, "send_now", lambda t: out.append(t)), \
                mock.patch("threading.Thread", lambda target, daemon: type("T", (), {"start": lambda self: target()})()):
            self.assertTrue(notify.alert("px:1", "Pool 1 down"))
            self.assertFalse(notify.alert("px:1", "Pool 1 down"))
            self.assertTrue(notify.alert("px:1", "Pool 1 back", solved=True))
            self.assertFalse(notify.alert("px:1", "Pool 1 back", solved=True))
            db.event("error", "channel", "YouTube access ended", 1, None, "YT_AUTH_REVOKED")
            db.event("warn", "channel", "small thing", 1, None, "PROXY_SLOW")
        self.assertEqual(len(out), 3)
        self.assertIn("YouTube access ended", out[-1])

    def test_off_without_setup(self):
        self.assertFalse(notify.alert("x", "y"))


class AccountsAndPick(unittest.TestCase):
    def setUp(self):
        self.dir = fresh_db()
        self.ws, self.g, self.ch, self.dest = route_world()

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_accounts_by_platform_and_deep(self):
        a = insights.accounts(self.ws)
        self.assertEqual((len(a["facebook"]), len(a["instagram"])), (1, 1))
        d = insights.account_deep(self.ws, "facebook", a["facebook"][0]["id"], "UTC")
        self.assertEqual(d["head"]["name"], "Art by Zara")
        self.assertIsNone(insights.account_deep(self.ws + 99, "facebook", a["facebook"][0]["id"], "UTC"))

    def test_ai_pick_reorders_only_within_the_lane(self):
        db.run("UPDATE workspaces SET groq_key_enc=? WHERE id=?", (security.seal("k"), self.ws))
        db.run("UPDATE channels SET ai_json=? WHERE id=?", (json.dumps({"pick": True}), self.ch))
        for i in range(4):
            db.run("INSERT INTO uploads(workspace_id,channel_id,destination,file_id,file_name,title,status,reserved_at,published_at,views) "
                   "VALUES(?,?,?,?,?,?,'published',0,0,?)", (self.ws, self.ch, "facebook:PAGE_A", f"o{i}", "x.mp4", f"Old {i}", 100 * i))
        files = [{"id": "a", "name": "A.mp4"}, {"id": "b", "name": "B.mp4"}, {"id": "c", "name": "C.mp4", "appProperties": {"dc_lane": "fresh"}}]
        reply = (200, {}, json.dumps({"choices": [{"message": {"content": '{"pick": 1, "why": "like the best ones"}'}}], "usage": {}}))
        with mock.patch.object(ai, "_post", lambda *a, **k: reply):
            out = ai.pick_next(store.channel(self.ch), files)
        self.assertEqual([f["id"] for f in out], ["b", "a", "c"])
        self.assertEqual(ai.last_pick(self.ch)["name"], "B")
        db.run("UPDATE channels SET ai_json=? WHERE id=?", (json.dumps({"pick": False}), self.ch))
        self.assertEqual(ai.pick_next(store.channel(self.ch), files), files)


if __name__ == "__main__":
    unittest.main()
