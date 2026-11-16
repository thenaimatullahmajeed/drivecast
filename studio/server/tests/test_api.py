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

        with mock.patch.object(api, "check_group", return_value=("5.6.7.8", 80)):
            st, r = student.call("POST", "/api/groups", {"host": "p.example", "port": 1080, "username": "u", "password": "pw"})
            self.assertEqual(st, 200, r)
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

        st, d = owner.call("GET", "/api/dashboard")
        self.assertEqual(st, 200, d)
        for k in ("today", "kpis", "trend", "heatmap", "consistency", "attention"):
            self.assertIn(k, d)

    def test_local_endpoints_need_localhost_and_static(self):
        c = Client(self.base)
        st, r = c.call("GET", "/local/status", headers={"X-Forwarded-For": "8.8.8.8"})
        self.assertEqual(st, 403)
        st, r = c.call("POST", "/local/public-url", {"url": "http://insecure"})
        self.assertEqual(st, 400)
        st, r = c.call("GET", "/healthz")
        self.assertEqual(st, 200)


if __name__ == "__main__":
    unittest.main()
