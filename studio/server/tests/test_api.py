import os
os.environ["DC_NO_KICK"] = "1"  # no background pass during tests
import http.cookiejar
import json
import os
import shutil
import socket
import tempfile
import threading
import unittest
import urllib.request
from unittest import mock

from drivecast import api, db, security, web


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


class Client:
    def __init__(self, base):
        self.base = base
        self.op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

    def call(self, method, path, body=None, headers=None):
        h = {"Content-Type": "application/json", "X-DC": "1"}
        h.update(headers or {})
        req = urllib.request.Request(self.base + path, method=method, headers=h,
                                     data=json.dumps(body).encode() if body is not None else None)
        try:
            with self.op.open(req) as r:
                return r.status, json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            raw = e.read()
            try:
                return e.code, json.loads(raw)
            except ValueError:
                return e.code, {"raw": raw.decode()}


class ApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir = tempfile.mkdtemp()
        security._key = os.urandom(32)
        db._local.__dict__.clear()
        db.init(os.path.join(cls.dir, "a.db"))
        db.meta_set("setup_code", "SETUP-AAAA-BBBB")
        from drivecast import config
        config.LOCAL_TOKEN = "tok123"
        cls.port = free_port()
        cls.httpd = web.serve("127.0.0.1", cls.port)
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.port}"

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        shutil.rmtree(cls.dir, ignore_errors=True)

    def test_full_account_flow(self):
        owner = Client(self.base)
        st, s = owner.call("GET", "/api/session")
        self.assertTrue(s["needs_setup"])
        st, r = owner.call("POST", "/api/setup", {"setup_code": "wrong", "name": "A", "email": "a@x.com", "password": "password1"})
        self.assertEqual(st, 403)
        st, r = owner.call("POST", "/api/setup", {"setup_code": "setup-aaaa-bbbb", "name": "Admin", "email": "a@x.com",
                                                   "password": "password1", "timezone": "Asia/Karachi"})
        self.assertEqual(st, 200, r)
        self.assertEqual(r["user"]["role"], "owner")
        # CSRF header required
        st, r = owner.call("POST", "/api/admin/invites", {"label": "Ali"}, headers={"X-DC": "0"})
        self.assertEqual(st, 403)
        st, r = owner.call("POST", "/api/admin/invites", {"label": "Ali", "limits": {"max_groups": 1}})
        self.assertEqual(st, 200, r)
        code = r["code"]
        self.assertRegex(code, r"^DC-[A-Z2-9]{4}-[A-Z2-9]{4}$")

        student = Client(self.base)
        st, r = student.call("POST", "/api/register", {"invite_code": code.lower(), "name": "Ali", "email": "ali@x.com",
                                                       "password": "password2"})
        self.assertEqual(st, 200, r)
        self.assertEqual(r["user"]["role"], "student")
        st, r = Client(self.base).call("POST", "/api/register", {"invite_code": code, "name": "B", "email": "b@x.com",
                                                                  "password": "password3"})
        self.assertEqual(st, 403)  # single use
        st, r = owner.call("GET", "/api/admin/invites")
        self.assertEqual(r["invites"][0]["used_name"], "Ali")

        # students cannot use admin endpoints
        st, r = student.call("GET", "/api/admin/users")
        self.assertEqual(st, 403)

        fake = {"ip": "5.6.7.8", "ms": 80, "country": "US", "country_name": "United States", "city": "Dallas", "isp": "Example"}
        with mock.patch.object(api, "probe", return_value=fake):
            st, r = student.call("POST", "/api/groups", {"proxy": "p.example:1080:u:pw"})
            self.assertEqual(st, 200, r)
            self.assertEqual((r["group"]["host"], r["group"]["port"], r["group"]["username"], r["group"]["country"]),
                             ("p.example", 1080, "u", "US"))
            gid = r["group"]["id"]
            st, r = student.call("POST", "/api/groups/test", {"proxy": "bad"})
            self.assertFalse(r["ok"])
            # edit: same IP needs no confirmation
            st, r = student.call("PATCH", f"/api/groups/{gid}", {"proxy": "p2.example:2000:u2:pw2"})
            self.assertEqual((st, r["group"]["host"], r["group"]["port"]), (200, "p2.example", 2000))
            self.assertEqual(r["group"]["number"], 1)
            self.assertEqual(r["group"]["outgoing_ip"], "5.6.7.8")
            self.assertNotIn("password_enc", r["group"])
            st, r = student.call("POST", "/api/groups", {"host": "p2.example", "port": 1080})
            self.assertEqual(st, 403)  # max_groups = 1
            st, r = owner.call("GET", "/api/groups")
            self.assertEqual(r["groups"], [])  # workspace isolation

        st, r = owner.call("GET", "/api/admin/users")
        self.assertEqual([u["name"] for u in r["users"]], ["Admin", "Ali"])
        ali = r["users"][1]
        st, rep = owner.call("GET", f"/api/admin/users/{ali['id']}/report")
        self.assertEqual(st, 200, rep)
        self.assertEqual(rep["user"]["name"], "Ali")

        st, r = owner.call("POST", f"/api/admin/users/{ali['id']}", {"status": "suspended"})
        self.assertEqual(st, 200)
        st, r = student.call("GET", "/api/dashboard")
        self.assertEqual(st, 401)  # session ended on suspension

        # owner resets a student's password; old one stops working
        st, r = owner.call("POST", f"/api/admin/users/{ali['id']}/reset-password")
        self.assertEqual(st, 200, r)
        temp = r["temporary_password"]
        st, r = Client(self.base).call("POST", "/api/login", {"email": "ali@x.com", "password": "password2"})
        self.assertEqual(st, 401)
        # owner forgot password: only from the phone with the private token
        st, r = Client(self.base).call("POST", "/local/reset-owner", {"password": "newpassword9"})
        self.assertEqual(st, 403)
        st, r = Client(self.base).call("POST", "/local/reset-owner", {"password": "newpassword9"},
                                       headers={"X-Local-Token": "tok123"})
        self.assertEqual(st, 200, r)
        st, r = Client(self.base).call("POST", "/api/login", {"email": "a@x.com", "password": "newpassword9"})
        self.assertEqual(st, 200, r)
        st, r = owner.call("GET", "/api/dashboard")
        self.assertEqual(st, 401)  # old owner session was signed out
        owner = Client(self.base)
        owner.call("POST", "/api/login", {"email": "a@x.com", "password": "newpassword9"})
        st, d = owner.call("GET", "/api/dashboard")
        self.assertEqual(st, 200, d)
        # delete the student: account and workspace data are gone
        ws = db.one("SELECT workspace_id FROM users WHERE id=?", (ali["id"],))["workspace_id"]
        st, r = owner.call("POST", f"/api/admin/users/{ali['id']}/delete")
        self.assertEqual(st, 200, r)
        self.assertIsNone(db.one("SELECT 1 FROM users WHERE id=?", (ali["id"],)))
        self.assertIsNone(db.one("SELECT 1 FROM ip_groups WHERE workspace_id=?", (ws,)))
        st, r = owner.call("GET", "/api/admin/users")
        self.assertEqual([u["name"] for u in r["users"]], ["Admin"])
        st, r = owner.call("POST", f"/api/admin/users/{r['users'][0]['id']}/delete")
        self.assertEqual(st, 404)  # the owner cannot be deleted
        for k in ("today", "kpis", "trend", "heatmap", "consistency", "attention"):
            self.assertIn(k, d)

    def test_local_endpoints_need_localhost_and_static(self):
        c = Client(self.base)
        st, r = c.call("GET", "/local/status", headers={"X-Forwarded-For": "8.8.8.8"})
        self.assertEqual(st, 403)
        st, r = c.call("POST", "/local/public-url", {"url": "https://x.example"})
        self.assertEqual(st, 403)  # no private token
        st, r = c.call("POST", "/local/public-url", {"url": "http://insecure"}, headers={"X-Local-Token": "tok123"})
        self.assertEqual(st, 400)
        st, r = c.call("GET", "/local/status", headers={"X-Local-Token": "wrong"})
        self.assertEqual(st, 403)
        st, r = c.call("GET", "/local/status", headers={"X-Local-Token": "tok123"})
        self.assertEqual(st, 200)
        st, r = c.call("GET", "/healthz")
        self.assertEqual(st, 200)


if __name__ == "__main__":
    unittest.main()


class SourceApiTests(unittest.TestCase):
    """Instagram source: add, change, pause, remove, resume — the ledger survives every change."""

    @classmethod
    def setUpClass(cls):
        ApiTests.setUpClass.__func__(cls)

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        shutil.rmtree(cls.dir, ignore_errors=True)

    def test_source_lifecycle(self):
        import time as _t
        from drivecast import drive, instagram
        c = Client(self.base)
        st, r = c.call("POST", "/api/setup", {"setup_code": "SETUP-AAAA-BBBB", "name": "Admin", "email": "s@x.com", "password": "password1"})
        self.assertEqual(st, 200, r)
        ws = r["user"]["workspace_id"]
        now = _t.time()
        db.run("UPDATE workspaces SET drive_token_enc=?, drive_root_id='ROOT' WHERE id=?", (security.seal('{"access_token":"a","refresh_token":"r","expires_at":9e9}'), ws))
        g = db.run("INSERT INTO ip_groups(workspace_id,number,host,port,status,created_at) VALUES(?,1,'h',1,'ok',?)", (ws, now))
        ch = db.run("INSERT INTO channels(workspace_id,external_id,title,group_id,state,created_at) VALUES(?,?,?,?,'setup',?)", (ws, "UC9", "Ink", g, now))
        made = []
        with mock.patch.object(drive, "child_folder", lambda s, b, p, n: (made.append((p, n)) or f"{p}/{n}")), \
             mock.patch.object(instagram.Client, "whoami", lambda self: {"username": "test_g1", "id": "9"}):
            # cookies: bad then good
            st, r = c.call("POST", f"/api/groups/{g}/instagram", {"cookies": "sessionid=1"})
            self.assertEqual(st, 400)
            self.assertIn("missing", r["error"].lower())
            st, r = c.call("POST", f"/api/groups/{g}/instagram", {"cookies": "sessionid=S; ds_user_id=9; csrftoken=C"})
            self.assertEqual(st, 200, r)
            self.assertEqual(r["group"]["instagram"]["username"], "test_g1")
            # wizard: Instagram only, no Drive folder chosen → folder + Inbox created under the main folder
            st, r = c.call("POST", f"/api/channels/{ch}/route", {"group_id": g, "instagram": {"handle": "https://instagram.com/InkEpoch", "content_type": "videos"},
                                                                 "plan": {"posts_per_day": 2}})
            self.assertEqual(st, 200, r)
            self.assertIn(("ROOT", "Ink"), made)  # the folder carries the client's name
            self.assertIn(("ROOT/Ink", "Inbox"), made)
            st, d = c.call("GET", f"/api/channels/{ch}/detail")
            self.assertEqual((d["source"]["handle"], d["source"]["state"], d["has_inbox"]), ("inkepoch", "active", True))
            sid = d["source"]["id"]
            db.run("INSERT INTO media(workspace_id,channel_id,source_id,origin,ext_id,state,created_at) VALUES(?,?,?,'instagram','1','in_drive',?)", (ws, ch, sid, now))
            # starting the route again: a source that is not chosen this time stops, the chosen one stays
            tt = db.run("INSERT INTO sources(channel_id,kind,handle,url,started_at,created_at) VALUES(?,?,?,?,?,?)", (ch, "tiktok", "@x", "https://www.tiktok.com/@x", now, now))
            st, r = c.call("POST", f"/api/channels/{ch}/route", {"group_id": g, "instagram": {"handle": "inkepoch", "content_type": "videos"}, "plan": {"posts_per_day": 3}})
            self.assertEqual(st, 200, r)
            self.assertEqual(db.one("SELECT state FROM sources WHERE id=?", (tt,))["state"], "removed")
            self.assertEqual(db.one("SELECT state FROM sources WHERE id=?", (sid,))["state"], "active")
            # change content type + pause
            st, d = c.call("PUT", f"/api/channels/{ch}/source", {"content_type": "mix"})
            self.assertEqual(d["source"]["content_type"], "mix")
            st, d = c.call("PUT", f"/api/channels/{ch}/source", {"paused": True})
            self.assertEqual(d["source"]["state"], "paused")
            # switch handle → new lane, old records kept
            st, d = c.call("PUT", f"/api/channels/{ch}/source", {"handle": "@other.client"})
            self.assertEqual(d["source"]["handle"], "other.client")
            self.assertEqual(d["pipeline"]["ready"], 1)
            # back to the first handle → same source row resumes (no double posts)
            st, d = c.call("PUT", f"/api/channels/{ch}/source", {"handle": "inkepoch"})
            self.assertEqual(d["source"]["id"], sid)
            # remove Instagram → Drive only, ledger stays
            st, d = c.call("PUT", f"/api/channels/{ch}/source", {"enabled": False})
            self.assertIsNone(d["source"])
            self.assertEqual(db.one("SELECT COUNT(*) n FROM media WHERE channel_id=?", (ch,))["n"], 1)
            # titles + encore settings
            st, d = c.call("PUT", f"/api/channels/{ch}/settings", {"encore": False, "titles": "Fine line magic\n\nInk stories"})
            self.assertEqual((d["encore_on"], d["titles"]), (False, ["Fine line magic", "Ink stories"]))
            # hidden custom domain
            st, r = c.call("POST", "/api/settings/advanced", {"public_url_override": "http://bad"})
            self.assertEqual(st, 400)
            st, r = c.call("POST", "/api/settings/advanced", {"public_url_override": "https://studio.example.com"})
            self.assertEqual(r["public_url_override"], "https://studio.example.com")
            c.call("POST", "/api/settings/advanced", {"public_url_override": ""})
