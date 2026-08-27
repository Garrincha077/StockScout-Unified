import {useEffect,useMemo,useState} from 'react'
import {useStockScoutData} from './data/StockScoutDataProvider'
import './context-pages.css'

type HistoryPoint={month?:string;observation_month?:string;score?:number;positive?:number}
type GmliPayload={
  schemaVersion:1;status:'OK';generatedAt:string|null;stockScoutImpact:string
  freshness?:{status:'fresh'|'stale';fallback:boolean;checkedAt:string;provenance:string}
  source:{repository:string;upstreamRefreshStatus:string}
  consumerContract:{mode:'READ_ONLY_SIDECAR';mutatesStockScoutScoring:false;lastGoodFallbackAllowed:boolean}
  dataHealth?:{status?:string}
  regime:{label:string|null;tilt:string|null;money:{usdScore:number|null;usdYoYPct:number|null;usdRegime:string|null;fxNeutralScore:number|null;fxNeutralYoYPct:number|null;fxNeutralRegime:string|null};funding:{score:number|null;regime:string|null;role:string};fiscal:{score:number|null;regime:string|null;role:string;automaticGlobalConvictionWeight?:number|null};market:{month:string;positive:number|null;total:number|null;assetsPositive?:Record<string,boolean>}}
  moneyExtremes:{latest:{usd_level:{value_pct:number;z:number;percentile:number};fx_neutral_level:{value_pct:number;z:number;percentile:number};usd_accel3:{value_pp:number;z:number;percentile:number};fx_neutral_accel3:{value_pp:number;z:number;percentile:number}}}
  history:{funding:HistoryPoint[];fiscal:HistoryPoint[];market:HistoryPoint[]}
}

const fmt=(value:number|null|undefined,digits=1)=>value==null||!Number.isFinite(value)?'—':value.toFixed(digits)
const signed=(value:number|null|undefined,digits=2)=>value==null||!Number.isFinite(value)?'—':`${value>=0?'+':''}${value.toFixed(digits)}`

function MiniTrend({rows,field,max=100}:{rows:HistoryPoint[];field:'score'|'positive';max?:number}){
  const points=rows.slice(-60).filter(row=>typeof row[field]==='number')
  if(points.length<2)return <div className="ctx-empty">History pending</div>
  const path=points.map((point,index)=>`${index?'L':'M'}${(index/(points.length-1)*300).toFixed(1)},${(70-Number(point[field])/max*64).toFixed(1)}`).join(' ')
  return <svg className="ctx-spark" viewBox="0 0 300 76" preserveAspectRatio="none" role="img" aria-label="Recent GMLI history"><line x1="0" x2="300" y1="38" y2="38"/><path d={path}/></svg>
}

function Extreme({label,value,z,percentile}:{label:string;value:string;z:number;percentile:number}){
  return <article className={`ctx-extreme ${Math.abs(z)>=2?'is-extreme':''}`}><small>{label}</small><strong>{value}</strong><span>Z {signed(z)} · {fmt(percentile,0)}th pctile</span></article>
}

export default function GmliContextPage(){
  const{loadContextAsset}=useStockScoutData()
  const[payload,setPayload]=useState<GmliPayload|null>(null),[error,setError]=useState(''),[loading,setLoading]=useState(true),[retry,setRetry]=useState(0)
  useEffect(()=>{let live=true;setLoading(true);setError('');loadContextAsset<GmliPayload>('gmliContext',retry>0).then(value=>{if(live)setPayload(value)}).catch(reason=>{if(live)setError(reason instanceof Error?reason.message:String(reason))}).finally(()=>{if(live)setLoading(false)});return()=>{live=false}},[loadContextAsset,retry])
  const assets=useMemo(()=>Object.entries(payload?.regime.market.assetsPositive??{}).map(([name,positive])=>`${name} ${positive?'✓':'×'}`).join(' · '),[payload])
  if(loading&&!payload)return <div className="ctx-state ctx-loading"><b>Loading GMLI context…</b><span>Validating its read-only consumer contract.</span></div>
  if(error&&!payload)return <div className="ctx-state ctx-error" role="alert"><b>GMLI unavailable</b><span>{error}</span><button onClick={()=>setRetry(value=>value+1)}>Retry</button></div>
  if(!payload)return null
  const r=payload.regime,x=payload.moneyExtremes.latest,fallback=payload.freshness?.status==='stale'||payload.source.upstreamRefreshStatus==='PASS_WITH_LAST_GOOD_FALLBACK'
  return <main className="ctx-page">
    <header className="ctx-hero"><div><small>GARRINCHA077/NUEVO · READ-ONLY MACRO CONTEXT</small><h1>GMLI Context</h1><p>Liquidity, funding, fiscal and market-confirmation context. Stock selection remains entirely inside StockScout.</p></div><div className="ctx-meta"><span><small>Regime</small><b>{r.label??'—'}</b></span><span><small>Health</small><b>{payload.dataHealth?.status??'—'}</b></span><button onClick={()=>setRetry(value=>value+1)} disabled={loading}>{loading?'Checking…':'Refresh'}</button></div></header>
    {fallback?<div className="ctx-warning">Showing the last verified GMLI snapshot; StockScout scoring is unaffected.</div>:null}{error?<div className="ctx-warning">Refresh failed; preserving the verified asset. {error}</div>:null}
    <section className="ctx-grid ctx-summary-grid">
      <article className="ctx-card ctx-wide"><small>GMLI REGIME</small><div className="ctx-primary"><strong>{r.label??'—'}</strong><span>{r.tilt??'—'}</span></div></article>
      <article className="ctx-card"><small>MONEY · USD</small><div className="ctx-primary"><strong>{fmt(r.money.usdScore)}</strong><span>{fmt(r.money.usdYoYPct,2)}% YoY · {r.money.usdRegime}</span></div></article>
      <article className="ctx-card"><small>MONEY · FX-NEUTRAL</small><div className="ctx-primary"><strong>{fmt(r.money.fxNeutralScore)}</strong><span>{fmt(r.money.fxNeutralYoYPct,2)}% YoY · {r.money.fxNeutralRegime}</span></div></article>
      <article className="ctx-card"><small>FUNDING</small><div className="ctx-primary"><strong>{fmt(r.funding.score)}</strong><span>{r.funding.regime} · {r.funding.role}</span></div><MiniTrend rows={payload.history.funding} field="score"/></article>
      <article className="ctx-card"><small>FISCAL</small><div className="ctx-primary"><strong>{fmt(r.fiscal.score)}</strong><span>{r.fiscal.regime} · weight {fmt(r.fiscal.automaticGlobalConvictionWeight,0)}</span></div><MiniTrend rows={payload.history.fiscal} field="score"/></article>
      <article className="ctx-card"><small>MARKET CONFIRMATION</small><div className="ctx-primary"><strong>{r.market.positive??'—'}/{r.market.total??'—'}</strong><span>{r.market.month} · {assets||'—'}</span></div><MiniTrend rows={payload.history.market} field="positive" max={4}/></article>
    </section>
    <section className="ctx-extremes"><Extreme label="USD LEVEL" value={`${fmt(x.usd_level.value_pct,2)}% YoY`} z={x.usd_level.z} percentile={x.usd_level.percentile}/><Extreme label="FX-NEUTRAL LEVEL" value={`${fmt(x.fx_neutral_level.value_pct,2)}% YoY`} z={x.fx_neutral_level.z} percentile={x.fx_neutral_level.percentile}/><Extreme label="USD ACCEL3" value={`${signed(x.usd_accel3.value_pp)} pp`} z={x.usd_accel3.z} percentile={x.usd_accel3.percentile}/><Extreme label="FX-NEUTRAL ACCEL3" value={`${signed(x.fx_neutral_accel3.value_pp)} pp`} z={x.fx_neutral_accel3.z} percentile={x.fx_neutral_accel3.percentile}/></section>
    <section className="ctx-contract"><div><small>Source</small><b>{payload.source.repository}</b></div><div><small>Consumer contract</small><b>Read-only sidecar</b></div><div><small>StockScout impact</small><b>None · context only</b></div></section>
  </main>
}
