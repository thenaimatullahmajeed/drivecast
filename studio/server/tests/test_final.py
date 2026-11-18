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


class BootPicksNewest(unittest.TestCase):
    def test_newer_apk_wins_over_an_older_update(self):
        import tempfile
        import dcboot
        d = tempfile.mkdtemp()
        hot = os.path.join(d, "hot")

        def mk(v):
            os.makedirs(os.path.join(hot, v, "drivecast"), exist_ok=True)
            open(os.path.join(hot, v, "drivecast", "__init__.py"), "w").write(f'VERSION = "{v}"\n')
            json.dump({"version": v, "shell_api": 2}, open(os.path.join(hot, "current.json"), "w"))
        with mock.patch.dict(os.environ, {"DC_SHELL_API": "2", "DC_APP_VERSION": "2.0.29"}):
            mk("2.0.26")
            self.assertIsNone(dcboot._pick(d))  # the APK (2.0.29) runs
            self.assertFalse(os.path.exists(os.path.join(hot, "current.json")))
            mk("2.0.31")
            self.assertTrue(dcboot._pick(d))  # a newer update still wins
        shutil.rmtree(d, ignore_errors=True)


class InstagramCheckNeverDeadEnds(unittest.TestCase):
    def setUp(self):
        from drivecast import igsync
        self.ig = igsync
        self.dir = fresh_db()
        self.ws, self.g, self.ch = make_world()
        self.g2 = db.run("INSERT INTO ip_groups(workspace_id,number,host,port,status,outgoing_ip,created_at) VALUES(?,2,'h2',1080,'ok','5.6.7.8',?)",
                         (self.ws, time.time()))
        now = time.time()
        for gid, user, uid in ((self.g, "thekhanbhaii", "111"), (self.g2, "thekhanbhaii", "111")):
            db.run("INSERT INTO ig_accounts(workspace_id,group_id,username,ig_user_id,cookies_enc,created_at,updated_at,status) VALUES(?,?,?,?,?,?,?,'ok')",
                   (self.ws, gid, user, uid, security.seal("sessionid=x; ds_user_id=111; csrftoken=c"), now, now))
        db.run("INSERT INTO ig_vault(workspace_id,label,username,ig_user_id,cookies_enc,status,created_at) VALUES(?,?,?,?,?,'ok',?)",
               (self.ws, "spare", "spare_acc", "222", security.seal("sessionid=y; ds_user_id=222; csrftoken=c"), now))
        self.calls = []

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def fake(self, behaviour):
        from drivecast.errors import CodedError
        test = self

        class C:
            min_gap = (7, 14)

            def __init__(self, who):
                self.who = who

            def profile(self, h):
                test.calls.append(self.who)
                b = behaviour.get(self.who, "limit")
                if b == "limit":
                    raise CodedError("IG_RATE_LIMIT", "429")
                if b == "missing":
                    raise CodedError("IG_PROFILE_NOT_FOUND", "", handle=h)
                return {"id": "9", "username": h, "full_name": "Ok", "picture": None, "is_private": False, "followed": False, "posts": 5}
        return (mock.patch.object(self.ig, "client_for", lambda a, g: C(a["username"])),
                mock.patch.object(self.ig, "_vault_client", lambda v, g: C(v["username"])))

    def run_lookup(self, behaviour, public="limit"):
        from drivecast import instagram
        from drivecast.errors import CodedError
        p1, p2 = self.fake(behaviour)

        def pub(s, h):
            self.calls.append("public")
            if public == "ok":
                return {"id": None, "username": h, "full_name": "Public", "picture": None, "is_private": False, "followed": False, "posts": "12"}
            raise CodedError("IG_PROFILE_NOT_FOUND" if public == "missing" else "IG_RATE_LIMIT", "x", handle=h)
        with p1, p2, mock.patch.object(instagram, "public_profile", pub):
            return self.ig.lookup(self.ws, store.group(self.g2), "https://www.instagram.com/YourFavBostonMom/")

    def test_busy_account_then_the_vault_account_reads(self):
        p = self.run_lookup({"thekhanbhaii": "limit", "spare_acc": "ok"})
        self.assertEqual(p["via"], "@spare_acc")
        self.assertEqual(self.calls, ["thekhanbhaii", "spare_acc"])  # the shared account is asked once, not twice
        # Instagram limits the account, not the pool: both pools' rows rest together
        self.assertEqual({r["status"] for r in db.all("SELECT status FROM ig_accounts")}, {"resting"})

    def test_everyone_busy_then_the_public_page(self):
        p = self.run_lookup({}, public="ok")
        self.assertEqual((p["full_name"], p["via"]), ("Public", "public page"))

    def test_all_busy_is_never_a_dead_end(self):
        p = self.run_lookup({}, public="limit")
        self.assertTrue(p["pending"])
        self.assertEqual(p["username"], "yourfavbostonmom")

    def test_a_real_typo_is_reported(self):
        from drivecast.errors import CodedError
        with self.assertRaises(CodedError):
            self.run_lookup({"thekhanbhaii": "missing"}, public="missing")

    def test_cached_for_a_while(self):
        self.run_lookup({"thekhanbhaii": "ok"})
        self.calls.clear()
        p = self.run_lookup({})
        self.assertEqual(self.calls, [])
        self.assertEqual(p["username"], "yourfavbostonmom")

    def test_a_resting_pool_is_read_by_another_account(self):
        db.run("UPDATE ig_accounts SET status='resting', paused_until=? WHERE group_id=?", (time.time() + 3600, self.g2))
        db.run("UPDATE ig_accounts SET username='other', ig_user_id='333' WHERE group_id=?", (self.g,))
        served = []
        with mock.patch.object(self.ig, "account_pass", lambda a, now=None, serve=None: served.append((a["group_id"], serve and serve["id"])) or "idle"):
            db.run("INSERT INTO channels(workspace_id,platform,external_id,title,group_id,state,created_at) VALUES(?,?,?,?,?,'live',?)",
                   (self.ws, "route", "r2", "C2", self.g2, time.time()))
            c2 = db.one("SELECT id FROM channels WHERE external_id='r2'")["id"]
            db.run("INSERT INTO sources(channel_id,kind,handle,state,started_at,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
                   (c2, "instagram", "x", "active", time.time(), time.time(), time.time()))
            self.ig.borrow({self.g: "idle", self.g2: "waiting"})
        self.assertIn((self.g, self.g2), served)
