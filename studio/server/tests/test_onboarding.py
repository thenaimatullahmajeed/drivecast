"""A new client: latest Instagram video → Drive (with a live bar) → channel. Post now before it is ready waits for it."""
import os
os.environ["DC_NO_KICK"] = "1"  # no background pass during tests
import time
import unittest
from unittest import mock

from drivecast import db, igsync, store, worker

import test_instagram as ti
from test_core import fresh_db  # noqa: F401  (keeps the shared helpers imported the same way)


class Onboarding(unittest.TestCase):
    setUp = ti.PipelineTests.setUp
    tearDown = ti.PipelineTests.tearDown
    run_passes = ti.PipelineTests.run_passes

    def new_client(self):
        """The client was added just now and wants only new posts — yet Drive must not stay empty."""
        db.run("UPDATE sources SET include_old=0, started_at=?, next_check=0 WHERE id=?", (time.time(), self.src))
        db.run("UPDATE channels SET state='live' WHERE id=?", (self.ch,))
        self.stages = []
        real = worker._stage

        def rec(ch_id, stage, pct=None, title=None):
            self.stages.append((stage, pct))
            return real(ch_id, stage, pct, title)
        self.rec = mock.patch.object(worker, "_stage", rec)
        self.rec.start()
        self.addCleanup(self.rec.stop)

    def test_latest_video_is_taken_even_though_older_than_the_client(self):
        self.new_client()
        steps = self.run_passes(3)
        self.assertEqual(steps[0], "check")
        rows = db.all("SELECT ext_id, lane, state FROM media WHERE source_id=?", (self.src,))
        self.assertEqual([(r["ext_id"], r["lane"]) for r in rows if r["lane"] == "fresh"], [("100", "fresh")])
        self.assertEqual(db.one("SELECT state FROM media WHERE ext_id='100'")["state"], "in_drive")
        self.assertEqual(len(self.fd.uploaded), 1)

    def test_progress_is_real_and_cleared(self):
        self.new_client()
        self.run_passes(3)
        names = [s for s, _ in self.stages]
        for want in ("Downloading from Instagram", "Removing hidden data", "Saving to Google Drive", "Saved in Google Drive"):
            self.assertIn(want, names)
        pcts = [p for _, p in self.stages if p is not None]
        self.assertEqual(pcts, sorted(pcts))
        self.assertEqual(pcts[-1], 1.0)
        self.assertNotIn(self.ch, worker.PROGRESS)

    def test_post_now_waits_for_the_first_video_then_posts(self):
        self.new_client()
        igsync.want_post(self.ch)
        self.run_passes(3)
        job = db.one("SELECT * FROM jobs WHERE channel_id=?", (self.ch,))
        self.assertIsNotNone(job)
        self.assertIsNone(db.meta_get(f"postwhenready:{self.ch}"))
        self.assertNotIn(self.ch, worker.PROGRESS)

    def test_no_request_no_extra_post(self):
        self.new_client()
        self.run_passes(3)
        self.assertIsNone(db.one("SELECT * FROM jobs WHERE channel_id=?", (self.ch,)))


if __name__ == "__main__":
    unittest.main()
