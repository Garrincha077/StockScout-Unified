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

## 2026-08-27 — Next/Ryan parity, owner UX, and delivery hardening

- Updated the reviewed Next engine pin to
  `528386109c5991ab8443ece446f85a48cc1e9c53` and refreshed normalized critical-file
  hashes. Upstream `main` has since advanced; the new drift workflow reports that
  condition without importing code automatically.
- Made the validated Next Groups aggregate publication-critical and added isolated
  Screener, Groups, Factors, and GMLI views. Factor and liquidity contexts are
  hash-bound read-only sidecars with provenance, freshness, and last-good fallback;
  ranking, trade-plan, Ryan baseline, and price-basis fields remain unchanged.
- Reintroduced shared candlestick/volume/moving-average charts in Ryan Original,
  including daily/weekly views, read-only Ryan levels, owner drawings/alerts, retry
  states, and guarded Space navigation through the visible result order.
- Replaced the dead resize implementation with accessible persisted splitters and
  a mobile stacked layout. Added lazy context/chart surfaces, request de-duplication,
  responsive owner dialogs, typed v1 alerts, stale/error/loading states, focus and
  reduced-motion support, and removed prompt/raw-alert editing flows.
- Hardened owner RLS with explicit authenticated/non-null checks and an 18-assertion
  pgTAP allow/deny suite. Scheduled delivery now fails closed on missing/partial
  owner configuration; manual `notify=false` remains usable for dry verification.
- Added required PR CI, full-SHA action pins, Dependabot coverage, a hash-locked
  vendored Next environment, upstream drift reporting, reusable Pages deploy/smoke,
  exact active-run verification, and recovery publication that preserves historical
  source provenance.
- Local verification: Ruff passed; 156 Python tests passed with 2 intentional skips;
  59 frontend tests and the production build passed; 6 desktop/mobile Playwright
  fixtures, 3 MCP tests, and 3 Deno Edge tests passed. The live context builder
  produced valid fresh Factors and GMLI sidecars. Local database RLS execution awaits
  Docker, while Required CI provisions the local Supabase stack automatically.
- Provisioned the separate `StockScout Unified Owner State` Supabase project
  (`whmjhpaxpcepmpdykrdt`) in `eu-central-1`, exposed only the dedicated API schema,
  applied the cloud-aligned migration history, deployed the OIDC-only operations
  function, created one confirmed allowlisted owner, and set the three browser/
  delivery repository variables. A rollback-only cloud RLS smoke test proved owner
  read/write and non-owner denial; the security advisor reported no RLS findings.
- Production cutover remains intentionally pending until hosted Auth uses the exact
  Unified Pages site/redirect URL with public signup disabled, the PR CI check is
  required on `main`, a manual `notify=false` run succeeds, and five consecutive
  scheduled sessions pass.

## 2026-08-27 — Unified filter-contract EOD hotfix

- The first post-merge `notify=false` EOD run completed Bottom Fishing and the
  full Next/Ryan scan, then failed during the sortable-field audit because that
  audit still assumed the upstream Next frontend lived under `engines/next`.
- Made the audit resolve both the upstream layout and Unified's repository-root
  shared frontend, matching the portability contract already used by the payload
  publisher. Added a regression test that loads the real Unified Filter Builder
  fields; ranking, scoring, price basis, and source repositories are unchanged.

## 2026-08-27 — Scan-free reuse publication hardening

- The first reuse publication correctly rejected the mutable upstream Pages URL:
  it had advanced to the 2026-08-27 Next session while the pinned reuse snapshot
  was 2026-08-25. No mixed-session data was published.
- Preserved the exact 2026-08-25 Next Pages artifact from upstream workflow
  `32903235954` as a SHA-256-pinned asset in the Unified reuse release and changed
  `publish-existing` to verify the release asset, embedded manifest, source commit,
  workflow run, chart aggregate, and byte count before publishing. This keeps CI
  independent of cross-repository artifact permissions and avoids a new market scan.

## 2026-08-28 — Drawing-first alerts and full Bottom surfaces

- Replaced interval-local drawing snapshots with UTC-anchored drawing payload v2.
  New drawings are shared across Daily and Weekly, and the SVG renderer and Edge
  evaluator now use the same geometry module for lines, rays, channels, zones and
  Fibonacci levels. Existing v1 drawings remain readable without a forced rewrite.
- Made drawing alerts reference their drawing row instead of copying geometry. Added
  armed/rearm runtime state, alert badges and inspector states, edit resets, linked
  Telegram context/deep links, and owner-safe composite foreign keys with cascade.
- Consolidated all three modes on one persistent chart engine with OHLCV crosshair,
  preserved viewport, retry/fullscreen/freshness context and mode-specific owner tools.
  Ryan Original is read-only and routes drawing work to the same ticker in Next.
- Ported the exact 19 Bottom built-in screens from the original read-only source,
  including nested ANY/ALL semantics and priority sorting. Published an immutable,
  hash-bound Bottom sidecar with the original scalar fields plus read-only trade-plan
  projections; the UI exposes grouped source filters and columns without touching
  the Bottom scorer or ranking.
- Added the standalone responsive `StockScout-Bottom-Fishing` PWA. It reads the same
  active Unified run, sidecar and chart shards, shares the owner Supabase state, and
  provides Overview, Screener, all 19 presets, setup/stage/group lenses, watchlists,
  saved screens, alerts and ticker details without a second scan or backend copy.
- Hardened `publish-existing` with an explicit registered run ID and a documented
  `scan_invoked=false` contract; upstream drift now distinguishes data-only changes
  from code/config/schema/workflow changes.
- Local verification: Ruff passed; 157 Python tests passed with 2 intentional skips;
  64 Unified frontend tests and production build passed; 6 Playwright desktop/mobile
  fixtures, 6 Deno Edge tests, and 2 Bottom PWA tests/builds passed. The 2026-08-25
  production fixture gives real matches for 18/19 exact Bottom screens; the Strict
  RWB screen correctly has no candidate satisfying all of its original thresholds.

## 2026-08-28 — Drawing alert FK advisor follow-up

- Added a covering `(drawing_id, user_id)` index for the owner-safe drawing-alert foreign key after the Supabase performance advisor flagged the original query-oriented partial index as non-covering.
- This is a schema-only, backward-compatible optimization; alert payloads, ranking, scoring, and market-data processing are unchanged.

## 2026-08-28 — Auth provider-aware fallback

- Unified now reads Supabase's public Auth settings once and only offers Google sign-in when that provider is actually enabled.
- Until Google OAuth credentials are configured, the owner panel presents the working magic-link path directly with a 60-second client countdown and an explicit built-in email quota warning instead of a dead Google button.

## 2026-08-28 — GitHub Pages runtime maintenance

- Upgraded the fully pinned Pages artifact and deployment actions to v5.0.0 across EOD and frontend-only publish workflows, removing their deprecated Node.js 20 runtimes without changing scan or deployment semantics.
- The standalone Bottom PWA now carries the verified active Unified run ID through every push or manual build, embeds it in the frontend contract, and verifies the same ID in the deployed JavaScript asset before its Pages job can pass.

## 2026-08-28 — Trendline future-projection clarity

- Extended the chart renderer into the visible future time-scale margin using the same UTC-day geometry as the EOD evaluator, so projected trendlines no longer appear to stop at the last market bar.
- New trendlines now follow the familiar forward-ray default. The drawing inspector exposes explicit Segment, Extend right, and Extend both controls and states that daily EOD alerts use the same projection.
- Existing v1 and v2 drawings retain their stored extension semantics. The live owner MEI trendline and its linked alert were verified read-only in Supabase; no alert was evaluated or delivered during this change.

## 2026-08-28 — Reversible TradingView-style cockpit QA

- Added a versioned, persistent Cockpit/Classic layout switch and preserved the
  pre-change tree as the `pre-tradingview-cockpit-20260828` tag and
  `rollback/pre-tradingview-cockpit-20260828` branch. The rollback is immediate
  in the UI and does not alter scan data, scoring, rankings or owner records.
- Aligned the desktop screener table and chart/detail pane to one viewport-height
  workspace, retained accessible splitters, moved detail evidence and Bottom trade
  setup into focused tabs, removed empty panel space and added useful empty states.
- Reworked narrow-screen navigation into a native mode selector, one Next context
  row and a five-item bottom bar with a More menu. Owner access is now part of the
  header, touch targets remain at least 44 px, and 320/390/768 px audits found no
  page-level horizontal overflow or sticky-header overlap.
- Added on-demand mobile Draw, Layers and Alert sheets so persistent drawing tools
  no longer cover the chart. Drawing alerts remain primary, price-only alerts stay
  under Advanced, and trendline projection remains explicitly selectable.
- Made Grid chart loading viewport-aware, increased future chart space for projected
  drawings, and changed Ryan mobile evidence into a collapsed drawer while keeping
  its chart first and its ticker table independently scrollable. Ryan remains
  read-only with Open in Next.
- Added a development-only proxy to the currently published immutable data so local
  visual QA exercises real Unified assets without checking generated market data
  into the repository.
- Verification: 67 frontend unit/contract tests and the production build passed;
  all 6 Playwright fixture tests passed in Pixel 5 and 1440 px desktop profiles.
  Real-data browser QA covered Next Screener/Groups/Factors, Bottom Fishing and Ryan
  at 320, 390, 768 and 1440 px. The GMLI fixture passed in Playwright; the external
  Browser QA extension itself blocks direct raw GMLI JSON navigation.

## 2026-08-29 — Bottom mobile chart height hotfix

- Fixed the stacked cockpit height chain where the resizable chart panel retained
  roughly 490 px but its content wrapper collapsed to about 32 px, leaving
  Lightweight Charts with a two-pixel canvas and a large empty Bottom chart.
- The mobile chart content and chart box now explicitly inherit the full panel
  height. Real-data checks show the Bottom and Next canvases filling the intended
  chart area with candlesticks, volume and published price levels intact.
- Added a Pixel 5 regression assertion that requires the resizable panel content
  to match its parent and the rendered chart canvas to remain taller than 250 px.
  No scan, scoring, ranking, trade-plan or owner-state behavior changed.
