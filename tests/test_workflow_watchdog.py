"""Watchdog retries only an initial infrastructure failure and deduplicates issues."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import workflow_watchdog


class WorkflowWatchdogTests(unittest.TestCase):
    def _event(self, attempt=1):
        return {
            "repository": {"full_name": "owner/repo"},
            "workflow_run": {
                "name": "Required CI", "id": 42, "run_attempt": attempt,
                "conclusion": "failure", "html_url": "https://github.com/owner/repo/actions/runs/42",
            },
        }

    def _run(self, event, log, issue_list="[]"):
        calls = []

        def fake_gh(*args):
            calls.append(args)
            if args[:2] == ("run", "view"):
                return log
            if args[:2] == ("issue", "list"):
                return issue_list
            return ""

        with tempfile.TemporaryDirectory() as folder:
            event_path = Path(folder) / "event.json"
            event_path.write_text(json.dumps(event), encoding="utf-8")
            with (
                patch.dict(os.environ, {"GITHUB_EVENT_PATH": str(event_path), "WATCHDOG_RETRY_WORKFLOWS": "Required CI"}),
                patch.object(workflow_watchdog, "gh", side_effect=fake_gh),
            ):
                self.assertEqual(workflow_watchdog.main(), 0)
        return calls

    def test_transient_first_attempt_retries_once_without_issue(self):
        calls = self._run(self._event(), "HTTP 503 Service Unavailable")
        self.assertTrue(any(call[:3] == ("api", "-X", "POST") for call in calls))
        self.assertFalse(any(call[:2] == ("issue", "create") for call in calls))

    def test_second_attempt_comments_on_existing_issue_without_retry(self):
        issues = json.dumps([{"number": 7, "title": "[watchdog] Required CI failed"}])
        calls = self._run(self._event(2), "connection reset", issues)
        self.assertFalse(any(call[:3] == ("api", "-X", "POST") for call in calls))
        self.assertTrue(any(call[:3] == ("issue", "comment", "7") for call in calls))

    def test_code_error_creates_issue_without_retry(self):
        calls = self._run(self._event(), "AssertionError: expected HTTP 503")
        self.assertFalse(any(call[:3] == ("api", "-X", "POST") for call in calls))
        self.assertTrue(any(call[:2] == ("issue", "create") for call in calls))


if __name__ == "__main__":
    unittest.main()
