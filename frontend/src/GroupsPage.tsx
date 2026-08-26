import {useMemo,useState} from 'react'
import {useStockScoutData} from './data/StockScoutDataProvider'
import {marketRegimeLabel} from './deepvue/runtime'
import './groups.css'

type GroupRow={ticker:string;name:string;rank:number;rel1m:number;rel3m:number;rel6m:number;stocks:number;stage2Pct:number;earlyLeaders:number;medianOpportunity:number;avgConfidence?:number;topTickers:string[]}
type Stock={ticker:string;leadershipScore?:number;opportunityScore?:number;rsRank?:number;groupRank?:number;groupRS?:number;groupConfidence?:number;sectorProxy?:string;sectorRank?:number;sectorProxyConfidence?:number;industryProxy?:string;industryRank?:number;industryProxyConfidence?:number;primarySetup?:string}
type Payload={generatedAt:string;market:Record<string,any>;universe:Stock[];groups?:{method:string;description:string;confidenceMethod?:string;averageConfidence?:number;maxLeadershipAdjustmentPoints?:number;sectorCoverage:number;industryCoverage:number;sectors:GroupRow[];industries:GroupRow[]}}
type Mode='Sectors'|'Industries'
type Sort='rank'|'early'|'breadth'|'opportunity'|'confidence'

const fmt=(v:any,d=1)=>typeof v==='number'&&Number.isFinite(v)?v.toFixed(d):'—'
const signed=(v:any,d=1)=>typeof v==='number'&&Number.isFinite(v)?`${v>0?'+':''}${v.toFixed(d)}%`:'—'
const score=(s:Stock)=>s.opportunityScore??s.leadershipScore??0

export default function GroupsPage({onBack,onOpenTicker}:{onBack:()=>void;onOpenTicker:(ticker:string)=>void}){
  const{core,error}=useStockScoutData()
  const payload=core as Payload|null
  const[mode,setMode]=useState<Mode>('Sectors'),[sort,setSort]=useState<Sort>('rank')
  const groups=mode==='Sectors'?payload?.groups?.sectors||[]:payload?.groups?.industries||[]
  const ordered=useMemo(()=>[...groups].sort((a,b)=>{
    if(sort==='early')return b.earlyLeaders-a.earlyLeaders||b.rank-a.rank
    if(sort==='breadth')return b.stage2Pct-a.stage2Pct||b.rank-a.rank
    if(sort==='opportunity')return b.medianOpportunity-a.medianOpportunity||b.rank-a.rank
    if(sort==='confidence')return (b.avgConfidence||0)-(a.avgConfidence||0)||b.rank-a.rank
    return b.rank-a.rank||b.earlyLeaders-a.earlyLeaders
  }),[groups,sort])
  const stockLeaders=useMemo(()=>[...(payload?.universe||[])].sort((a,b)=>score(b)-score(a)||(b.groupConfidence||0)-(a.groupConfidence||0)||(b.rsRank||0)-(a.rsRank||0)).slice(0,20),[payload])
  if(!payload)return <div className="grp-loading">{error||'Loading group leadership…'}</div>
  const coverage=mode==='Sectors'?payload.groups?.sectorCoverage||0:payload.groups?.industryCoverage||0
  const total=payload.universe?.length||0
  return <div className="grp-app">
    <header className="grp-top"><div><button onClick={onBack}>← Terminal</button><span className="grp-dot">◉</span><b>STOCKSCOUT GROUPS</b><small>CONFIDENCE-WEIGHTED LEADERSHIP</small></div><div><span>{marketRegimeLabel(payload.market)}</span><span>{coverage.toLocaleString()}/{total.toLocaleString()} mapped</span><span>{payload.market?.groupModel||'no group model'}</span></div></header>
    <section className="grp-hero"><div><small>LEADING SECTOR</small><b>{payload.market?.topSector||'—'}</b><span>Raw proxy rank {payload.market?.topSectorRank||'—'}/99</span></div><div><small>LEADING INDUSTRY PROXY</small><b>{payload.market?.topIndustry||'—'}</b><span>Raw proxy rank {payload.market?.topIndustryRank||'—'}/99</span></div><div><small>AVG GROUP CONFIDENCE</small><b>{fmt(payload.groups?.averageConfidence??payload.market?.groupAverageConfidence,0)}%</b><span>Max Opportunity group modifier ±{fmt(payload.groups?.maxLeadershipAdjustmentPoints??payload.market?.groupLeadershipMaxAdjustment,0)} pts</span></div></section>
    <section className="grp-note"><b>How to read this:</b> groups remain behavioral proxies, not official GICS metadata. StockScout measures correlation strength plus recent/prior persistence and stability. Raw ETF rank is pulled toward neutral 50 as confidence falls. Opportunity v2 then uses group leadership only as a bounded confirmation modifier; there is no second leadership adjustment on top of final Opportunity.</section>
    <section className="grp-controls"><div><button className={mode==='Sectors'?'active':''} onClick={()=>setMode('Sectors')}>Sectors</button><button className={mode==='Industries'?'active':''} onClick={()=>setMode('Industries')}>Industry proxies</button></div><label>Sort <select value={sort} onChange={e=>setSort(e.target.value as Sort)}><option value="rank">Relative rank</option><option value="confidence">Mapped confidence</option><option value="early">Early leaders</option><option value="breadth">Stage 2 breadth</option><option value="opportunity">Median opportunity</option></select></label></section>
    <main className="grp-layout"><section className="grp-board"><header><span>#</span><span>Group</span><span>1M RS</span><span>3M RS</span><span>6M RS</span><span>S2 breadth</span><span>Early</span><span>Median opp</span><span>Top stocks</span></header>{ordered.map((g,i)=><article key={g.ticker}><div className="grp-rank"><b>{g.rank}</b><i><em style={{width:`${Math.max(2,g.rank)}%`}}/></i><small>{i+1}</small></div><div className="grp-name"><b>{g.name}</b><span>{g.ticker} · {g.stocks} mapped · conf {fmt(g.avgConfidence,0)}%</span></div><strong className={g.rel1m>=0?'good':'bad'}>{signed(g.rel1m)}</strong><strong className={g.rel3m>=0?'good':'bad'}>{signed(g.rel3m)}</strong><strong className={g.rel6m>=0?'good':'bad'}>{signed(g.rel6m)}</strong><div className="grp-breadth"><b>{fmt(g.stage2Pct)}%</b><i><em style={{width:`${Math.max(0,Math.min(100,g.stage2Pct))}%`}}/></i></div><b className={g.earlyLeaders>0?'good':''}>{g.earlyLeaders}</b><b>{fmt(g.medianOpportunity,0)}</b><div className="grp-tickers">{g.topTickers.slice(0,5).map(t=><button key={t} onClick={()=>onOpenTicker(t)}>{t}</button>)}</div></article>)}</section>
      <aside className="grp-leaders"><h2>Opportunity v2 leaders</h2><p>Final Opportunity already contains the bounded group confirmation modifier. Group rank, RS and confidence below provide context without adding the same signal twice.</p>{stockLeaders.map((s,i)=><button key={s.ticker} onClick={()=>onOpenTicker(s.ticker)}><em>{i+1}</em><div><b>{s.ticker}</b><span>{s.primarySetup||'—'}</span><small>{s.industryProxy||s.sectorProxy||'Unclassified'} · Group {fmt(s.groupRank,0)} · GRS {signed(s.groupRS)} · Conf {fmt(s.groupConfidence,0)}%</small></div><strong>{score(s)}</strong><span>RS {fmt(s.rsRank,0)}</span></button>)}</aside>
    </main>
  </div>
}
