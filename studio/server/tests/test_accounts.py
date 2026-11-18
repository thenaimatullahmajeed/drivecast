"""Forgot password, reset links, Google sign-in, owner-only phone details, update all phones."""
import json
import os
import shutil
import sys
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from drivecast import api, db, fleet, mail, security, store  # noqa: E402

from test_core import fresh_db  # noqa: E402


class R:
    def __init__(self, user=None, ws=None, body=None, query=None):
        self.user, self.ws, self.json, self.query = user, ws, body or {}, query or {}
        self.headers, self.ip, self.set_cookie = {}, "1.2.3.4", None


class Accounts(unittest.TestCase):
    def setUp(self):
        self.dir = fresh_db()
        api._forgot_hits.clear()
        self.owner = api._create_user("Owner", "owner@x.com", "password1", "owner", "UTC")
        self.u = api._create_user("Ali", "ali@x.com", "password1", "student", "UTC")
        db.run("UPDATE workspaces SET google_client_id='cid.apps', google_client_secret_enc=? WHERE id=?",
               (security.seal("sec"), self.owner["workspace_id"]))

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_forgot_without_email_tells_the_owner_and_never_reveals_accounts(self):
        a = api.forgot_password(R(body={"email": "ali@x.com"}))
        b = api.forgot_password(R(body={"email": "nobody@x.com"}))
        self.assertEqual(a, b)
        self.assertEqual(db.one("SELECT COUNT(*) n FROM events WHERE code='PW_RESET_ASK'")["n"], 1)

    def test_forgot_by_email_then_reset(self):
        sent = []
        mail.save({"host": "smtp.gmail.com", "port": 465, "user": "me@gmail.com", "password": "abcd efgh ijkl mnop"})
        with mock.patch.object(mail, "send", lambda to, sub, text, html=None: sent.append((to, text))):
            api.forgot_password(R(body={"email": "ALI@x.com"}))
        self.assertEqual(sent[0][0], "ali@x.com")
        tok = sent[0][1].split("/#/reset/")[1].split()[0]
        req = R(body={"token": tok, "password": "newpass99"})
        out = api.reset_password(req)
        self.assertEqual(out["user"]["email"], "ali@x.com")
        self.assertTrue(req.set_cookie)
        u = db.one("SELECT * FROM users WHERE id=?", (self.u["id"],))
        self.assertTrue(security.check_password("newpass99", u["pw_hash"]))
        with self.assertRaises(api.HttpError):  # one use only
            api.reset_password(R(body={"token": tok, "password": "another99"}))
        self.assertEqual(security.unseal(mail.config()["pass_enc"]), "abcdefghijklmnop")
        self.assertNotIn("pass_enc", mail.view())

    def test_owner_reset_link(self):
        out = api.admin_reset_link(R(user=self.owner, ws=self.owner["workspace_id"]), str(self.u["id"]))
        self.assertIn("/#/reset/", out["url"])
        self.assertFalse(out["emailed"])

    def test_expired_link(self):
        tok = api._make_reset(self.u, "email")
        db.run("UPDATE password_resets SET expires_at=?", (time.time() - 1,))
        with self.assertRaises(api.HttpError):
            api.reset_password(R(body={"token": tok, "password": "newpass99"}))

    def _google(self, mode="login", info=None, invite=""):
        st, loc = api.google_start(R(query={"mode": mode, "invite": invite}))
        self.assertEqual(st, 302)
        self.assertIn("scope=openid+email+profile", loc["Location"])
        code = loc["Location"].split("state=")[1].split("&")[0]
        l = db.one("SELECT * FROM connect_links WHERE code=?", (code,))
        req = R(query={"state": code, "code": "abc"})
        with mock.patch.object(api, "_google_identity", lambda r, link: info):
            return api.oauth_callback(req), req

    def test_google_signs_in_an_existing_account_and_links_it(self):
        (st, loc), req = self._google(info={"email": "Ali@x.com", "sub": "g-1", "email_verified": True, "name": "Ali"})
        self.assertEqual((st, loc["Location"]), (302, "/#/dashboard"))
        self.assertTrue(req.set_cookie)
        self.assertEqual(db.one("SELECT google_sub FROM users WHERE id=?", (self.u["id"],))["google_sub"], "g-1")

    def test_google_unverified_or_closed_signup(self):
        (st, _), _ = self._google(info={"email": "z@x.com", "sub": "g-2", "email_verified": False})
        self.assertEqual(st, 400)
        db.meta_set("signup_mode", "invite")
        (st, _), _ = self._google(mode="signup", info={"email": "new@x.com", "sub": "g-3", "email_verified": True})
        self.assertEqual(st, 403)  # invite-only and no invite
        self.assertIsNone(db.one("SELECT 1 FROM users WHERE email='new@x.com'"))

    def test_google_signup_with_open_signups(self):
        db.meta_set("open_signup", "1")
        db.meta_set("max_users", "10")
        (st, loc), _ = self._google(mode="signup", info={"email": "new@x.com", "sub": "g-4", "email_verified": True, "name": "New"})
        self.assertEqual((st, loc["Location"]), (302, "/#/settings"))
        u = db.one("SELECT * FROM users WHERE email='new@x.com'")
        self.assertEqual((u["role"], u["google_sub"]), ("student", "g-4"))

    def test_phone_details_only_for_the_owner(self):
        db.meta_set("device_info", json.dumps({"model": "realme RMX2170", "android": "12", "battery": 31, "charging": False, "temp": 29.9}))
        mine = api._device_for(R(user=self.owner))
        theirs = api._device_for(R(user=self.u))
        self.assertEqual(mine["model"], "realme RMX2170")
        self.assertNotIn("model", theirs)
        self.assertNotIn("android", theirs)
        self.assertEqual(theirs["battery"], 31)

    def test_update_all_phones(self):
        called = []
        with mock.patch("drivecast.hotupdate.apply", lambda force=False: called.append(1)):
            fleet.follow_update(None)
            fleet.follow_update(time.time())
            time.sleep(0.2)
            ts = db.meta_get("fleet_update_done")
            fleet.follow_update(float(ts))  # same request again: nothing
            time.sleep(0.2)
        self.assertEqual(len(called), 1)


if __name__ == "__main__":
    unittest.main()


class Approval(unittest.TestCase):
    def setUp(self):
        self.dir = fresh_db()
        self.owner = api._create_user("Owner", "owner@x.com", "password1", "owner", "UTC")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_no_invite_waits_for_the_owner(self):
        out = api.register(R(body={"name": "Sara", "email": "sara@x.com", "password": "password1"}))
        self.assertTrue(out["pending"])
        with self.assertRaises(api.HttpError) as e:
            api.login(R(body={"email": "sara@x.com", "password": "password1"}))
        self.assertIn("approval", str(e.exception.args))
        self.assertEqual(db.one("SELECT COUNT(*) n FROM events WHERE code='SIGNUP_PENDING'")["n"], 1)
        uid = api.pending_list(R(user=self.owner))["users"][0]["id"]
        api.approve_user(R(user=self.owner, body={}), str(uid))
        out = api.login(R(body={"email": "sara@x.com", "password": "password1"}))
        self.assertEqual(out["user"]["email"], "sara@x.com")

    def test_invite_only_mode(self):
        api.pending_mode(R(user=self.owner, body={"mode": "invite"}))
        with self.assertRaises(api.HttpError):
            api.register(R(body={"name": "Sara", "email": "sara@x.com", "password": "password1"}))

    def test_reject_deletes(self):
        api.register(R(body={"name": "Sara", "email": "sara@x.com", "password": "password1"}))
        uid = db.one("SELECT id FROM users WHERE email='sara@x.com'")["id"]
        api.reject_user(R(user=self.owner), str(uid))
        self.assertIsNone(db.one("SELECT 1 FROM users WHERE email='sara@x.com'"))
