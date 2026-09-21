import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import requests

from stockscout_unified import gridview_notification as notification


def publication():
    snapshot = {
        "source": {"runId": "run-123", "sessionDate": "2026-09-21"},
        "candidateCount": 1,
        "chartCount": 1,
        "candidates": [{"ticker": "AAA", "sources": ["next"], "chartBars": [[1, 2]]}],
    }
    content = json.dumps(snapshot).encode()
    digest = hashlib.sha256(content).hexdigest()
    identity = "run-123--" + digest
    manifest = {
        "schemaVersion": "trend-birth-publication-v1",
        "snapshotId": identity,
        "snapshotPath": f"data/snapshots/{identity}.json",
        "sha256": digest,
        "runId": "run-123",
        "sessionDate": "2026-09-21",
        "candidateCount": 1,
        "chartCount": 1,
    }
    return manifest, content, notification.snapshot_summary(snapshot)


class DeliveryTests(unittest.TestCase):
    def test_committed_but_undeployed_revision_is_rejected(self):
        manifest, _, _ = publication()
        newer = {**manifest, "runId": "new-run"}
        with (
            patch.object(
                notification,
                "fetch_bytes",
                side_effect=[json.dumps(manifest).encode(), json.dumps(newer).encode()],
            ),
            self.assertRaisesRegex(ValueError, "not deployed"),
        ):
            notification.verify_publication()

    def test_deployed_review_behind_unified_is_rejected(self):
        manifest, content, _ = publication()
        pointer = json.dumps(manifest).encode()
        replies = [
            pointer,
            pointer,
            content,
            b"snapshot-loader.js",
            b"ReviewSnapshots data/snapshots/",
            pointer,
            json.dumps(
                {"status": "healthy", "runId": "new-run", "sessionDate": "2026-09-21"}
            ).encode(),
        ]
        with (
            patch.object(notification, "fetch_bytes", side_effect=replies),
            self.assertRaisesRegex(ValueError, "behind"),
        ):
            notification.verify_publication()

    def test_deployed_archive_and_frontend_are_checked(self):
        manifest, content, summary = publication()
        pointer = json.dumps(manifest).encode()
        replies = [
            pointer,
            pointer,
            content,
            b'<script src="snapshot-loader.js">',
            b"ReviewSnapshots data/snapshots/",
            pointer,
            json.dumps(
                {
                    "runId": manifest["runId"],
                    "sessionDate": manifest["sessionDate"],
                    "status": "healthy",
                }
            ).encode(),
        ]
        with patch.object(notification, "fetch_bytes", side_effect=replies) as fetch:
            self.assertEqual((manifest, summary), notification.verify_publication())
            self.assertFalse(any("latest.json" in call.args[0] for call in fetch.call_args_list))

    def test_failed_or_partial_deploy_is_rejected_before_send(self):
        manifest, content, _ = publication()
        pointer = json.dumps(manifest).encode()
        for replies in (
            [pointer, pointer, b"corrupt"],
            [pointer, pointer, content, b"old frontend"],
            [pointer, pointer, content, b"snapshot-loader.js", b"not a loader"],
        ):
            with (
                patch.object(notification, "fetch_bytes", side_effect=replies),
                self.assertRaises(ValueError),
            ):
                notification.verify_publication()

    def test_reservation_crash_and_success_both_block_duplicate(self):
        manifest, _, summary = publication()
        with tempfile.TemporaryDirectory() as tmp:
            ledger, marker = Path(tmp) / "ledger.json", Path(tmp) / "legacy.txt"
            self.assertTrue(
                notification.reserve(
                    manifest,
                    summary,
                    ledger_path=ledger,
                    marker_path=marker,
                    reservation_id="attempt-1",
                )
            )
            with self.assertRaises(ValueError):
                notification.reserve(
                    manifest,
                    summary,
                    ledger_path=ledger,
                    marker_path=marker,
                    reservation_id="attempt-2",
                )
            record = notification.read_ledger(ledger)["sessions"]["2026-09-21"]
            self.assertIn("?snapshot=" + manifest["snapshotId"], record["gridUrl"])
            with (
                patch.object(notification, "sends_suppressed", return_value=False),
                patch.object(
                    notification,
                    "_telegram_config",
                    return_value=SimpleNamespace(bot_token="test", chat_id="test"),
                ),
                patch.object(
                    notification.requests,
                    "post",
                    return_value=Mock(json=lambda: {"ok": True, "result": {"message_id": 123}}),
                ) as post,
            ):
                self.assertTrue(notification.send_reserved(ledger, "attempt-1"))
                with self.assertRaises(ValueError):
                    notification.send_reserved(ledger, "attempt-1")
                post.assert_called_once()
            self.assertFalse(
                notification.reserve(
                    manifest,
                    summary,
                    ledger_path=ledger,
                    marker_path=marker,
                    reservation_id="attempt-3",
                )
            )

    def test_timeout_never_retries_and_suppression_prevents_send(self):
        manifest, _, summary = publication()
        with tempfile.TemporaryDirectory() as tmp:
            ledger, marker = Path(tmp) / "ledger.json", Path(tmp) / "legacy.txt"
            notification.reserve(
                manifest, summary, ledger_path=ledger, marker_path=marker, reservation_id="a"
            )
            with (
                patch.object(notification, "sends_suppressed", return_value=True),
                patch.object(notification.requests, "post") as post,
            ):
                with self.assertRaises(ValueError):
                    notification.send_reserved(ledger, "a")
                post.assert_not_called()
            with (
                patch.object(notification, "sends_suppressed", return_value=False),
                patch.object(
                    notification,
                    "_telegram_config",
                    return_value=SimpleNamespace(bot_token="test", chat_id="test"),
                ),
                patch.object(notification.requests, "post", side_effect=requests.Timeout) as post,
            ):
                self.assertFalse(notification.send_reserved(ledger, "a"))
                with self.assertRaises(ValueError):
                    notification.send_reserved(ledger, "a")
                post.assert_called_once()
            self.assertEqual(
                "uncertain", notification.read_ledger(ledger)["sessions"]["2026-09-21"]["status"]
            )

    def test_legacy_marker_prevents_migration_duplicate(self):
        manifest, _, summary = publication()
        with tempfile.TemporaryDirectory() as tmp:
            marker = Path(tmp) / "legacy.txt"
            marker.write_text("2026-09-21\n")
            self.assertFalse(
                notification.reserve(
                    manifest,
                    summary,
                    ledger_path=Path(tmp) / "ledger.json",
                    marker_path=marker,
                    reservation_id="a",
                )
            )

    def test_cli_defaults_to_read_only(self):
        manifest, _, summary = publication()
        with (
            patch("sys.argv", ["gridview"]),
            patch.object(notification, "verify_publication", return_value=(manifest, summary)),
            patch.object(notification, "reserve") as reserve,
            patch.object(notification, "send_reserved") as send,
        ):
            self.assertEqual(0, notification.main())
            reserve.assert_not_called()
            send.assert_not_called()


if __name__ == "__main__":
    unittest.main()
