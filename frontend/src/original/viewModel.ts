import type { LegacyIndex, StockScoutRow } from "../data/StockScoutDataProvider";

export type OriginalTab = "buy" | "sell";
export type OriginalScope = "emitted" | "raw" | "all";
export type OriginalSortKey =
  | "score"
  | "ticker"
  | "phase"
  | "entry"
  | "riskReward"
  | "tt"
  | "vcp"
  | "severity"
  | "breakdown";
export type OriginalSortDirection = "asc" | "desc";

export type OriginalCandidate = StockScoutRow & {
  ticker: string;
  price?: number;
  stage?: number;
  stageName?: string;
  originalBuyScore?: number | null;
  originalBuy?: boolean;
  originalMarketQualifiedBuy?: boolean;
  originalRunBuySignal?: boolean;
  originalRR?: number | null;
  originalStopLoss?: number | null;
  originalRiskPct?: number | null;
  originalEntryQuality?: string | null;
  originalTTPasses?: number | null;
  originalVcpQuality?: number | null;
  originalSellScore?: number | null;
  originalSell?: boolean;
  originalMarketQualifiedSell?: boolean;
  originalRunSellSignal?: boolean;
  originalSellSeverity?: string | null;
  originalBreakoutType?: string | null;
  originalBreakoutLevel?: number | null;
  rsRank?: number | null;
  rsScore?: number | null;
  phaseConfidence?: number | null;
  originalEngine?: OriginalEngineDetail | null;
};

export type OriginalEngineDetail = {
  model?: string;
  completeSourceCaptureModel?: string;
  phase?: number | null;
  phaseConfidence?: number | null;
  phaseReasons?: unknown;
  phaseInfo?: Record<string, unknown>;
  sourceInputs?: Record<string, unknown>;
  sourceOutputs?: Record<string, unknown>;
  minervini?: Record<string, unknown>;
  breakout?: Record<string, unknown>;
  vcp?: Record<string, unknown>;
  buy?: Record<string, unknown>;
  sell?: Record<string, unknown>;
  [key: string]: unknown;
};

export type OriginalSort = {
  key: OriginalSortKey;
  direction: OriginalSortDirection;
};

export type OriginalTabState = {
  scope: OriginalScope;
  query: string;
  sort: OriginalSort;
  page: number;
};

export type OriginalSummary = {
  buySignals: number;
  sellSignals: number;
  topScore: number | null;
  universe: number;
  spyPhase: number | null;
  spyPhaseName: string;
  spyTrend: string;
};

export type FlattenedValue = {
  path: string;
  value: unknown;
};

export const ORIGINAL_PAGE_SIZE = 50;

const scoreKey = (tab: OriginalTab): keyof OriginalCandidate =>
  tab === "buy" ? "originalBuyScore" : "originalSellScore";

const rawKey = (tab: OriginalTab): keyof OriginalCandidate =>
  tab === "buy" ? "originalBuy" : "originalSell";

const emittedKey = (tab: OriginalTab): keyof OriginalCandidate =>
  tab === "buy" ? "originalRunBuySignal" : "originalRunSellSignal";

const qualifiedKey = (tab: OriginalTab): keyof OriginalCandidate =>
  tab === "buy"
    ? "originalMarketQualifiedBuy"
    : "originalMarketQualifiedSell";

const finite = (value: unknown): number | null => {
  if (typeof value !== "number" || !Number.isFinite(value)) return null;
  return value;
};

export function signalScore(
  row: OriginalCandidate,
  tab: OriginalTab,
): number | null {
  return finite(row[scoreKey(tab)]);
}
export function isEmitted(row: OriginalCandidate, tab: OriginalTab): boolean {
  return Boolean(row[emittedKey(tab)] || row[qualifiedKey(tab)]);
}

export function isRawCandidate(
  row: OriginalCandidate,
  tab: OriginalTab,
): boolean {
  return Boolean(row[rawKey(tab)] || (signalScore(row, tab) ?? 0) > 0);
}

export function matchesScope(
  row: OriginalCandidate,
  tab: OriginalTab,
  scope: OriginalScope,
): boolean {
  if (scope === "emitted") return isEmitted(row, tab);
  if (scope === "raw") return isRawCandidate(row, tab);
  return signalScore(row, tab) !== null;
}

function searchableText(row: OriginalCandidate, tab: OriginalTab): string {
  const engine = row.originalEngine as OriginalEngineDetail | null | undefined;
  const branch = engine?.[tab];
  return [
    row.ticker,
    row.stageName,
    row.originalEntryQuality,
    row.originalSellSeverity,
    row.originalBreakoutType,
    branch && typeof branch === "object" ? JSON.stringify(branch) : "",
  ]
    .filter(Boolean)
    .join(" ")
    .toUpperCase();
}

export function filterOriginalRows(
  rows: readonly OriginalCandidate[],
  tab: OriginalTab,
  scope: OriginalScope,
  query: string,
): OriginalCandidate[] {
  const needle = query.trim().toUpperCase();
  return rows.filter((row) => {
    if (!matchesScope(row, tab, scope)) return false;
    return !needle || searchableText(row, tab).includes(needle);
  });
}

function sortValue(row: OriginalCandidate, tab: OriginalTab, key: OriginalSortKey): unknown {
  switch (key) {
    case "ticker":
      return row.ticker;
    case "score":
      return signalScore(row, tab);
    case "phase":
      return row.phase ?? row.stage;
    case "entry":
      return row.originalEntryQuality;
    case "riskReward":
      return row.originalRR;
    case "tt":
      return row.originalTTPasses;
    case "vcp":
      return row.originalVcpQuality;
    case "severity":
      return row.originalSellSeverity;
    case "breakdown":
      return row.originalEngine?.sell?.breakdownLevel;
  }
}

export function sortOriginalRows(
  rows: readonly OriginalCandidate[],
  tab: OriginalTab,
  sort: OriginalSort,
): OriginalCandidate[] {
  const direction = sort.direction === "asc" ? 1 : -1;
  return [...rows].sort((left, right) => {
    const a = sortValue(left, tab, sort.key);
    const b = sortValue(right, tab, sort.key);
    if (typeof a === "number" || typeof b === "number") {
      const an = typeof a === "number" && Number.isFinite(a) ? a : -Infinity;
      const bn = typeof b === "number" && Number.isFinite(b) ? b : -Infinity;
      if (an !== bn) return (an - bn) * direction;
    } else {
      const compared = String(a ?? "").localeCompare(String(b ?? ""));
      if (compared !== 0) return compared * direction;
    }
    return left.ticker.localeCompare(right.ticker);
  });
}

export function paginateOriginalRows(
  rows: readonly OriginalCandidate[],
  page: number,
  pageSize = ORIGINAL_PAGE_SIZE,
): { rows: OriginalCandidate[]; page: number; pageCount: number } {
  const pageCount = Math.max(1, Math.ceil(rows.length / pageSize));
  const boundedPage = Math.max(0, Math.min(pageCount - 1, page));
  return {
    rows: rows.slice(boundedPage * pageSize, (boundedPage + 1) * pageSize),
    page: boundedPage,
    pageCount,
  };
}

export function selectOriginalRows(
  rows: readonly OriginalCandidate[],
  tab: OriginalTab,
  state: OriginalTabState,
): { filtered: OriginalCandidate[]; rows: OriginalCandidate[]; page: number; pageCount: number } {
  const filtered = sortOriginalRows(
    filterOriginalRows(rows, tab, state.scope, state.query),
    tab,
    state.sort,
  );
  const page = paginateOriginalRows(filtered, state.page);
  return { filtered, ...page };
}

export function originalSummary(
  rows: readonly OriginalCandidate[],
  market: Record<string, any> = {},
): OriginalSummary {
  const buySignals = rows.filter((row) => isEmitted(row, "buy")).length;
  const sellSignals = rows.filter((row) => isEmitted(row, "sell")).length;
  const buyScores = rows
    .filter((row) => isEmitted(row, "buy"))
    .map((row) => signalScore(row, "buy"))
    .filter((value): value is number => value !== null);
  const spy = market.originalSignalGate?.spy ?? {};
  return {
    buySignals,
    sellSignals,
    topScore: buyScores.length ? Math.max(...buyScores) : null,
    universe: rows.length,
    spyPhase: finite(spy.phase),
    spyPhaseName: String(spy.phase_name ?? spy.phaseName ?? "Unknown"),
    spyTrend: String(spy.trend ?? "Unknown"),
  };
}

export function detailFromIndex(
  payload: LegacyIndex | null,
  ticker: string,
): OriginalCandidate | null {
  const normalized = ticker.trim().toUpperCase();
  const row = (payload?.universe ?? []).find(
    (candidate) => String(candidate.ticker ?? "").toUpperCase() === normalized,
  );
  return row ? (row as OriginalCandidate) : null;
}

export function flattenSourceValues(
  value: unknown,
  path = "",
): FlattenedValue[] {
  if (value === null || value === undefined) return [{ path, value: null }];
  if (Array.isArray(value)) {
    if (!value.length) return [{ path, value: [] }];
    return value.flatMap((item, index) =>
      flattenSourceValues(item, `${path}[${index}]`),
    );
  }
  if (typeof value === "object") {
    const entries = Object.entries(value as Record<string, unknown>);
    if (!entries.length) return [{ path, value: {} }];
    return entries.flatMap(([key, child]) =>
      flattenSourceValues(child, path ? `${path}.${key}` : key),
    );
  }
  return [{ path, value }];
}
