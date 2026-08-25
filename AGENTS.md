# StockScout Unified

Public, mobile-first EOD screener with three isolated modes: Bottom Fishing,
Next, and Ryan Original.

## Invariants

- Never modify or push to the source StockScout, StockScreener-next,
  stock-screener2, or StockScout-EOD repositories.
- Never merge mode rankings or let one mode mutate another mode's score.
- Bottom Fishing uses `split_only`; Next and Ryan Original use adjusted OHLCV.
- Preserve the frozen detector, ranking, trade-plan, and Ryan baseline behavior.
- Test and manual scans default to notifications disabled.
- Never commit credentials, provider caches, reports, generated scan assets, or
  personal state.
- Sizing requires `entry_ready` and a valid tactical stop.
- Only claim a deployment healthy after verifying its exact run and manifest.

## Development

- Work on `codex/unified-app`.
- Use Node.js 22 and Python 3.12 in CI.
- Pin dependencies and commit lockfiles.
- Record meaningful implementation and verification results in
  `docs/PROJECT_LOG.md`.

