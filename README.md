# StockScout Unified

Installable, mobile-first EOD stock screening with three intentionally separate modes:

- **Bottom Fishing** — StockScout Stage 1 → Stage 2 discovery.
- **Next** — the current StockScreener-next workflow.
- **Ryan Original** — the frozen RyanJHamby/stock-screener methodology.

The modes share one application and one nightly orchestrator, but never share a ranking.
Bottom Fishing keeps split-only prices; Next and Ryan Original share adjusted OHLCV.

## What is included

- Responsive Grid, Table, Today/New, ticker detail, daily/weekly charts, and mode-specific “why”.
- Bottom Fishing trade-plan safety: trigger, structural invalidation, tactical stop, and sizing only when `entry_ready`.
- Owner-only synced watchlists, saved screens, chart drawings, and EOD alerts through a small Supabase state schema.
- Nine chart tools: trendline, ray, horizontal/vertical, rectangle, channel, Fibonacci, text, and measure; touch uses two taps and an explicit pan/select lock.
- Stateless read-only MCP for ChatGPT and five resumable Telegram series without truncation.
- One GitHub Actions workflow that activates a scan only after all three modes, asset hashes, charts, tests, and the PWA pass.

## Local verification

```powershell
$env:PYTHONPATH='src'
python -m pytest -q
python -m ruff check src tests scripts
npm ci --prefix frontend
npm run check --prefix frontend
npm run test:e2e --prefix frontend
npm ci --prefix services/mcp
npm test --prefix services/mcp
```

Test and manual scans must keep notifications disabled. Generated market data, reports, chart assets, state, and credentials are ignored by Git.

Deployment and owner-state setup are documented in [docs/SETUP.md](docs/SETUP.md). Provenance is pinned in [config/source_pins.json](config/source_pins.json).

The deployed read-only MCP endpoint is
[`https://stockscout-unified-mcp.vercel.app/mcp`](https://stockscout-unified-mcp.vercel.app/mcp).

Development happens on `codex/unified-app`. Existing StockScout and StockScreener
repositories are read-only sources and are never modified by this repository.
