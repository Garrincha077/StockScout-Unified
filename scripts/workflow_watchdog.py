"""Classify failed Actions runs and open one issue per affected workflow.

Runs only in a trusted workflow_run job. Never executes checked-out PR code.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from contextlib import suppress
from pathlib import Path

TRANSIENT = re.compile(r"timed? out|connection reset|temporary failure|service unavailable|HTTP (?:500|502|503|504)|rate limit", re.I)
CODE_FAILURE = re.compile(r"AssertionError|ModuleNotFoundError|SyntaxError|schema mismatch|hash mismatch|ValueError", re.I)


def gh(*args: str) -> str:
    return subprocess.run(["gh", *args], check=True, text=True, capture_output=True).stdout


def main() -> int:
    event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text(encoding="utf-8"))
    run = event["workflow_run"]
    if run.get("conclusion") != "failure":
        return 0
    repo = event["repository"]["full_name"]
    name = str(run["name"])
    run_id = int(run["id"])
    attempt = int(run.get("run_attempt") or 1)
    url = str(run["html_url"])
    retry_names = {name.strip() for name in os.getenv("WATCHDOG_RETRY_WORKFLOWS", "").split(",") if name.strip()}
    logs = ""
    with suppress(subprocess.CalledProcessError):
        logs = gh("run", "view", str(run_id), "--repo", repo, "--log-failed")[-250_000:]
    transient = bool(TRANSIENT.search(logs)) and not bool(CODE_FAILURE.search(logs))
    if name in retry_names and attempt == 1 and transient:
        try:
            gh("api", "-X", "POST", f"repos/{repo}/actions/runs/{run_id}/rerun-failed-jobs")
            print(json.dumps({"status": "retry_started", "workflow": name, "runId": run_id}))
            return 0
        except subprocess.CalledProcessError:
            pass

    title = f"[watchdog] {name} failed"
    issues = json.loads(gh("issue", "list", "--repo", repo, "--state", "open", "--limit", "100", "--json", "number,title"))
    existing = next((issue for issue in issues if issue["title"] == title), None)
    category = "transient after retry" if transient and attempt > 1 else "transient; auto-retry unavailable" if transient else "code/data/configuration or unknown"
    body = f"Run: {url}\nAttempt: {attempt}\nClassification: {category}\n\nInspect failed steps and logs before changing code or rerunning a delivery."
    if existing:
        gh("issue", "comment", str(existing["number"]), "--repo", repo, "--body", body)
    else:
        gh("issue", "create", "--repo", repo, "--title", title, "--body", body)
    print(json.dumps({"status": "issue_recorded", "workflow": name, "runId": run_id}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
