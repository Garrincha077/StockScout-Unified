import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import test from "node:test";
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { InMemoryTransport } from "@modelcontextprotocol/sdk/inMemory.js";
import { MODES, TOOL_NAMES, type JsonRecord } from "../src/contracts.js";
import { createStockScoutServer } from "../src/mcpServer.js";
import { PagesScanRepository } from "../src/pagesRepository.js";

const base = "https://example.test/data/", runId = "2026-08-24-eod", date = "2026-08-24";
const encoded = (value: unknown) => Buffer.from(JSON.stringify(value));
const sha = (value: Buffer) => createHash("sha256").update(value).digest("hex");

function fixture() {
  const assets = new Map<string, Buffer>(), pointers: JsonRecord = {};
  for (const mode of MODES) {
    const row = { id: `scan:${runId}:mode:${mode}:candidate:AAA`, canonicalUrl: `https://garrincha077.github.io/StockScout-Unified/ticker/AAA?run=${runId}&mode=${mode}`, ticker: "AAA", mode, scanOrder: 0, excluded: false, primarySetup: mode === "bottom-fishing" ? "rwb_squeeze_thrust" : `${mode}_setup`, setupNames: mode === "bottom-fishing" ? ["rwb_squeeze_thrust"] : [], tradeStatus: mode === "bottom-fishing" ? "entry_ready" : "insufficient_data", entryRiskPct: mode === "bottom-fishing" ? 8.2 : null };
    const detail = { ...row, setupHits: { rwb_squeeze_thrust: { triggered: mode === "bottom-fishing", bundle_width_pct: 2.4 } } };
    const prefix = `modes/${mode}/runs/${runId}/`;
    assets.set(`${prefix}core.json`, encoded({ generatedAt: `${date}T22:00:00Z`, universe: [row], detailShards: { AAA: "000" } }));
    assets.set(`${prefix}excluded.json`, encoded({ rows: [] }));
    assets.set(`${prefix}details/000.json`, encoded({ AAA: detail })); assets.set(`${prefix}details/001.json`, encoded({}));
    const manifest = { manifestVersion: 1, schemaVersion: "stockscout-eod/v1", mode, runId, sessionDate: date, marketDataDate: date, generatedAt: `${date}T22:00:00Z`, status: "healthy", priceMode: mode === "bottom-fishing" ? "split_only" : "split_div", health: { status: "healthy" }, counts: { candidates: 1, excluded: 0 }, assets: { core: { path: `runs/${runId}/core.json`, sha256: "a", bytes: 1 }, excluded: { path: `runs/${runId}/excluded.json`, sha256: "b", bytes: 1 }, history: { path: `runs/${runId}/history.json`, sha256: "c", bytes: 1 }, details: { path: `runs/${runId}/details`, sha256: "d", bytes: 1, bucketCount: 2, pattern: "{bucket}.json" } } };
    const manifestBytes = encoded(manifest); assets.set(`modes/${mode}/manifest.json`, manifestBytes);
    pointers[mode] = { mode, label: mode, priceBasis: manifest.priceMode, status: "healthy", manifestPath: `modes/${mode}/manifest.json`, manifestSha256: sha(manifestBytes), candidates: 1, excluded: 0, chartCoveragePct: 100, ranking: `${mode}-order` };
  }
  assets.set("manifest.json", encoded({ schemaVersion: "stockscout-unified/v1", runId, sessionDate: date, generatedAt: `${date}T22:00:00Z`, status: "healthy", defaultMode: "bottom-fishing", modes: pointers }));
  const fetcher = (async (input: string | URL | Request) => { const url = new URL(typeof input === "string" || input instanceof URL ? input.toString() : input.url), body = assets.get(url.pathname.replace("/data/", "")); return body ? new Response(new Uint8Array(body), { status: 200, headers: { "content-type": "application/json" } }) : new Response("missing", { status: 404 }); }) as typeof fetch;
  return new PagesScanRepository(base, fetcher);
}

async function client() { const server = createStockScoutServer(fixture()), connected = new Client({ name: "test", version: "1" }), [clientTransport, serverTransport] = InMemoryTransport.createLinkedPair(); await server.connect(serverTransport); await connected.connect(clientTransport); return { server, connected }; }

test("MCP exposes exactly five read-only, mode-isolated tools", async () => { const { server, connected } = await client(); try { const listed = await connected.listTools(); assert.deepEqual(listed.tools.map(tool => tool.name).sort(), [...TOOL_NAMES].sort()); for (const tool of listed.tools) assert.equal(tool.annotations?.readOnlyHint, true, tool.name); const modes = (await connected.callTool({ name: "list_modes", arguments: {} })).structuredContent as { modes: JsonRecord[] }; assert.equal(modes.modes.length, 3); } finally { await connected.close(); await server.close(); } });

test("search defaults to Bottom Fishing and fetch preserves stable mode IDs", async () => { const { server, connected } = await client(); try { const searched = (await connected.callTool({ name: "search", arguments: { query: "RWB entry ready risk max 10" } })).structuredContent as { scan: JsonRecord; results: JsonRecord[] }; assert.equal(searched.scan.mode, "bottom-fishing"); assert.equal(searched.scan.prices_are_live, false); assert.equal(searched.results[0]?.id, `scan:${runId}:mode:bottom-fishing:candidate:AAA`); const fetched = (await connected.callTool({ name: "fetch", arguments: { id: searched.results[0]?.id } })).structuredContent as { scan: JsonRecord; text: string }; assert.equal(fetched.scan.scan_date, date); assert.match(fetched.text, /bundle_width_pct/); } finally { await connected.close(); await server.close(); } });

test("numeric nested filters work and arbitrary paths are rejected", async () => { const { server, connected } = await client(); try { const screened = (await connected.callTool({ name: "screen_scan", arguments: { mode: "bottom-fishing", filters: [{ field: "setupHits.rwb_squeeze_thrust.bundle_width_pct", op: "lte", value: 3 }] } })).structuredContent as { records: JsonRecord[] }; assert.equal(screened.records.length, 1); const rejected = await connected.callTool({ name: "screen_scan", arguments: { mode: "next", filters: [{ field: "record.secret", op: "eq", value: "x" }] } }); assert.equal(rejected.isError, true); } finally { await connected.close(); await server.close(); } });
