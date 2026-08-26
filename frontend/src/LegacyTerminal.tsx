import {useEffect,useMemo,useState} from 'react'
import {useStockScoutData,type LegacyIndex} from './data/StockScoutDataProvider'
import './legacy-terminal.css'

type Row={ticker:string;price?:number;stage?:number;stageName?:string;originalBuyScore?:number;originalBuy?:boolean;originalMarketQualifiedBuy?:boolean;originalRunBuySignal?:boolean;originalRR?:number;originalStopLoss?:number;originalRiskPct?:number;originalRewardTarget?:number;originalEntryQuality?:string;originalTTScore?:number;originalTTPasses?:number;originalVcpQuality?:number;originalAdVolumeRatio?:number;originalBreakoutType?:string|null;originalBreakoutLevel?:number|null;originalBreakoutVolumeConfirmed?:boolean;originalSellScore?:number;originalSell?:boolean;originalRunSellSignal?:boolean;originalMarketQualifiedSell?:boolean;originalSellSeverity?:string;phaseConfidence?:number;originalEngine?:any;richData?:any}
type Screen='ALL'|'BUY ≥60'|'BUY EMITTED'|'TT 7+'|'VCP'|'SELL RAW'|'SELL EMITTED'
type SortKey='originalBuyScore'|'originalRR'|'originalTTPasses'|'originalVcpQuality'|'originalAdVolumeRatio'|'originalSellScore'|'ticker'

const fmt=(v:any,d=1)=>typeof v==='number'&&Number.isFinite(v)?v.toFixed(d):'—'
const money=(v:any)=>typeof v==='number'&&Number.isFinite(v)?`$${v.toFixed(2)}`:'—'
const pct=(v:any)=>typeof v==='number'&&Number.isFinite(v)?`${v.toFixed(1)}%`:'—'
const screens:Screen[]=['ALL','BUY ≥60','BUY EMITTED','TT 7+','VCP','SELL RAW','SELL EMITTED']
const criteriaLabels:Record<string,string>={price_above_150_200:'Price > 150 & 200 DMA',sma_150_above_200:'150 DMA > 200 DMA',sma_200_rising:'200 DMA rising',sma_50_above_150:'50 DMA > 150 DMA',price_above_50:'Price > 50 DMA',price_30pct_above_52w_low:'≥30% above 52W low',price_near_52w_high:'Within 25% of 52W high',confirmed_stage_2:'Confirmed Stage 2'}
const entries=(v:any)=>v&&typeof v==='object'&&!Array.isArray(v)?Object.entries(v):[]
const val=(v:any)=>typeof v==='number'?fmt(v,3):typeof v==='boolean'?(v?'true':'false'):Array.isArray(v)?v.join(', '):v==null?'—':String(v)
const PAGE_SIZE=100

export default function LegacyTerminal(){
  const{selectedTicker,selectTicker,loadLegacyIndex,loadLegacyDetail,reload}=useStockScoutData()
  const[payload,setPayload]=useState<LegacyIndex|null>(null)
  const[error,setError]=useState('')
  const[indexAttempt,setIndexAttempt]=useState(0)
  const[detail,setDetail]=useState<Row|null>(null)
  const[detailError,setDetailError]=useState('')
  const[detailRetry,setDetailRetry]=useState<{ticker:string;nonce:number}|null>(null)
  const[query,setQuery]=useState('')
  const[screen,setScreen]=useState<Screen>('ALL')
  const[sort,setSort]=useState<SortKey>('originalBuyScore')
  const[desc,setDesc]=useState(true)
  const[page,setPage]=useState(0)

  useEffect(()=>{
    let live=true
    loadLegacyIndex().then(next=>{if(live){setPayload(next);setError('')}}).catch(next=>{if(live)setError(String(next))})
    return()=>{live=false}
  },[loadLegacyIndex,indexAttempt])

  const rows=(payload?.universe||[]) as Row[]
  const filtered=useMemo(()=>rows.filter(r=>{
    if(query.trim()&&!r.ticker.includes(query.trim().toUpperCase()))return false
    if(screen==='BUY ≥60'&&(r.originalBuyScore||0)<60)return false
    if(screen==='BUY EMITTED'&&!r.originalRunBuySignal&&!r.originalMarketQualifiedBuy)return false
    if(screen==='TT 7+'&&(r.originalTTPasses||0)<7)return false
    if(screen==='VCP'&&(r.originalVcpQuality||0)<=0)return false
    if(screen==='SELL RAW'&&!r.originalSell&&(r.originalSellScore||0)<=0)return false
    if(screen==='SELL EMITTED'&&!r.originalRunSellSignal&&!r.originalMarketQualifiedSell)return false
    return true
  }).sort((a,b)=>{
    const av=(a as any)[sort],bv=(b as any)[sort]
    if(sort==='ticker')return desc?String(bv||'').localeCompare(String(av||'')):String(av||'').localeCompare(String(bv||''))
    const an=typeof av==='number'?av:-Infinity,bn=typeof bv==='number'?bv:-Infinity
    return desc?bn-an:an-bn
  }),[rows,query,screen,sort,desc])
  const pageCount=Math.max(1,Math.ceil(filtered.length/PAGE_SIZE))
  const pagedRows=filtered.slice(page*PAGE_SIZE,(page+1)*PAGE_SIZE)
  const indexRow=filtered.find(r=>r.ticker===selectedTicker)||filtered[0]
  const detailMatches=Boolean(detail&&indexRow&&detail.ticker===indexRow.ticker)
  const row=detailMatches?detail:indexRow
  const e=detailMatches?detail?.originalEngine:null
  const components=e?.buy?.components||{}
  const criteria=e?.minervini?.criteria||{}
  const contractions=e?.vcp?.contractions||[]
  const gate=payload?.market?.originalSignalGate?.gate
  const baseline=payload?.layers?.legacy?.upstreamCommit||'2fce788b7c95e595bdbb012bd35d3a92fcc49e5a'
  const sortBy=(key:SortKey)=>{if(sort===key)setDesc(x=>!x);else{setSort(key);setDesc(key!=='ticker')}}
  const movePage=(next:number)=>{const bounded=Math.max(0,Math.min(pageCount-1,next));setPage(bounded);const first=filtered[bounded*PAGE_SIZE];if(first)selectTicker(first.ticker)}

  useEffect(()=>{setPage(0)},[query,screen,sort,desc])
  useEffect(()=>{if(page>=pageCount)setPage(pageCount-1)},[page,pageCount])
  useEffect(()=>{
    if(filtered.length&&!filtered.some(row=>row.ticker===selectedTicker))selectTicker(filtered[0].ticker)
  },[filtered,selectedTicker,selectTicker])
  useEffect(()=>{
    if(!indexRow)return
    let live=true
    const force=detailRetry?.ticker===indexRow.ticker
    setDetail(null);setDetailError('')
    loadLegacyDetail(indexRow.ticker,force)
      .then(next=>{if(live){setDetail(next as Row|null);if(force)setDetailRetry(null)}})
      .catch(next=>{if(live)setDetailError(String(next))})
    return()=>{live=false}
  },[indexRow?.ticker,detailRetry?.nonce,loadLegacyDetail])

  if(!payload)return <div className="lg-loading">{error?<span>LEGACY index failed: {error} <button onClick={()=>setIndexAttempt(value=>value+1)}>Retry</button></span>:'Loading LEGACY index…'}</div>
  return <div className="lg-app">
    <header className="lg-head"><div><small>FROZEN SOURCE METHODOLOGY</small><h1>LEGACY</h1><span>RyanJHamby/stock-screener @ {baseline.slice(0,8)}</span></div><div className="lg-market"><b className={gate?.should_generate_buys?'good':'warn'}>BUY GATE {gate?.should_generate_buys?'ON':'OFF'}</b><b className={gate?.should_generate_sells?'good':'warn'}>SELL GATE {gate?.should_generate_sells?'ON':'OFF'}</b><span>{rows.length.toLocaleString()} stocks</span><button onClick={reload}>↻</button></div></header>
    <section className="lg-toolbar"><div>{screens.map(s=><button key={s} className={screen===s?'active':''} onClick={()=>setScreen(s)}>{s}</button>)}</div><input value={query} onChange={x=>setQuery(x.target.value)} placeholder="Ticker…"/><span>{filtered.length.toLocaleString()} matches</span></section>
    <main className="lg-work">
      <section className="lg-tablebox"><div className="lg-note">Frozen upstream runtime. BUY/SELL EMITTED means the original full-market run would actually output the signal after its market gate; RAW is the underlying source scorer result.</div><div className="lg-tablewrap"><table><thead><tr>
        <H label="Ticker" k="ticker" sort={sort} desc={desc} click={sortBy}/><th>Phase</th><H label="Buy /125" k="originalBuyScore" sort={sort} desc={desc} click={sortBy}/><th>Run BUY</th><H label="TT" k="originalTTPasses" sort={sort} desc={desc} click={sortBy}/><H label="VCP" k="originalVcpQuality" sort={sort} desc={desc} click={sortBy}/><H label="R/R" k="originalRR" sort={sort} desc={desc} click={sortBy}/><th>Entry</th><H label="A/D vol" k="originalAdVolumeRatio" sort={sort} desc={desc} click={sortBy}/><th>Breakout</th><H label="Sell" k="originalSellScore" sort={sort} desc={desc} click={sortBy}/><th>Run SELL</th>
      </tr></thead><tbody>{pagedRows.map(r=><tr key={r.ticker} className={r.ticker===row?.ticker?'selected':''} onClick={()=>selectTicker(r.ticker)}><td><b>{r.ticker}</b><small>{money(r.price)}</small></td><td>{r.stage??'—'}</td><td><strong>{fmt(r.originalBuyScore,0)}</strong></td><td>{r.originalRunBuySignal||r.originalMarketQualifiedBuy?<span className="pill good">EMITTED</span>:r.originalBuy?<span className="pill">RAW BUY</span>:'—'}</td><td>{fmt(r.originalTTPasses,0)}/8</td><td>{fmt(r.originalVcpQuality,0)}</td><td>{fmt(r.originalRR,1)}:1</td><td>{r.originalEntryQuality||'—'}</td><td>{fmt(r.originalAdVolumeRatio,2)}x</td><td>{r.originalBreakoutType||'—'}</td><td className={r.originalSell?'bad':''}>{fmt(r.originalSellScore,0)}</td><td>{r.originalRunSellSignal||r.originalMarketQualifiedSell?<span className="pill good">EMITTED</span>:r.originalSell?<span className="pill">RAW SELL</span>:'—'}</td></tr>)}</tbody></table></div><footer className="lg-pagination"><span>{filtered.length?page*PAGE_SIZE+1:0}–{Math.min(filtered.length,(page+1)*PAGE_SIZE)} of {filtered.length.toLocaleString()}</span><div><button disabled={page===0} onClick={()=>movePage(page-1)}>←</button><b>{page+1}/{pageCount}</b><button disabled={page>=pageCount-1} onClick={()=>movePage(page+1)}>→</button></div></footer></section>
      <aside className="lg-detail">{detailError?<div className="lg-empty">Detail shard failed: {detailError} <button onClick={()=>indexRow&&setDetailRetry({ticker:indexRow.ticker,nonce:Date.now()})}>Retry</button></div>:!e?<div className="lg-empty">Loading LEGACY detail shard…</div>:<>
        <header><div><h2>{row?.ticker}</h2><span>Phase {e.phase} · confidence {pct(e.phaseConfidence)}</span></div><strong>{fmt(e.buy?.score,0)}<small>/125</small></strong></header>
        <div className="lg-kpis"><K l="Run BUY" v={e.buy?.emittedByOriginalRun?'EMITTED':e.buy?.isBuy?'RAW ONLY':'NO'} good={e.buy?.emittedByOriginalRun}/><K l="Run SELL" v={e.sell?.emittedByOriginalRun?'EMITTED':e.sell?.isSell?'RAW ONLY':'NO'} good={e.sell?.emittedByOriginalRun}/><K l="TT" v={`${fmt(e.minervini?.passed,0)}/8`} good={e.minervini?.passes}/><K l="VCP" v={fmt(e.vcp?.quality,0)}/><K l="R/R" v={`${fmt(e.buy?.riskReward,1)}:1`} good={(e.buy?.riskReward||0)>=2}/><K l="Stop" v={money(e.buy?.stopLoss)}/><K l="Risk" v={pct(e.buy?.riskPct)}/><K l="Target" v={money(e.buy?.rewardTarget)}/><K l="Entry" v={e.buy?.entryQuality||'—'}/></div>
        <section><h3>SOURCE SCORE ANATOMY</h3><div className="lg-components"><K l="Trend" v={`${fmt(components.trend,1)}/40`}/><K l="Fundamental" v={`${fmt(components.fundamental,1)}/40`}/><K l="R/R" v={`${fmt(components.riskReward,1)}/15`}/><K l="RS" v={`${fmt(components.relativeStrength,1)}/10`}/><K l="Volume" v={`${fmt(components.volume,1)}/10`}/><K l="Entry" v={`${fmt(components.entry,1)}/5`}/><K l="VCP bonus" v={`${fmt(components.vcpBonus,1)}/5`}/></div></section>
        <section><h3>MINERVINI 8-POINT TEMPLATE</h3><div className="lg-checks">{Object.entries(criteriaLabels).map(([k,label])=><span key={k} className={criteria[k]?'pass':'fail'}><i>{criteria[k]?'✓':'×'}</i>{label}</span>)}</div></section>
        <section><h3>BREAKOUT / VOLUME</h3><div className="lg-components"><K l="Type" v={e.breakout?.breakout_type||'—'}/><K l="Level" v={money(e.breakout?.breakout_level)}/><K l="Volume" v={e.breakout?.volume_confirmed?'CONFIRMED':'not confirmed'} good={e.breakout?.volume_confirmed}/><K l="A/D vol" v={`${fmt(e.buy?.adVolumeRatio,2)}x`}/></div></section>
        <section><h3>VCP ANATOMY</h3><p>{e.vcp?.pattern||'No source VCP pattern description.'}</p>{contractions.length>0&&<div className="lg-contract">{contractions.map((c:any,i:number)=><span key={i}><b>C{i+1}</b>{pct(c.drawdownPct)}<small>{c.durationDays||0}d · vol {fmt(c.volumeRatio,2)}x</small></span>)}</div>}</section>
        <section><h3>ORIGINAL BUY REASONS</h3>{e.buy?.sourceReason&&<p>{e.buy.sourceReason}</p>}<ul>{(e.buy?.reasons||[]).map((r:string,i:number)=><li key={i}>{r}</li>)}</ul></section>
        {(e.sell?.score||0)>0||e.sell?.sourceReason?<section className="lg-sell"><h3>SELL / RISK ENGINE</h3><b>{fmt(e.sell?.score,0)} · {String(e.sell?.severity||'none').toUpperCase()}</b>{e.sell?.sourceReason&&<p>{e.sell.sourceReason}</p>}<ul>{(e.sell?.reasons||[]).map((r:string,i:number)=><li key={i}>{r}</li>)}</ul></section>:null}
        {e.phaseInfo&&<section><h3>FULL PHASE INFO · SOURCE</h3><div className="lg-components">{entries(e.phaseInfo).filter(([k])=>!['reasons'].includes(k)).map(([k,v])=><K key={k} l={k} v={val(v)}/>)}</div></section>}
        {e.buy?.allDetails&&<section><h3>FULL BUY DETAILS · SOURCE</h3><div className="lg-components">{entries(e.buy.allDetails).filter(([,v])=>typeof v!=='object'||v==null).map(([k,v])=><K key={k} l={k} v={val(v)}/>)}</div></section>}
        {e.sell?.allDetails&&<section><h3>FULL SELL DETAILS · SOURCE</h3><div className="lg-components">{entries(e.sell.allDetails).filter(([,v])=>typeof v!=='object'||v==null).map(([k,v])=><K key={k} l={k} v={val(v)}/>)}</div></section>}
        <footer>LEGACY is immutable. Complete source outputs are preserved under originalEngine.sourceOutputs; shared richData never changes source rules or scores.</footer>
      </>}</aside>
    </main>
  </div>
}

function H({label,k,sort,desc,click}:{label:string;k:SortKey;sort:SortKey;desc:boolean;click:(k:SortKey)=>void}){return <th onClick={()=>click(k)} className={sort===k?'sorted':''}>{label}{sort===k?<b>{desc?'↓':'↑'}</b>:''}</th>}
function K({l,v,good=false}:{l:string;v:string;good?:boolean}){return <span className={good?'good':''}><small>{l}</small><b>{v}</b></span>}
