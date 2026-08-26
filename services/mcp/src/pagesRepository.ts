import { createHash } from "node:crypto";
import type { Asset, JsonRecord, ModeId, ModeManifest, ScanFilter, ScanSort, UnifiedManifest } from "./contracts.js";

type FetchLike = typeof fetch;
const FIELD_PATTERN = /^[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)*$/;

function record(value: unknown): JsonRecord { return value && typeof value === "object" && !Array.isArray(value) ? value as JsonRecord : {}; }
function text(value: unknown): string { return typeof value === "string" ? value : ""; }
function ticker(value: unknown): string { return text(value).trim().toUpperCase(); }
function scalar(value: unknown): boolean { return value === null || ["string", "number", "boolean"].includes(typeof value); }
function pathValue(source: unknown, path: string): unknown {
  let value = source;
  for (const part of path.split(".")) value = value && typeof value === "object" && !Array.isArray(value) ? (value as JsonRecord)[part] : undefined;
  return value;
}
function compare(left: unknown, right: unknown): number {
  if (left == null && right == null) return 0;
  if (left == null) return 1;
  if (right == null) return -1;
  if (typeof left === "number" && typeof right === "number") return left - right;
  return String(left).localeCompare(String(right), undefined, { numeric: true, sensitivity: "base" });
}
function matches(value: unknown, filter: ScanFilter): boolean {
  const expected = filter.value;
  switch (filter.op) {
    case "eq": return value === expected || String(value ?? "").toLowerCase() === String(expected ?? "").toLowerCase();
    case "ne": return !matches(value, { ...filter, op: "eq" });
    case "gt": return Number(value) > Number(expected);
    case "gte": return Number(value) >= Number(expected);
    case "lt": return Number(value) < Number(expected);
    case "lte": return Number(value) <= Number(expected);
    case "in": return Array.isArray(expected) && expected.some(item => matches(value, { field: filter.field, op: "eq", value: item }));
    case "contains": return Array.isArray(value)
      ? value.some(item => String(item).toLowerCase().includes(String(expected ?? "").toLowerCase()))
      : String(value ?? "").toLowerCase().includes(String(expected ?? "").toLowerCase());
    case "is_true": return value === true;
    case "is_false": return value === false;
  }
}

export class PagesScanRepository {
  private readonly cache = new Map<string, Promise<unknown>>();
  readonly baseUrl: URL;
  constructor(baseUrl = process.env.SCAN_BASE_URL ?? "https://garrincha077.github.io/StockScout-Unified/data/", private readonly fetcher: FetchLike = fetch) {
    this.baseUrl = new URL(baseUrl);
    if (this.baseUrl.protocol !== "https:") throw new Error("SCAN_BASE_URL must use HTTPS");
    if (!this.baseUrl.pathname.endsWith("/")) this.baseUrl.pathname += "/";
  }

  private json<T>(url: URL): Promise<T> {
    const key = url.toString();
    const current = this.cache.get(key);
    if (current) return current as Promise<T>;
    const request = this.fetcher(url, { headers: { Accept: "application/json" } }).then(async response => {
      if (!response.ok) throw new Error(`Pages asset ${url.pathname} returned HTTP ${response.status}`);
      return await response.json() as T;
    }).catch(error => { this.cache.delete(key); throw error; });
    this.cache.set(key, request);
    return request;
  }

  unified(): Promise<UnifiedManifest> { return this.json(new URL("manifest.json", this.baseUrl)); }
  private modeRoot(mode: ModeId): URL { return new URL(`modes/${mode}/`, this.baseUrl); }
  async modeManifest(mode: ModeId): Promise<ModeManifest> {
    const unified = await this.unified();
    const pointer = unified.modes[mode];
    if (!pointer || pointer.status !== "healthy") throw new Error(`Mode ${mode} is unavailable`);
    const url = new URL(pointer.manifestPath, this.baseUrl);
    const response = await this.fetcher(url, { headers: { Accept: "application/json" } });
    if (!response.ok) throw new Error(`Mode manifest returned HTTP ${response.status}`);
    const bytes = Buffer.from(await response.arrayBuffer());
    if (createHash("sha256").update(bytes).digest("hex") !== pointer.manifestSha256) throw new Error(`Mode ${mode} manifest hash mismatch`);
    const manifest = JSON.parse(bytes.toString("utf8")) as ModeManifest;
    if (manifest.runId !== unified.runId || manifest.sessionDate !== unified.sessionDate || manifest.mode !== mode || manifest.status !== "healthy") throw new Error(`Mode ${mode} identity mismatch`);
    return manifest;
  }
  private assetUrl(mode: ModeId, asset: Asset, path = asset.path): URL { return new URL(path.replace(/^\//, ""), this.modeRoot(mode)); }
  async core(mode: ModeId): Promise<JsonRecord> { const manifest = await this.modeManifest(mode); return this.json(this.assetUrl(mode, manifest.assets.core)); }
  async excluded(mode: ModeId): Promise<JsonRecord[]> {
    const manifest = await this.modeManifest(mode), payload = record(await this.json(this.assetUrl(mode, manifest.assets.excluded)));
    return Array.isArray(payload.rows) ? payload.rows.map(record) : [];
  }
  async summaries(mode: ModeId): Promise<JsonRecord[]> {
    const core = await this.core(mode), rows = Array.isArray(core.universe) ? core.universe.map(record) : [];
    return [...rows, ...(await this.excluded(mode))];
  }
  async details(mode: ModeId): Promise<JsonRecord[]> {
    const manifest = await this.modeManifest(mode), count = manifest.assets.details.bucketCount ?? 128;
    const pattern = manifest.assets.details.pattern ?? "{bucket}.json";
    const urls = Array.from({ length: count }, (_, index) => {
      const bucket = String(index).padStart(3, "0");
      return this.assetUrl(mode, manifest.assets.details, `${manifest.assets.details.path}/${pattern.replace("{bucket}", bucket)}`);
    });
    const shards = await Promise.all(urls.map(url => this.json<JsonRecord>(url)));
    return shards.flatMap(shard => Object.values(shard).map(record));
  }
  async all(mode: ModeId, fields: string[] = []): Promise<JsonRecord[]> {
    const summaries = await this.summaries(mode);
    const needsDetails = fields.some(path => summaries.some(row => pathValue(row, path) === undefined));
    if (!needsDetails) return summaries;
    const details = new Map((await this.details(mode)).map(row => [ticker(row.ticker), row]));
    return summaries.map(row => ({ ...row, ...(details.get(ticker(row.ticker)) ?? {}) }));
  }
  async fetchDocument(id: string): Promise<{ mode: ModeId; row: JsonRecord } | null> {
    const match = /^scan:(.+):mode:(bottom-fishing|next|ryan-original):candidate:([A-Z0-9._-]{1,20})$/.exec(id);
    if (!match) return null;
    const mode = match[2] as ModeId, symbol = match[3] ?? "";
    const rows = await this.all(mode, ["ticker", "setupHits"]);
    const row = rows.find(candidate => ticker(candidate.ticker) === symbol);
    return row ? { mode, row } : null;
  }
  async fieldCatalog(mode: ModeId, query?: string): Promise<JsonRecord[]> {
    const rows = await this.all(mode, ["setupHits"]), catalog = new Map<string, { types: Set<string>; count: number; example: unknown }>();
    const visit = (value: unknown, prefix: string) => {
      if (Array.isArray(value) && value.every(scalar)) {
        if (!prefix || !FIELD_PATTERN.test(prefix)) return;
        const item = catalog.get(prefix) ?? { types: new Set<string>(), count: 0, example: value };
        item.types.add("array"); item.count += 1; catalog.set(prefix, item); return;
      }
      if (scalar(value)) {
        if (!prefix || !FIELD_PATTERN.test(prefix)) return;
        const item = catalog.get(prefix) ?? { types: new Set<string>(), count: 0, example: value };
        item.types.add(value === null ? "null" : typeof value); item.count += 1;
        if (item.example == null && value != null) item.example = value; catalog.set(prefix, item); return;
      }
      if (value && typeof value === "object" && !Array.isArray(value)) for (const [key, child] of Object.entries(value)) visit(child, prefix ? `${prefix}.${key}` : key);
    };
    for (const row of rows) visit(row, "");
    const needle = query?.trim().toLowerCase();
    return [...catalog.entries()]
      .filter(([field]) => !needle || field.toLowerCase().includes(needle))
      .sort(([left], [right]) => left.localeCompare(right))
      .slice(0, 500)
      .map(([field, item]) => ({ field, types: [...item.types].sort(), count: item.count, example: item.example }));
  }
  async screen(mode: ModeId, filters: ScanFilter[], sort: ScanSort[], limit: number): Promise<JsonRecord[]> {
    const fields = [...filters.map(item => item.field), ...sort.map(item => item.field)];
    if (fields.some(field => !FIELD_PATTERN.test(field))) throw new Error("Filter and sort fields must be safe dotted paths");
    const allowed = new Set((await this.fieldCatalog(mode)).map(item => String(item.field)));
    for (const field of fields) if (!allowed.has(field)) throw new Error(`Unknown or non-scalar scan field: ${field}`);
    const rows = (await this.all(mode, fields)).filter(row => filters.every(filter => matches(pathValue(row, filter.field), filter)));
    if (sort.length) rows.sort((left, right) => { for (const item of sort) { const order = compare(pathValue(left, item.field), pathValue(right, item.field)); if (order) return item.direction === "desc" ? -order : order; } return Number(left.scanOrder ?? 0) - Number(right.scanOrder ?? 0); });
    else rows.sort((left, right) => Number(left.scanOrder ?? 0) - Number(right.scanOrder ?? 0));
    return rows.slice(0, limit);
  }
  async search(mode: ModeId, query: string, filters: ScanFilter[] = []): Promise<JsonRecord[]> {
    if (filters.length) return this.screen(mode, filters, [], 20);
    const needle = query.trim().replace(/^\$/, "").toLowerCase(), rows = await this.summaries(mode);
    return rows.filter(row => JSON.stringify(row).toLowerCase().includes(needle)).slice(0, 20);
  }
}

export function pathValueForOutput(source: unknown, path: string): unknown { return pathValue(source, path); }
