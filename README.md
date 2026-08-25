# StockScout Unified

Mobile-first end-of-day stock screening with three intentionally separate modes:

- **Bottom Fishing** — StockScout Stage 1 → Stage 2 discovery.
- **Next** — the current StockScreener-next workflow.
- **Ryan Original** — the frozen RyanJHamby/stock-screener methodology.

The modes share one application and one nightly orchestrator, but never share a ranking.
Bottom Fishing keeps split-only prices; Next and Ryan Original share adjusted OHLCV.

Development happens on `codex/unified-app`. Existing StockScout and StockScreener
repositories are read-only sources and are never modified by this repository.

