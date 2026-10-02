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
EOD_WORKFLOW = "StockScout Unified EOD"
READINESS_STEP = "Wait for selected session to be available from Next provider"
PROVIDER_NOT_READY = re.compile(r"NEXT_PROVIDER_NOT_READY session=\d{4}-\d{2}-\d{2}")


def gh(*args: str) -> str:
    return subprocess.run(["gh", *args], check=True, text=True, capture_output=True).stdout


def retry_eod_readiness(repo: str, run: dict, logs: str) -> bool:
    """Rerun only Next and its dependents after a classified pre-scan lag.

    Never replay a failed publishing, alert evaluation or delivery job. GitHub
    reruns this exact job at the original SHA; the successful Bottom handoff and
    immutable prepare/session outputs remain in the same Actions run.
    """
    if (
        run.get("head_branch") != "main"
        or run.get("event") not in {"schedule", "workflow_dispatch"}
        or not PROVIDER_NOT_READY.search(logs)
        or CODE_FAILURE.search(logs)
    ):
        return False
    run_id = int(run["id"])
    current = json.loads(gh("api", f"repos/{repo}/actions/runs/{run_id}"))
    if current.get("status") != "completed" or int(current.get("run_attempt") or 1) != 1:
        return False
    pages = json.loads(gh("api", f"repos/{repo}/actions/runs/{run_id}/jobs?per_page=100", "--paginate", "--slurp"))
    jobs = [job for page in pages for job in page["jobs"]]
    by_name = {job["name"]: job for job in jobs}
    if any(by_name.get(name, {}).get("conclusion") != "success" for name in ("prepare", "bottom")):
        return False
    if any(by_name.get(name, {}).get("conclusion") != "skipped" for name in ("assemble", "deploy-pages", "verify-notify")):
        return False
    failed = [job for job in jobs if job.get("conclusion") == "failure"]
    if len(failed) != 1 or failed[0]["name"] != "next":
        return False
    steps = [step["name"] for step in failed[0].get("steps", []) if step.get("conclusion") == "failure"]
    if steps != [READINESS_STEP]:
        return False
    gh("api", "-X", "POST", f"repos/{repo}/actions/jobs/{int(failed[0]['id'])}/rerun")
    print(json.dumps({"status": "retry_started", "workflow": EOD_WORKFLOW, "runId": run_id, "jobId": failed[0]["id"], "reason": "provider_not_ready"}))
    return True


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
    provider_lag = name == EOD_WORKFLOW and bool(PROVIDER_NOT_READY.search(logs))
    transient = bool(TRANSIENT.search(logs) or provider_lag) and not bool(CODE_FAILURE.search(logs))
    if name in retry_names and attempt == 1 and transient:
        try:
            if name == EOD_WORKFLOW:
                if retry_eod_readiness(repo, run, logs):
                    return 0
            else:
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
