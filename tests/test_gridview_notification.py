import unittest

from stockscout_unified.gridview_notification import render_message, snapshot_summary


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


if __name__ == "__main__":
    unittest.main()
