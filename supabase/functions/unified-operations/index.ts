import{evaluateDrawingGeometry,rearmAfterClear,type GeometryAlertCondition,type GeometryAlertTarget,type GeometryDrawing,type GeometryEvaluation}from'../_shared/chartGeometry.ts';
import{buildIndicatorSeries,confirmationsPass,crossed,isUpsloping,normalizedSlope,type IndicatorConfirmation,type IndicatorDirection,type IndicatorSeries,type IndicatorSignal}from'../_shared/indicatorSignals.ts';

const SCHEMA = "stockscout_unified_api";
const EXPECTED_ISSUER = "https://token.actions.githubusercontent.com";
const EXPECTED_AUDIENCE = "stockscout-unified-operations";
const EXPECTED_REPOSITORY = "Garrincha077/StockScout-Unified";
const EXPECTED_REF = "refs/heads/main";
const EXPECTED_WORKFLOW = `${EXPECTED_REPOSITORY}/.github/workflows/eod.yml@${EXPECTED_REF}`;
const UNIFIED_PAGES_BASE_URL = "https://garrincha077.github.io/StockScout-Unified";
const MODE_IDS = ["bottom-fishing", "next", "ryan-original"] as const;

type Json = null | boolean | number | string | Json[] | { [key: string]: Json };
type Claims = Record<string, unknown> & { iss: string; aud: string | string[]; exp: number };
export type AlertRow = {
  id: string;
  user_id: string;
  name: string;
  ticker: string | null;
  mode: (typeof MODE_IDS)[number];
  price_basis: string;
  payload: Record<string, unknown>;
  drawing_id?: string | null;
  updated_at?: string;
};
type PriceBar = { time: string; open: number; high: number; low: number; close: number };
type ModeDocument = { manifest: Record<string, unknown>; core: Record<string, unknown>; rows: Map<string, Record<string, unknown>> };
export type IndicatorEvaluation = {
  condition: boolean;
  relation: "triggered" | "clear" | "insufficient_history" | "not_completed" | "invalid";
  signal: IndicatorSignal | null;
  direction: IndicatorDirection | null;
  barDate: string | null;
  previousFast: number | null;
  currentFast: number | null;
  previousSlow: number | null;
  currentSlow: number | null;
  sma50DailySlope: number | null;
  sma30WeeklySlope: number | null;
  confirmations: Record<string, Json>;
};

function response(status: number, body: Json): Response {
  return new Response(status === 204 ? null : JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json", "cache-control": "no-store" },
  });
}

function bytes(value: string): ArrayBuffer {
  const padded = value.replace(/-/g, "+").replace(/_/g, "/").padEnd(Math.ceil(value.length / 4) * 4, "=");
  const decoded = atob(padded);
  const buffer = new ArrayBuffer(decoded.length);
  const view = new Uint8Array(buffer);
  for (let index = 0; index < decoded.length; index += 1) view[index] = decoded.charCodeAt(index);
  return buffer;
}

function decodePart<T>(value: string): T {
  return JSON.parse(new TextDecoder().decode(bytes(value))) as T;
}

async function verifyGithubOidc(request: Request): Promise<Claims> {
  const authorization = request.headers.get("authorization") ?? "";
  if (!authorization.startsWith("Bearer ")) throw new Error("missing GitHub OIDC bearer token");
  const token = authorization.slice(7);
  const parts = token.split(".");
  if (parts.length !== 3) throw new Error("invalid GitHub OIDC token");
  const header = decodePart<{ alg?: string; kid?: string }>(parts[0]);
  const claims = decodePart<Claims>(parts[1]);
  if (header.alg !== "RS256" || !header.kid) throw new Error("unsupported GitHub OIDC signature");
  if (claims.iss !== EXPECTED_ISSUER || !(Array.isArray(claims.aud) ? claims.aud : [claims.aud]).includes(EXPECTED_AUDIENCE)) throw new Error("invalid GitHub OIDC issuer or audience");
  if (claims.exp <= Math.floor(Date.now() / 1000)) throw new Error("expired GitHub OIDC token");
  const discovery = await fetch(`${EXPECTED_ISSUER}/.well-known/openid-configuration`).then((result) => result.json());
  const keys = await fetch(String(discovery.jwks_uri)).then((result) => result.json()) as { keys: Json[] };
  const jwk = keys.keys.find((candidate) => (candidate as Record<string, unknown>).kid === header.kid) as JsonWebKey | undefined;
  if (!jwk) throw new Error("GitHub OIDC signing key not found");
  const key = await crypto.subtle.importKey("jwk", jwk, { name: "RSASSA-PKCS1-v1_5", hash: "SHA-256" }, false, ["verify"]);
  const valid = await crypto.subtle.verify("RSASSA-PKCS1-v1_5", key, bytes(parts[2]), new TextEncoder().encode(`${parts[0]}.${parts[1]}`));
  if (!valid) throw new Error("invalid GitHub OIDC signature");
  if (claims.repository !== EXPECTED_REPOSITORY || claims.ref !== EXPECTED_REF || claims.workflow_ref !== EXPECTED_WORKFLOW || claims.environment !== "production" || String(claims.ref_protected) !== "true") throw new Error("GitHub OIDC workflow claims are outside the production allowlist");
  return claims;
}

function env(name: string): string {
  const value = Deno.env.get(name)?.trim();
  if (!value) throw new Error(`${name} is not configured`);
  return value;
}

async function database(path: string, init: RequestInit = {}): Promise<unknown> {
  const key = env("SUPABASE_SERVICE_ROLE_KEY");
  const result = await fetch(`${env("SUPABASE_URL")}/rest/v1/${path}`, {
    ...init,
    headers: {
      apikey: key,
      authorization: `Bearer ${key}`,
      "accept-profile": SCHEMA,
      "content-profile": SCHEMA,
      "content-type": "application/json",
      prefer: "return=representation,resolution=merge-duplicates",
      ...(init.headers ?? {}),
    },
  });
  const text = await result.text();
  if (!result.ok) throw new Error(`database ${result.status}: ${text.slice(0, 500)}`);
  return text ? JSON.parse(text) : null;
}

async function ownerId(): Promise<string> {
  const rows = await database("owner_allowlist?select=user_id&limit=2") as Array<{ user_id: string }>;
  if (rows.length !== 1) throw new Error("exactly one allowlisted owner is required");
  return rows[0].user_id;
}

function text(value: unknown, label: string, max = 120): string {
  if (typeof value !== "string" || !value.trim() || value.length > max) throw new Error(`${label} is invalid`);
  return value.trim();
}

function integer(value: unknown, label: string, minimum: number, maximum: number): number {
  const parsed = Number(value);
  if (!Number.isInteger(parsed) || parsed < minimum || parsed > maximum) throw new Error(`${label} is invalid`);
  return parsed;
}

async function deliveryGet(body: Record<string, unknown>): Promise<Json> {
  const user = await ownerId();
  const series = encodeURIComponent(text(body.series, "series"));
  const rows = await database(`unified_delivery_state?select=content_hash,total_parts,last_successful_part,completed_at&user_id=eq.${user}&channel=eq.telegram&series=eq.${series}`) as Array<Record<string, unknown>>;
  const row = rows[0];
  return row ? {
    contentHash: row.content_hash as string,
    totalParts: Number(row.total_parts),
    lastSuccessfulPart: Number(row.last_successful_part),
    completed: Boolean(row.completed_at),
  } : { contentHash: null, totalParts: 0, lastSuccessfulPart: 0, completed: false };
}

async function deliveryMark(body: Record<string, unknown>): Promise<Json> {
  const user = await ownerId();
  const series = text(body.series, "series");
  const contentHash = text(body.contentHash, "contentHash", 64);
  if (!/^[0-9a-f]{64}$/.test(contentHash)) throw new Error("contentHash is invalid");
  const totalParts = integer(body.totalParts, "totalParts", 1, 1000);
  const requested = integer(body.lastSuccessfulPart, "lastSuccessfulPart", 0, totalParts);
  const previous = await deliveryGet({ series }) as Record<string, unknown>;
  const same = previous.contentHash === contentHash && Number(previous.totalParts) === totalParts;
  const lastSuccessfulPart = same ? Math.max(Number(previous.lastSuccessfulPart), requested) : requested;
  const completed = lastSuccessfulPart === totalParts;
  await database("unified_delivery_state?on_conflict=user_id,channel,series", {
    method: "POST",
    body: JSON.stringify({
      user_id: user,
      channel: "telegram",
      series,
      content_hash: contentHash,
      total_parts: totalParts,
      last_successful_part: lastSuccessfulPart,
      completed_at: completed ? new Date().toISOString() : null,
    }),
  });
  return { contentHash, totalParts, lastSuccessfulPart, completed };
}

function nested(record: Record<string, unknown>, path: string): unknown {
  if (!/^[A-Za-z][A-Za-z0-9_]{0,63}(\.[A-Za-z][A-Za-z0-9_]{0,63}){0,3}$/.test(path)) return undefined;
  const parts = path.split(".");
  if (parts.some((part) => ["__proto__", "prototype", "constructor"].includes(part))) return undefined;
  return parts.reduce<unknown>((value, key) => value && typeof value === "object" && !Array.isArray(value) ? (value as Record<string, unknown>)[key] : undefined, record);
}

function compare(actual: unknown, operator: string, expected: unknown): boolean {
  if (operator === "eq") return actual === expected;
  if (operator === "ne") return actual !== expected;
  if (operator === "contains") return Array.isArray(actual) ? actual.includes(expected) : String(actual ?? "").toLowerCase().includes(String(expected ?? "").toLowerCase());
  const left = Number(actual), right = Number(expected);
  if (!Number.isFinite(left) || !Number.isFinite(right)) return false;
  return operator === "gt" ? left > right : operator === "gte" ? left >= right : operator === "lt" ? left < right : operator === "lte" ? left <= right : false;
}

export function priceBar(value: unknown): PriceBar | null {
  const source = Array.isArray(value)
    ? { time: value[0], open: value[1], high: value[2], low: value[3], close: value[4] }
    : value && typeof value === "object" ? value as Record<string, unknown> : {};
  const timestamp = Number(source.time);
  const time = typeof source.time === "string"
    ? source.time.slice(0, 10)
    : Number.isFinite(timestamp) ? new Date(timestamp > 10_000_000_000 ? timestamp : timestamp * 1000).toISOString().slice(0, 10) : "";
  const open = Number(source.open), high = Number(source.high), low = Number(source.low), close = Number(source.close);
  return time && [open, high, low, close].every(Number.isFinite) ? { time, open, high, low, close } : null;
}

async function decodedJson(result: Response): Promise<Record<string, unknown>> {
  if (!result.ok) throw new Error(`chart request failed with HTTP ${result.status}`);
  const encoding = result.headers.get("content-encoding")?.toLowerCase();
  if (encoding === "gzip") return await result.json() as Record<string, unknown>;
  const bytes = await result.arrayBuffer();
  const gzip = new Uint8Array(bytes).slice(0, 2);
  if (gzip[0] === 0x1f && gzip[1] === 0x8b) {
    const stream = new Blob([bytes]).stream().pipeThrough(new DecompressionStream("gzip"));
    return JSON.parse(await new Response(stream).text()) as Record<string, unknown>;
  }
  return JSON.parse(new TextDecoder().decode(bytes)) as Record<string, unknown>;
}

async function loadBars(pages: string, mode: string, ticker: string, document: ModeDocument): Promise<PriceBar[]> {
  const assets = document.manifest.assets as Record<string, Record<string, unknown>>;
  const chartAsset = assets.charts;
  if (!chartAsset) return [];
  let payload: Record<string, unknown>;
  if (String(chartAsset.path).endsWith("manifest.json")) {
    const indexUrl = `${pages}/data/modes/${mode}/${String(chartAsset.path)}`;
    const index = await fetch(indexUrl, { cache: "no-store" }).then((result) => result.json()) as Record<string, unknown>;
    const byTicker = index.shardsByTicker as Record<string, string>;
    const shard = byTicker?.[ticker];
    if (!shard) return [];
    payload = await decodedJson(await fetch(`${String(index.storageBaseUrl).replace(/\/$/, "")}/shards/${shard}.json.gz`, { cache: "no-store" }));
  } else {
    const byTicker = document.core.chartShards as Record<string, string>;
    const shard = byTicker?.[ticker];
    if (!shard) return [];
    payload = await decodedJson(await fetch(`${pages}/data/modes/${mode}/${String(chartAsset.path).replace(/\/$/, "")}/${shard}`, { cache: "no-store" }));
  }
  const candidate = payload[ticker] as Record<string, unknown> | unknown[] | undefined;
  const rows = Array.isArray(candidate) ? candidate : Array.isArray(candidate?.daily) ? candidate.daily : [];
  return rows.map(priceBar).filter((bar): bar is PriceBar => bar !== null);
}

function levelTriggered(operator: string, level: number, bars: PriceBar[]): boolean {
  if (!bars.length || !Number.isFinite(level)) return false;
  const last = bars.at(-1)!, previous = bars.at(-2) ?? last;
  if (operator === "above" || operator === "greater_than") return last.close > level;
  if (operator === "below" || operator === "less_than") return last.close < level;
  if (operator === "touch") return last.low <= level && level <= last.high;
  if (operator === "crossing") return (previous.close <= level && level <= last.close) || (previous.close >= level && level >= last.close);
  return operator === "crossing_down" ? previous.close >= level && last.close < level : previous.close <= level && last.close > level;
}

function geometryDrawing(value:Record<string,unknown>):GeometryDrawing|null{
  const points=Array.isArray(value.points)?value.points.flatMap(point=>{
    if(!point||typeof point!=="object"||Array.isArray(point))return[];
    const row=point as Record<string,unknown>,time=String(row.time??""),price=Number(row.price);
    return /^\d{4}-\d{2}-\d{2}$/.test(time)&&Number.isFinite(price)?[{time,price}]:[];
  }):[];
  const type=String(value.type??"trendline") as GeometryDrawing["type"];
  if(points.length<2||!["trendline","ray","horizontal","vertical","rectangle","channel","fib","text","measure"].includes(type))return null;
  return{type,points,extend:(value.extend??(type==="ray"?"right":["trendline","horizontal","channel"].includes(type)?"both":"none"))as GeometryDrawing["extend"],fibLevels:Array.isArray(value.fibLevels)?value.fibLevels.map(Number).filter(Number.isFinite):undefined};
}

function drawingEvaluation(payload:Record<string,unknown>,drawingValue:Record<string,unknown>,bars:PriceBar[]):GeometryEvaluation{
  const drawing=geometryDrawing(drawingValue);
  if(!drawing)return{condition:false,relation:"invalid",previousPrice:null,currentPrice:null,previousLevel:null,currentLevel:null};
  const rawCondition=String(payload.condition??payload.operator??"touch").replace("breaking_up","break_up").replace("breaking_down","break_down");
  const condition=(rawCondition==="crossing"?"crossing_up":rawCondition==="inside"?"entering":rawCondition==="outside"?"exiting":rawCondition)as GeometryAlertCondition;
  const explicit=payload.target&&typeof payload.target==="object"&&!Array.isArray(payload.target)?payload.target as GeometryAlertTarget:null;
  if(explicit)return evaluateDrawingGeometry(drawing,explicit,condition,bars);
  if(drawing.type==="rectangle")return evaluateDrawingGeometry(drawing,{kind:"zone"},condition,bars);
  if(drawing.type==="channel"&&["entering","exiting"].includes(condition))return evaluateDrawingGeometry(drawing,{kind:"zone"},condition,bars);
  if(drawing.type==="fib"){
    const levels=drawing.fibLevels??[0,.236,.382,.5,.618,.786,1];
    for(const level of levels){const result=evaluateDrawingGeometry(drawing,{kind:"fib-level",level},condition,bars);if(result.condition)return result}
    return evaluateDrawingGeometry(drawing,{kind:"fib-level",level:levels[0]??0},condition,bars);
  }
  if(rawCondition==="crossing"){
    const up=evaluateDrawingGeometry(drawing,{kind:"line"},"crossing_up",bars);
    return up.condition?up:evaluateDrawingGeometry(drawing,{kind:"line"},"crossing_down",bars);
  }
  return evaluateDrawingGeometry(drawing,{kind:"line"},condition,bars);
}

export function drawingTriggered(payload: Record<string, unknown>, bars: PriceBar[]): boolean {
  if (!bars.length) return false;
  const drawing = payload.drawing && typeof payload.drawing === "object" && !Array.isArray(payload.drawing) ? payload.drawing as Record<string, unknown> : payload;
  return drawingEvaluation(payload,drawing,bars).condition;
}

function indicatorInvalid(relation: IndicatorEvaluation["relation"] = "invalid"): IndicatorEvaluation {
  return { condition: false, relation, signal: null, direction: null, barDate: null, previousFast: null, currentFast: null, previousSlow: null, currentSlow: null, sma50DailySlope: null, sma30WeeklySlope: null, confirmations: {} };
}

function latestPair(values: Array<number | null>): [number, number] | null {
  let current: number | null = null;
  for (let index = values.length - 1; index >= 0; index -= 1) {
    const value = values[index];
    if (value === null || !Number.isFinite(value)) continue;
    if (current === null) current = value;
    else return [value, current];
  }
  return null;
}

function completedWeeklySession(time: string): boolean {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(time)) return false;
  const parsed = Date.parse(`${time}T00:00:00.000Z`);
  return Number.isFinite(parsed) && new Date(parsed).getUTCDay() === 5;
}

function indicatorConditions(payload: Record<string, unknown>): { mode: "all" | "any"; conditions: IndicatorConfirmation[]; valid: boolean } {
  const source = payload.confirmations && typeof payload.confirmations === "object" && !Array.isArray(payload.confirmations) ? payload.confirmations as Record<string, unknown> : {};
  const mode = source.mode === "any" ? "any" : "all";
  const validMode = source.mode === undefined || source.mode === "all" || source.mode === "any";
  const rawConditions = Array.isArray(source.conditions) ? source.conditions.map(String) : [];
  const valid = validMode && rawConditions.every((value) => value === "sma50_daily_up" || value === "sma30_weekly_up");
  const conditions = [...new Set(rawConditions)].filter((value): value is IndicatorConfirmation => value === "sma50_daily_up" || value === "sma30_weekly_up");
  return { mode, conditions, valid };
}

export function indicatorEvaluation(payload: Record<string, unknown>, bars: PriceBar[]): IndicatorEvaluation {
  const signal = String(payload.signal ?? "") as IndicatorSignal;
  const direction = String(payload.direction ?? "up") as IndicatorDirection;
  if (!(signal === "daily_ema_10_20" || signal === "weekly_sma_10_20") || !(["up", "down", "either"] as string[]).includes(direction)) return indicatorInvalid();
  const series: IndicatorSeries = buildIndicatorSeries(bars.map((bar) => ({ time: bar.time, close: bar.close })));
  const weekly = signal === "weekly_sma_10_20",expectedInterval = weekly ? "weekly" : "daily";
  if (String(payload.evaluationInterval ?? expectedInterval) !== expectedInterval || String(payload.rearm ?? "after_clear") !== "after_clear") return { ...indicatorInvalid(), signal, direction };
  if (weekly && !completedWeeklySession(series.daily.bars.at(-1)?.time ?? "")) return { ...indicatorInvalid("not_completed"), signal, direction, barDate: series.weekly.bars.at(-1)?.time ?? null };
  const target = weekly ? series.weekly : series.daily;
  const fast = weekly ? target.sma10 : target.ema10;
  const slow = weekly ? target.sma20 : target.ema20;
  const pairFast = latestPair(fast), pairSlow = latestPair(slow);
  if (!pairFast || !pairSlow || !target.bars.at(-1)) return { ...indicatorInvalid("insufficient_history"), signal, direction, barDate: target.bars.at(-1)?.time ?? null };
  const sma50Values = series.daily.sma50.filter((value): value is number => value !== null);
  const sma30Values = series.weekly.sma30.filter((value): value is number => value !== null);
  const sma50DailySlope = normalizedSlope(sma50Values, 20);
  const sma30WeeklySlope = normalizedSlope(sma30Values, 8);
  const configuration = indicatorConditions(payload);
  if (!configuration.valid) return { ...indicatorInvalid(), signal, direction, barDate: target.bars.at(-1)?.time ?? null };
  const confirmationResult = confirmationsPass(series, configuration.conditions, configuration.mode);
  const signalCrossed = crossed(pairFast[0], pairSlow[0], pairFast[1], pairSlow[1], direction);
  const confirmations: Record<string, Json> = {
    mode: configuration.mode,
    selected: configuration.conditions as unknown as Json,
    sma50DailyUp: isUpsloping(sma50Values, 20),
    sma30WeeklyUp: isUpsloping(sma30Values, 8),
  };
  if (confirmationResult === null) return { condition: false, relation: "insufficient_history", signal, direction, barDate: target.bars.at(-1)!.time, previousFast: pairFast[0], currentFast: pairFast[1], previousSlow: pairSlow[0], currentSlow: pairSlow[1], sma50DailySlope, sma30WeeklySlope, confirmations };
  const condition = signalCrossed && confirmationResult;
  return { condition, relation: condition ? "triggered" : "clear", signal, direction, barDate: target.bars.at(-1)!.time, previousFast: pairFast[0], currentFast: pairFast[1], previousSlow: pairSlow[0], currentSlow: pairSlow[1], sma50DailySlope, sma30WeeklySlope, confirmations };
}

export function triggered(alert: AlertRow, candidate: Record<string, unknown> | undefined, bars: PriceBar[]): boolean {
  const payload = alert.payload ?? {};
  const kind = String(payload.kind ?? "price");
  if (kind === "watchlist") return true;
  if (kind === "screen") {
    if(!candidate)return false;
    const filters = Array.isArray(payload.filters) ? payload.filters.slice(0, 12) : [];
    return filters.length > 0 && filters.every((filter) => {
      if (!filter || typeof filter !== "object") return false;
      const item = filter as Record<string, unknown>;
      return compare(nested(candidate, String(item.field ?? "")), String(item.op ?? "eq"), item.value);
    });
  }
  if (kind === "drawing") return drawingTriggered(payload, bars);
  if (kind === "indicator") return indicatorEvaluation(payload, bars).condition;
  const points = Array.isArray(payload.points) ? payload.points : [];
  const firstPoint = points[0] && typeof points[0] === "object" ? points[0] as Record<string, unknown> : {};
  const target = Number(payload.price ?? firstPoint.price);
  const current = bars.at(-1)?.close ?? Number(candidate?.price ?? candidate?.close);
  const previous = bars.at(-2)?.close ?? Number(candidate?.previousClose ?? candidate?.prevClose ?? candidate?.closePrev);
  if (!Number.isFinite(target) || !Number.isFinite(current)) return false;
  const operator = String(payload.operator ?? "crossing_up");
  if (operator === "above" || operator === "greater_than") return current > target;
  if (operator === "below" || operator === "less_than") return current < target;
  if (operator === "touch") return bars.length ? bars.at(-1)!.low <= target && target <= bars.at(-1)!.high : Math.abs(current - target) / Math.max(target, 0.01) <= 0.005;
  if (operator === "crossing") return Number.isFinite(previous) && ((previous <= target && target <= current) || (previous >= target && target >= current));
  if (!Number.isFinite(previous)) return false;
  return operator === "crossing_down" ? previous >= target && current < target : previous <= target && current > target;
}

async function sha256(value: string): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(value));
  return [...new Uint8Array(digest)].map((byte) => byte.toString(16).padStart(2, "0")).join("");
}

async function evaluateAlerts(body: Record<string, unknown>): Promise<Json> {
  const expectedRun = text(body.runId, "runId", 160);
  const pages = UNIFIED_PAGES_BASE_URL;
  const root = await fetch(`${pages}/data/manifest.json`, { cache: "no-store" }).then((result) => result.json()) as Record<string, unknown>;
  if (root.runId !== expectedRun || root.status !== "healthy") throw new Error("deployed Pages run does not match alert request");
  const user = await ownerId();
  const alerts = await database(`unified_alerts?select=id,user_id,name,ticker,mode,price_basis,payload,drawing_id,updated_at&user_id=eq.${user}&enabled=eq.true`) as AlertRow[];
  const drawingRows = await database(`unified_drawings?select=id,user_id,ticker,mode,price_basis,payload,updated_at&user_id=eq.${user}`) as Array<Record<string,unknown>>;
  const drawings = new Map(drawingRows.map(row=>[String(row.id),row]));
  const stateRows = await database(`unified_alert_state?select=alert_id,config_version,armed,last_condition,diagnostics&user_id=eq.${user}`) as Array<Record<string,unknown>>;
  const stateByAlert = new Map(stateRows.map(row=>[String(row.alert_id),row]));
  const modeDocuments = new Map<string, ModeDocument>();
  for (const mode of MODE_IDS) {
    const modeManifest = await fetch(`${pages}/data/modes/${mode}/manifest.json`, { cache: "no-store" }).then((result) => result.json()) as Record<string, unknown>;
    if (modeManifest.runId !== expectedRun) throw new Error(`${mode} run does not match active run`);
    const corePath = String((modeManifest.assets as Record<string, Record<string, unknown>>).core.path);
    const core = await fetch(`${pages}/data/modes/${mode}/${corePath}`, { cache: "no-store" }).then((result) => result.json()) as Record<string, unknown>;
    const rows = Array.isArray(core.universe) ? core.universe as Record<string, unknown>[] : [];
    modeDocuments.set(mode, { manifest: modeManifest, core, rows: new Map(rows.map((row) => [String(row.ticker).toUpperCase(), row])) });
  }
  const events: Record<string, Json>[] = [];
  const nextStates: Record<string, Json>[] = [];
  const barCache = new Map<string, Promise<PriceBar[]>>();
  for (const alert of alerts) {
    const document = modeDocuments.get(alert.mode);
    if (!document || document.manifest.priceMode !== alert.price_basis) continue;
    const rows = document.rows;
    const tickers = alert.ticker ? [alert.ticker.toUpperCase()] : Array.isArray(alert.payload.tickers) ? alert.payload.tickers.slice(0, 100).map(String) : [...rows.keys()];
    for (const ticker of tickers) {
      const normalized = ticker.toUpperCase(), candidate = rows.get(normalized);
      const kind = String(alert.payload.kind ?? "price"), needsBars = kind === "price" || kind === "drawing" || kind === "indicator";
      if (kind === "indicator" && !alert.ticker) continue;
      const cacheKey = `${alert.mode}:${normalized}`;
      if (needsBars && !barCache.has(cacheKey)) barCache.set(cacheKey, loadBars(pages, alert.mode, normalized, document));
      const bars = needsBars ? await barCache.get(cacheKey)! : [];
      let didTrigger=false,indicator:IndicatorEvaluation|undefined,context:Record<string,Json>={ticker:normalized,name:alert.name,price:bars.at(-1)?.close??Number(candidate?.price??candidate?.close??0),mode:alert.mode};
      const linked=String(alert.payload.kind??"price")==="drawing"&&alert.payload.version===2;
      if(linked){
        const drawing=alert.drawing_id?drawings.get(alert.drawing_id):undefined;
        const configurationVersion=[String(alert.updated_at??""),String(drawing?.updated_at??"")].sort().at(-1)??new Date(0).toISOString();
        const existing=stateByAlert.get(alert.id),stateMatches=String(existing?.config_version??"")===configurationVersion;
        if(!drawing||String(drawing.ticker)!==normalized||drawing.mode!==alert.mode||drawing.price_basis!==alert.price_basis){
          nextStates.push({alert_id:alert.id,user_id:user,config_version:configurationVersion,armed:true,last_condition:false,last_run_id:expectedRun,last_session_date:bars.at(-1)?.time??null,evaluated_at:new Date().toISOString(),error:"Linked drawing is missing or outside the alert scope."});
          continue;
        }
        const geometry=drawing.payload&&typeof drawing.payload==="object"&&!Array.isArray(drawing.payload)?drawing.payload as Record<string,unknown>:{};
        const evaluation=drawingEvaluation(alert.payload,geometry,bars),armed=stateMatches?Boolean(existing?.armed):true;
        const transition=rearmAfterClear(armed,evaluation.condition);didTrigger=transition.triggered;
        nextStates.push({
          alert_id:alert.id,user_id:user,config_version:configurationVersion,armed:transition.armed,last_condition:evaluation.condition,
          last_relation:evaluation.relation,last_run_id:expectedRun,last_session_date:bars.at(-1)?.time??null,
          previous_price:evaluation.previousPrice,current_price:evaluation.currentPrice,previous_level:evaluation.previousLevel,current_level:evaluation.currentLevel,
          evaluated_at:new Date().toISOString(),error:evaluation.relation==="invalid"?"Drawing geometry could not be evaluated.":null,
        });
        context={
          ...context,drawingId:String(drawing.id),drawingType:String(geometry.type??"drawing"),condition:String(alert.payload.condition??"touch"),
          target:alert.payload.target as Json,previousPrice:evaluation.previousPrice,currentPrice:evaluation.currentPrice,
          previousLevel:evaluation.previousLevel,currentLevel:evaluation.currentLevel,sessionDate:bars.at(-1)?.time??"",
          deepLink:`${pages}/?mode=${encodeURIComponent(alert.mode)}&view=screener&ticker=${encodeURIComponent(normalized)}&drawing=${encodeURIComponent(String(drawing.id))}`,
        };
      }else if(kind==="indicator"){
        const configurationVersion=String(alert.updated_at??new Date(0).toISOString());
        const existing=stateByAlert.get(alert.id),stateMatches=String(existing?.config_version??"")===configurationVersion;
        indicator=indicatorEvaluation(alert.payload,bars);
        const armed=stateMatches?Boolean(existing?.armed):true;
        const transition=rearmAfterClear(armed,indicator.condition);
        const nextArmed=indicator.relation==="insufficient_history"||indicator.relation==="not_completed"?true:transition.armed;
        didTrigger=indicator.relation==="triggered"&&transition.triggered;
        nextStates.push({
          alert_id:alert.id,user_id:user,config_version:configurationVersion,armed:nextArmed,last_condition:indicator.condition,
          last_relation:indicator.relation,last_run_id:expectedRun,last_session_date:bars.at(-1)?.time??null,
          previous_price:bars.at(-2)?.close??null,current_price:bars.at(-1)?.close??null,
          previous_level:indicator.previousSlow,current_level:indicator.currentSlow,
          evaluated_at:new Date().toISOString(),error:indicator.relation==="invalid"?"Indicator configuration could not be evaluated.":null,
          diagnostics:{signal:indicator.signal,direction:indicator.direction,barDate:indicator.barDate,previousFast:indicator.previousFast,currentFast:indicator.currentFast,previousSlow:indicator.previousSlow,currentSlow:indicator.currentSlow,sma50DailySlope:indicator.sma50DailySlope,sma30WeeklySlope:indicator.sma30WeeklySlope,confirmations:indicator.confirmations,status:indicator.relation},
        });
        context={...context,alertKind:"indicator",signal:indicator.signal,direction:indicator.direction,barDate:indicator.barDate,previousFast:indicator.previousFast,currentFast:indicator.currentFast,previousSlow:indicator.previousSlow,currentSlow:indicator.currentSlow,confirmations:indicator.confirmations,deepLink:`${pages}/?mode=${encodeURIComponent(alert.mode)}&view=screener&ticker=${encodeURIComponent(normalized)}&interval=${encodeURIComponent(String(alert.payload.evaluationInterval??"daily"))}&alert=${encodeURIComponent(alert.id)}`};
      }else didTrigger=triggered(alert,candidate,bars);
      if (!didTrigger) continue;
      const eventKey = await sha256(kind==="indicator"&&indicator?.barDate?`${alert.id}|${normalized}|${indicator.signal}|${indicator.direction}|${indicator.barDate}`:`${expectedRun}|${alert.id}|${ticker.toUpperCase()}`);
      events.push({
        user_id: user,
        alert_id: alert.id,
        run_id: expectedRun,
        mode: alert.mode,
        price_basis: alert.price_basis,
        event_key: eventKey,
        payload: context,
      });
    }
  }
  if(nextStates.length)await database("unified_alert_state?on_conflict=alert_id,user_id",{method:"POST",body:JSON.stringify(nextStates)});
  if (events.length) await database("unified_alert_events?on_conflict=user_id,event_key", { method: "POST", headers: { prefer: "return=representation,resolution=ignore-duplicates" }, body: JSON.stringify(events) });
  return { runId: expectedRun, events: events.map((event) => event.payload as Json) };
}

if (import.meta.main) Deno.serve(async (request) => {
  if (request.method === "OPTIONS") return response(204, null);
  if (request.method !== "POST") return response(405, { error: "method_not_allowed" });
  try {
    await verifyGithubOidc(request);
    const body = await request.json() as Record<string, unknown>;
    const action = text(body.action, "action", 40);
    if (action === "delivery_get") return response(200, await deliveryGet(body));
    if (action === "delivery_mark") return response(200, await deliveryMark(body));
    if (action === "evaluate_alerts") return response(200, await evaluateAlerts(body));
    return response(400, { error: "unsupported_action" });
  } catch (error) {
    return response(400, { error: error instanceof Error ? error.message : String(error) });
  }
});
