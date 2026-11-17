"""Backup → restore on a fresh server brings everything back, including sealed Google tokens. Pause holds everything."""
import base64
import json
import os
import shutil
import tempfile
import time
import unittest

from drivecast import backup, config, db, scheduler, security, store, worker

from test_core import FakeDrive, FakeYT, adapters, fresh_db, job_for, make_world


class BackupTests(unittest.TestCase):
    def setUp(self):
        self.dir = fresh_db()
        config.DATA_DIR = self.dir
        config.TMP_DIR = self.dir
        with open(os.path.join(self.dir, "master.key"), "wb") as f:
            f.write(security._key)
        self.ws, self.g, self.ch = make_world()

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_round_trip(self):
        tok_before = store.channel(self.ch)["token_enc"]
        blob = backup.create("long-password-1")
        self.assertEqual(blob[:5], b"DCBK1")
        with self.assertRaises(ValueError):
            backup.open_file(blob, "wrong-password")
        with self.assertRaises(ValueError):
            backup.open_file(b"garbage", "long-password-1")
        # a brand-new phone: other key, empty database
        new = tempfile.mkdtemp()
        try:
            config.DATA_DIR = new
            summ, created = backup.stage(blob, "long-password-1")
            self.assertEqual(summ["channels"], 1)
            self.assertTrue(backup.apply_staged(new))
            security.load_key(os.path.join(new, "master.key"))
            db._local.__dict__.clear()
            db.init(os.path.join(new, "drivecast.db"))
            c = store.channel(self.ch)
            self.assertEqual(c["token_enc"], tok_before)
            self.assertEqual(json.loads(security.unseal(c["token_enc"]))["refresh_token"], "r")  # sign-in survives
            self.assertFalse(backup.apply_staged(new))  # applied once only
        finally:
            shutil.rmtree(new, ignore_errors=True)


class PauseTests(unittest.TestCase):
    def setUp(self):
        self.dir = fresh_db()
        worker.CLEAN_BEFORE_PUBLISH = False
        self.ws, self.g, self.ch = make_world()

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_pause_holds_and_resumes(self):
        now = time.time()
        db.run("UPDATE workspaces SET paused_until=?, paused_at=? WHERE id=?", (now + 3600, now, self.ws))
        job, sid = job_for(self.ch)
        yt = FakeYT()
        worker.run(job, adapters(FakeDrive([{"id": "f1", "name": "A.mp4"}]), yt))
        self.assertEqual(yt.calls, 0)
        self.assertEqual(db.one("SELECT status FROM slots WHERE id=?", (sid,))["status"], "held")
        self.assertIsNone(scheduler.post_now(store.channel(self.ch), now))
        # time passes: the pause ends by itself
        scheduler.tick(now + 3700)
        self.assertIsNone(store.workspace(self.ws)["paused_until"])


if __name__ == "__main__":
    unittest.main()
