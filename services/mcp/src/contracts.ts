export const MODES = ["bottom-fishing", "next", "ryan-original"] as const;
export type ModeId = typeof MODES[number];
export const FILTER_OPERATORS = ["eq", "ne", "gt", "gte", "lt", "lte", "in", "contains", "is_true", "is_false"] as const;
export type FilterOperator = typeof FILTER_OPERATORS[number];
export type JsonRecord = Record<string, unknown>;
export type ScanFilter = { field: string; op: FilterOperator; value?: unknown };
export type ScanSort = { field: string; direction: "asc" | "desc" };

export type Asset = { path: string; sha256: string; bytes: number; count?: number; bucketCount?: number; pattern?: string };
export type ModeManifest = {
  mode: ModeId; runId: string; sessionDate: string; marketDataDate: string; generatedAt: string;
  status: string; priceMode: string; health?: { status?: string }; counts: { candidates: number; excluded: number };
  assets: { core: Asset; details: Asset; excluded: Asset; history: Asset; charts?: Asset };
};
export type UnifiedManifest = {
  schemaVersion: "stockscout-unified/v1"; runId: string; sessionDate: string; generatedAt: string;
  status: "healthy"; defaultMode: "bottom-fishing";
  modes: Record<ModeId, { mode: ModeId; label: string; priceBasis: string; status: "healthy"; manifestPath: string; manifestSha256: string; candidates: number; excluded: number; chartCoveragePct: number; ranking: string }>;
};

export const TOOL_NAMES = ["list_modes", "search", "fetch", "describe_scan_fields", "screen_scan"] as const;
