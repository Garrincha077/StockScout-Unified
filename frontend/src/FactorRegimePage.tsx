import {useEffect,useMemo,useState} from 'react'
import {useStockScoutData} from './data/StockScoutDataProvider'
import './context-pages.css'

type Regime='STRONG'|'DETERIORATING'|'RECOVERY'|'DEEPENING_DROUGHT'
type FactorPoint={month:string;premiumPct:number}
type Drought={active:boolean;startMonth:string|null;endMonth:string|null;duration:string;ongoing:boolean}
type Factor={
  id:string;sourceCode:string;label:string
  latest:{month:string;premiumPct:number;delta1mPp:number|null;delta6mPp:number|null;delta12mPp:number|null;recent12mPremiumPct:number|null;historicalPercentile:number|null;regime:Regime}
  currentDrought:Drought;longestDrought:Drought;series:FactorPoint[]
}
type FactorPayload={
  schemaVersion:1;generatedAt:string|null;status?:string
  freshness?:{status:'fresh'|'stale';fallback:boolean;checkedAt:string;provenance:string}
  source:{provider:string};method:{windowMonths:number;annualization:string;droughtDefinition:string;deltaDefinition:string;stockScoutImpact:string}
  range:{firstMonth:string;lastMonth:string;rollingFirstMonth:string;alignedMonths:number}|null
  summary:{mostImproving12m:string[];activeDroughts:number}|null;factors:Factor[]
}

const signed=(value:number|null|undefined,digits=2)=>value==null||!Number.isFinite(value)?'—':`${value>=0?'+':''}${value.toFixed(digits)}`
const fmt=(value:number|null|undefined,digits=0)=>value==null||!Number.isFinite(value)?'—':value.toFixed(digits)
const labels:Record<Regime,string>={STRONG:'Strong',DETERIORATING:'Deteriorating',RECOVERY:'Recovery',DEEPENING_DROUGHT:'Deepening drought'}

function Sparkline({points,domain}:{points:FactorPoint[];domain:{min:number;max:number}}){
  const rows=points.slice(-120)
  if(rows.length<2)return <div className="ctx-empty">No history</div>
  const min=domain.min,max=domain.max,range=Math.max(.01,max-min)
  const path=rows.map((point,index)=>`${index?'L':'M'}${(index/(rows.length-1)*300).toFixed(1)},${(70-(point.premiumPct-min)/range*64).toFixed(1)}`).join(' ')
  const zero=70-(0-min)/range*64
  return <svg className="ctx-spark" viewBox="0 0 300 76" preserveAspectRatio="none" role="img" aria-label="Trailing ten-year annualised premium"><line x1="0" x2="300" y1={zero} y2={zero}/><path d={path}/></svg>
}

export default function FactorRegimePage(){
  const{loadContextAsset,manifest,loading:appLoading}=useStockScoutData()
  const[payload,setPayload]=useState<FactorPayload|null>(null),[error,setError]=useState(''),[loading,setLoading]=useState(true),[retry,setRetry]=useState(0)
  useEffect(()=>{if(appLoading||!manifest)return;let live=true;setLoading(true);setError('');loadContextAsset<FactorPayload>('factorRegime',retry>0).then(value=>{if(live)setPayload(value)}).catch(reason=>{if(live)setError(reason instanceof Error?reason.message:String(reason))}).finally(()=>{if(live)setLoading(false)});return()=>{live=false}},[loadContextAsset,manifest,appLoading,retry])
  const improving=useMemo(()=>new Set(payload?.summary?.mostImproving12m??[]),[payload])
  const domain=useMemo(()=>{const values=(payload?.factors??[]).flatMap(factor=>factor.series.slice(-120).map(point=>point.premiumPct));return{min:Math.min(0,...values),max:Math.max(0,...values)}},[payload])
  const deteriorating=useMemo(()=>[...(payload?.factors??[])].filter(factor=>factor.latest.delta12mPp!=null).sort((a,b)=>Number(a.latest.delta12mPp)-Number(b.latest.delta12mPp)).slice(0,2).map(factor=>factor.label),[payload])
  if(loading&&!payload)return <div className="ctx-state ctx-loading"><b>Loading Factor Regime…</b><span>Validating the immutable context asset.</span></div>
  if(error&&!payload)return <div className="ctx-state ctx-error" role="alert"><b>Factor Regime unavailable</b><span>{error}</span><button onClick={()=>setRetry(value=>value+1)}>Retry</button></div>
  if(!payload)return null
  return <main className="ctx-page">
    <header className="ctx-hero"><div><small>KENNETH R. FRENCH · SIX FACTORS · READ-ONLY</small><h1>Factor Regime</h1><p>Trailing 10-year premiums, drought duration and change direction. This evidence layer never changes StockScout scores.</p></div><div className="ctx-meta"><span><small>Data through</small><b>{payload.range?.lastMonth??'—'}</b></span><span><small>Active droughts</small><b>{payload.summary?.activeDroughts??0}/6</b></span><button onClick={()=>setRetry(value=>value+1)} disabled={loading}>{loading?'Checking…':'Refresh'}</button></div></header>
    {payload.freshness?.status==='stale'?<div className="ctx-warning" role="status">Showing the last verified factor snapshot; StockScout scoring is unaffected.</div>:null}
    <div className="ctx-warning" role="status">Improving: {(payload.summary?.mostImproving12m??[]).join(', ')||'—'} · Deteriorating: {deteriorating.join(', ')||'—'} · Build {payload.generatedAt?new Date(payload.generatedAt).toLocaleString():'—'}</div>
    {error?<div className="ctx-warning" role="status">Refresh failed; preserving the verified asset already on screen. {error}</div>:null}
    <section className="ctx-grid ctx-factor-grid">
      {payload.factors.map(factor=><article className={`ctx-card ctx-${factor.latest.regime.toLowerCase()}`} key={factor.id}>
        <header><div><small>{factor.sourceCode}</small><h2>{factor.label}</h2></div><span className="ctx-chip">{labels[factor.latest.regime]}</span></header>
        <div className="ctx-primary"><strong>{signed(factor.latest.premiumPct)}%</strong><span>10Y annualised premium</span></div>
        <Sparkline points={factor.series} domain={domain}/>
        <div className="ctx-metrics"><span><small>Δ 1M</small><b>{signed(factor.latest.delta1mPp)} pp</b></span><span><small>Δ 6M</small><b>{signed(factor.latest.delta6mPp)} pp</b></span><span className={improving.has(factor.id)?'positive':''}><small>Δ 12M</small><b>{signed(factor.latest.delta12mPp)} pp</b></span></div>
        <footer><span><small>Current drought</small><b>{factor.currentDrought.active?factor.currentDrought.duration:'None'}</b></span><span><small>Longest drought</small><b>{factor.longestDrought.duration||'—'}</b></span><span><small>Historical percentile</small><b>{fmt(factor.latest.historicalPercentile)}%</b></span></footer>
      </article>)}
    </section>
    <section className="ctx-contract"><div><small>Method</small><b>{payload.method.droughtDefinition}</b></div><div><small>Source</small><b>{payload.source.provider}</b></div><div><small>StockScout impact</small><b>None · independent context</b></div></section>
  </main>
}
