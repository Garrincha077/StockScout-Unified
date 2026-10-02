# EOD provider recovery and Trend Birth follow-up

## Observed failures (2 October 2026)

- [EOD run 36948673920](https://github.com/Garrincha077/StockScout-Unified/actions/runs/36948673920)
  completed Bottom successfully, but Next failed in the provider readiness step.
  All ten complete probe histories ended on 30 September instead of the immutable
  1 October session. Exit code 75 blocked assembly, Pages and notification jobs.
- The original attempt of
  [run 36797293382](https://github.com/Garrincha077/StockScout-Unified/actions/runs/36797293382/attempts/1)
  retried 3,673 stale five-year histories, then excluded 3,675 histories and failed
  because SPY was also stale. Repeating the same wide history request did not
  resolve that attempt. A later attempt succeeded for 30 September.
- [Watchdog run 36953359324](https://github.com/Garrincha077/StockScout-Unified/actions/runs/36953359324)
  recorded an issue. Its retry allowlist contained only Required CI, so it could
  not recover the EOD job.
- Direct read-only checks of the deployed Unified manifest and Trend Birth
  publication both identified `2026-09-30-eod-36797293382-2`. Trend Birth's green
  refresh/delivery jobs therefore did not establish freshness for 1 October.

The old logs distinguish complete versus stale histories, but do not preserve
the raw terminal fields. A partial terminal row and genuine provider lag remain
different possible causes. This patch targets both operationally: an exact-day
repair for partial/missing rows, and bounded fail-closed recovery for continued
lag. A local live Yahoo probe returned rate-limit errors, so fixture tests alone
are not evidence that Yahoo is currently healthy.

## Implemented boundaries

1. The readiness probe requests complete adjusted OHLCV for the selected session
   using a separate exact-day endpoint when the broad probe is stale. It still
   requires SPY and at least 80% of the ten-symbol sample. Wrong-date, nonfinite
   and incomplete rows cannot satisfy the gate.
2. The production resumable Next wrapper uses the same validated exact-day
   request for stale histories within each batch. It replaces only the terminal
   bar and retains the historical window. Existing stale-symbol exclusion and
   final dataset/chart/session validations still apply. A genuinely unavailable
   session cannot be fabricated from a quote or a different date.
3. The gate writes per-attempt dates, recovered tickers, session and retryable
   status to `data/logs/market_readiness.json` and the Actions job summary, even
   when the scanner never starts. Output is flushed immediately.
4. The trusted watchdog allows one automatic recovery of a first-attempt main
   EOD run only when Next failed specifically in the readiness step, prepare and
   Bottom succeeded, and all publishing/delivery jobs were skipped. It reruns
   only the original Next job and its dependent jobs. Successful Bottom artifacts
   and the immutable prepare/session outputs remain in that same Actions run.
5. A changed run attempt or in-progress run blocks duplicate retry requests.
   Validation, code, publication and delivery failures are never auto-replayed by
   the EOD recovery path. A persistent failure still produces a deduplicated
   watchdog issue.
6. The repair module participates in checkpoint source identity. Existing Next
   resume reconciliation also verifies current market-data fingerprints.
7. The review branch runs a no-send, no-deploy 100-stock Next smoke test against
   the latest completed exchange session, plus Next repair regressions. Manual
   smoke requests continue to support either scanner. Before market close, blank
   smoke dates now select the actual previous completed session.

No detector, scoring formula, ranking, frozen Ryan source, Bottom price basis,
owner data or notification ledger is changed. More complete same-session price
data can allow previously excluded tickers through the existing scoring rules.
Exact-day repair adds provider requests only for stale/partial batches. A
provider outage or rate limit can still exhaust the bounded retries.

## Verification and rollout

- Local root Python suite: 240 passed, two documented existing skips.
- Local targeted Next resume/session/fundamentals suite: 19 passed.
- Ruff, protected source pins and the frozen Ryan baseline passed.
- Changed workflow YAML parsed successfully; checkpoint/handoff workflow tests
  passed. Successful Bottom handoffs can be rebound to the new assembly attempt.
- Required CI and the branch's live smoke are the remote review gates. Their
  results must be checked before claiming live recovery or promoting the patch.
- A retry executes the original run's SHA. It cannot load newly merged scanner
  code. Recovery that needs the new code must start a new main EOD run; its
  same-session Bottom checkpoint avoids repeating a completed Bottom scan when
  cache validation permits reuse.

## Proposed Trend Birth improvements

| Priority | Change | Acceptance criterion |
| --- | --- | --- |
| 1 | Separate publication alignment from market freshness. | Show expected completed NYSE session, active Unified session, Review/Kell session and deployment identity; a stale upstream remains visibly stale even when a refresh command succeeds. |
| 2 | Trigger the sole Review publisher after verified Unified activation, retaining a low-frequency schedule as recovery. | Request the exact source run/session; verify the Vercel pointer and immutable archive match before notifying. Repeated requests remain idempotent. |
| 3 | Make Morning Recovery actively request a Trend Birth refresh after EOD. | A scoped cross-repository credential dispatches the existing publisher; recovery then waits for its exact deployment. Waiting for a periodic refresh alone must not consume the current 15-minute timeout. |
| 4 | Reconcile runtime locks with the editable package's constraints. | The installed yfinance version matches a deliberate lock. Current runner logs install yfinance 1.7.0 and then downgrade to 0.2.66 during editable installation, weakening reproducibility. Treat upgrading the provider as a separate compatibility change. |

Keep the existing daily tracked-watchlist enrichment, immutable archives and
delivery reservations. The proposals above concern orchestration and visibility;
they do not imply changing Trend Birth's signals or enabling its weekly-v2 mode.
