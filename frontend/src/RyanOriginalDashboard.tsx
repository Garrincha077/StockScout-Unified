import { useCallback, useEffect, useMemo, useState } from "react";
import {
  detailFromIndex,
  flattenSourceValues,
  isEmitted,
  originalSummary,
  ORIGINAL_PAGE_SIZE,
  selectOriginalRows,
  signalScore,
  type OriginalCandidate,
  type OriginalEngineDetail,
  type OriginalScope,
  type OriginalSort,
  type OriginalSortKey,
  type OriginalTab,
  type OriginalTabState,
} from "./original/viewModel";
import { useStockScoutData, type LegacyIndex } from "./data/StockScoutDataProvider";
import StockChart,{normalizeChartRows,type ChartBar,type ChartInterval,type ChartPriceLine,type ChartRange}from'./StockChart'
import{ResizableHeight,ResizableWorkspace}from'./ResizablePanels'
import "./original-dashboard.css";

const tabs: readonly OriginalTab[] = ["buy", "sell"];
const scopes: readonly OriginalScope[] = ["emitted", "raw", "all"];
const defaultState: Record<OriginalTab, OriginalTabState> = {
  buy: {
    scope: "emitted",
    query: "",
    sort: { key: "score", direction: "desc" },
    page: 0,
  },
  sell: {
    scope: "emitted",
    query: "",
    sort: { key: "score", direction: "desc" },
    page: 0,
  },
};

const fmt = (value: unknown, decimals = 1): string =>
  typeof value === "number" && Number.isFinite(value)
    ? value.toFixed(decimals)
    : "—";
const money = (value: unknown): string =>
  typeof value === "number" && Number.isFinite(value)
    ? `$${value.toFixed(2)}`
    : "—";
const pct = (value: unknown): string =>
  typeof value === "number" && Number.isFinite(value)
    ? `${value.toFixed(1)}%`
    : "—";
const titleCase = (value: string): string =>
  value
    .replaceAll("_", " ")
    .replace(/\b\w/g, (letter) => letter.toUpperCase());

function originalEngine(row: OriginalCandidate | null): OriginalEngineDetail | null {
  const value = row?.originalEngine;
  return value && typeof value === "object" ? value : null;
}

function branch(engine: OriginalEngineDetail | null, tab: OriginalTab): Record<string, any> {
  const value = engine?.[tab];
  return value && typeof value === "object" ? (value as Record<string, any>) : {};
}

function reasonsFor(row: OriginalCandidate, tab: OriginalTab): string[] {
  const current = branch(originalEngine(row), tab);
  const reasons = current.reasons;
  if (Array.isArray(reasons)) return reasons.map(String);
  return current.sourceReason ? [String(current.sourceReason)] : [];
}

function scopeLabel(scope: OriginalScope): string {
  return scope === "emitted"
    ? "Emitted"
    : scope === "raw"
      ? "Raw"
      : "All scored";
}

function formatValue(value: unknown): string {
  if (value === null || value === undefined || value === "") return "—";
  if (typeof value === "number") return Number.isFinite(value) ? String(value) : "—";
  if (typeof value === "boolean") return value ? "true" : "false";
  return String(value);
}

function SummaryCard({
  label,
  value,
  tone = "blue",
  detail,
}: {
  label: string;
  value: string;
  tone?: "green" | "red" | "blue" | "yellow";
  detail?: string;
}) {
  return (
    <article className={`ryan-summary-card ${tone}`}>
      <span>{label}</span>
      <strong>{value}</strong>
      {detail ? <small>{detail}</small> : null}
    </article>
  );
}

function SignalBadge({ row, tab }: { row: OriginalCandidate; tab: OriginalTab }) {
  const emitted = isEmitted(row, tab);
  const raw = branch(originalEngine(row), tab).isBuy === true || branch(originalEngine(row), tab).isSell === true;
  return (
    <span className={`ryan-signal-badge ${emitted ? "emitted" : raw ? "raw" : "none"}`}>
      {emitted ? "EMITTED" : raw ? "RAW" : "—"}
    </span>
  );
}

function SortHeader({
  label,
  sortKey,
  sort,
  onSort,
}: {
  label: string;
  sortKey: OriginalSortKey;
  sort: OriginalSort;
  onSort: (key: OriginalSortKey) => void;
}) {
  const active = sort.key === sortKey;
  return (
    <th>
      <button className={active ? "active" : ""} onClick={() => onSort(sortKey)}>
        {label}
        {active ? <b>{sort.direction === "desc" ? "↓" : "↑"}</b> : null}
      </button>
    </th>
  );
}

function SourceValues({ value }: { value: unknown }) {
  const values = flattenSourceValues(value);
  if (!values.length) return <p className="ryan-empty-inline">No source values.</p>;
  return (
    <div className="ryan-source-values">
      {values.map((entry, index) => (
        <div key={`${entry.path}-${index}`}>
          <span>{entry.path || "value"}</span>
          <b>{formatValue(entry.value)}</b>
        </div>
      ))}
    </div>
  );
}

function SourceSection({
  title,
  value,
  open = false,
}: {
  title: string;
  value: unknown;
  open?: boolean;
}) {
  return (
    <details className="ryan-source-section" open={open}>
      <summary>
        <span>{title}</span>
        <small>{value && typeof value === "object" ? "nested source data" : "value"}</small>
      </summary>
      <SourceValues value={value} />
    </details>
  );
}

function SignalReasons({ row, tab }: { row: OriginalCandidate; tab: OriginalTab }) {
  const reasons = reasonsFor(row, tab);
  if (!reasons.length) return <span className="ryan-muted">No source reason</span>;
  return (
    <ul className="ryan-reasons">
      {reasons.slice(0, 3).map((reason, index) => (
        <li key={`${reason}-${index}`}>{reason}</li>
      ))}
      {reasons.length > 3 ? <li className="ryan-more">+{reasons.length - 3} more</li> : null}
    </ul>
  );
}

function numberOrNull(value:unknown){return value!==null&&value!==undefined&&Number.isFinite(Number(value))?Number(value):null}

function RyanChartPanel({row}:{row:OriginalCandidate}){
  const{loadChart}=useStockScoutData()
  const[interval,setInterval]=useState<ChartInterval>('D'),[range,setRange]=useState<ChartRange>('2Y')
  const[state,setState]=useState<{status:'loading'|'ready'|'unavailable'|'error';bars:ChartBar[];error?:string}>({status:'loading',bars:[]}),[retry,setRetry]=useState(0)
  useEffect(()=>{let live=true;setState({status:'loading',bars:[]});loadChart(row.ticker,retry>0).then(result=>{if(!live)return;if(result.status==='ready'){const bars=normalizeChartRows(result.rows);setState(bars.length?{status:'ready',bars}:{status:'error',bars:[],error:'Chart payload has no valid EOD bars'})}else setState({...result,bars:[]})}).catch(reason=>{if(live)setState({status:'error',bars:[],error:reason instanceof Error?reason.message:String(reason)})});return()=>{live=false}},[loadChart,row.ticker,retry])
  const lines=useMemo(()=>{
    const buy=branch(originalEngine(row),'buy'),sell=branch(originalEngine(row),'sell'),items:[number|null,string,string,ChartPriceLine['style']][]=[
      [numberOrNull(buy.entryPrice??buy.entryLevel??row.originalEntryPrice),'Original entry','#62a8ff','dashed'],
      [numberOrNull(buy.stopLoss??row.originalStopLoss),'Original stop','#ff6f7d','solid'],
      [numberOrNull(buy.rewardTarget??row.originalRewardTarget),'Original target','#46e394','dashed'],
      [numberOrNull(sell.breakdownLevel??row.originalBreakdownLevel),'Sell breakdown','#f5a15d','dotted'],
    ]
    return items.flatMap(([price,title,color,style])=>price==null?[]:[{price,title,color,style}])
  },[row])
  return <section className="ryan-chart-panel" aria-label={`${row.ticker} price chart`}>
    <header><div><span className="ryan-eyebrow">IMMUTABLE PUBLIC CHART</span><b>{row.ticker} · {interval==='D'?'Daily':'Weekly'}</b></div><div className="ryan-chart-controls"><a className="ryan-open-next" href={`?mode=next&view=screener&ticker=${encodeURIComponent(row.ticker)}`}>Open in Next</a><button className={interval==='D'?'active':''} onClick={()=>setInterval('D')}>D</button><button className={interval==='W'?'active':''} onClick={()=>setInterval('W')}>W</button><select aria-label="Chart range" value={range} onChange={event=>setRange(event.target.value as ChartRange)}><option>6M</option><option>1Y</option><option>2Y</option><option>5Y</option></select></div></header>
    <div className="ryan-chart-stage">{state.status==='ready'?<StockChart bars={state.bars} interval={interval} range={range} ticker={row.ticker} priceLines={lines} ownerTools={false}/>:state.status==='loading'?<div className="ryan-chart-state">Loading chart…</div>:<div className="ryan-chart-state" role="alert"><span>{state.status==='unavailable'?'Chart is not published for this ticker.':state.error||'Chart could not be loaded.'}</span><button onClick={()=>setRetry(value=>value+1)}>Retry</button></div>}</div>
    <footer><span className="entry">Entry</span><span className="stop">Stop</span><span className="target">Target</span><span>Drag the lower edge to resize</span></footer>
  </section>
}

function BuyTable({
  rows,
  selectedTicker,
  onSelect,
  sort,
  onSort,
}: {
  rows: readonly OriginalCandidate[];
  selectedTicker: string;
  onSelect: (ticker: string) => void;
  sort: OriginalSort;
  onSort: (key: OriginalSortKey) => void;
}) {
  return (
    <div className="ryan-table-wrap">
      <table className="ryan-table">
        <thead>
          <tr>
            <SortHeader label="Ticker" sortKey="ticker" sort={sort} onSort={onSort} />
            <SortHeader label="Score" sortKey="score" sort={sort} onSort={onSort} />
            <SortHeader label="Entry" sortKey="entry" sort={sort} onSort={onSort} />
            <th>Stop</th>
            <th>Risk</th>
            <SortHeader label="R/R" sortKey="riskReward" sort={sort} onSort={onSort} />
            <SortHeader label="TT" sortKey="tt" sort={sort} onSort={onSort} />
            <SortHeader label="VCP" sortKey="vcp" sort={sort} onSort={onSort} />
            <th>RS</th>
            <th>Signal</th>
            <th>Key reasons</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr
              key={row.ticker}
              className={row.ticker === selectedTicker ? "selected" : ""}
              onClick={() => onSelect(row.ticker)}
            >
              <td className="ryan-ticker">
                <b>{row.ticker}</b>
                <small>{money(row.price)}</small>
              </td>
              <td className="ryan-score">{fmt(row.originalBuyScore, 0)}</td>
              <td>{row.originalEntryQuality || "—"}</td>
              <td>{money(row.originalStopLoss)}</td>
              <td>{pct(row.originalRiskPct)}</td>
              <td>{fmt(row.originalRR, 1)}:1</td>
              <td>{fmt(row.originalTTPasses, 0)}/8</td>
              <td>{fmt(row.originalVcpQuality, 0)}</td>
              <td>{fmt(row.rsRank ?? row.rsScore, 0)}</td>
              <td><SignalBadge row={row} tab="buy" /></td>
              <td><SignalReasons row={row} tab="buy" /></td>
            </tr>
          ))}
        </tbody>
      </table>
      {!rows.length ? <div className="ryan-empty">No BUY candidates match this scope.</div> : null}
    </div>
  );
}

function SellTable({
  rows,
  selectedTicker,
  onSelect,
  sort,
  onSort,
}: {
  rows: readonly OriginalCandidate[];
  selectedTicker: string;
  onSelect: (ticker: string) => void;
  sort: OriginalSort;
  onSort: (key: OriginalSortKey) => void;
}) {
  return (
    <div className="ryan-table-wrap">
      <table className="ryan-table">
        <thead>
          <tr>
            <SortHeader label="Ticker" sortKey="ticker" sort={sort} onSort={onSort} />
            <SortHeader label="Score" sortKey="score" sort={sort} onSort={onSort} />
            <SortHeader label="Severity" sortKey="severity" sort={sort} onSort={onSort} />
            <SortHeader label="Breakdown" sortKey="breakdown" sort={sort} onSort={onSort} />
            <SortHeader label="Phase" sortKey="phase" sort={sort} onSort={onSort} />
            <th>RS</th>
            <th>Signal</th>
            <th>Key reasons</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => {
            const sell = branch(originalEngine(row), "sell");
            return (
              <tr
                key={row.ticker}
                className={row.ticker === selectedTicker ? "selected" : ""}
                onClick={() => onSelect(row.ticker)}
              >
                <td className="ryan-ticker">
                  <b>{row.ticker}</b>
                  <small>{money(row.price)}</small>
                </td>
                <td className="ryan-score sell">{fmt(row.originalSellScore, 0)}</td>
                <td><span className={`ryan-severity ${String(row.originalSellSeverity || "none")}`}>{String(row.originalSellSeverity || "none")}</span></td>
                <td>{money(sell.breakdownLevel ?? row.originalBreakdownLevel)}</td>
                <td>Phase {row.stage ?? originalEngine(row)?.phase ?? "—"}</td>
                <td>{fmt(row.rsRank ?? row.rsScore, 0)}</td>
                <td><SignalBadge row={row} tab="sell" /></td>
                <td><SignalReasons row={row} tab="sell" /></td>
              </tr>
            );
          })}
        </tbody>
      </table>
      {!rows.length ? <div className="ryan-empty">No SELL candidates match this scope.</div> : null}
    </div>
  );
}

function defaultRyanEvidenceOpen() {
  return typeof window === "undefined" || !window.matchMedia("(max-width: 720px)").matches;
}

function DetailPanel({
  row,
  loading,
  error,
  onRetry,
}: {
  row: OriginalCandidate | null;
  loading: boolean;
  error: string;
  onRetry: () => void;
}) {
  const [evidenceOpen, setEvidenceOpen] = useState(defaultRyanEvidenceOpen);
  const engine = originalEngine(row);
  const buy = branch(engine, "buy");
  const sell = branch(engine, "sell");
  const phase = engine?.phaseInfo ?? {
    phase: engine?.phase,
    confidence: engine?.phaseConfidence,
    reasons: engine?.phaseReasons,
  };
  return (
    <aside className="ryan-detail">
      {!row ? (
        <div className="ryan-empty">Select a candidate to inspect source evidence.</div>
      ) : (
        <>
          <header className="ryan-detail-head">
            <div>
              <span className="ryan-eyebrow">SOURCE METHODOLOGY</span>
              <h2>{row.ticker}</h2>
              <p>Phase {row.stage ?? engine?.phase ?? "—"} · confidence {pct(engine?.phaseConfidence ?? row.phaseConfidence)}</p>
            </div>
            <div className="ryan-detail-score">
              <small>BUY</small>
              <strong>{fmt(row.originalBuyScore, 0)}</strong>
              <span>SELL {fmt(row.originalSellScore, 0)}</span>
            </div>
          </header>
          <ResizableHeight id="ryan-chart" defaultHeight={460}><RyanChartPanel row={row}/></ResizableHeight>
          {loading ? <div className="ryan-detail-loading">Loading source detail shard…</div> : null}
          {error ? <div className="ryan-detail-error">{error} <button onClick={onRetry}>Retry</button></div> : null}
          {!loading && !error && !engine ? <div className="ryan-detail-loading">Source detail is unavailable for this ticker.</div> : null}
          {engine ? (
            <div className="ryan-detail-body">
              <section className="ryan-detail-kpis">
                <div><span>Entry</span><b>{buy.entryQuality || "—"}</b></div>
                <div><span>Stop</span><b>{money(buy.stopLoss ?? row.originalStopLoss)}</b></div>
                <div><span>Risk</span><b>{pct(buy.riskPct ?? row.originalRiskPct)}</b></div>
                <div><span>Target</span><b>{money(buy.rewardTarget ?? row.originalRewardTarget)}</b></div>
                <div><span>R/R</span><b>{fmt(buy.riskReward ?? row.originalRR, 1)}:1</b></div>
                <div><span>Sell severity</span><b>{String(sell.severity ?? row.originalSellSeverity ?? "none")}</b></div>
              </section>
              <details
                className="ryan-evidence-drawer"
                open={evidenceOpen}
                onToggle={(event) => setEvidenceOpen(event.currentTarget.open)}
              >
                <summary>
                  <span>Source evidence</span>
                  <small>Original score anatomy, engine inputs and reasons</small>
                </summary>
                <div className="ryan-evidence-drawer-body">
                  <section className="ryan-detail-section">
                    <h3>BUY SCORE ANATOMY</h3>
                    <SourceValues value={buy.components} />
                  </section>
                  <section className="ryan-detail-section">
                    <h3>SELL / RISK ENGINE</h3>
                    <SourceValues value={sell} />
                  </section>
                  <SourceSection title="Source inputs" value={engine.sourceInputs} />
                  <SourceSection title="Source outputs" value={engine.sourceOutputs} />
                  <SourceSection title="Phase information" value={phase} />
                  <SourceSection title="Minervini trend template" value={engine.minervini} />
                  <SourceSection title="VCP anatomy" value={engine.vcp} />
                  <SourceSection title="Breakout / volume" value={engine.breakout} />
                  <SourceSection title="Complete BUY details" value={buy.allDetails} />
                  <SourceSection title="Complete SELL details" value={sell.allDetails} />
                  <section className="ryan-detail-section">
                    <h3>ALL SOURCE REASONS</h3>
                    <div className="ryan-reason-columns">
                      <div><strong>BUY</strong><SignalReasons row={row} tab="buy" /></div>
                      <div><strong>SELL</strong><SignalReasons row={row} tab="sell" /></div>
                    </div>
                  </section>
                  <footer className="ryan-detail-footer">Model: {String(engine.model || "unknown")} · source outputs are immutable and read-only.</footer>
                </div>
              </details>
            </div>
          ) : null}
        </>
      )}
    </aside>
  );
}

export default function RyanOriginalDashboard() {
  const {
    selectedTicker,
    selectTicker,
    loadLegacyIndex,
    loadLegacyDetail,
    reload,
  } = useStockScoutData();
  const [payload, setPayload] = useState<LegacyIndex | null>(null);
  const [indexError, setIndexError] = useState("");
  const [indexAttempt, setIndexAttempt] = useState(0);
  const [tab, setTab] = useState<OriginalTab>("buy");
  const [states, setStates] = useState<Record<OriginalTab, OriginalTabState>>(defaultState);
  const [detail, setDetail] = useState<OriginalCandidate | null>(null);
  const [detailError, setDetailError] = useState("");
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailAttempt, setDetailAttempt] = useState(0);

  useEffect(() => {
    let live = true;
    setIndexError("");
    loadLegacyIndex()
      .then((next) => {
        if (live) setPayload(next);
      })
      .catch((error) => {
        if (live) setIndexError(error instanceof Error ? error.message : String(error));
      });
    return () => {
      live = false;
    };
  }, [loadLegacyIndex, indexAttempt]);

  const rows = useMemo(
    () => (payload?.universe ?? []) as OriginalCandidate[],
    [payload],
  );
  const selectedState = states[tab];
  const selection = useMemo(
    () => selectOriginalRows(rows, tab, selectedState),
    [rows, tab, selectedState],
  );
  const selectedRow = useMemo(
    () =>
      selection.filtered.find((row) => row.ticker === selectedTicker) ??
      selection.rows[0] ??
      selection.filtered[0] ??
      null,
    [selection.filtered, selection.rows, selectedTicker],
  );
  const selectedForDetail = useMemo(
    () => detailFromIndex(payload, selectedRow?.ticker ?? "") ?? selectedRow,
    [payload, selectedRow],
  );
  const summary = useMemo(
    () => originalSummary(rows, payload?.market ?? {}),
    [rows, payload?.market],
  );

  useEffect(() => {
    if (selectedRow && selectedTicker !== selectedRow.ticker) selectTicker(selectedRow.ticker);
  }, [selectedRow, selectedTicker, selectTicker]);

  useEffect(() => {
    if (!selectedRow) {
      setDetail(null);
      setDetailError("");
      return;
    }
    let live = true;
    setDetail(null);
    setDetailError("");
    setDetailLoading(true);
    loadLegacyDetail(selectedRow.ticker, detailAttempt > 0)
      .then((next) => {
        if (live) {
          setDetail(next as OriginalCandidate | null);
        }
      })
      .catch((error) => {
        if (live) setDetailError(error instanceof Error ? error.message : String(error));
      })
      .finally(() => {
        if (live) setDetailLoading(false);
      });
    return () => {
      live = false;
    };
  }, [selectedRow?.ticker, loadLegacyDetail, detailAttempt]);

  const updateState = useCallback(
    (next: Partial<OriginalTabState>) => {
      setStates((current) => ({
        ...current,
        [tab]: { ...current[tab], ...next },
      }));
    },
    [tab],
  );
  const setSort = useCallback(
    (key: OriginalSortKey) => {
      const current = states[tab].sort;
      const direction = current.key === key
        ? current.direction === "desc" ? "asc" : "desc"
        : key === "ticker" || key === "entry" || key === "severity" ? "asc" : "desc";
      updateState({ sort: { key, direction }, page: 0 });
    },
    [states, tab, updateState],
  );
  const chooseTab = (next: OriginalTab) => {
    setTab(next);
    const nextRows = selectOriginalRows(rows, next, states[next]).rows;
    if (nextRows[0] && nextRows[0].ticker !== selectedTicker) selectTicker(nextRows[0].ticker);
  };
  useEffect(()=>{
    const onKeyDown=(event:KeyboardEvent)=>{
      if(event.code!=='Space'||event.defaultPrevented||event.altKey||event.ctrlKey||event.metaKey||event.shiftKey||!selection.filtered.length)return
      const target=event.target instanceof Element?event.target:null
      if(target?.closest('input,textarea,select,button,a,[contenteditable="true"],[role="dialog"]'))return
      event.preventDefault()
      const current=Math.max(0,selection.filtered.findIndex(row=>row.ticker===selectedRow?.ticker)),next=(current+1)%selection.filtered.length,row=selection.filtered[next]
      updateState({page:Math.floor(next/ORIGINAL_PAGE_SIZE)})
      selectTicker(row.ticker)
    }
    window.addEventListener('keydown',onKeyDown)
    return()=>window.removeEventListener('keydown',onKeyDown)
  },[selection.filtered,selectedRow?.ticker,selectTicker,updateState])
  const pageStart = selection.filtered.length ? selection.page * ORIGINAL_PAGE_SIZE + 1 : 0;
  const pageEnd = Math.min((selection.page + 1) * ORIGINAL_PAGE_SIZE, selection.filtered.length);
  const detailRow = detail?.ticker === selectedRow?.ticker ? detail : selectedForDetail;

  if (!payload) {
    return (
      <main className="ryan-dashboard ryan-loading">
        {indexError ? (
          <div className="ryan-error">Ryan Original index failed: {indexError} <button onClick={() => setIndexAttempt((value) => value + 1)}>Retry</button></div>
        ) : <div>Loading Ryan Original source index…</div>}
      </main>
    );
  }

  const table = tab === "buy" ? (
    <BuyTable rows={selection.rows} selectedTicker={selectedRow?.ticker ?? ""} onSelect={selectTicker} sort={selectedState.sort} onSort={setSort} />
  ) : (
    <SellTable rows={selection.rows} selectedTicker={selectedRow?.ticker ?? ""} onSelect={selectTicker} sort={selectedState.sort} onSort={setSort} />
  );

  return (
    <main className="ryan-dashboard">
      <header className="ryan-hero">
        <div>
          <span className="ryan-eyebrow">FROZEN SOURCE METHODOLOGY</span>
          <h1>Ryan Original</h1>
          <p>Read-only BUY/SELL signals from the original Stage and Minervini workflow.</p>
        </div>
        <div className="ryan-hero-actions">
          <span className="ryan-source-pill">{String(payload.layers?.legacy?.upstreamRepository || "RyanJHamby/stock-screener")}</span>
          <button className="ryan-refresh" onClick={() => { reload(); setPayload(null); }}>↻ Reload</button>
        </div>
      </header>

      <section className="ryan-summary" aria-label="Ryan Original summary">
        <SummaryCard label="Buy Signals" value={String(summary.buySignals)} tone="green" detail="original-run emitted" />
        <SummaryCard label="Sell Signals" value={String(summary.sellSignals)} tone="red" detail="original-run emitted" />
        <SummaryCard label="Top Score" value={fmt(summary.topScore, 0)} tone="yellow" detail="best emitted BUY" />
        <SummaryCard label="Universe" value={summary.universe.toLocaleString()} tone="blue" detail="source rows" />
        <SummaryCard label="SPY Phase / Regime" value={summary.spyPhase === null ? "—" : `Phase ${fmt(summary.spyPhase, 0)}`} tone="blue" detail={`${summary.spyPhaseName} · ${summary.spyTrend}`} />
      </section>

      <ResizableWorkspace id="ryan-workspace" className="ryan-workspace" defaultSecondary={540}>
        <div className="ryan-list-panel">
          <nav className="ryan-tabs" aria-label="Original signal type">
            {tabs.map((item) => (
              <button key={item} className={tab === item ? "active" : ""} onClick={() => chooseTab(item)}>
                {item.toUpperCase()}
                <b>{item === "buy" ? summary.buySignals : summary.sellSignals}</b>
              </button>
            ))}
          </nav>
          <div className="ryan-toolbar">
            <div className="ryan-scope-toggle" aria-label={`${tab} candidate scope`}>
              {scopes.map((scope) => (
                <button key={scope} className={selectedState.scope === scope ? "active" : ""} onClick={() => updateState({ scope, page: 0 })}>
                  {scopeLabel(scope)}
                </button>
              ))}
            </div>
            <input
              value={selectedState.query}
              onChange={(event) => updateState({ query: event.target.value, page: 0 })}
              placeholder={`Search ${tab} ticker or reason…`}
              aria-label={`Search ${tab} candidates`}
            />
            <span className="ryan-match-count">{selection.filtered.length.toLocaleString()} matches</span>
          </div>
          <div className="ryan-table-note">{scopeLabel(selectedState.scope)} {tab.toUpperCase()} candidates · source scores are displayed without recalculation. Press <kbd>Space</kbd> for the next match.</div>
          {table}
          <footer className="ryan-pagination">
            <span>{pageStart}–{pageEnd} of {selection.filtered.length.toLocaleString()}</span>
            <div>
              <button disabled={selection.page === 0} onClick={() => updateState({ page: selection.page - 1 })}>←</button>
              <b>{selection.page + 1}/{selection.pageCount}</b>
              <button disabled={selection.page >= selection.pageCount - 1} onClick={() => updateState({ page: selection.page + 1 })}>→</button>
            </div>
          </footer>
        </div>
        <DetailPanel key={detailRow?.ticker ?? "empty"} row={detailRow} loading={detailLoading} error={detailError} onRetry={() => setDetailAttempt((value) => value + 1)} />
      </ResizableWorkspace>
    </main>
  );
}
