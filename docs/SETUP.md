# StockScout Unified deployment

## GitHub Pages and EOD workflow

The Pages source must be **GitHub Actions**. The workflow runs at 21:30 UTC on weekdays and uses the NYSE calendar to select one completed session. A manual run accepts `scan_date`; `notify` defaults to `false`.

Repository variables:

- `VITE_SUPABASE_URL` — new owner-state project URL;
- `VITE_SUPABASE_PUBLISHABLE_KEY` — browser-safe publishable key;
- `UNIFIED_DELIVERY_ENDPOINT` — `https://<project>.supabase.co/functions/v1/unified-operations`.

Repository secrets:

- `TELEGRAM_BOT_TOKEN`;
- `TELEGRAM_CHAT_ID`.

No service-role key or provider cache belongs in GitHub. The `production` environment and `codex/unified-app` branch must be protected before the OIDC operations endpoint will accept delivery or alert requests.

## Owner state

Create a new Supabase project, apply `supabase/migrations`, and deploy `unified-operations` with JWT verification disabled at the platform gateway—the function verifies GitHub OIDC itself. Set the function secret `UNIFIED_PAGES_BASE_URL=https://garrincha077.github.io/StockScout-Unified`.

In Auth:

1. create or invite the single owner email;
2. disable public signup and password/reset flows;
3. allow the GitHub Pages root as a redirect URL;
4. insert that user UUID into `stockscout_unified_api.owner_allowlist`;
5. expose only the `stockscout_unified_api` schema to the Data API.

The browser receives only the publishable key. Every owner table uses `auth.uid()`, an explicit allowlist, RLS, explicit grants, and mode/price-basis columns.

## ChatGPT MCP

The stateless MCP is deployed as the separate Vercel project
`stockscout-unified-mcp`; its stable endpoint is
`https://stockscout-unified-mcp.vercel.app/mcp`. `SCAN_BASE_URL` defaults to
the public Pages `/data/` root. The MCP has no Supabase or broker credential.

Connect the stable `/mcp` endpoint in ChatGPT developer mode. The tools are `list_modes`, `describe_scan_fields`, `screen_scan`, `search`, and `fetch`. Bottom Fishing is the default and cross-mode ranking is intentionally unavailable.

## Cutover

Start with a manual workflow run using `notify=false`. Keep the local 22:30 task and all older repositories enabled until five consecutive completed sessions pass parity and one scheduled live run passes Pages, MCP, alert, and multipart Telegram verification. Do not delete the fallbacks for at least 30 days.
