import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { z } from "zod";
import { FILTER_OPERATORS, MODES, type JsonRecord, type ModeId, type ScanFilter } from "./contracts.js";
import { PagesScanRepository, pathValueForOutput } from "./pagesRepository.js";

const modeSchema = z.enum(MODES);
const recordSchema = z.record(z.string(), z.unknown());
const filterSchema = z.object({ field: z.string().regex(/^[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)*$/), op: z.enum(FILTER_OPERATORS), value: z.unknown().optional() });
const sortSchema = z.object({ field: z.string().regex(/^[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)*$/), direction: z.enum(["asc", "desc"]).default("asc") });
const annotations = { readOnlyHint: true, openWorldHint: false, destructiveHint: false, idempotentHint: true };

function response<T extends JsonRecord>(structuredContent: T, summary: string) { return { structuredContent, content: [{ type: "text" as const, text: summary }] }; }
async function context(repository: PagesScanRepository, mode: ModeId) {
  const unified = await repository.unified(), pointer = unified.modes[mode];
  return { run_id: unified.runId, scan_date: unified.sessionDate, health_status: unified.status, mode, price_basis: pointer.priceBasis, ranking: pointer.ranking, prices_are_live: false };
}
function summary(scan: JsonRecord) { return `Scan ${String(scan.scan_date)} is ${String(scan.health_status)} in ${String(scan.mode)} mode; prices are not live.`; }
function resultRow(row: JsonRecord) { return { id: String(row.id), title: `${String(row.ticker)} — ${String(row.primarySetup ?? row.setup ?? "scan candidate")}`, url: String(row.canonicalUrl) }; }
function naturalFilters(query: string): ScanFilter[] {
  const filters: ScanFilter[] = [];
  if (/\bentry[-_ ]ready\b/i.test(query)) filters.push({ field: "tradeStatus", op: "eq", value: "entry_ready" });
  const risk = /(?:risk|rizik)[^0-9]{0,20}(?:<=|≤|najvi(?:š|s)e|max(?:imum)?)?\s*(\d+(?:\.\d+)?)/i.exec(query);
  if (risk?.[1]) filters.push({ field: "entryRiskPct", op: "lte", value: Number(risk[1]) });
  if (/\brwb\b/i.test(query)) filters.push({ field: "setupNames", op: "contains", value: "rwb_squeeze_thrust" });
  if (/\bcrash[-_ ]base\b/i.test(query)) filters.push({ field: "primarySetup", op: "contains", value: "crash_base" });
  if (/\baccumulation\b/i.test(query)) filters.push({ field: "primarySetup", op: "contains", value: "accumulation" });
  return filters;
}

export function createStockScoutServer(repository = new PagesScanRepository()): McpServer {
  const server = new McpServer({ name: "stockscout-unified", version: "1.0.0" }, { instructions: "StockScout Unified exposes three isolated EOD modes. Default to Bottom Fishing unless the user names Next or Ryan Original. Never compare or combine mode rankings. Always state scan date, mode, price basis, health, and that prices are not live. Show excluded/risk labels. Bottom setup criteria are not automatically backtested edge. Position sizing is allowed only for Bottom Fishing records with tradePlan.status=entry_ready and a valid tactical stop." });
  server.registerTool("list_modes", { title: "List scanner modes", description: "List the three isolated scanner modes, their price bases, health and ranking contracts.", inputSchema: {}, outputSchema: { scan: recordSchema, modes: z.array(recordSchema) }, annotations }, async () => { const unified = await repository.unified(); const modes = MODES.map(mode => unified.modes[mode]); return response({ scan: { run_id: unified.runId, scan_date: unified.sessionDate, health_status: unified.status, prices_are_live: false }, modes }, `Three isolated modes are available. Scan ${unified.sessionDate} is ${unified.status}; prices are not live.`); });
  server.registerTool("search", { title: "Search a StockScout Unified scan", description: "Standard read-only search over one mode. Defaults to Bottom Fishing and preserves that mode's scan order.", inputSchema: { query: z.string().min(1).max(500), mode: modeSchema.default("bottom-fishing") }, outputSchema: { scan: recordSchema, results: z.array(z.object({ id: z.string(), title: z.string(), url: z.string().url() })) }, annotations }, async ({ query, mode }) => { const filters = naturalFilters(query); const rows = await repository.search(mode, query, filters); const scan = await context(repository, mode); return response({ scan, results: rows.map(resultRow) }, `Found ${rows.length} documents. ${summary(scan)}`); });
  server.registerTool("fetch", { title: "Fetch a complete scan candidate", description: "Standard read-only fetch for one stable mode-scoped candidate ID.", inputSchema: { id: z.string().regex(/^scan:.+:mode:(bottom-fishing|next|ryan-original):candidate:[A-Z0-9._-]{1,20}$/) }, outputSchema: { scan: recordSchema, id: z.string(), title: z.string(), text: z.string(), url: z.string().url(), metadata: recordSchema }, annotations }, async ({ id }) => { const found = await repository.fetchDocument(id); if (!found) throw new Error("Scan candidate not found"); const scan = await context(repository, found.mode), document = resultRow(found.row); return response({ scan, ...document, text: JSON.stringify(found.row, null, 2), metadata: { ticker: found.row.ticker, mode: found.mode, excluded: found.row.excluded === true, risk_level: found.row.riskLevel ?? found.row.risk_level ?? null, trade_status: found.row.tradeStatus ?? found.row.trade_status ?? null, price_type: "scan" } }, `Loaded ${String(found.row.ticker)}. ${summary(scan)}`); });
  server.registerTool("describe_scan_fields", { title: "Describe fields in one scan mode", description: "List allowlisted scalar dotted paths before building an exact screen.", inputSchema: { mode: modeSchema.default("bottom-fishing"), query: z.string().max(200).optional() }, outputSchema: { scan: recordSchema, records: z.array(recordSchema) }, annotations }, async ({ mode, query }) => { const records = await repository.fieldCatalog(mode, query), scan = await context(repository, mode); return response({ scan, records }, `Found ${records.length} scalar fields. ${summary(scan)}`); });
  server.registerTool("screen_scan", { title: "Screen one StockScout Unified mode", description: "Apply allowlisted filters and up to three explicit sorts within one mode. Never performs cross-mode ranking.", inputSchema: { mode: modeSchema.default("bottom-fishing"), filters: z.array(filterSchema).max(20).default([]), sort: z.array(sortSchema).max(3).default([]), limit: z.number().int().min(1).max(100).default(20) }, outputSchema: { scan: recordSchema, records: z.array(recordSchema) }, annotations }, async ({ mode, filters, sort, limit }) => { const rows = await repository.screen(mode, filters, sort, limit), requested = new Set([...filters.map(item => item.field), ...sort.map(item => item.field)]), scan = await context(repository, mode); const records = rows.map(row => ({ ...resultRow(row), values: { ticker: row.ticker, excluded: row.excluded === true, ...Object.fromEntries([...requested].map(path => [path, pathValueForOutput(row, path)])) } })); return response({ scan, records }, `Returned ${records.length} mode-isolated candidates. ${summary(scan)}`); });
  return server;
}
