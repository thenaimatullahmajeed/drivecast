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


class SafeQueueTests(unittest.TestCase):
    setUp = ti.PipelineTests.setUp
    tearDown = ti.PipelineTests.tearDown
    run_passes = ti.PipelineTests.run_passes

    def _media(self, ext, dur, text, state="in_drive", origin="tiktok"):
        now = time.time()
        return db.run("""INSERT INTO media(workspace_id,channel_id,source_id,origin,ext_id,kind,lane,duration,caption,state,created_at,updated_at)
                         VALUES(?,?,?,?,?,'video','backfill',?,?,?,?,?)""", (self.ws, self.ch, self.src, origin, ext, dur, text, state, now, now))

    def test_same_video_on_another_site_is_a_twin(self):
        self._media("T1", 31.4, "Easy chalk paint for old furniture #diy @someone")
        # Instagram copy: same length (±1 s) and nearly the same words
        self.assertIsNotNone(igsync.twin_of(self.ch, 31.9, "Easy chalk paint for old furniture ✨ link in bio"))
        # same length but a different video
        self.assertIsNone(igsync.twin_of(self.ch, 31.4, "Summer tomato harvest and canning tips"))
        # no caption to compare: never skipped on length alone
        self.assertIsNone(igsync.twin_of(self.ch, 31.4, ""))
        # a different length
        self.assertIsNone(igsync.twin_of(self.ch, 45.0, "Easy chalk paint for old furniture"))

    def test_found_only_items_are_not_twins(self):
        self._media("T2", 20.0, "Salted maple syrup marshmallows recipe", state="found")
        self.assertIsNone(igsync.twin_of(self.ch, 20.0, "Salted maple syrup marshmallows recipe"))

    def test_twin_is_skipped_with_a_plain_reason(self):
        self._media("T3", 12.0, "Homemade fruit juice gummies for kids")
        mid = self._media("T4", 12.3, "Homemade fruit juice gummies for kids", state="found", origin="instagram")
        m = db.one("SELECT * FROM media WHERE id=?", (mid,))
        igsync.skip_twin(m, igsync.twin_of(self.ch, 12.3, m["caption"], mid))
        row = db.one("SELECT state, skip_reason FROM media WHERE id=?", (mid,))
        self.assertEqual(row["state"], "skipped")
        self.assertIn("TikTok", row["skip_reason"])

    def test_pasted_instagram_links_join_the_queue_in_order(self):
        src = db.one("SELECT * FROM sources WHERE id=?", (self.src,))
        ch = store.channel(self.ch)
        links = ["https://www.instagram.com/reel/CxAbCdEfGhI/?igsh=x", "https://www.instagram.com/p/CyZzZzZzZzZ/", "not a link",
                 "https://www.instagram.com/reel/CxAbCdEfGhI/"]
        added, known = igsync.add_links(src, ch, links)
        self.assertEqual((added, known), (2, 1))
        rows = db.all("SELECT ext_id, lane, state FROM media WHERE code IN ('CxAbCdEfGhI','CyZzZzZzZzZ') ORDER BY taken_at")
        self.assertEqual([r["lane"] for r in rows], ["fresh", "fresh"])
        self.assertEqual(igsync.shortcode_pk("AAAAAAAAAAB"), "1")
        self.assertEqual(igsync.shortcode_pk("AAAAAAAAABA"), "64")

    def test_keep_ahead_sets_the_target(self):
        ch = {"plan_json": db.jdump({"posts_per_day": 3})} if hasattr(db, "jdump") else {"plan_json": '{"posts_per_day": 3}'}
        self.assertEqual(igsync._ready_target(ch, {"keep_days": 14}), 42)
        self.assertEqual(igsync._ready_target(ch, {"keep_days": 90}), 270)
        self.assertGreater(igsync._ready_target(ch, {"keep_days": 0}), 10 ** 5)
        self.assertEqual(igsync._ready_target(ch, {}), 21)

    def test_cooling_is_explained_and_runway_counts_days(self):
        from drivecast import api
        db.run("UPDATE channels SET state='live', plan_json=? WHERE id=?", ('{"posts_per_day": 2}', self.ch))
        for i in range(6):
            self._media(f"R{i}", 10 + i * 5, f"video number {i} unique words{i}", state="in_drive")
        for i in range(4):
            self._media(f"W{i}", 90 + i * 7, f"waiting item {i} other{i}", state="found")
        igsync.rest_route(igsync.route_of(db.one("SELECT * FROM ig_accounts LIMIT 1")), 900)
        q = api._queue_row(store.channel(self.ch), time.time())
        self.assertEqual(q["state"], "cooling")
        self.assertTrue(q["until"])
        self.assertEqual((q["ready"], q["waiting"], q["runway_days"], q["ready_days"]), (6, 4, 5, 3))

    def test_instagram_health_says_resting_with_a_time(self):
        acc = db.one("SELECT * FROM ig_accounts LIMIT 1")
        igsync.rest_route(igsync.route_of(acc), 600)
        h = igsync.health(acc)
        self.assertEqual(h["state"], "resting")
        self.assertTrue(h["rest_until"])
