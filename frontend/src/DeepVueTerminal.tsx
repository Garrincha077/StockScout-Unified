import {
  lazy,
  Suspense,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import {
  createColumnHelper,
  flexRender,
  getCoreRowModel,
  getPaginationRowModel,
  type PaginationState,
  type SortingState,
  type VisibilityState,
  useReactTable,
} from "@tanstack/react-table";
import {
  builtInScreens,
  calculatePositionSize,
  fieldValue,
  fieldDefs,
  invalidRules,
  levelFitsExpandedCandleBounds,
  makeGroup,
  makeRule,
  matchesGroups,
  normalizeEodDate,
  opsByKind,
  preserveScanOrder,
  stockScoutFocusBlend,
  validateRule,
  weekStartUtc,
  type Logic,
  type RuleGroup,
  type ScreenState,
} from "./deepvue/filterEngine";
import{bottomBuiltInScreens,bottomCoreColumns,bottomFieldDefs,bottomRecipeTabs}from'./deepvue/bottomRegistry'
import { marketRegimeLabel, nextGridCount } from "./deepvue/runtime";
import { useStockScoutData } from "./data/StockScoutDataProvider";
import { applyMultiSort } from "./deepvue/multiSort";
import { matchesReviewScope } from "./phase4Review";
import { useOwnerData } from "./owner/OwnerDataProvider";
import {
  isManifestV1,
  type ScanHistoryItemV1,
  type TradePlanV1,
  type TradeStatus,
} from "./data/contracts";
import { useMode, type ModeId } from "./modes/ModeProvider";
import StockChart from'./StockChart'
import{ResizableHeight,ResizableWorkspace}from'./ResizablePanels'

const OwnerWorkspace = lazy(() => import("./owner/OwnerWorkspace"));

type Bar = {
  time: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
  rs: number;
};
type RawBar = [string | number, number, number, number, number, number, number];
type Page =
  | "Screener"
  | "Grid"
  | "Changes"
  | "Excluded"
  | "History"
  | "Watchlist"
  | "Market"
  | "Owner";
type Interval = "D" | "W";
type Range = "3M" | "6M" | "1Y" | "2Y" | "5Y";
type ChartMode = "Price" | "RS" | "Volume";
type ChartLoadState = {
  status: "loading" | "ready" | "unavailable" | "error";
  bars: Bar[];
  error?: string;
};
type RowsLoadState<T> = {
  status: "idle" | "loading" | "ready" | "error";
  rows: T[];
  error?: string;
};
export type Stock = {
  ticker: string;
  price: number;
  stage: number;
  stageName: string;
  setup?: string;
  score?: number;
  primarySetup?: string;
  setupTags?: string[];
  setupNames?: string[];
  setupMatchCount?: number;
  accumulationScore?:number;
  crashBaseScore?:number;
  emaStackLaunchScore?:number;
  maClusterScore?:number;
  longBaseScore?:number;
  smaCompressionPct?:number;
  opportunityScore?: number;
  opportunityPotential?: number;
  opportunityTiming?: number;
  opportunityRank?: number;
  opportunityTier?: string;
  opportunityGroupModifier?: number;
  opportunityFundModifier?: number;
  opportunityPenalty?: number;
  emergingLeaderScore?: number;
  confluence?: number;
  structureScore?: number;
  rsScore?: number;
  baseScore?: number;
  triggerScore?: number;
  freshnessScore?: number;
  neglectedScore?: number;
  leadershipScore?: number;
  groupLeadership?: number;
  groupRank?: number;
  groupRS?: number;
  groupConfidence?: number;
  sectorProxy?: string;
  sectorRank?: number;
  sectorProxyConfidence?: number;
  sectorCorrelationStability?: number;
  industryProxy?: string;
  industryRank?: number;
  industryProxyConfidence?: number;
  industryCorrelationStability?: number;
  rsRank?: number;
  rsSlope?: number;
  rsAcceleration?: number;
  rs3m?: number;
  rs6m?: number;
  rs12m?: number;
  rsFromHigh?: number;
  rsNewHigh?: boolean;
  change20d?: number;
  return3m?: number;
  return6m?: number;
  return1y?: number;
  prior9mReturn?: number;
  volumeRatio?: number;
  avgVolume20?: number;
  avgDollarVolume20?: number;
  volumeDryUp?: number;
  vcpScore?: number;
  contraction?: number;
  atrPct?: number;
  atrCompression?: number;
  tightRange20?: number;
  tightRange60?: number;
  distance50?: number;
  distance200?: number;
  distance10w?: number;
  distance30w?: number;
  extensionAbs?: number;
  from52wHigh?: number;
  from52wLow?: number;
  slope50?: number;
  slope150?: number;
  slope200?: number;
  trendTemplatePasses?: number;
  stage2AgeWeeks?: number;
  baseWeeks?: number;
  baseDepthPct?: number;
  breakoutPct?: number;
  breakout60?: boolean;
  extended?: boolean;
  ema10d?: number | null;
  ema20d?: number | null;
  ema10d20dSpreadPct?: number | null;
  ema10d20dState?: string | null;
  ema10d20dCross?: string | null;
  ema10d20dCrossAge?: number | null;
  sma10w?: number | null;
  sma20w?: number | null;
  sma10w20wSpreadPct?: number | null;
  sma10w20wState?: string | null;
  sma10w20wCross?: string | null;
  sma10w20wCrossAge?: number | null;
  fundamentalSupport?: boolean | null;
  revenueYoY?: number | null;
  epsYoY?: number | null;
  grossMargin?: number | null;
  marginChange?: number | null;
  fundamentalEvidenceScore?: number | null;
  fundamentalEvidenceConfidence?: number;
  fundamentalEvidenceCoverage?: number;
  fundamentalEvidenceLabel?: string;
  fundamentalGrowthScore?: number | null;
  fundamentalMarginScore?: number | null;
  fundamentalInventoryScore?: number | null;
  operatingCashFlowYoY?: number | null;
  freeCashFlowYoY?: number | null;
  freeCashFlowMargin?: number | null;
  totalDebtYoY?: number | null;
  netDebt?: number | null;
  shareDilutionYoY?: number | null;
  fundamentalDataSource?: string | null;
  changedToday?: boolean;
  newUniverseMember?: boolean;
  changeImpact?: number;
  opportunityDelta?: number;
  rsRankDelta?: number;
  confluenceDelta?: number;
  volumeRatioDelta?: number;
  freshnessDelta?: number;
  stageFrom?: number | null;
  stageTo?: number | null;
  stageChanged?: boolean;
  newSetupTags?: string[];
  lostSetupTags?: string[];
  changeLabels?: string[];
  originalBuyScore?: number;
  originalRR?: number;
  originalTTPasses?: number;
  originalVcpQuality?: number;
  originalAdVolumeRatio?: number;
  originalRiskPct?: number;
  originalSellScore?: number;
  id?: string;
  scanOrder?: number;
  excluded?: boolean;
  focusBlend?: number;
  tradeStatus?: TradeStatus;
  entryRiskPct?: number | null;
  actionability?:string;
  tacticalStopLevel?: number | null;
  tradePlan?: Partial<TradePlanV1> & Record<string, any>;
  reasons?: string[];
};
type Payload = {
  version: number;
  generatedAt: string;
  market: Record<string, any>;
  universe: Stock[];
  chartShards?: Record<string, string>;
  featureModel?: string;
};

const helper = createColumnHelper<Stock>();
const defaultVisibility: VisibilityState = {
  opportunityScore: false,
  opportunityRank: false,
  exclusionReasons: false,
  originalTTPasses: false,
  originalVcpQuality: false,
  originalAdVolumeRatio: false,
  originalRiskPct: false,
  originalSellScore: false,
  rsFromHigh: false,
  volumeDryUp: false,
  baseWeeks: false,
  distance30w: false,
  structureScore: false,
  baseScore: false,
  triggerScore: false,
  neglectedScore: false,
  avgDollarVolume20: false,
  fundamentalSupport: false,
  fundamentalEvidenceCoverage: false,
  opportunityGroupModifier: false,
  opportunityFundModifier: false,
  opportunityPenalty: false,
  emergingLeaderScore: false,
  revenueYoY: false,
  epsYoY: false,
  operatingCashFlowYoY: false,
  freeCashFlowYoY: false,
  freeCashFlowMargin: false,
  totalDebtYoY: false,
  netDebt: false,
  shareDilutionYoY: false,
  leadershipScore: false,
  groupRank: false,
  groupRS: false,
  groupConfidence: false,
};
const allUnifiedColumnIds=[
  'watch','scanOrder','ticker','focusBlend','tradeStatus','triggerState','entryRiskPct','distanceToTrigger','actionability','opportunityScore','ema10d20dSpreadPct','sma10w20wSpreadPct',
  'opportunityTier','opportunityRank','opportunityPotential','opportunityTiming','opportunityGroupModifier','opportunityFundModifier','opportunityPenalty','emergingLeaderScore','leadershipScore',
  'groupRank','groupRS','groupConfidence','fundamentalEvidenceScore','fundamentalEvidenceConfidence','fundamentalEvidenceCoverage','revenueYoY','epsYoY','operatingCashFlowYoY',
  'freeCashFlowYoY','freeCashFlowMargin','totalDebtYoY','netDebt','shareDilutionYoY','originalBuyScore','originalRR','originalTTPasses','originalVcpQuality','originalAdVolumeRatio',
  'originalRiskPct','originalSellScore','changeImpact','todaySignals','exclusionReasons','primarySetup','confluence','freshnessScore','rsRank','rsRankDelta','rsAcceleration','stage',
  'stage2AgeWeeks','trendTemplatePasses','ema10d','ema20d','ema10d20dCrossAge','sma10w','sma20w','sma10w20wCrossAge','return3m','prior9mReturn','volumeRatio','breakoutPct',
  'vcpScore','atrCompression','tightRange20','baseWeeks','distance10w','distance30w','rsFromHigh','structureScore','baseScore','triggerScore','neglectedScore','avgDollarVolume20','fundamentalSupport',
]as const
const bottomDefaultVisibility:VisibilityState={...Object.fromEntries(bottomFieldDefs.map(field=>[field.id,false])),...Object.fromEntries(allUnifiedColumnIds.map(id=>[id,bottomCoreColumns.has(id)]))}
const recipeTabs = [
  "All",
  "Neglected → Leader",
  "S1→S2 Transition",
  "Fresh Breakout",
  "Long Base Breakout",
  "RS Before Price",
  "Tight / VCP",
  "10W Pullback",
  "Volume Wake-Up",
  "Fresh Stage 2",
];
const fmt = (v: any, d = 1) =>
  typeof v === "number" && Number.isFinite(v) ? v.toFixed(d) : "—";
const signed = (v: any, d = 1) =>
  typeof v === "number" && Number.isFinite(v)
    ? `${v > 0 ? "+" : ""}${v.toFixed(d)}%`
    : "—";
const compact = (v: any) =>
  typeof v === "number" && Number.isFinite(v)
    ? new Intl.NumberFormat("en", {
        notation: "compact",
        maximumFractionDigits: 1,
      }).format(v)
    : "—";
const num = (v: any, f = 0) =>
  typeof v === "number" && Number.isFinite(v) ? v : f;
const setupOf = (s: Stock) =>
  s.primarySetup || s.setup || s.stageName || "Other";
const tagsOf = (s: Stock) => (s.setupTags?.length ? s.setupTags : [setupOf(s)]);
const opp = (s: Stock) => s.opportunityScore ?? 0;
const stockScout = (s: Stock) =>
  stockScoutFocusBlend(s as Record<string, unknown>);
const modeScore = (s: Stock, mode: ModeId) =>
  mode === "bottom-fishing"
    ? stockScout(s)
    : mode === "next"
      ? num(s.opportunityScore ?? s.score)
      : num(s.originalBuyScore ?? s.score);
const modeScoreLabel = (mode: ModeId) =>
  mode === "bottom-fishing"
    ? "StockScout"
    : mode === "next"
      ? "Next score"
      : "Ryan buy";
const tradePlanOf = (s: Stock) => {
  const plan = s.tradePlan || {};
  const status = (s.tradeStatus ??
    plan.status ??
    plan.trade_status ??
    "insufficient_data") as TradeStatus;
  const rawTactical =
    s.tacticalStopLevel ?? plan.tacticalStopLevel ?? plan.tactical_stop_level;
  const numeric = (value: unknown) =>
    value !== null && value !== undefined && Number.isFinite(Number(value))
      ? Number(value)
      : null;
  return {
    status,
    trigger: numeric(
      plan.triggerReferenceLevel ?? plan.trigger_reference_level,
    ),
    entry: numeric(plan.entryReferenceLevel ?? plan.entry_reference_level),
    structural: numeric(
      plan.structuralInvalidationLevel ?? plan.structural_invalidation_level,
    ),
    tactical: status === "entry_ready" ? numeric(rawTactical) : null,
    risk: numeric(s.entryRiskPct ?? plan.entryRiskPct ?? plan.entry_risk_pct),
    extension: numeric(plan.extensionAtr ?? plan.extension_atr),
    reasons: (plan.reasonCodes ?? plan.reason_codes ?? []) as string[],
  };
};
const tradeLabel: Record<TradeStatus, string> = {
  entry_ready: "Entry ready",
  trigger_pending: "Trigger pending",
  wait_for_retest: "Wait for retest",
  not_tradeable: "Not tradeable",
  insufficient_data: "Insufficient data",
};
function TradeBadge({ stock }: { stock: Stock }) {
  const status = tradePlanOf(stock).status;
  return <span className={`trade-badge ${status}`}>{tradeLabel[status]}</span>;
}
const externalChartUrl = (ticker: string) =>
  (
    import.meta.env.VITE_EXTERNAL_CHART_URL ||
    "https://www.tradingview.com/symbols/{ticker}/"
  ).replace("{ticker}", encodeURIComponent(ticker));
const EMA_FRESH_SORT = "ema10d20dFresh",
  SMA_FRESH_SORT = "sma10w20wFresh";
const isFreshMaSort = (id: string) =>
  id === EMA_FRESH_SORT || id === SMA_FRESH_SORT;
const maBaseSortId = (id: string) =>
  id === EMA_FRESH_SORT
    ? "ema10d20dSpreadPct"
    : id === SMA_FRESH_SORT
      ? "sma10w20wSpreadPct"
      : id;
const maFreshSortId = (id: string) =>
  id === "ema10d20dSpreadPct"
    ? EMA_FRESH_SORT
    : id === "sma10w20wSpreadPct"
      ? SMA_FRESH_SORT
      : null;
function loadLocal<T>(key: string, fallback: T): T {
  try {
    const x = JSON.parse(localStorage.getItem(key) || "null");
    return x ?? fallback;
  } catch {
    return fallback;
  }
}
function aggregateWeekly(bars: Bar[]) {
  const out: Bar[] = [];
  for (const b of bars) {
    const key = weekStartUtc(b.time);
    if (!key) continue;
    const last = out[out.length - 1];
    if (!last || last.time !== key) out.push({ ...b, time: key });
    else {
      last.high = Math.max(last.high, b.high);
      last.low = Math.min(last.low, b.low);
      last.close = b.close;
      last.volume += b.volume;
      last.rs = b.rs;
    }
  }
  return out;
}
function ma(values: number[], n: number) {
  const out: (number | null)[] = [];
  let sum = 0;
  for (let i = 0; i < values.length; i++) {
    sum += values[i];
    if (i >= n) sum -= values[i - n];
    out.push(i + 1 >= n ? sum / n : null);
  }
  return out;
}
function ema(values: number[], n: number) {
  const out: (number | null)[] = [];
  if (!values.length) return out;
  const k = 2 / (n + 1);
  let value = values[0];
  for (let i = 0; i < values.length; i++) {
    value = i === 0 ? values[i] : values[i] * k + value * (1 - k);
    out.push(i + 1 >= n ? value : null);
  }
  return out;
}
function rangeCount(r: Range, i: Interval) {
  return i === "W"
    ? ({ "3M": 13, "6M": 26, "1Y": 52, "2Y": 104, "5Y": 260 } as any)[r]
    : ({ "3M": 66, "6M": 132, "1Y": 252, "2Y": 504, "5Y": 1265 } as any)[r];
}
function chartSource(bars: Bar[], interval: Interval, range: Range) {
  return (interval === "W" ? aggregateWeekly(bars) : bars).slice(
    -rangeCount(range, interval),
  );
}
function chartBar(row: any): Bar | null {
  const source = Array.isArray(row)
    ? {
        time: row[0],
        open: row[1],
        high: row[2],
        low: row[3],
        close: row[4],
        volume: row[5],
        rs: row[6],
      }
    : row;
  const time = normalizeEodDate(source?.time ?? source?.date),
    open = Number(source?.open),
    high = Number(source?.high),
    low = Number(source?.low),
    close = Number(source?.close),
    volume = Number(source?.volume ?? 0),
    rs = Number(source?.rs ?? 0);
  if (!time || ![open, high, low, close, volume, rs].every(Number.isFinite))
    return null;
  return { time, open, high, low, close, volume, rs };
}

function PriceChart({
  bars,
  interval = "W",
  range = "5Y",
  mode = "Price",
  mini = false,
  stock,
}: {
  bars: Bar[];
  interval?: Interval;
  range?: Range;
  mode?: ChartMode;
  mini?: boolean;
  stock?: Stock;
}) {
  return <StockChart bars={bars} interval={interval} range={range} display={mode} mini={mini} stock={stock} />;
}

function DeepVueTerminal() {
  const { mode, definition } = useMode();
  const modeFieldDefs=mode==='bottom-fishing'?bottomFieldDefs:fieldDefs
  const modeBuiltInScreens=mode==='bottom-fishing'?bottomBuiltInScreens:builtInScreens
  const recipeOptions=mode==='bottom-fishing'?bottomRecipeTabs:recipeTabs.map(value=>({label:value,value}))
  const modeDefaultVisibility=mode==='bottom-fishing'?bottomDefaultVisibility:defaultVisibility
  const storagePrefix=`dv:${mode}:${mode==='bottom-fishing'?'v4':'v3'}`
  const {
    core,
    error,
    manifest,
    selectedTicker,
    selectTicker,
    reviewScope,
    reload,
    loadChart,
    loadCandidateDetail,
    loadExcluded,
    loadHistory,
    loadContextAsset,
  } = useStockScoutData();
  const owner = useOwnerData();
  const payload = core as Payload | null;
  const routeQuery=new URLSearchParams(location.search),groupType=routeQuery.get('groupType'),groupName=routeQuery.get('group')
  const [page, setPage] = useState<Page>("Screener"),
    [recipe, setRecipe] = useState("All"),
    [query, setQuery] = useState("");
  const [sorting, setSorting] = useState<SortingState>(() =>
    loadLocal(`${storagePrefix}:sorts`, mode==='bottom-fishing'?[{id:'focusBlend',desc:true}]:[]),
  );
  const [visibility, setVisibility] = useState<VisibilityState>(() =>
    loadLocal(`${storagePrefix}:columns`, modeDefaultVisibility),
  );
  const [pagination, setPagination] = useState<PaginationState>({
    pageIndex: 0,
    pageSize: 100,
  });
  const [rootLogic, setRootLogic] = useState<Logic>(() =>
    loadLocal(`${storagePrefix}:root-logic`, "ALL"),
  );
  const [groups, setGroups] = useState<RuleGroup[]>(() =>
    loadLocal(`${storagePrefix}:groups`, []),
  );
  const [customScreens, setCustomScreens] = useState<ScreenState[]>(() =>
    loadLocal(`${storagePrefix}:screens`, []),
  );
  const [activeScreen, setActiveScreen] = useState("Custom"),
    [builderOpen, setBuilderOpen] = useState(false),
    [columnsOpen, setColumnsOpen] = useState(false),
    [screenDialogOpen, setScreenDialogOpen] = useState(false),
    [screenNameDraft, setScreenNameDraft] = useState("");
  const [selectedChart, setSelectedChart] = useState<ChartLoadState>({
    status: "loading",
    bars: [],
  });
  const [selectedDetail, setSelectedDetail] = useState<Stock | null>(null);
  const[bottomSidecar,setBottomSidecar]=useState<Record<string,Stock>|null>(null)
  const [excludedState, setExcludedState] = useState<RowsLoadState<Stock>>({
    status: "idle",
    rows: [],
  });
  const [historyState, setHistoryState] = useState<
    RowsLoadState<ScanHistoryItemV1>
  >({ status: "idle", rows: [] });
  const [interval, setInterval] = useState<Interval>("W"),
    [range, setRange] = useState<Range>("5Y"),
    [chartMode, setChartMode] = useState<ChartMode>("Price");
  const [gridCount, setGridCount] = useState(16),
    [gridRange, setGridRange] = useState<Range>("2Y");
  useEffect(() => {
    if (reviewScope) {
      setPage("Grid");
      setGridCount(16);
      setPagination((p) => ({ ...p, pageIndex: 0 }));
    }
  }, [reviewScope]);
  useEffect(
    () =>
      setVisibility((current) => ({
        ...current,
        exclusionReasons: page === "Excluded",
      })),
    [page],
  );
  useEffect(
    () => localStorage.setItem(`${storagePrefix}:sorts`, JSON.stringify(sorting)),
    [sorting,storagePrefix],
  );
  useEffect(
    () => localStorage.setItem(`${storagePrefix}:columns`, JSON.stringify(visibility)),
    [visibility,storagePrefix],
  );
  useEffect(
    () => localStorage.setItem(`${storagePrefix}:root-logic`, JSON.stringify(rootLogic)),
    [rootLogic,storagePrefix],
  );
  useEffect(
    () => localStorage.setItem(`${storagePrefix}:groups`, JSON.stringify(groups)),
    [groups,storagePrefix],
  );
  useEffect(
    () =>
      localStorage.setItem(
        `${storagePrefix}:screens`,
        JSON.stringify(customScreens),
      ),
    [customScreens,storagePrefix],
  );
  const loadBars = useCallback(
    async (ticker: string, retry = false): Promise<ChartLoadState> => {
      const result = await loadChart(ticker, retry);
      if (result.status !== "ready") return { ...result, bars: [] };
      const bars = result.rows
        .map(chartBar)
        .filter((bar): bar is Bar => bar !== null)
        .sort((left, right) => left.time.localeCompare(right.time));
      return bars.length
        ? { status: "ready", bars }
        : {
            status: "error",
            bars: [],
            error: "Chart payload has no valid EOD bars",
          };
    },
    [loadChart],
  );

  const snapshotKey =
    manifest && isManifestV1(manifest)
      ? `${manifest.runId}:${manifest.generatedAt}`
      : (payload?.generatedAt ?? "");
  useEffect(() => {
    setExcludedState({ status: "idle", rows: [] });
    setHistoryState({ status: "idle", rows: [] });
  }, [snapshotKey]);
  useEffect(() => {
    if (page !== "Excluded") return;
    let live = true;
    setExcludedState({ status: "loading", rows: [] });
    loadExcluded()
      .then((rows) => {
        if (!live) return;
        const normalized = rows
          .map(
            (row: any, index) =>
              ({
                ...row,
                ticker: String(row.ticker ?? "")
                  .trim()
                  .toUpperCase(),
                scanOrder: Number(row.scanOrder ?? row.scan_order ?? index),
                excluded: true,
                reasons:
                  row.reasons ??
                  row.reasonCodes ??
                  row.reason_codes ??
                  row.exclusionReasons ??
                  row.exclusion_reasons ??
                  [],
              }) as Stock,
          )
          .filter((row) => row.ticker);
        setExcludedState({
          status: "ready",
          rows: preserveScanOrder(normalized),
        });
      })
      .catch((next) => {
        if (live)
          setExcludedState({
            status: "error",
            rows: [],
            error: next instanceof Error ? next.message : String(next),
          });
      });
    return () => {
      live = false;
    };
  }, [page, loadExcluded, snapshotKey]);
  useEffect(() => {
    if (page !== "History") return;
    let live = true;
    setHistoryState({ status: "loading", rows: [] });
    loadHistory()
      .then((rows) => {
        if (!live) return;
        const latest = [...rows]
          .sort((left, right) =>
            String(
              (right as any).sessionDate ?? (right as any).session_date ?? "",
            ).localeCompare(
              String(
                (left as any).sessionDate ?? (left as any).session_date ?? "",
              ),
            ),
          )
          .slice(0, 252);
        setHistoryState({ status: "ready", rows: latest });
      })
      .catch((next) => {
        if (live)
          setHistoryState({
            status: "error",
            rows: [],
            error: next instanceof Error ? next.message : String(next),
          });
      });
    return () => {
      live = false;
    };
  }, [page, loadHistory, snapshotKey]);
  useEffect(() => {
    if (page === "Owner" && !owner.user) setPage("Screener");
  }, [page, owner.user]);

  useEffect(()=>{if(mode!=='bottom-fishing'||bottomSidecar||(!builderOpen&&!columnsOpen))return;let live=true;loadContextAsset<{rows:Stock[]}>('bottomScreener').then(payload=>{if(live)setBottomSidecar(Object.fromEntries(payload.rows.map(row=>[row.ticker,row]))) }).catch(()=>undefined);return()=>{live=false}},[mode,bottomSidecar,builderOpen,columnsOpen,loadContextAsset])

  const universe = useMemo(()=>{const rows=payload?.universe||[];return bottomSidecar?rows.map(row=>({...row,...bottomSidecar[row.ticker],id:row.id,scanOrder:row.scanOrder})):rows},[payload?.universe,bottomSidecar]);
  const displayUniverse = page === "Excluded" ? excludedState.rows : universe;
  const watchlist = owner.watchlist;
  const toggleWatch = (ticker: string) => {
    void owner.toggleWatch(ticker).catch(() => undefined);
  };
  const filtered = useMemo(
    () =>
      displayUniverse.filter((s) => {
        const q = query.trim().toUpperCase();
        if (
          q &&
          !s.ticker.includes(q) &&
          !tagsOf(s).join(" ").toUpperCase().includes(q) &&
          (s.changeLabels || []).join(" ").toUpperCase().includes(q) === false
        )
          return false;
        if (page === "Excluded") return true;
        if(mode==='next'&&groupName){
          const value=groupType==='industry'?s.industryProxy:s.sectorProxy
          if(String(value??'')!==groupName)return false
        }
        if (reviewScope) return matchesReviewScope(s, reviewScope);
        if (page === "Watchlist" && !watchlist.includes(s.ticker)) return false;
        if (page === "Changes" && !s.changedToday) return false;
        if (recipe !== "All" && !tagsOf(s).includes(recipe)) return false;
        return matchesGroups(s, groups, rootLogic,modeFieldDefs);
      }),
    [
      displayUniverse,
      reviewScope,
      page,
      watchlist,
      query,
      recipe,
      groups,
      rootLogic,
      modeFieldDefs,
      mode,
      groupType,
      groupName,
    ],
  );
  const sortedData = useMemo(
    () =>
      sorting.length
        ? applyMultiSort(filtered, sorting)
        : preserveScanOrder(filtered),
    [filtered, sorting],
  );
  const candidatePage = !["Market", "History", "Owner"].includes(page);
  useEffect(()=>{
    const advance=(event:KeyboardEvent)=>{
      if(event.code!=='Space'||event.defaultPrevented||event.altKey||event.ctrlKey||event.metaKey||event.shiftKey||!candidatePage||!sortedData.length)return
      const target=event.target as HTMLElement|null
      if(target?.closest('input,select,textarea,button,a,[contenteditable="true"],[role="dialog"]'))return
      event.preventDefault()
      const current=Math.max(0,sortedData.findIndex(row=>row.ticker===selectedTicker)),next=(current+1)%sortedData.length
      selectTicker(sortedData[next].ticker)
      setPagination(value=>({...value,pageIndex:Math.floor(next/value.pageSize)}))
    }
    window.addEventListener('keydown',advance)
    return()=>window.removeEventListener('keydown',advance)
  },[candidatePage,sortedData,selectedTicker,selectTicker])
  const selected = candidatePage
    ? sortedData.find((s) => s.ticker === selectedTicker) || sortedData[0]
    : undefined;
  useEffect(() => {
    if (
      !selected ||
      selected.excluded ||
      !manifest ||
      !isManifestV1(manifest)
    ) {
      setSelectedDetail(null);
      return;
    }
    let live = true;
    setSelectedDetail(null);
    loadCandidateDetail(selected.ticker)
      .then((detail) => {
        if (live && detail) setSelectedDetail(detail as Stock);
      })
      .catch(() => undefined);
    return () => {
      live = false;
    };
  }, [selected?.ticker, loadCandidateDetail, manifest]);
  const selectedFull =
    selected && selectedDetail?.ticker === selected.ticker
      ? { ...selected, ...selectedDetail }
      : selected;
  useEffect(() => {
    if (
      candidatePage &&
      sortedData.length &&
      selectedTicker !== selected?.ticker
    )
      selectTicker(sortedData[0].ticker);
  }, [
    candidatePage,
    sortedData,
    selectedTicker,
    selected?.ticker,
    selectTicker,
  ]);
  const loadSelectedChart = useCallback(
    (retry = false) => {
      if (!selected) {
        setSelectedChart({ status: "unavailable", bars: [] });
        return () => {};
      }
      let live = true;
      setSelectedChart({ status: "loading", bars: [] });
      loadBars(selected.ticker, retry).then((next) => {
        if (live) setSelectedChart(next);
      });
      return () => {
        live = false;
      };
    },
    [selected?.ticker, loadBars],
  );
  useEffect(() => loadSelectedChart(false), [loadSelectedChart]);
  useEffect(
    () => setPagination((p) => ({ ...p, pageIndex: 0 })),
    [page, recipe, query, groups, rootLogic, sorting, reviewScope],
  );

  const columns = useMemo(
    () => {
      const baseColumns=[
      helper.display({
        id: "watch",
        header: "",
        enableSorting: false,
        cell: ({ row }) => (
          <button
            className={`dv-star ${watchlist.includes(row.original.ticker) ? "on" : ""}`}
            disabled={!owner.user || row.original.excluded}
            title={
              !owner.user
                ? "Owner sign-in required"
                : row.original.excluded
                  ? "Excluded rows cannot be watched"
                  : "Toggle owner watchlist"
            }
            onClick={(e) => {
              e.stopPropagation();
              toggleWatch(row.original.ticker);
            }}
          >
            ★
          </button>
        ),
      }),
      helper.accessor("scanOrder", {
        header: "Scan #",
        cell: (i) =>
          Number.isFinite(Number(i.getValue()))
            ? `#${Number(i.getValue()) + 1}`
            : "—",
      }),
      helper.accessor("ticker", {
        header: "Ticker",
        cell: (i) => <b className="dv-ticker">{i.getValue()}</b>,
      }),
      helper.accessor((s) => modeScore(s, mode), {
        id: "focusBlend",
        header: modeScoreLabel(mode),
        cell: (i) => <b className="dv-score">{fmt(i.getValue(), 1)}</b>,
      }),
      helper.accessor((s) => tradePlanOf(s).status, {
        id: "tradeStatus",
        header: "Trade status",
        cell: (i) =>
          mode === "bottom-fishing" ? (
            <TradeBadge stock={i.row.original} />
          ) : (
            <span>Mode signal</span>
          ),
      }),
      helper.accessor((s) => tradePlanOf(s).risk, {
        id: "entryRiskPct",
        header: "Entry risk",
        cell: (i) =>
          mode !== "bottom-fishing" || i.getValue() == null
            ? "—"
            : `${fmt(i.getValue(), 1)}%`,
      }),
      helper.accessor((s)=>String(s.tradePlan?.triggerState??s.tradePlan?.trigger_state??'—'),{
        id:'triggerState',header:'Trigger state',cell:(i)=><span className="dv-primary">{i.getValue().replaceAll('_',' ')}</span>,
      }),
      helper.accessor((s)=>s.actionability??'—',{
        id:'actionability',header:'Actionability',cell:(i)=><span>{String(i.getValue()).replaceAll('_',' ')}</span>,
      }),
      helper.accessor((s)=>{const trigger=tradePlanOf(s).trigger;return trigger&&Number.isFinite(s.price)?(s.price-trigger)/trigger*100:null},{
        id:'distanceToTrigger',header:'To trigger',cell:(i)=>i.getValue()==null?'—':signed(i.getValue(),1),
      }),
      helper.accessor((s) => opp(s), {
        id: "opportunityScore",
        header: "Opportunity analysis",
        cell: (i) => <b>{fmt(i.getValue(), 0)}</b>,
      }),
      helper.accessor("ema10d20dSpreadPct", {
        header: "EMA 10/20",
        cell: (i) => {
          const state = i.row.original.ema10d20dState,
            v = i.getValue(),
            age = i.row.original.ema10d20dCrossAge;
          return (
            <b className={num(v) > 0 ? "dv-good" : num(v) < 0 ? "dv-bad" : ""}>
              {state || "—"} {signed(v, 2)}
              {typeof age === "number" ? ` · ${age}d` : ""}
            </b>
          );
        },
      }),
      helper.accessor("sma10w20wSpreadPct", {
        header: "SMA 10/20",
        cell: (i) => {
          const state = i.row.original.sma10w20wState,
            v = i.getValue(),
            age = i.row.original.sma10w20wCrossAge;
          return (
            <b className={num(v) > 0 ? "dv-good" : num(v) < 0 ? "dv-bad" : ""}>
              {state || "—"} {signed(v, 2)}
              {typeof age === "number" ? ` · ${age}w` : ""}
            </b>
          );
        },
      }),
      helper.accessor("opportunityTier", {
        header: "Tier",
        cell: (i) => <b>{i.getValue() || "—"}</b>,
      }),
      helper.accessor("opportunityRank", {
        header: "Opp %ile",
        cell: (i) => (
          <b className={num(i.getValue()) >= 95 ? "dv-good" : ""}>
            {fmt(i.getValue(), 0)}
          </b>
        ),
      }),
      helper.accessor("opportunityPotential", {
        header: "Potential",
        cell: (i) => fmt(i.getValue(), 0),
      }),
      helper.accessor("opportunityTiming", {
        header: "Timing",
        cell: (i) => (
          <b className={num(i.getValue()) >= 80 ? "dv-good" : ""}>
            {fmt(i.getValue(), 0)}
          </b>
        ),
      }),
      helper.accessor("opportunityGroupModifier", {
        header: "Group Δ",
        cell: (i) => (
          <span
            className={
              num(i.getValue()) > 0
                ? "dv-good"
                : num(i.getValue()) < 0
                  ? "dv-bad"
                  : ""
            }
          >
            {num(i.getValue()) > 0 ? "+" : ""}
            {fmt(i.getValue(), 1)}
          </span>
        ),
      }),
      helper.accessor("opportunityFundModifier", {
        header: "Fund Δ",
        cell: (i) => (
          <span
            className={
              num(i.getValue()) > 0
                ? "dv-good"
                : num(i.getValue()) < 0
                  ? "dv-bad"
                  : ""
            }
          >
            {num(i.getValue()) > 0 ? "+" : ""}
            {fmt(i.getValue(), 1)}
          </span>
        ),
      }),
      helper.accessor("opportunityPenalty", {
        header: "Penalty",
        cell: (i) => (
          <span className={num(i.getValue()) > 0 ? "dv-bad" : ""}>
            {num(i.getValue()) ? `-${fmt(i.getValue(), 0)}` : "—"}
          </span>
        ),
      }),
      helper.accessor("emergingLeaderScore", {
        header: "Emerging",
        cell: (i) => fmt(i.getValue(), 0),
      }),
      helper.accessor("leadershipScore", {
        header: "Lead Adj",
        cell: (i) => <b>{fmt(i.getValue(), 0)}</b>,
      }),
      helper.accessor("groupRank", {
        header: "Group Rank",
        cell: (i) => (
          <b className={num(i.getValue()) >= 65 ? "dv-good" : ""}>
            {fmt(i.getValue(), 0)}
          </b>
        ),
      }),
      helper.accessor("groupRS", {
        header: "Group RS",
        cell: (i) => (
          <span
            className={
              num(i.getValue()) > 0
                ? "dv-good"
                : num(i.getValue()) < 0
                  ? "dv-bad"
                  : ""
            }
          >
            {signed(i.getValue())}
          </span>
        ),
      }),
      helper.accessor("groupConfidence", {
        header: "Group Conf",
        cell: (i) => `${fmt(i.getValue(), 0)}%`,
      }),
      helper.accessor("fundamentalEvidenceScore", {
        header: "Fund Ev",
        cell: (i) => (
          <b
            className={
              num(i.getValue(), -1) >= 75
                ? "dv-good"
                : num(i.getValue(), -1) >= 0 && num(i.getValue(), -1) < 40
                  ? "dv-bad"
                  : ""
            }
          >
            {fmt(i.getValue(), 0)}
          </b>
        ),
      }),
      helper.accessor("fundamentalEvidenceConfidence", {
        header: "Fund Conf",
        cell: (i) => `${fmt(i.getValue(), 0)}%`,
      }),
      helper.accessor("fundamentalEvidenceCoverage", {
        header: "Fund Cov",
        cell: (i) => `${fmt(i.getValue(), 0)}%`,
      }),
      helper.accessor("revenueYoY", {
        header: "Rev YoY",
        cell: (i) => (
          <span
            className={
              num(i.getValue()) > 0
                ? "dv-good"
                : num(i.getValue()) < 0
                  ? "dv-bad"
                  : ""
            }
          >
            {signed(i.getValue())}
          </span>
        ),
      }),
      helper.accessor("epsYoY", {
        header: "EPS YoY",
        cell: (i) => (
          <span
            className={
              num(i.getValue()) > 0
                ? "dv-good"
                : num(i.getValue()) < 0
                  ? "dv-bad"
                  : ""
            }
          >
            {signed(i.getValue())}
          </span>
        ),
      }),
      helper.accessor("operatingCashFlowYoY", {
        header: "OCF YoY",
        cell: (i) => (
          <span
            className={
              num(i.getValue()) > 0
                ? "dv-good"
                : num(i.getValue()) < 0
                  ? "dv-bad"
                  : ""
            }
          >
            {signed(i.getValue())}
          </span>
        ),
      }),
      helper.accessor("freeCashFlowYoY", {
        header: "FCF YoY",
        cell: (i) => (
          <span
            className={
              num(i.getValue()) > 0
                ? "dv-good"
                : num(i.getValue()) < 0
                  ? "dv-bad"
                  : ""
            }
          >
            {signed(i.getValue())}
          </span>
        ),
      }),
      helper.accessor("freeCashFlowMargin", {
        header: "FCF Margin",
        cell: (i) => (
          <span
            className={
              num(i.getValue()) > 0
                ? "dv-good"
                : num(i.getValue()) < 0
                  ? "dv-bad"
                  : ""
            }
          >
            {signed(i.getValue())}
          </span>
        ),
      }),
      helper.accessor("totalDebtYoY", {
        header: "Debt YoY",
        cell: (i) => (
          <span
            className={
              num(i.getValue()) < 0
                ? "dv-good"
                : num(i.getValue()) > 0
                  ? "dv-bad"
                  : ""
            }
          >
            {signed(i.getValue())}
          </span>
        ),
      }),
      helper.accessor("netDebt", {
        header: "Net Debt",
        cell: (i) => (
          <span className={num(i.getValue()) < 0 ? "dv-good" : ""}>
            {compact(i.getValue())}
          </span>
        ),
      }),
      helper.accessor("shareDilutionYoY", {
        header: "Dilution YoY",
        cell: (i) => (
          <span
            className={
              num(i.getValue()) <= 0
                ? "dv-good"
                : num(i.getValue()) > 2
                  ? "dv-bad"
                  : ""
            }
          >
            {signed(i.getValue())}
          </span>
        ),
      }),
      helper.accessor("originalBuyScore", {
        header: "LEG Buy",
        cell: (i) => (
          <b className={num(i.getValue()) >= 90 ? "dv-good" : ""}>
            {fmt(i.getValue(), 0)}
          </b>
        ),
      }),
      helper.accessor("originalRR", {
        header: "LEG R/R",
        cell: (i) => (
          <b className={num(i.getValue()) >= 2 ? "dv-good" : ""}>
            {fmt(i.getValue(), 1)}:1
          </b>
        ),
      }),
      helper.accessor("originalTTPasses", {
        header: "LEG TT",
        cell: (i) => `${fmt(i.getValue(), 0)}/8`,
      }),
      helper.accessor("originalVcpQuality", {
        header: "LEG VCP",
        cell: (i) => fmt(i.getValue(), 0),
      }),
      helper.accessor("originalAdVolumeRatio", {
        header: "LEG A/D",
        cell: (i) => `${fmt(i.getValue(), 2)}x`,
      }),
      helper.accessor("originalRiskPct", {
        header: "LEG Risk",
        cell: (i) => `${fmt(i.getValue(), 1)}%`,
      }),
      helper.accessor("originalSellScore", {
        header: "LEG Sell",
        cell: (i) => (
          <b className={num(i.getValue()) >= 60 ? "dv-bad" : ""}>
            {fmt(i.getValue(), 0)}
          </b>
        ),
      }),
      helper.accessor("changeImpact", {
        header: "Since scan Δ",
        cell: (i) => (
          <b
            className={
              num(i.getValue()) > 0
                ? "dv-good"
                : num(i.getValue()) < 0
                  ? "dv-bad"
                  : ""
            }
          >
            {num(i.getValue()) ? signed(i.getValue(), 0) : "—"}
          </b>
        ),
      }),
      helper.display({
        id: "todaySignals",
        header: "What changed",
        enableSorting: false,
        cell: ({ row }) => (
          <div className="dv-changechips">
            {(row.original.changeLabels || []).slice(0, 2).map((x) => (
              <span key={x}>{x}</span>
            ))}
          </div>
        ),
      }),
      helper.display({
        id: "exclusionReasons",
        header: "Excluded because",
        enableSorting: false,
        cell: ({ row }) => (
          <div className="dv-changechips">
            {(row.original.reasons || []).slice(0, 3).map((reason) => (
              <span key={reason}>{String(reason).replaceAll("_", " ")}</span>
            ))}
          </div>
        ),
      }),
      helper.accessor((s) => setupOf(s), {
        id: "primarySetup",
        header: "Primary setup",
        cell: (i) => <span className="dv-primary">{i.getValue()}</span>,
      }),
      helper.accessor("confluence", {
        header: "Conf",
        cell: (i) => fmt(i.getValue(), 0),
      }),
      helper.accessor("freshnessScore", {
        header: "Fresh",
        cell: (i) => fmt(i.getValue(), 0),
      }),
      helper.accessor("rsRank", {
        header: "RS Rank",
        cell: (i) => (
          <b className={num(i.getValue()) >= 90 ? "dv-good" : ""}>
            {fmt(i.getValue(), 0)}
          </b>
        ),
      }),
      helper.accessor("rsRankDelta", {
        header: "Δ RS",
        cell: (i) => (
          <span
            className={
              num(i.getValue()) > 0
                ? "dv-good"
                : num(i.getValue()) < 0
                  ? "dv-bad"
                  : ""
            }
          >
            {num(i.getValue()) ? signed(i.getValue(), 0) : "—"}
          </span>
        ),
      }),
      helper.accessor("rsAcceleration", {
        header: "RS Accel",
        cell: (i) => (
          <span className={num(i.getValue()) > 0 ? "dv-good" : "dv-bad"}>
            {fmt(i.getValue(), 2)}
          </span>
        ),
      }),
      helper.accessor("stage", { header: "Stage" }),
      helper.accessor("stage2AgeWeeks", {
        header: "S2 age",
        cell: (i) => `${fmt(i.getValue(), 1)}w`,
      }),
      helper.accessor("trendTemplatePasses", {
        header: "TT",
        cell: (i) => `${fmt(i.getValue(), 0)}/8`,
      }),
      helper.accessor("ema10d", {
        header: "EMA 10D",
        cell: (i) => fmt(i.getValue(), 2),
      }),
      helper.accessor("ema20d", {
        header: "EMA 20D",
        cell: (i) => fmt(i.getValue(), 2),
      }),
      helper.accessor("ema10d20dCrossAge", {
        header: "D EMA X",
        cell: (i) => {
          const cross = i.row.original.ema10d20dCross;
          return cross ? (
            <b className={cross === "BULL" ? "dv-good" : "dv-bad"}>
              {cross} {fmt(i.getValue(), 0)}d
            </b>
          ) : (
            "—"
          );
        },
      }),
      helper.accessor("sma10w", {
        header: "SMA 10W",
        cell: (i) => fmt(i.getValue(), 2),
      }),
      helper.accessor("sma20w", {
        header: "SMA 20W",
        cell: (i) => fmt(i.getValue(), 2),
      }),
      helper.accessor("sma10w20wCrossAge", {
        header: "W SMA X",
        cell: (i) => {
          const cross = i.row.original.sma10w20wCross;
          return cross ? (
            <b className={cross === "BULL" ? "dv-good" : "dv-bad"}>
              {cross} {fmt(i.getValue(), 0)}w
            </b>
          ) : (
            "—"
          );
        },
      }),
      helper.accessor("return3m", {
        header: "3M",
        cell: (i) => signed(i.getValue()),
      }),
      helper.accessor("prior9mReturn", {
        header: "Prior 9M",
        cell: (i) => signed(i.getValue()),
      }),
      helper.accessor("volumeRatio", {
        header: "Vol x",
        cell: (i) => (
          <span className={num(i.getValue()) >= 1.5 ? "dv-good" : ""}>
            {fmt(i.getValue(), 2)}x
          </span>
        ),
      }),
      helper.accessor("breakoutPct", {
        header: "Breakout",
        cell: (i) => signed(i.getValue()),
      }),
      helper.accessor("vcpScore", {
        header: "VCP",
        cell: (i) => fmt(i.getValue(), 0),
      }),
      helper.accessor("atrCompression", {
        header: "ATR comp",
        cell: (i) => `${fmt(i.getValue(), 0)}%`,
      }),
      helper.accessor("tightRange20", {
        header: "20D range",
        cell: (i) => `${fmt(i.getValue(), 1)}%`,
      }),
      helper.accessor("baseWeeks", {
        header: "Base w",
        cell: (i) => fmt(i.getValue(), 0),
      }),
      helper.accessor("distance10w", {
        header: "10W",
        cell: (i) => signed(i.getValue()),
      }),
      helper.accessor("distance30w", {
        header: "30W",
        cell: (i) => signed(i.getValue()),
      }),
      helper.accessor("rsFromHigh", {
        header: "RS vs High",
        cell: (i) => signed(i.getValue()),
      }),
      helper.accessor("structureScore", {
        header: "Structure",
        cell: (i) => fmt(i.getValue(), 0),
      }),
      helper.accessor("baseScore", {
        header: "Base",
        cell: (i) => fmt(i.getValue(), 0),
      }),
      helper.accessor("triggerScore", {
        header: "Trigger",
        cell: (i) => fmt(i.getValue(), 0),
      }),
      helper.accessor("neglectedScore", {
        header: "Neglected",
        cell: (i) => fmt(i.getValue(), 0),
      }),
      helper.accessor("avgDollarVolume20", {
        header: "$ Vol",
        cell: (i) => compact(i.getValue()),
      }),
      helper.accessor("fundamentalSupport", {
        header: "Fund",
        cell: (i) => (i.getValue() == null ? "—" : i.getValue() ? "✓" : "×"),
      }),
      ]
      if(mode!=='bottom-fishing')return baseColumns
      const existing=new Set(['ticker','focusBlend','actionability'])
      const sourceColumns=bottomFieldDefs.filter(field=>!existing.has(field.id)).map(field=>helper.accessor((stock)=>fieldValue(stock,field.id),{
        id:field.id,
        header:field.label,
        cell:(info)=>{const value=info.getValue();if(value===null||value===undefined||value==='')return'—';if(field.kind==='boolean')return value?'✓':'×';if(field.kind==='number'&&Number.isFinite(Number(value)))return Number(value).toLocaleString('en',{maximumFractionDigits:2});return Array.isArray(value)?value.join(' · '):String(value).replaceAll('_',' ')},
      }))
      return[...baseColumns,...sourceColumns]
    },
    [watchlist, owner.user, mode],
  );
  const table = useReactTable({
    data: sortedData,
    columns,
    state: { pagination, columnVisibility: visibility },
    onPaginationChange: setPagination,
    onColumnVisibilityChange: setVisibility,
    getCoreRowModel: getCoreRowModel(),
    getPaginationRowModel: getPaginationRowModel(),
  });
  const cycleSort = (id: string) =>
    setSorting((prev) => {
      const base = maBaseSortId(id),
        fresh = maFreshSortId(base);
      if (!fresh) {
        const i = prev.findIndex((x) => x.id === id);
        if (i < 0) return [...prev, { id, desc: true }];
        if (prev[i].desc)
          return prev.map((x, n) => (n === i ? { ...x, desc: false } : x));
        return prev.filter((_, n) => n !== i);
      }
      const i = prev.findIndex((x) => x.id === base || x.id === fresh);
      if (i < 0) return [...prev, { id: base, desc: true }];
      const current = prev[i];
      if (current.id === fresh) return prev.filter((_, n) => n !== i);
      if (current.desc)
        return prev.map((x, n) => (n === i ? { id: base, desc: false } : x));
      return prev.map((x, n) => (n === i ? { id: fresh, desc: true } : x));
    });
  const moveSort = (id: string, dir: -1 | 1) =>
    setSorting((s) => {
      const a = [...s],
        i = a.findIndex((x) => x.id === id),
        j = i + dir;
      if (i < 0 || j < 0 || j >= a.length) return s;
      [a[i], a[j]] = [a[j], a[i]];
      return a;
    });

  const allScreens = [...modeBuiltInScreens, ...customScreens];
  const invalidRuleCount = invalidRules(groups,modeFieldDefs).length;
  const applyScreen = (screen: ScreenState) => {
    setRootLogic(screen.rootLogic);
    setGroups(screen.groups);
    setSorting(screen.sorting);
    setVisibility({ ...modeDefaultVisibility, ...(screen.visibility || {}) });
    setRecipe(screen.recipe || "All");
    setQuery(screen.query || "");
    setPagination({ pageIndex: 0, pageSize: screen.pageSize || 100 });
    setActiveScreen(screen.name);
  };
  const saveScreen = () => {
    if (invalidRuleCount) {
      setBuilderOpen(true);
      return;
    }
    setScreenNameDraft(activeScreen === "Custom" ? "My Screen" : activeScreen);
    setScreenDialogOpen(true);
  };
  const confirmSaveScreen = () => {
    const name = screenNameDraft.trim();
    if (!name) return;
    const state: ScreenState = {
      name,
      rootLogic,
      groups,
      sorting,
      visibility,
      recipe,
      query,
      pageSize: pagination.pageSize,
    };
    setCustomScreens((old) => [...old.filter((s) => s.name !== name), state]);
    setActiveScreen(name);
    setScreenDialogOpen(false);
  };
  const deleteScreen = () => {
    if (modeBuiltInScreens.some((s) => s.name === activeScreen)) return;
    setCustomScreens((x) => x.filter((s) => s.name !== activeScreen));
    setActiveScreen("Custom");
  };
  const addGroup = () =>
    setGroups((g) => [...g, makeGroup("ALL", [makeRule("rsRank",modeFieldDefs)])]);
  const updateGroup = (id: string, fn: (g: RuleGroup) => RuleGroup) =>
    setGroups((gs) => gs.map((g) => (g.id === id ? fn(g) : g)));
  const removeGroup = (id: string) =>
    setGroups((gs) => gs.filter((g) => g.id !== id));
  const exportCsv = () => {
    const cols = table
      .getAllLeafColumns()
      .filter(
        (c) => !["watch", "todaySignals"].includes(c.id) && c.getIsVisible(),
      );
    const lines = [
      cols.map((c) => c.id).join(","),
      ...sortedData.map((s) =>
        cols
          .map((c) =>
            JSON.stringify(
              (s as any)[c.id] ??
                (c.id === "focusBlend"
                  ? modeScore(s, mode)
                  : c.id === "opportunityScore"
                    ? opp(s)
                    : c.id === "primarySetup"
                      ? setupOf(s)
                      : ""),
            ),
          )
          .join(","),
      ),
    ];
    const u = URL.createObjectURL(
        new Blob([lines.join("\n")], { type: "text/csv" }),
      ),
      a = document.createElement("a");
    a.href = u;
    a.download = `stockscout-${(page === "Excluded" ? "excluded" : reviewScope ? `review-${reviewScope}` : activeScreen).replace(/\W+/g, "-").toLowerCase()}-${new Date().toISOString().slice(0, 10)}.csv`;
    a.click();
    URL.revokeObjectURL(u);
  };

  if (!payload)
    return (
      <div className="dv-loading">
        {error || `Loading ${definition.label}…`}
      </div>
    );
  const m = payload.market || {},
    daily = m.dailyChanges || {},
    ageH = Math.max(
      0,
      Math.round(
        (Date.now() - new Date(payload.generatedAt).getTime()) / 3600000,
      ),
    ),
    regimeLabel = marketRegimeLabel(m);
  const scanDate =
    manifest && isManifestV1(manifest)
      ? manifest.sessionDate
      : payload.generatedAt.slice(0, 10);
  const scanStatus =
    manifest && isManifestV1(manifest) ? manifest.status : "legacy";
  const navPages: Page[] = [
    "Screener",
    "Grid",
    "Changes",
    "Excluded",
    "History",
    "Watchlist",
    "Market",
    ...(owner.user ? ["Owner" as const] : []),
  ];
  return (
    <div className="dv-app">
      <header className="dv-top">
        <div className="dv-brand">
          ◉ <b>{definition.label.toUpperCase()}</b>
          <small>
            {scanDate} · {scanStatus} ·{" "}
            {definition.priceBasis === "split_only" ? "split-only" : "adjusted"}
          </small>
        </div>
        <nav>
          {navPages.map((p) => (
            <button
              key={p}
              className={page === p ? "active" : ""}
              onClick={() => setPage(p)}
            >
              {p}
              {p === "Changes" && daily.changed
                ? ` ${daily.changed}`
                : p === "Excluded" && manifest && isManifestV1(manifest)
                  ? ` ${manifest.counts.excluded}`
                  : p === "History" && historyState.rows.length
                    ? ` ${historyState.rows.length}`
                    : ""}
            </button>
          ))}
        </nav>
        <div className={`dv-live ${ageH > 36 ? "stale" : ""}`}>
          <b>{regimeLabel}</b>
          <span>{universe.length.toLocaleString()} stocks</span>
          <span>{ageH}h old</span>
          <button onClick={reload} aria-label="Refresh scan">
            ↻
          </button>
          {mode==='bottom-fishing'?<a className="dv-open-bottom" href={`https://garrincha077.github.io/StockScout-Bottom-Fishing/?ticker=${encodeURIComponent(selectedTicker??'')}&screen=${encodeURIComponent(activeScreen)}`}>Open full Bottom Fishing</a>:null}
        </div>
      </header>

      {page === "Market" ? (
        <Market universe={universe} market={m} mode={mode} />
      ) : page === "History" ? (
        <HistoryView state={historyState} />
      ) : page === "Owner" ? (
        <Suspense
          fallback={<div className="dv-loading">Loading owner workspace…</div>}
        >
          <OwnerWorkspace ticker={selectedTicker ?? ""} />
        </Suspense>
      ) : (
        <>
          <section className="dv-screenbar">
            {mode==='next'&&groupName?<a className="dv-group-filter" href={`?mode=next&view=screener&ticker=${encodeURIComponent(selectedTicker??'')}`}>{groupType==='industry'?'Industry':'Sector'}: {groupName} ×</a>:null}
            {page === "Excluded" ? (
              <>
                <div className="dv-screenpick">
                  <span>EXCLUDED</span>
                  <b>Sanitized rejected records</b>
                </div>
                <div className="dv-screenmeta">
                  <b>
                    {excludedState.status === "loading"
                      ? "Loading…"
                      : `${excludedState.rows.length.toLocaleString()} records`}
                  </b>
                  <span>risk and exclusion labels remain visible</span>
                  <span>
                    {sorting.length
                      ? `${sorting.length} explicit sort levels`
                      : `${definition.label} scan order`}
                  </span>
                </div>
              </>
            ) : (
              <>
                <div className="dv-screenpick">
                  <span>SCREEN</span>
                  <select
                    value={
                      allScreens.some((s) => s.name === activeScreen)
                        ? activeScreen
                        : ""
                    }
                    onChange={(e) => {
                      const s = allScreens.find(
                        (x) => x.name === e.target.value,
                      );
                      if (s) applyScreen(s);
                    }}
                  >
                    <option value="">Custom</option>
                    {modeBuiltInScreens.map((s) => (
                      <option key={s.name}>{s.name}</option>
                    ))}
                    {customScreens.length > 0 && (
                      <optgroup label="My screens">
                        {customScreens.map((s) => (
                          <option key={s.name}>{s.name}</option>
                        ))}
                      </optgroup>
                    )}
                  </select>
                  <button
                    onClick={saveScreen}
                    disabled={invalidRuleCount > 0}
                    title={
                      invalidRuleCount ? "Fix invalid rules before saving" : ""
                    }
                  >
                    Save as…
                  </button>
                  {customScreens.some((s) => s.name === activeScreen) && (
                    <button className="danger" onClick={deleteScreen}>
                      Delete
                    </button>
                  )}
                </div>
                <div className="dv-screenmeta">
                  <b>
                    {reviewScope
                      ? `Review · ${reviewScope === "today" ? "Today" : "New since last scan"}`
                      : activeScreen}
                  </b>
                  <span
                    className={
                      !reviewScope && invalidRuleCount ? "dv-rule-warning" : ""
                    }
                  >
                    {reviewScope
                      ? "screen membership paused"
                      : invalidRuleCount
                        ? `${invalidRuleCount} invalid · ignored`
                        : `${groups.reduce((n, g) => n + g.rules.length, 0)} rules`}
                  </span>
                  <span>
                    {sorting.length
                      ? `${sorting.length} explicit sort levels`
                      : `${definition.label} scan order`}
                  </span>
                  <span>{filtered.length.toLocaleString()} matches</span>
                </div>
              </>
            )}
          </section>

          {page !== "Excluded" && (
            <section className="dv-recipes">
              {recipeOptions.map((option) => (
                <button
                  key={option.value}
                  className={recipe === option.value ? "active" : ""}
                  onClick={() => setRecipe(option.value)}
                >
                  {option.label}
                  <small>
                    {option.value === "All"
                      ? universe.length
                      : universe.filter((s) => tagsOf(s).includes(option.value)).length}
                  </small>
                </button>
              ))}
            </section>
          )}

          <section className="dv-sortbar">
            <span>{sorting.length ? "EXPLICIT SORT" : definition.shortLabel.toUpperCase()}</span>
            {sorting.length ? (
              sorting.map((s, i) => (
                <div className="dv-sortchip" key={s.id}>
                  <b>{i + 1}</b>
                  <em>
                    {s.id === EMA_FRESH_SORT
                      ? "EMA 10/20 · Fresh"
                      : s.id === SMA_FRESH_SORT
                        ? "SMA 10/20 · Fresh"
                        : String(
                            table.getColumn(s.id)?.columnDef.header || s.id,
                          )}
                  </em>
                  <button
                    title={
                      isFreshMaSort(s.id)
                        ? "Fresh crossover first"
                        : s.desc
                          ? "Strength descending"
                          : "Strength ascending"
                    }
                    onClick={() => cycleSort(s.id)}
                  >
                    {isFreshMaSort(s.id) ? "✨" : s.desc ? "↓" : "↑"}
                  </button>
                  <button disabled={i === 0} onClick={() => moveSort(s.id, -1)}>
                    ‹
                  </button>
                  <button
                    disabled={i === sorting.length - 1}
                    onClick={() => moveSort(s.id, 1)}
                  >
                    ›
                  </button>
                  <button
                    onClick={() =>
                      setSorting((x) => x.filter((y) => y.id !== s.id))
                    }
                  >
                    ×
                  </button>
                </div>
              ))
            ) : (
              <i>
                Canonical scan order · click a column only when you want an
                explicit sort
              </i>
            )}
            <button className="dv-clear" onClick={() => setSorting([])}>
              Scan order
            </button>
          </section>

          <section className="dv-toolbar">
            <button
              className={builderOpen ? "active" : ""}
              onClick={() => setBuilderOpen((x) => !x)}
            >
              ⌁ ANY / ALL Builder{" "}
              <b>{groups.reduce((n, g) => n + g.rules.length, 0)}</b>
            </button>
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Ticker, setup or change since last scan…"
            />
            <button
              className={columnsOpen ? "active" : ""}
              onClick={() => setColumnsOpen((x) => !x)}
            >
              ▦ Columns
            </button>
            <select
              value={pagination.pageSize}
              onChange={(e) =>
                setPagination({
                  pageIndex: 0,
                  pageSize: Number(e.target.value),
                })
              }
            >
              <option>50</option>
              <option>100</option>
              <option>250</option>
            </select>
            <button onClick={exportCsv}>⇩ CSV</button>
          </section>

          {builderOpen && page !== "Excluded" && (
            <FilterBuilder
              rootLogic={rootLogic}
              setRootLogic={setRootLogic}
              groups={groups}
              setGroups={setGroups}
              addGroup={addGroup}
              updateGroup={updateGroup}
              removeGroup={removeGroup}
              definitions={modeFieldDefs}
            />
          )}
          {columnsOpen && (
            <ColumnPicker table={table} setVisibility={setVisibility} definitions={mode==='bottom-fishing'?bottomFieldDefs:undefined} defaultVisibilityState={modeDefaultVisibility}/>
          )}

          {excludedState.status === "error" && page === "Excluded" ? (
            <div className="dv-loading">
              Excluded records could not be loaded: {excludedState.error}
            </div>
          ) : excludedState.status === "loading" && page === "Excluded" ? (
            <div className="dv-loading">Loading excluded records…</div>
          ) : page === "Grid" ? (
            <GridView
              stocks={sortedData}
              count={gridCount}
              setCount={setGridCount}
              range={gridRange}
              setRange={setGridRange}
              loadBars={loadBars}
              selected={selected?.ticker}
              onSelect={selectTicker}
              watchlist={watchlist}
              toggleWatch={toggleWatch}
              canWatch={Boolean(owner.user)}
            />
          ) : (
            <ResizableWorkspace id={`terminal-${mode}`} className="dv-work" defaultSecondary={600}>
              <div className="dv-tablebox">
                <div className="dv-tablewrap">
                  <table>
                    <thead>
                      {table.getHeaderGroups().map((hg) => (
                        <tr key={hg.id}>
                          {hg.headers.map((h) => {
                            const fid = maFreshSortId(h.column.id),
                              si = sorting.findIndex(
                                (s) =>
                                  s.id === h.column.id ||
                                  (fid !== null && s.id === fid),
                              ),
                              ss = si >= 0 ? sorting[si] : null;
                            return (
                              <th
                                key={h.id}
                                className={si >= 0 ? "sorted" : ""}
                                onClick={() =>
                                  h.column.getCanSort() &&
                                  cycleSort(h.column.id)
                                }
                              >
                                {flexRender(
                                  h.column.columnDef.header,
                                  h.getContext(),
                                )}
                                {si >= 0 && (
                                  <>
                                    <i>{si + 1}</i>
                                    <b>
                                      {ss && isFreshMaSort(ss.id)
                                        ? "✨"
                                        : ss?.desc
                                          ? "↓"
                                          : "↑"}
                                    </b>
                                  </>
                                )}
                              </th>
                            );
                          })}
                        </tr>
                      ))}
                    </thead>
                    <tbody>
                      {table.getRowModel().rows.map((r) => (
                        <tr
                          key={r.id}
                          className={
                            r.original.ticker === selected?.ticker
                              ? "selected"
                              : ""
                          }
                          onClick={() => selectTicker(r.original.ticker)}
                        >
                          {r.getVisibleCells().map((c) => (
                            <td key={c.id}>
                              {flexRender(
                                c.column.columnDef.cell,
                                c.getContext(),
                              )}
                            </td>
                          ))}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                <footer>
                  <span>
                    {page === "Changes"
                      ? "Meaningful changes since the previous scan"
                      : page === "Excluded"
                        ? "Excluded records · no silent hiding"
                        : sorting.length
                          ? "Explicit user-selected sort"
                          : `Canonical ${definition.label} scan order`}
                  </span>
                  <div>
                    <button
                      disabled={!table.getCanPreviousPage()}
                      onClick={() => table.previousPage()}
                    >
                      ←
                    </button>
                    <b>
                      {pagination.pageIndex + 1}/
                      {Math.max(1, table.getPageCount())}
                    </b>
                    <button
                      disabled={!table.getCanNextPage()}
                      onClick={() => table.nextPage()}
                    >
                      →
                    </button>
                  </div>
                </footer>
              </div>
              {selectedFull && (
                <Detail
                  stock={selectedFull}
                  chart={selectedChart}
                  retryChart={() => loadSelectedChart(true)}
                  interval={interval}
                  setInterval={setInterval}
                  range={range}
                  setRange={setRange}
                  mode={chartMode}
                  setMode={setChartMode}
                  watched={watchlist.includes(selectedFull.ticker)}
                  canWatch={Boolean(owner.user) && !selectedFull.excluded}
                  toggleWatch={() => toggleWatch(selectedFull.ticker)}
                />
              )}
            </ResizableWorkspace>
          )}
        </>
      )}
      {screenDialogOpen && (
        <div className="dv-dialog-backdrop" role="presentation">
          <form
            className="dv-dialog"
            role="dialog"
            aria-modal="true"
            aria-labelledby="dv-save-screen-title"
            onSubmit={(event) => {
              event.preventDefault();
              confirmSaveScreen();
            }}
          >
            <small>SAVED SCREEN</small>
            <h2 id="dv-save-screen-title">Name this screen</h2>
            <p>Rules, columns, sorting and page size will be saved in this browser.</p>
            <label>
              Screen name
              <input
                autoFocus
                maxLength={80}
                value={screenNameDraft}
                onChange={(event) => setScreenNameDraft(event.target.value)}
              />
            </label>
            <div>
              <button type="button" onClick={() => setScreenDialogOpen(false)}>
                Cancel
              </button>
              <button type="submit" disabled={!screenNameDraft.trim()}>
                Save screen
              </button>
            </div>
          </form>
        </div>
      )}
    </div>
  );
}

function FilterBuilder({
  rootLogic,
  setRootLogic,
  groups,
  setGroups,
  addGroup,
  updateGroup,
  removeGroup,
  definitions,
}: {
  rootLogic: Logic;
  setRootLogic: (v: Logic) => void;
  groups: RuleGroup[];
  setGroups: (v: RuleGroup[]) => void;
  addGroup: () => void;
  updateGroup: (id: string, fn: (g: RuleGroup) => RuleGroup) => void;
  removeGroup: (id: string) => void;
  definitions:typeof fieldDefs;
}) {
  return (
    <section className="dv-builder">
      <div className="dv-builderhead">
        <div>
          <b>GROUP JOIN</b>
          <button
            className={rootLogic === "ALL" ? "active" : ""}
            onClick={() => setRootLogic("ALL")}
          >
            ALL groups
          </button>
          <button
            className={rootLogic === "ANY" ? "active" : ""}
            onClick={() => setRootLogic("ANY")}
          >
            ANY group
          </button>
          <span>
            {rootLogic === "ALL"
              ? "Every group must pass"
              : "At least one group must pass"}
          </span>
        </div>
        <div>
          <button onClick={addGroup}>+ Group</button>
          <button onClick={() => setGroups([])}>Clear rules</button>
        </div>
      </div>
      {groups.length === 0 ? (
        <div className="dv-builderempty">
          No custom rules. Add a group or load a saved screen.
        </div>
      ) : (
        groups.map((g, gi) => (
          <div className="dv-rulegroup" key={g.id}>
            <div className="dv-groupjoin">
              <b>GROUP {gi + 1}</b>
              <button
                className={g.logic === "ALL" ? "active" : ""}
                onClick={() =>
                  updateGroup(g.id, (x) => ({ ...x, logic: "ALL" }))
                }
              >
                ALL
              </button>
              <button
                className={g.logic === "ANY" ? "active" : ""}
                onClick={() =>
                  updateGroup(g.id, (x) => ({ ...x, logic: "ANY" }))
                }
              >
                ANY
              </button>
              <span>
                {g.logic === "ALL"
                  ? "Every valid rule must pass"
                  : "At least one valid rule must pass"}
              </span>
              <button className="danger" onClick={() => removeGroup(g.id)}>
                ×
              </button>
            </div>
            {g.rules.map((r) => {
              const def =
                  definitions.find((x) => x.id === r.field) || definitions[0],
                ops = opsByKind[def.kind],
                ruleError = validateRule(r,definitions);
              return (
                <div
                  className={`dv-rule ${ruleError ? "invalid" : ""}`}
                  key={r.id}
                >
                  <select
                    value={r.field}
                    onChange={(e) => {
                      const d =
                        definitions.find((x) => x.id === e.target.value) ||
                        definitions[0];
                      updateGroup(g.id, (x) => ({
                        ...x,
                        rules: x.rules.map((q) =>
                          q.id === r.id
                            ? { ...q, field: d.id, op: d.defaultOp, value: "" }
                            : q,
                        ),
                      }));
                    }}
                  >
                    {definitions.map((f) => (
                      <option value={f.id} key={f.id}>
                        {f.label}
                      </option>
                    ))}
                  </select>
                  <select
                    value={r.op}
                    onChange={(e) =>
                      updateGroup(g.id, (x) => ({
                        ...x,
                        rules: x.rules.map((q) =>
                          q.id === r.id
                            ? { ...q, op: e.target.value as any }
                            : q,
                        ),
                      }))
                    }
                  >
                    {ops.map((o) => (
                      <option key={o}>{o}</option>
                    ))}
                  </select>
                  {!["true", "false"].includes(r.op) && (
                    <input
                      value={r.value}
                      aria-invalid={Boolean(ruleError)}
                      placeholder={def.placeholder || "value"}
                      onChange={(e) =>
                        updateGroup(g.id, (x) => ({
                          ...x,
                          rules: x.rules.map((q) =>
                            q.id === r.id ? { ...q, value: e.target.value } : q,
                          ),
                        }))
                      }
                    />
                  )}
                  <button
                    onClick={() =>
                      updateGroup(g.id, (x) => ({
                        ...x,
                        rules: x.rules.filter((q) => q.id !== r.id),
                      }))
                    }
                  >
                    ×
                  </button>
                  {ruleError && (
                    <small role="alert">
                      {ruleError} · ignored until fixed
                    </small>
                  )}
                </div>
              );
            })}
            <button
              className="dv-addrule"
              onClick={() =>
                updateGroup(g.id, (x) => ({
                  ...x,
                  rules: [...x.rules, makeRule("rsRank",definitions)],
                }))
              }
            >
              + Rule
            </button>
          </div>
        ))
      )}
    </section>
  );
}

function ColumnPicker({
  table,
  setVisibility,
  definitions,
  defaultVisibilityState,
}: {
  table: any;
  setVisibility: (v: VisibilityState) => void;
  definitions?:typeof fieldDefs;
  defaultVisibilityState:VisibilityState;
}) {
  const nextSets: Record<string, VisibilityState> = {
    Core: defaultVisibilityState,
    Opportunity: {
      ...defaultVisibilityState,
      opportunityScore: true,
      opportunityTier: true,
      opportunityRank: true,
      opportunityPotential: true,
      opportunityTiming: true,
      opportunityGroupModifier: true,
      opportunityFundModifier: true,
      opportunityPenalty: true,
      emergingLeaderScore: true,
    },
    Early: {
      ...defaultVisibilityState,
      prior9mReturn: true,
      stage2AgeWeeks: true,
      neglectedScore: true,
      atrCompression: false,
      vcpScore: false,
    },
    Crosses: {
      ...defaultVisibilityState,
      ema10d: true,
      ema20d: true,
      ema10d20dCrossAge: true,
      ema10d20dSpreadPct: true,
      sma10w: true,
      sma20w: true,
      sma10w20wCrossAge: true,
      sma10w20wSpreadPct: true,
    },
    Groups: {
      ...defaultVisibilityState,
      leadershipScore: true,
      groupRank: true,
      groupRS: true,
      groupConfidence: true,
    },
    Breakout: {
      ...defaultVisibilityState,
      breakoutPct: true,
      volumeRatio: true,
      triggerScore: true,
      atrCompression: true,
    },
    Base: {
      ...defaultVisibilityState,
      vcpScore: true,
      atrCompression: true,
      tightRange20: true,
      baseWeeks: true,
      baseScore: true,
    },
    Fundamentals: {
      ...defaultVisibilityState,
      fundamentalEvidenceScore: true,
      fundamentalEvidenceConfidence: true,
      fundamentalEvidenceCoverage: true,
      fundamentalSupport: true,
      revenueYoY: true,
      epsYoY: true,
      operatingCashFlowYoY: true,
      freeCashFlowYoY: true,
      freeCashFlowMargin: true,
      totalDebtYoY: true,
      netDebt: true,
      shareDilutionYoY: true,
    },
    Changes: {
      ...defaultVisibilityState,
      changeImpact: true,
      opportunityDelta: true,
      rsRankDelta: true,
      todaySignals: true,
    },
  };
  const sets:Record<string,VisibilityState>=definitions?Object.fromEntries([...new Set(definitions.map(field=>field.group??'Source'))].map(group=>[group,{...defaultVisibilityState,...Object.fromEntries(definitions.filter(field=>(field.group??'Source')===group).map(field=>[field.id,true]))}])):nextSets
  const grouped:Record<string,any[]>=table.getAllLeafColumns().filter((column:any)=>column.id!=='watch').reduce((result:Record<string,any[]>,column:any)=>{const group=definitions?.find(field=>field.id===column.id)?.group??(definitions?'Table':'Columns');(result[group]??=[]).push(column);return result},{} as Record<string,any[]>)
  return (
    <section className="dv-colpicker">
      <div className="dv-colsets">
        {Object.entries(sets).map(([name, v]) => (
          <button key={name} onClick={() => setVisibility(v)}>
            {name}
          </button>
        ))}
        <button
          onClick={() =>
            table
              .getAllLeafColumns()
              .forEach((c: any) => c.toggleVisibility(true))
          }
        >
          All
        </button>
      </div>
      {Object.entries(grouped).map(([group,columns])=><div className="dv-colgroup" key={group}><b>{group}</b>{columns.map((c:any)=><label key={c.id}><input type="checkbox" checked={c.getIsVisible()} onChange={c.getToggleVisibilityHandler()}/>{String(c.columnDef.header||c.id)}</label>)}</div>)}
    </section>
  );
}

function GridView({
  stocks,
  count,
  setCount,
  range,
  setRange,
  loadBars,
  selected,
  onSelect,
  watchlist,
  toggleWatch,
  canWatch,
}: {
  stocks: Stock[];
  count: number;
  setCount: (n: number) => void;
  range: Range;
  setRange: (r: Range) => void;
  loadBars: (t: string, retry?: boolean) => Promise<ChartLoadState>;
  selected?: string;
  onSelect: (t: string) => void;
  watchlist: string[];
  toggleWatch: (ticker: string) => void;
  canWatch: boolean;
}) {
  const { definition } = useMode();
  const sentinel = useRef<HTMLDivElement>(null);
  const presets = [12, 16, 24, 36, 48];
  const visible = Math.min(count, stocks.length);
  const selectValue = count >= stocks.length ? stocks.length : count;
  const showProgress = count < stocks.length && !presets.includes(count);
  useEffect(() => {
    const node = sentinel.current;
    if (!node || count >= stocks.length) return;
    const observer = new IntersectionObserver(
      ([entry]) => {
        if (entry?.isIntersecting)
          setCount(nextGridCount(count, stocks.length));
      },
      { rootMargin: "1200px 0px" },
    );
    observer.observe(node);
    return () => observer.disconnect();
  }, [count, stocks.length, setCount]);
  return (
    <main className="dv-gridview">
      <header>
        <div>
          <b>RAPID REVIEW</b>
          <span>
            {visible} of {stocks.length.toLocaleString()} current matches ·
            explicit user sort or canonical {definition.label} scan order
          </span>
        </div>
        <div>
          <select
            value={selectValue}
            onChange={(e) => setCount(Number(e.target.value))}
          >
            {showProgress && <option value={count}>{count}</option>}
            {presets.map((n) => (
              <option key={n} value={n}>
                {n}
              </option>
            ))}
            {!presets.includes(stocks.length) && (
              <option value={stocks.length}>All ({stocks.length})</option>
            )}
          </select>
          {(["6M", "1Y", "2Y", "5Y"] as Range[]).map((r) => (
            <button
              className={range === r ? "active" : ""}
              key={r}
              onClick={() => setRange(r)}
            >
              {r}
            </button>
          ))}
        </div>
      </header>
      <section className="dv-chartgrid">
        {stocks.slice(0, visible).map((s) => (
          <MiniCard
            key={s.ticker}
            stock={s}
            range={range}
            loadBars={loadBars}
            selected={selected === s.ticker}
            watched={watchlist.includes(s.ticker)}
            canWatch={canWatch && !s.excluded}
            toggleWatch={() => toggleWatch(s.ticker)}
            onClick={() => onSelect(s.ticker)}
          />
        ))}
      </section>
      <div className="dv-grid-sentinel" ref={sentinel} aria-hidden="true" />
    </main>
  );
}
function MiniCard({
  stock,
  range,
  loadBars,
  selected,
  watched,
  canWatch,
  toggleWatch,
  onClick,
}: {
  stock: Stock;
  range: Range;
  loadBars: (t: string, retry?: boolean) => Promise<ChartLoadState>;
  selected: boolean;
  watched: boolean;
  canWatch: boolean;
  toggleWatch: () => void;
  onClick: () => void;
}) {
  const { mode } = useMode();
  const interval: Interval = range === "6M" || range === "1Y" ? "D" : "W";
  const [state, setState] = useState<ChartLoadState>({
      status: "loading",
      bars: [],
    }),
    [attempt, setAttempt] = useState(0);
  useEffect(() => {
    let live = true;
    setState({ status: "loading", bars: [] });
    loadBars(stock.ticker, attempt > 0).then((next) => {
      if (live) setState(next);
    });
    return () => {
      live = false;
    };
  }, [stock.ticker, loadBars, attempt]);
  return (
    <article
      className={`dv-minicard ${selected ? "selected" : ""}`}
      onClick={onClick}
    >
      <header>
        <div>
          <b>{stock.ticker}</b>
          <span>{setupOf(stock)}</span>
          {mode === "bottom-fishing" && <TradeBadge stock={stock} />}
        </div>
        <div className="dv-miniactions">
          <button
            className={`dv-heart ${watched ? "on" : ""}`}
            disabled={!canWatch}
            aria-label={
              canWatch
                ? watched
                  ? `Remove ${stock.ticker} from watchlist`
                  : `Add ${stock.ticker} to watchlist`
                : "Owner sign-in required for watchlist"
            }
            title={
              canWatch
                ? watched
                  ? "Remove from watchlist"
                  : "Add to watchlist"
                : "Owner sign-in required"
            }
            onClick={(e) => {
              e.stopPropagation();
              if (canWatch) toggleWatch();
            }}
          >
            {watched ? "♥" : "♡"}
          </button>
          <strong title={modeScoreLabel(mode)}>
            {fmt(modeScore(stock, mode), 1)}
          </strong>
        </div>
      </header>
      <div className="dv-miniinfo">
        <span>
          Scan <b>#{num(stock.scanOrder) + 1}</b>
        </span>
        <span>
          RS <b>{fmt(stock.rsRank, 0)}</b>
        </span>
        <span>
          Fund <b>{fmt(stock.fundamentalEvidenceScore, 0)}</b>
        </span>
        <span>
          Vol <b>{fmt(stock.volumeRatio, 1)}x</b>
        </span>
        <span>
          10W <b>{signed(stock.distance10w)}</b>
        </span>
        {stock.changeImpact !== undefined && num(stock.changeImpact) !== 0 && (
          <span className={num(stock.changeImpact) > 0 ? "dv-good" : "dv-bad"}>
            Δ <b>{signed(stock.changeImpact, 0)}</b>
          </span>
        )}
      </div>
      {state.status === "ready" ? (
        <StockChart bars={state.bars} interval={interval} range={range} mini />
      ) : state.status === "loading" ? (
        <div className="dv-miniload">loading chart…</div>
      ) : state.status === "unavailable" ? (
        <div className="dv-miniload">
          <span>Chart unavailable</span>
          <a
            href={externalChartUrl(stock.ticker)}
            target="_blank"
            rel="noreferrer"
            onClick={(event) => event.stopPropagation()}
          >
            Open external chart ↗
          </a>
        </div>
      ) : (
        <div className="dv-miniload">
          <button
            onClick={(e) => {
              e.stopPropagation();
              setAttempt((x) => x + 1);
            }}
          >
            retry chart
          </button>
        </div>
      )}
      <footer>
        {(stock.changeLabels || []).slice(0, 2).map((x) => (
          <span key={x}>{x}</span>
        ))}
      </footer>
    </article>
  );
}

function PositionSizer({ stock }: { stock: Stock }) {
  const plan = tradePlanOf(stock);
  const [nav, setNav] = useState("10000000"),
    [riskPct, setRiskPct] = useState("0.5");
  const [entry, setEntry] = useState(() =>
    String(plan.entry ?? stock.price ?? ""),
  );
  useEffect(
    () => setEntry(String(plan.entry ?? stock.price ?? "")),
    [stock.ticker, plan.entry, stock.price],
  );
  const sizing = calculatePositionSize(
    plan.status,
    nav,
    riskPct,
    entry,
    plan.tactical,
  );
  const money = (value: number) =>
    new Intl.NumberFormat("en-US", {
      style: "currency",
      currency: "USD",
      maximumFractionDigits: 0,
    }).format(value);
  const sectionStyle = {
    margin: "0 0 8px",
    padding: 9,
    border: "1px solid #1b354c",
    borderRadius: 8,
    background: "#071722",
  };
  if (plan.status !== "entry_ready" || plan.tactical == null)
    return (
      <section className="position-sizer" style={sectionStyle}>
        <header>
          <div>
            <b>POSITION SIZER</b>
            <small>
              {" "}
              · NAV default $10M · enabled only with entry-ready and a valid
              tactical stop.
            </small>
          </div>
        </header>
        <div className="trade-levels">
          <span className="disabled">
            <small>Sizing unavailable</small>
            <b>{sizing.reason}</b>
          </span>
        </div>
      </section>
    );
  const inputStyle = {
    width: "100%",
    boxSizing: "border-box" as const,
    marginTop: 3,
    padding: "5px 6px",
    border: "1px solid #27425e",
    borderRadius: 5,
    background: "#07111d",
    color: "#eef7ff",
  };
  return (
    <section className="position-sizer" style={sectionStyle}>
      <header>
        <div>
          <b>POSITION SIZER</b>
          <small>
            {" "}
            · Uses tactical stop only · structural invalidation is never a
            sizing fallback.
          </small>
        </div>
      </header>
      <div className="trade-levels">
        <span>
          <small>NAV (USD)</small>
          <input
            aria-label="Portfolio NAV"
            type="number"
            min="1"
            step="1000"
            value={nav}
            style={inputStyle}
            onChange={(event) => setNav(event.target.value)}
          />
        </span>
        <span>
          <small>Risk per trade (%)</small>
          <input
            aria-label="Risk per trade percent"
            type="number"
            min="0.01"
            step="0.05"
            value={riskPct}
            style={inputStyle}
            onChange={(event) => setRiskPct(event.target.value)}
          />
        </span>
        <span>
          <small>Entry price</small>
          <input
            aria-label="Entry price"
            type="number"
            min="0.01"
            step="0.01"
            value={entry}
            style={inputStyle}
            onChange={(event) => setEntry(event.target.value)}
          />
        </span>
        <span>
          <small>Tactical stop</small>
          <b>${fmt(plan.tactical, 2)}</b>
        </span>
        {sizing.enabled ? (
          <>
            <span>
              <small>Shares</small>
              <b>{sizing.shares.toLocaleString()}</b>
            </span>
            <span>
              <small>Position</small>
              <b>
                {money(sizing.positionValue)} · {fmt(sizing.positionPctNav, 1)}%
                NAV
              </b>
            </span>
            <span>
              <small>Planned risk</small>
              <b>
                {money(sizing.riskUsed)} / {money(sizing.riskBudget)}
              </b>
            </span>
            <span>
              <small>Risk / share</small>
              <b>
                ${fmt(sizing.riskPerShare, 2)}
                {sizing.cashLimited ? " · NAV capped" : ""}
              </b>
            </span>
          </>
        ) : (
          <span className="disabled">
            <small>Sizing unavailable</small>
            <b>{sizing.reason}</b>
          </span>
        )}
      </div>
    </section>
  );
}

function Detail({
  stock,
  chart,
  retryChart,
  interval,
  setInterval,
  range,
  setRange,
  mode,
  setMode,
  watched,
  canWatch,
  toggleWatch,
}: {
  stock: Stock;
  chart: ChartLoadState;
  retryChart: () => void;
  interval: Interval;
  setInterval: (v: Interval) => void;
  range: Range;
  setRange: (v: Range) => void;
  mode: ChartMode;
  setMode: (v: ChartMode) => void;
  watched: boolean;
  canWatch: boolean;
  toggleWatch: () => void;
}) {
  const { mode: scannerMode } = useMode();
  const plan = tradePlanOf(stock);
  const dims = [
    ["Structure", stock.structureScore],
    ["RS", stock.rsScore],
    ["Base", stock.baseScore],
    ["Trigger", stock.triggerScore],
    ["Freshness", stock.freshnessScore],
    ["Neglected", stock.neglectedScore],
  ] as [string, number | undefined][];
  const fundDims = [
    ["Growth", stock.fundamentalGrowthScore],
    ["Margins", stock.fundamentalMarginScore],
    ["Inventory", stock.fundamentalInventoryScore],
  ] as [string, number | null | undefined][];
  const fundScore = num(stock.fundamentalEvidenceScore, -1);
  const structuralOutside =
    plan.structural != null &&
    chart.status === "ready" &&
    !levelFitsExpandedCandleBounds(
      plan.structural,
      chartSource(chart.bars, interval, range),
      0.1,
    );
  return (
    <aside className="dv-detail">
      <div className="dv-detailhead">
        <button
          className={`dv-star big ${watched ? "on" : ""}`}
          disabled={!canWatch}
          title={canWatch ? "Toggle owner watchlist" : "Owner sign-in required"}
          onClick={() => {
            if (canWatch) toggleWatch();
          }}
        >
          ★
        </button>
        <div>
          <h1>{stock.ticker}</h1>
          <span>
            Stage {stock.stage} · {stock.stageName}
          </span>
          {scannerMode === "bottom-fishing" && <TradeBadge stock={stock} />}
        </div>
        <div className="dv-opp">
          <small>{modeScoreLabel(scannerMode).toUpperCase()}</small>
          <b>{fmt(modeScore(stock, scannerMode), 1)}</b>
          <span>Scan #{num(stock.scanOrder) + 1}</span>
          {num(stock.opportunityDelta) !== 0 && (
            <span
              className={num(stock.opportunityDelta) > 0 ? "dv-good" : "dv-bad"}
            >
              Opp Δ {signed(stock.opportunityDelta, 0)}
            </span>
          )}
        </div>
        <div className="dv-price">
          <b>${fmt(stock.price, 2)}</b>
          <span>{signed(stock.change20d)} 20D</span>
        </div>
      </div>
      {(stock.changeLabels || []).length > 0 && (
        <div className="dv-todaybox">
          <b>CHANGED SINCE LAST SCAN</b>
          {stock.changeLabels!.map((x) => (
            <span key={x}>{x}</span>
          ))}
        </div>
      )}
      <div className="dv-tags">
        {tagsOf(stock).map((t) => (
          <span key={t} className={t.startsWith("⚠") ? "warn" : ""}>
            {t}
          </span>
        ))}
      </div>
      {scannerMode === "bottom-fishing" && <section className="trade-plan">
        <header>
          <div>
            <b>TRADE PLAN</b>
            <small>
              Structural invalidation is not a position stop unless entry is
              ready.
            </small>
          </div>
          <TradeBadge stock={stock} />
        </header>
        <div className="trade-levels">
          <span>
            <small>Entry trigger</small>
            <b>{plan.trigger == null ? "—" : `$${fmt(plan.trigger, 2)}`}</b>
          </span>
          <span>
            <small>Entry reference</small>
            <b>{plan.entry == null ? "—" : `$${fmt(plan.entry, 2)}`}</b>
          </span>
          <span>
            <small>Structural invalidation</small>
            <b>
              {plan.structural == null
                ? "—"
                : `$${fmt(plan.structural, 2)}${structuralOutside ? " · outside chart" : ""}`}
            </b>
          </span>
          <span className={plan.tactical == null ? "disabled" : ""}>
            <small>Tactical stop</small>
            <b>
              {plan.tactical == null
                ? "Not defined — sizing disabled"
                : `$${fmt(plan.tactical, 2)}`}
            </b>
          </span>
          <span>
            <small>Entry risk</small>
            <b>{plan.risk == null ? "—" : `${fmt(plan.risk, 1)}%`}</b>
          </span>
          <span>
            <small>Extension</small>
            <b>
              {plan.extension == null ? "—" : `${fmt(plan.extension, 2)} ATR`}
            </b>
          </span>
        </div>
        {plan.reasons.length > 0 && (
          <footer>
            {plan.reasons.map((reason) => (
              <span key={reason}>{String(reason).replaceAll("_", " ")}</span>
            ))}
          </footer>
        )}
      </section>}
      {scannerMode === "bottom-fishing" && <PositionSizer stock={stock} />}
      {scannerMode === "bottom-fishing" && <BottomSetupDetail stock={stock} />}
      <div className="dv-chartcontrols">
        <div>
          {(["Price", "RS", "Volume"] as ChartMode[]).map((x) => (
            <button
              className={mode === x ? "active" : ""}
              onClick={() => setMode(x)}
              key={x}
            >
              {x}
            </button>
          ))}
        </div>
        <div>
          {(["D", "W"] as Interval[]).map((x) => (
            <button
              className={interval === x ? "active" : ""}
              onClick={() => setInterval(x)}
              key={x}
            >
              {x === "D" ? "Daily" : "Weekly"}
            </button>
          ))}
        </div>
        <div>
          {(["3M", "6M", "1Y", "2Y", "5Y"] as Range[]).map((x) => (
            <button
              className={range === x ? "active" : ""}
              onClick={() => setRange(x)}
              key={x}
            >
              {x}
            </button>
          ))}
        </div>
      </div>
      <div className="dv-ma-note">
        {interval === "D"
          ? "EMA 10 · EMA 20 · SMA 50 · SMA 200"
          : "SMA 10W · SMA 20W"}
      </div>
      <ResizableHeight id={`terminal-chart-${scannerMode}`} className="dv-chart-resizer" defaultHeight={410}>
      <div className="dv-chartbox">
        {chart.status === "loading" ? (
          <div className="dv-chartmsg">Loading 5Y history…</div>
        ) : chart.status === "ready" ? (
          <StockChart
            bars={chart.bars}
            interval={interval}
            range={range}
            display={mode}
            stock={stock}
          />
        ) : chart.status === "unavailable" ? (
          <div className="dv-chartmsg">
            Chart unavailable for this ticker.{" "}
            <a
              href={externalChartUrl(stock.ticker)}
              target="_blank"
              rel="noreferrer"
            >
              Open external chart ↗
            </a>
          </div>
        ) : (
          <div className="dv-chartmsg">
            Chart request failed. <button onClick={retryChart}>Retry</button>
          </div>
        )}
      </div>
      </ResizableHeight>
      <div className="dv-kpis">
        <K l="RS Rank" v={fmt(stock.rsRank, 0)} d={stock.rsRankDelta} />
        <K l="RS Δ" v={fmt(stock.rsAcceleration, 2)} />
        <K l="Group" v={fmt(stock.groupRank, 0)} />
        <K l="Group RS" v={signed(stock.groupRS)} />
        <K l="Group conf" v={`${fmt(stock.groupConfidence, 0)}%`} />
        <K l="TT" v={`${fmt(stock.trendTemplatePasses, 0)}/8`} />
        <K l="EMA 10D" v={fmt(stock.ema10d, 2)} />
        <K l="EMA 20D" v={fmt(stock.ema20d, 2)} />
        <K
          l="D EMA X"
          v={
            stock.ema10d20dCross
              ? `${stock.ema10d20dCross} ${fmt(stock.ema10d20dCrossAge, 0)}d`
              : "—"
          }
        />
        <K l="D EMA spread" v={signed(stock.ema10d20dSpreadPct, 2)} />
        <K l="SMA 10W" v={fmt(stock.sma10w, 2)} />
        <K l="SMA 20W" v={fmt(stock.sma20w, 2)} />
        <K
          l="W SMA X"
          v={
            stock.sma10w20wCross
              ? `${stock.sma10w20wCross} ${fmt(stock.sma10w20wCrossAge, 0)}w`
              : "—"
          }
        />
        <K l="W SMA spread" v={signed(stock.sma10w20wSpreadPct, 2)} />
        <K l="S2 age" v={`${fmt(stock.stage2AgeWeeks, 1)}w`} />
        <K
          l="Vol"
          v={`${fmt(stock.volumeRatio, 2)}x`}
          d={stock.volumeRatioDelta}
        />
        <K l="Breakout" v={signed(stock.breakoutPct)} />
        <K l="10W" v={signed(stock.distance10w)} />
        <K l="30W" v={signed(stock.distance30w)} />
        <K l="Potential" v={fmt(stock.opportunityPotential, 0)} />
        <K l="Timing" v={fmt(stock.opportunityTiming, 0)} />
        <K l="Opp %ile" v={fmt(stock.opportunityRank, 0)} />
        <K
          l="Group Δ"
          v={`${num(stock.opportunityGroupModifier) > 0 ? "+" : ""}${fmt(stock.opportunityGroupModifier, 1)}`}
        />
        <K
          l="Fund Δ"
          v={`${num(stock.opportunityFundModifier) > 0 ? "+" : ""}${fmt(stock.opportunityFundModifier, 1)}`}
        />
        <K l="Fund Ev" v={fmt(stock.fundamentalEvidenceScore, 0)} />
        <K
          l="Fund conf"
          v={`${fmt(stock.fundamentalEvidenceConfidence, 0)}%`}
        />
      </div>
      <div className="dv-dims">
        {dims.map(([n, v]) => (
          <div key={n}>
            <span>{n}</span>
            <i>
              <b style={{ width: `${Math.max(0, Math.min(100, num(v)))}%` }} />
            </i>
            <strong>{fmt(v, 0)}</strong>
          </div>
        ))}
      </div>
      <div className="dv-fundbox">
        <header>
          <div>
            <b>FUNDAMENTAL EVIDENCE</b>
            <small>
              confirmation evidence · bounded ±5 Opportunity modifier
            </small>
          </div>
          <strong
            className={
              fundScore >= 75
                ? "dv-good"
                : fundScore >= 0 && fundScore < 40
                  ? "dv-bad"
                  : ""
            }
          >
            {fmt(stock.fundamentalEvidenceScore, 0)}{" "}
            {stock.fundamentalEvidenceLabel || ""}
          </strong>
          <span>
            confidence {fmt(stock.fundamentalEvidenceConfidence, 0)}% · coverage{" "}
            {fmt(stock.fundamentalEvidenceCoverage, 0)}%
          </span>
        </header>
        <div className="dv-dims">
          {fundDims.map(([n, v]) => (
            <div key={n}>
              <span>{n}</span>
              <i>
                <b
                  style={{ width: `${Math.max(0, Math.min(100, num(v)))}%` }}
                />
              </i>
              <strong>{fmt(v, 0)}</strong>
            </div>
          ))}
        </div>
      </div>
    </aside>
  );
}
function K({ l, v, d }: { l: string; v: string; d?: number }) {
  return (
    <span>
      <small>{l}</small>
      <b>{v}</b>
      {d !== undefined && num(d) !== 0 && (
        <em className={num(d) > 0 ? "dv-good" : "dv-bad"}>{signed(d, 0)}</em>
      )}
    </span>
  );
}

function BottomSetupDetail({stock}:{stock:Stock}){
  const tags=new Set([...(stock.setupTags??[]),...(stock.setupNames??[]),stock.primarySetup,stock.setup].filter(Boolean).map(value=>String(value)))
  const cards=[
    {id:'accumulation_base',title:'Accumulation',values:[['Score',stock.accumulationScore],['Base',stock.baseScore],['RVOL',stock.volumeRatio]]},
    {id:'crash_base_stage1',title:'Crash Base',values:[['Score',stock.crashBaseScore],['Stage',stock.stage],['200D distance',stock.distance200]]},
    {id:'rwb_squeeze_thrust',title:'RWB',values:[['Structure',stock.structureScore],['RS rating',stock.rsRank],['Entry risk %',stock.entryRiskPct]]},
    {id:'ema_stack_launch',title:'EMA Stack',values:[['Launch score',stock.emaStackLaunchScore],['20D change %',stock.change20d],['RVOL',stock.volumeRatio]]},
    {id:'ma_cluster_volume_breakout',title:'MA Cluster',values:[['Cluster score',stock.maClusterScore],['Compression %',stock.smaCompressionPct],['RVOL',stock.volumeRatio]]},
    {id:'long_base_launch',title:'Long Base',values:[['Long-base score',stock.longBaseScore],['Base weeks',stock.baseWeeks],['Base depth %',stock.baseDepthPct]]},
    {id:'weinstein',title:'Stage / Weinstein',values:[['Stage',stock.stage],['Substage',stock.stageName],['30W distance %',stock.distance30w]]},
  ].filter(card=>tags.has(card.id)||card.id==='weinstein')
  const plan=tradePlanOf(stock),triggerDistance=plan.trigger&&Number.isFinite(stock.price)?(stock.price-plan.trigger)/plan.trigger*100:null
  return <section className="bottom-setup-detail">
    <header><div><b>SETUP LENSES</b><small>Source detector evidence; no ranking changes.</small></div><span>{cards.length} matched</span></header>
    <div className="bottom-setup-grid">
      <article className="ready"><b>Trade readiness</b><dl><div><dt>Status</dt><dd>{plan.status.replaceAll('_',' ')}</dd></div><div><dt>Trigger state</dt><dd>{String(stock.tradePlan?.triggerState??stock.tradePlan?.trigger_state??'—').replaceAll('_',' ')}</dd></div><div><dt>To trigger</dt><dd>{triggerDistance==null?'—':signed(triggerDistance,1)}</dd></div></dl></article>
      {cards.map(card=><article key={card.id}><b>{card.title}</b><dl>{card.values.map(([label,value])=><div key={String(label)}><dt>{label}</dt><dd>{typeof value==='number'?fmt(value,1):String(value??'—')}</dd></div>)}</dl></article>)}
    </div>
  </section>
}

function HistoryView({ state }: { state: RowsLoadState<ScanHistoryItemV1> }) {
  if (state.status === "loading" || state.status === "idle")
    return <div className="dv-loading">Loading up to 252 market sessions…</div>;
  if (state.status === "error")
    return (
      <div className="dv-loading">
        History could not be loaded: {state.error}
      </div>
    );
  if (!state.rows.length)
    return (
      <div className="dv-loading">
        No compact scan history is available for this snapshot.
      </div>
    );
  const healthy = state.rows.filter(
    (row) => (row as any).status === "healthy",
  ).length;
  return (
    <main className="dv-market">
      <section>
        <h2>Scan history</h2>
        <div className="dv-marketgrid">
          <div>
            <b>{state.rows.length}</b>
            <span>Sessions retained</span>
          </div>
          <div>
            <b>{healthy}</b>
            <span>Healthy</span>
          </div>
          <div>
            <b>{state.rows.length - healthy}</b>
            <span>Degraded / failed</span>
          </div>
          <div>
            <b>252</b>
            <span>Maximum sessions</span>
          </div>
        </div>
        <p>Compact EOD history only. Prices are not live.</p>
      </section>
      <section>
        <h2>Recent sessions</h2>
        {state.rows.slice(0, 12).map((row: any) => (
          <div className="dv-leader" key={row.runId ?? row.run_id}>
            <b>{row.sessionDate ?? row.session_date}</b>
            <span>{row.status}</span>
            <em>
              {row.candidateCount ??
                row.candidate_count ??
                row.counts?.candidates ??
                0}{" "}
              candidates
            </em>
            <strong>{fmt(row.coveragePct ?? row.coverage_pct, 1)}%</strong>
          </div>
        ))}
      </section>
      <section>
        <h2>All retained sessions</h2>
        {state.rows.map((row: any) => (
          <div className="dv-leader" key={`all-${row.runId ?? row.run_id}`}>
            <b>{row.sessionDate ?? row.session_date}</b>
            <span>{row.status}</span>
            <em>
              {row.excludedCount ??
                row.excluded_count ??
                row.counts?.excluded ??
                0}{" "}
              excluded
            </em>
            <strong>{fmt(row.coveragePct ?? row.coverage_pct, 1)}%</strong>
          </div>
        ))}
      </section>
    </main>
  );
}

function Market({
  universe,
  market,
  mode,
}: {
  universe: Stock[];
  market: Record<string, any>;
  mode: ModeId;
}) {
  const daily = market.dailyChanges || {},
    stages = [1, 2, 3, 4].map(
      (s) => [s, universe.filter((x) => x.stage === s).length] as const,
    ),
    leaders = preserveScanOrder(universe).slice(0, 20),
    changes = [...universe]
      .filter((x) => x.changedToday)
      .sort((a, b) => num(b.changeImpact) - num(a.changeImpact))
      .slice(0, 20);
  return (
    <main className="dv-market">
      <section>
        <h2>Market structure</h2>
        <div className="dv-marketgrid">
          {stages.map(([s, n]) => (
            <div key={s}>
              <b>{n}</b>
              <span>Stage {s}</span>
            </div>
          ))}
        </div>
        <p>
          Regime <b>{marketRegimeLabel(market)}</b> · Stage 2 breadth{" "}
          <b>{market.stage2Pct ?? 0}%</b>
        </p>
      </section>
      <section>
        <h2>What changed since last scan</h2>
        <div className="dv-marketgrid">
          <div>
            <b>{daily.changed ?? 0}</b>
            <span>Changed</span>
          </div>
          <div>
            <b>{daily.newSetups ?? 0}</b>
            <span>New setups</span>
          </div>
          <div>
            <b>{daily.stageChanges ?? 0}</b>
            <span>Stage changes</span>
          </div>
          <div>
            <b>{daily.rsMovers ?? 0}</b>
            <span>RS movers</span>
          </div>
        </div>
        {changes.slice(0, 8).map((s) => (
          <div className="dv-leader" key={s.ticker}>
            <b>{s.ticker}</b>
            <span>{(s.changeLabels || [])[0] || "Changed"}</span>
            <strong className={num(s.changeImpact) > 0 ? "dv-good" : "dv-bad"}>
              {signed(s.changeImpact, 0)}
            </strong>
          </div>
        ))}
      </section>
      <section>
        <h2>{modeScoreLabel(mode)} scan order</h2>
        {leaders.slice(0, 12).map((s) => (
          <div className="dv-leader" key={s.ticker}>
            <b>{s.ticker}</b>
            <span>{setupOf(s)}</span>
            <em>Scan #{num(s.scanOrder) + 1}</em>
            <strong>{fmt(modeScore(s, mode), 1)}</strong>
          </div>
        ))}
      </section>
    </main>
  );
}

export default DeepVueTerminal;
