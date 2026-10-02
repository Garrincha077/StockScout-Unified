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

    def _run(self, event, log, issue_list="[]", jobs=None, current=None):
        calls = []

        def fake_gh(*args):
            calls.append(args)
            if args[:2] == ("run", "view"):
                return log
            if args[:2] == ("issue", "list"):
                return issue_list
            if args[0] == "api" and args[1].endswith("/jobs?per_page=100"):
                return json.dumps([{"jobs": jobs or []}])
            if args[0] == "api" and args[1].endswith("/actions/runs/42"):
                return json.dumps(current or {"status": "completed", "run_attempt": 1})
            return ""

        with tempfile.TemporaryDirectory() as folder:
            event_path = Path(folder) / "event.json"
            event_path.write_text(json.dumps(event), encoding="utf-8")
            with (
                patch.dict(os.environ, {"GITHUB_EVENT_PATH": str(event_path), "WATCHDOG_RETRY_WORKFLOWS": "Required CI,StockScout Unified EOD"}),
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

    def _eod(self, attempt=1):
        event = self._event(attempt)
        event["workflow_run"].update(name="StockScout Unified EOD", head_branch="main", event="schedule")
        return event

    def _eod_jobs(self, step=workflow_watchdog.READINESS_STEP):
        return [
            {"name": "prepare", "conclusion": "success"},
            {"name": "bottom", "conclusion": "success"},
            {"name": "next", "id": 123, "conclusion": "failure", "steps": [{"name": step, "conclusion": "failure"}]},
            *[{"name": name, "conclusion": "skipped"} for name in ("assemble", "deploy-pages", "verify-notify")],
        ]

    def test_eod_readiness_retries_only_next_and_dependents(self):
        calls = self._run(self._eod(), "NEXT_PROVIDER_NOT_READY session=2026-10-01", jobs=self._eod_jobs())
        posts = [call for call in calls if call[:3] == ("api", "-X", "POST")]
        self.assertEqual(posts, [("api", "-X", "POST", "repos/owner/repo/actions/jobs/123/rerun")])
        self.assertFalse(any(call[:2] == ("issue", "create") for call in calls))

    def test_eod_never_retries_delivery_or_dataset_validation_failure(self):
        for step in ("Deliver five resumable Telegram series", "Validate Next dataset before handoff"):
            calls = self._run(self._eod(), "HTTP 503 NEXT_PROVIDER_NOT_READY session=2026-10-01", jobs=self._eod_jobs(step))
            self.assertFalse(any(call[:3] == ("api", "-X", "POST") for call in calls))
            self.assertTrue(any(call[:2] == ("issue", "create") for call in calls))

    def test_eod_requires_successful_bottom_and_no_publishing_attempt(self):
        for name in ("bottom", "assemble", "deploy-pages", "verify-notify"):
            jobs = self._eod_jobs()
            next(job for job in jobs if job["name"] == name)["conclusion"] = "failure"
            calls = self._run(self._eod(), "NEXT_PROVIDER_NOT_READY session=2026-10-01", jobs=jobs)
            self.assertFalse(any(call[:3] == ("api", "-X", "POST") for call in calls))

    def test_eod_second_attempt_or_duplicate_event_does_not_retry(self):
        for attempt, current in ((2, None), (1, {"status": "in_progress", "run_attempt": 2})):
            calls = self._run(self._eod(attempt), "NEXT_PROVIDER_NOT_READY session=2026-10-01", jobs=self._eod_jobs(), current=current)
            self.assertFalse(any(call[:3] == ("api", "-X", "POST") for call in calls))

    def test_eod_code_error_or_untrusted_branch_does_not_retry(self):
        calls = self._run(self._eod(), "ValueError NEXT_PROVIDER_NOT_READY session=2026-10-01", jobs=self._eod_jobs())
        self.assertFalse(any(call[:3] == ("api", "-X", "POST") for call in calls))
        event = self._eod()
        event["workflow_run"]["head_branch"] = "topic"
        calls = self._run(event, "NEXT_PROVIDER_NOT_READY session=2026-10-01", jobs=self._eod_jobs())
        self.assertFalse(any(call[:3] == ("api", "-X", "POST") for call in calls))


if __name__ == "__main__":
    unittest.main()
