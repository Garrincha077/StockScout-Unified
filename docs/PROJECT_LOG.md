# StockScout Unified project log

## 2026-08-25 — Isolated clean-history bootstrap

- Created a new repository instead of modifying any existing StockScout or
  StockScreener working tree.
- Locked the three independent modes and their source commits.
- Chose GitHub Pages for public scan/chart assets, a new Supabase project only
  for owner state, and a stateless Vercel MCP.
- No ranking, detector, trade-plan, or source repository was changed.

## 2026-08-25 — Three-mode implementation

- Added hash-bound `UnifiedManifestV1` activation and immutable, isolated mode assets.
- Vendored the pinned Next workflow and protected Ryan baseline; added source-file drift checks.
- Added responsive three-mode PWA routing, public charts, graphical owner drawings, magic-link owner state, and mode-aware alerts.
- Replaced the old scan-backed MCP with a stateless GitHub Pages MCP exposing five read-only tools.
- Added five resumable Telegram series and an allowlisted GitHub OIDC operations function for delivery state and alert evaluation.
- Added the weekday exchange-guarded scan/build/deploy workflow, mobile/desktop E2E coverage, security contract tests, and source parity tests.
- Verification: 136 Python tests passed (2 intentional skips), including 57 direct private-source parity tests (1 research-only skip); 52 frontend unit tests, 4 Playwright mobile/desktop tests, 3 MCP contract tests, and 3 Deno alert tests passed. Python lint and production builds passed.
- Deployed the stateless read-only MCP to `stockscout-unified-mcp.vercel.app`; its production health and MCP initialization contracts returned HTTP 200.

## 2026-08-26 — First cloud-run hardening

- The first completed-session run finished Bottom Fishing and the Next scan, then correctly stopped before publication because the inherited Next validator treated every pre-close invocation as a current-session publish.
- Bound that validator to the orchestrator-selected session: replay is accepted only when SPY and the universe's coherent modal session exactly match the requested prior session.
- Added a pre-Next Bottom checkpoint and enabled hidden-file inclusion for every `.staging` artifact so a downstream failure cannot silently discard the expensive scan result again.
- No replacement scan or notification was started. Targeted validation passed: 9 Next session-guard tests, 3 Unified workflow/snapshot tests, source-pin checks, Ryan baseline checks, and Python lint.

## 2026-08-26 — Scan-free workflow recovery

- Added a separate GitHub Actions recovery workflow that can publish one
  hash-pinned existing session without executing Bottom, Next, or Ryan scans.
- Verified the completed 2026-08-25 Bottom cloud snapshot (2,096 candidates,
  16 excluded) and exported 100% split-only chart coverage from the existing
  local DuckDB in read-only mode. Seven rows retain their older source date;
  freshness is reported explicitly as 99.67% rather than silently rewritten.
- Pinned the matching public Next/Ryan snapshot to its real source commit
  `a878b671e93617f3331604a8ea4eea592fddc6e4`; the previously recorded full
  hash did not exist, although its critical-file hashes were correct.
- The recovery workflow validates release, raw-scan, chart-manifest, session,
  price-basis, chart-coverage, and final Pages manifest hashes. It renders the
  Telegram series as a dry run but contains no delivery path.
- Next provenance now pins the engine source commit separately from the data
  snapshot commit. The former identifies the code that calculated the scan;
  the latter identifies the Git object containing the immutable 2026-08-25
  canonical JSON and chart shards.
- Because Next keeps its public chart shards in its Pages artifact rather than
  Git, recovery downloads all 128 shards from that exact public snapshot and
  verifies the manifest aggregate hash, byte count, deterministic ticker-to-
  shard mapping, non-empty series, and 100% universe coverage before publish.

## 2026-08-26 — Recovery publication verification

- Fixed the recovery-only publication failure: unified verification now accepts
  Bottom Fishing's manifest-backed gzip chart shards while retaining the
  directory-backed chart verification used by Next and Ryan Original.
- Added a regression fixture that activates all three isolated modes with the
  production Bottom chart layout and verifies the resulting public pointer.
- Recovery now propagates `notify=false` into the reusable workflow and skips
  Telegram rendering entirely in that case, so a notification-formatting error
  cannot block an otherwise verified scan-free publication.
