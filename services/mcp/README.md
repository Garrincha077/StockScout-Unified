# StockScout Unified MCP

Stateless, read-only MCP over the exact active GitHub Pages scan. It exposes
`list_modes`, `search`, `fetch`, `describe_scan_fields`, and `screen_scan`.
Each request is restricted to Bottom Fishing, Next, or Ryan Original; rankings
are never mixed.

The default asset root is
`https://garrincha077.github.io/StockScout-Unified/data/`. Override it with
`SCAN_BASE_URL` for preview deployments and contract tests.

No Supabase credential, OAuth callback, password reset, broker socket, or write
capability exists in this service.

```powershell
npm ci
npm test
npm run build
```

Deploy `services/mcp` as its own Vercel project on Node.js 22. The ChatGPT MCP
URL is the stable HTTPS `/mcp` route.
