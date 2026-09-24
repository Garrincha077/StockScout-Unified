import unittest

from stockscout_unified.gridview_notification import (
    render_message,
    snapshot_summary,
    verify_active_unified,
)


class GridViewNotificationTests(unittest.TestCase):
    def test_summary_counts_sources_and_kell_sections(self):
        payload = {
            "source": {"sessionDate": "2026-09-18"},
            "candidateCount": 4,
            "kell": {"qualifiedCount": 3},
            "kellGap": {"qualifiedCount": 2},
            "candidates": [
                {"ticker": "AAA", "sources": ["bottom-fishing", "kell-daily"]},
                {"ticker": "BBB", "sources": ["next"]},
                {"ticker": "CCC", "sources": ["ryan-original", "kell-gap"]},
                {"ticker": "DDD", "sources": ["bottom-fishing"]},
            ],
        }
        summary = snapshot_summary(payload)
        self.assertEqual(summary["sessionDate"], "2026-09-18")
        self.assertEqual(summary["candidateCount"], 4)
        self.assertEqual(summary["kell3x"], 3)
        self.assertEqual(summary["gapUp"], 2)
        self.assertEqual(summary["multiHit"], 2)

    def test_message_contains_stable_gridview_link(self):
        message = render_message(
            {
                "sessionDate": "2026-09-18",
                "candidateCount": 82,
                "kell3x": 3,
                "gapUp": 5,
                "multiHit": 7,
            },
            grid_url="https://stockscout-trend-birth-review-lab.vercel.app/?snapshot=run--hash",
        )
        self.assertIn("Trend Birth GridView", message)
        self.assertIn("Kell 3x RVOL", message)
        self.assertIn("stockscout\\-trend\\-birth\\-review\\-lab\\.vercel\\.app", message)

    def test_active_unified_fingerprint_is_backward_compatible_and_exact_when_present(self):
        content = b'{"status":"healthy","runId":"run-1","sessionDate":"2026-09-22"}'
        legacy = {"runId": "run-1", "sessionDate": "2026-09-22"}
        active = verify_active_unified(legacy, content)
        self.assertEqual(active["runId"], "run-1")

        import hashlib
        exact = {
            **legacy,
            "unifiedManifestSha256": hashlib.sha256(content).hexdigest(),
        }
        self.assertEqual(verify_active_unified(exact, content)["sessionDate"], "2026-09-22")

        wrong = {**exact, "unifiedManifestSha256": "0" * 64}
        with self.assertRaisesRegex(ValueError, "active Unified activation"):
            verify_active_unified(wrong, content)

    def test_active_unified_still_rejects_wrong_run_or_unhealthy_scan(self):
        content = b'{"status":"healthy","runId":"run-2","sessionDate":"2026-09-22"}'
        with self.assertRaisesRegex(ValueError, "behind the active Unified scan"):
            verify_active_unified({"runId": "run-1", "sessionDate": "2026-09-22"}, content)

        unhealthy = b'{"status":"failed","runId":"run-1","sessionDate":"2026-09-22"}'
        with self.assertRaisesRegex(ValueError, "behind the active Unified scan"):
            verify_active_unified({"runId": "run-1", "sessionDate": "2026-09-22"}, unhealthy)


if __name__ == "__main__":
    unittest.main()
