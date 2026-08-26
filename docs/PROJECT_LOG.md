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
- Verification: 136 Python tests passed (2 intentional skips), 52 frontend unit tests passed, 4 Playwright mobile/desktop tests passed, 3 MCP contract tests passed, Python lint and production builds passed.
