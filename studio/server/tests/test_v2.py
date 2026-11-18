"""v2.0: one address rule, Drive app split, hot updates + rollback, takeover lease,
alerts, fleet stats and moving an IP group between phones. Network is faked."""
import hashlib
import io
import json
import os
import shutil
import sys
import time
import unittest
import zipfile
from unittest import mock

from drivecast import alerts, api, config, db, fleet, hotupdate, lease, security, store, urls

from test_core import fresh_db, make_world

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class AddressRule(unittest.TestCase):
    def setUp(self):
        self.dir = fresh_db()

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_domain_for_people_tailscale_for_files(self):
        db.meta_set("public_url", "https://phone.tail1.ts.net")
        self.assertEqual(urls.public(), "https://phone.tail1.ts.net")
        db.meta_set("public_url_override", "https://s1.drivecast.site")
        self.assertEqual(urls.public(), "https://s1.drivecast.site")
        self.assertEqual(api.public_base(None), "https://s1.drivecast.site")
        self.assertEqual(urls.media(), "https://phone.tail1.ts.net")
        self.assertEqual(urls.backup(), "https://phone.tail1.ts.net")
        db.meta_set("public_url", "")
        self.assertEqual(urls.media(), "https://s1.drivecast.site")


class DriveAppSplit(unittest.TestCase):
    def setUp(self):
        self.dir = fresh_db()
        self.ws, _, _ = make_world()

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_drive_uses_its_own_app_only_for_tokens_it_issued(self):
        ws = store.workspace(self.ws)
        self.assertEqual(store.drive_box(ws).client_id if hasattr(store.drive_box(ws), "client_id") else store.drive_app(ws)[0],
                         "x.apps.googleusercontent.com")
        db.run("UPDATE workspaces SET drive_client_id='drive.apps', drive_client_secret_enc=? WHERE id=?", (security.seal("ds"), self.ws))
        ws = store.workspace(self.ws)
        self.assertEqual(store.drive_app(ws), ("drive.apps", "ds"))
        # the current token came from the main app: it keeps refreshing with the main app
        with mock.patch.object(store, "TokenBox", lambda tok, cid, sec, save: (cid, sec)):
            self.assertEqual(store.drive_box(ws), ("x.apps.googleusercontent.com", "s"))
            db.run("UPDATE workspaces SET drive_token_client='drive.apps' WHERE id=?", (self.ws,))
            self.assertEqual(store.drive_box(store.workspace(self.ws)), ("drive.apps", "ds"))
        self.assertNotIn("drive_client_secret_enc", store.public(store.workspace(self.ws)))


class FakeResp:
    def __init__(self, body=None, content=None):
        self._b, self.content, self.status_code = body, content, 200

    def json(self):
        return self._b

    def raise_for_status(self):
        pass


class HotUpdate(unittest.TestCase):
    def setUp(self):
        self.dir = fresh_db()
        config.DATA_DIR = self.dir
        hotupdate._cache.update(at=0, data=None)
        hotupdate.SHELL_API = 2
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("drivecast/__init__.py", 'VERSION = "2.0.99"\n')
            z.writestr("drivecast/main.py", "def start(*a, **k):\n    return 'hot'\n")
        self.blob = buf.getvalue()
        self.man = {"version": "2.0.99", "sha256": hashlib.sha256(self.blob).hexdigest(), "size": len(self.blob), "shell_api": 2}

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def session(self, blob=None, man=None):
        rel = [{"tag_name": "server-2.0.99", "assets": [
            {"name": "dc-server-2.0.99.zip", "browser_download_url": "Z", "size": len(self.blob)},
            {"name": "dc-server-2.0.99.json", "browser_download_url": "M", "size": 10},
            {"name": "DriveCast-Studio-2.0.99.apk", "browser_download_url": "A", "size": 1}]}]

        class S:
            def get(s, url, **k):
                if url == hotupdate.API:
                    return FakeResp(rel)
                if url == "M":
                    return FakeResp(man or self.man)
                return FakeResp(content=blob if blob is not None else self.blob)
        return S()

    def test_apply_stages_and_boot_loader_falls_back_after_a_failed_start(self):
        with mock.patch("drivecast.net.session_for", lambda g: self.session()), \
                mock.patch.object(hotupdate, "_restart", lambda: None), mock.patch.object(hotupdate, "VERSION", "2.0.1"):
            st = hotupdate.state()
            self.assertTrue(st["newer"])
            out = hotupdate.apply()
        self.assertEqual(out["version"], "2.0.99")
        cur = json.load(open(os.path.join(self.dir, "hot", "current.json")))
        self.assertEqual(cur["version"], "2.0.99")
        sys.path.insert(0, HERE)
        import dcboot
        os.environ["DC_SHELL_API"] = "2"
        path = dcboot._pick(self.dir)
        self.assertTrue(path.endswith("2.0.99"))
        # never confirmed healthy → next start goes back to the app copy and remembers the bad version
        self.assertIsNone(dcboot._pick(self.dir))
        self.assertTrue(os.path.exists(os.path.join(self.dir, "hot", "bad-2.0.99.json")))

    def test_checksum_and_shell_level_are_enforced(self):
        with mock.patch("drivecast.net.session_for", lambda g: self.session(blob=self.blob + b"x")), \
                mock.patch.object(hotupdate, "_restart", lambda: None), mock.patch.object(hotupdate, "VERSION", "2.0.1"):
            with self.assertRaises(ValueError):
                hotupdate.apply()
        hotupdate._cache.update(at=0, data=None)
        with mock.patch("drivecast.net.session_for", lambda g: self.session(man={**self.man, "shell_api": 3})), \
                mock.patch.object(hotupdate, "_restart", lambda: None), mock.patch.object(hotupdate, "VERSION", "2.0.1"):
            with self.assertRaises(ValueError) as cm:
                hotupdate.apply()
            self.assertIn("new APK", str(cm.exception))


class FakeDriveLease:
    def __init__(self):
        self.files = {}

    def child_folder(self, s, box, parent, name):
        return "F"

    def find_by_prop(self, s, box, folder, k, v):
        return self.files.get(v)

    def create_meta(self, s, box, folder, name, props):
        self.files[props["dc_lease"]] = {"id": "L", "appProperties": dict(props)}
        return self.files[props["dc_lease"]]

    def set_props(self, s, box, fid, props):
        self.files[props["dc_lease"]]["appProperties"] = dict(props)


class Takeover(unittest.TestCase):
    def setUp(self):
        self.dir = fresh_db()
        config.DATA_DIR = self.dir
        self.ws, _, _ = make_world()
        db.run("UPDATE workspaces SET drive_root_id='ROOT' WHERE id=?", (self.ws,))
        db.meta_set("public_url_override", "https://s2.drivecast.site")
        self.fd = FakeDriveLease()

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_spare_phone_takes_over_and_old_phone_stops(self):
        with mock.patch.object(lease, "_ctx", lambda: (self.fd, None, None, "F")):
            self.assertTrue(lease.check())  # first phone claims the lease
            first = lease.instance_id()
            # the spare restores a backup: new install id + takeover flag
            os.remove(os.path.join(self.dir, "instance.id"))
            open(os.path.join(self.dir, "takeover.flag"), "w").write("x")
            lease.on_start()
            self.assertNotEqual(self.fd.files["s2.drivecast.site"]["appProperties"]["dc_instance"], first)
            # the old phone comes back
            open(os.path.join(self.dir, "instance.id"), "w").write(first)
            self.assertFalse(lease.check())
            self.assertTrue(db.meta_get("superseded_by"))
            self.assertTrue(any(a["code"] == "PHONE_REPLACED" for a in alerts.collect()))


class FleetMove(unittest.TestCase):
    def setUp(self):
        self.dir = fresh_db()

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_group_moves_whole_with_secrets_resealed(self):
        ws, g, ch = make_world()
        now = time.time()
        db.run("UPDATE ip_groups SET password_enc=? WHERE id=?", (security.seal("proxypass"), g))
        db.run("INSERT INTO ig_accounts(workspace_id,group_id,username,cookies_enc,created_at) VALUES(?,?,?,?,?)",
               (ws, g, "tester", security.seal("sessionid=abc"), now))
        sid = db.run("INSERT INTO sources(channel_id,kind,handle,started_at,created_at) VALUES(?,?,?,?,?)", (ch, "instagram", "zara", now, now))
        mid = db.run("""INSERT INTO media(workspace_id,channel_id,source_id,origin,ext_id,state,ref,created_at)
                        VALUES(?,?,?,?,?,?,?,?)""", (ws, ch, sid, "instagram", "P1", "published", "DC-ZZZZZZ", now))
        db.run("""INSERT INTO uploads(workspace_id,channel_id,destination,file_id,file_name,title,status,reserved_at,media_id)
                  VALUES(?,?,?,?,?,?,?,?,?)""", (ws, ch, "youtube:UC1", "f1", "a.mp4", "A", "published", now, mid))
        bundle = json.loads(json.dumps(fleet.export_group(g)))
        self.assertEqual(bundle["ip_groups"][0]["password__plain"], "proxypass")
        # the other phone: a different database and master key
        shutil.rmtree(self.dir, ignore_errors=True)
        self.dir = fresh_db()
        ws2 = db.run("INSERT INTO workspaces(name,created_at,google_client_id) VALUES('b',?,?)", (now, "x.apps.googleusercontent.com"))
        db.run("INSERT INTO users(workspace_id,email,name,pw_hash,role,created_at) VALUES(?,?,?,?, 'owner',?)",
               (ws2, "b@x.com", "B", security.hash_password("password1"), now))
        out = fleet.import_group(bundle)
        self.assertEqual(out["clients"], 1)
        self.assertTrue(out["same_google_app"])
        g2 = db.one("SELECT * FROM ip_groups")
        self.assertEqual(security.unseal(g2["password_enc"]), "proxypass")
        self.assertEqual(security.unseal(db.one("SELECT cookies_enc FROM ig_accounts")["cookies_enc"]), "sessionid=abc")
        m2 = db.one("SELECT * FROM media")
        u2 = db.one("SELECT * FROM uploads")
        self.assertEqual(u2["media_id"], m2["id"])  # the ledger stays linked: nothing posts twice
        self.assertEqual(db.one("SELECT source_id FROM media")["source_id"], db.one("SELECT id FROM sources")["id"])
        with self.assertRaises(ValueError):  # importing the same group twice is refused
            fleet.import_group(bundle)

    def test_hub_totals(self):
        db.meta_set("public_url_override", "https://s1.drivecast.site")
        fleet.make_hub()
        fleet.apply("/fleet/hello", {"host": "s2.drivecast.site", "users": 4, "max_users": 4, "stats": {"channels": 9, "live": 8, "posts_24h": 20}})
        t = fleet.totals(fleet.devices())
        self.assertEqual(t["phones"], 2)
        self.assertGreaterEqual(t["channels"], 9)
        self.assertTrue(any(a["code"] == "PHONE_FULL" for a in alerts.collect()))


if __name__ == "__main__":
    unittest.main()
