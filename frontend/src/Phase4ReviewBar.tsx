import {useEffect,useMemo,useState} from 'react'
import {buildReviewInbox,dataHealth,explainStock,reviewScopeLabel,type ReviewManifest,type ReviewPayload,type ReviewScope,type ValidationStatus} from './phase4Review'
import {useStockScoutData} from './data/StockScoutDataProvider'

type InboxMode=Exclude<ReviewScope,null>

export default function Phase4ReviewBar(){
  const{core,manifest:selectedManifest,selectedTicker,selectTicker,reviewScope,setReviewScope,loadOptional}=useStockScoutData()
  const payload=core as ReviewPayload|null
  const manifest=selectedManifest as ReviewManifest|null
  const[validation,setValidation]=useState<ValidationStatus|null>(null)
  const[inboxMode,setInboxMode]=useState<InboxMode|null>(null)
  const[queueMode,setQueueMode]=useState<InboxMode|null>(null)
  const[whyOpen,setWhyOpen]=useState(false)
  const[reviewed,setReviewed]=useState<string[]>([])

  useEffect(()=>{
    let live=true
    loadOptional<ValidationStatus>('validation-status.json').then(next=>{if(live)setValidation(next)})
    return()=>{live=false}
  },[loadOptional])

  const reviewedStorageKey=payload?.generatedAt?`stockscout-phase4-reviewed:${payload.generatedAt}`:null
  useEffect(()=>{
    if(!reviewedStorageKey){setReviewed([]);return}
    try{
      const stored=JSON.parse(sessionStorage.getItem(reviewedStorageKey)||'[]')
      setReviewed(Array.isArray(stored)?stored.filter(value=>typeof value==='string'):[])
    }catch{setReviewed([])}
  },[reviewedStorageKey])

  const inbox=useMemo(()=>buildReviewInbox(payload?.universe||[]),[payload])
  const selected=useMemo(()=>payload?.universe.find(stock=>stock.ticker===selectedTicker)||null,[payload,selectedTicker])
  const why=useMemo(()=>selected?explainStock(selected):[],[selected])
  const health=useMemo(()=>dataHealth(payload,manifest,validation),[payload,manifest,validation])
  const reviewedSet=useMemo(()=>new Set(reviewed),[reviewed])
  const rows=inboxMode==='today'?inbox.today:inboxMode==='new'?inbox.newSinceLastScan:[]
  const queueRows=queueMode==='today'?inbox.today:queueMode==='new'?inbox.newSinceLastScan:[]
  const scopeRows=reviewScope==='today'?inbox.today:reviewScope==='new'?inbox.newSinceLastScan:[]
  const queueIndex=queueRows.findIndex(stock=>stock.ticker===selectedTicker)
  const todayUnseen=inbox.today.filter(stock=>!reviewedSet.has(stock.ticker)).length
  const newUnseen=inbox.newSinceLastScan.filter(stock=>!reviewedSet.has(stock.ticker)).length

  useEffect(()=>{
    if(queueMode&&selectedTicker&&queueRows.length&&queueIndex<0)setQueueMode(null)
  },[queueMode,selectedTicker,queueRows.length,queueIndex])

  const markReviewed=(ticker:string)=>{
    if(!reviewedStorageKey)return
    setReviewed(current=>{
      if(current.includes(ticker))return current
      const next=[...current,ticker]
      try{sessionStorage.setItem(reviewedStorageKey,JSON.stringify(next))}catch{}
      return next
    })
  }
  const openTicker=(ticker:string,mode:InboxMode|null=null)=>{
    selectTicker(ticker)
    setInboxMode(null)
    setQueueMode(mode)
    setWhyOpen(true)
    if(mode)markReviewed(ticker)
  }
  const moveQueue=(delta:-1|1)=>{
    if(!queueMode||queueIndex<0)return
    const nextIndex=queueIndex+delta
    if(nextIndex<0||nextIndex>=queueRows.length)return
    openTicker(queueRows[nextIndex].ticker,queueMode)
  }
  const closeWhy=()=>{setWhyOpen(false);setQueueMode(null)}
  const unseenLabel=(count:number)=>count===0?'all seen':`${count} unseen`
  const toggleScope=(scope:InboxMode)=>{
    const next=reviewScope===scope?null:scope
    setReviewScope(next)
    setInboxMode(null)
    setQueueMode(null)
    setWhyOpen(false)
  }
  const clearScope=()=>{
    setReviewScope(null)
    setInboxMode(null)
    setQueueMode(null)
  }

  return <section className="p4-review" aria-label="Phase 4 review workflow">
    <div className="p4-review-main">
      <div className={`p4-health ${health.level}`} title={health.detail}>
        <span className="p4-health-dot"/>
        <b>{health.label}</b>
        {health.ageHours!==null&&<small>{Math.round(health.ageHours)}h snapshot</small>}
        <span>{validation?.conclusion==='success'?`validation #${validation.run_id??'—'}`:validation?.conclusion?`validation: ${validation.conclusion}`:'validation status: unknown'}</span>
      </div>
      <div className="p4-inbox-actions">
        <button className={reviewScope==='today'?'active':''} onClick={()=>toggleScope('today')} aria-pressed={reviewScope==='today'}><b>Today</b><span>{inbox.today.length}<small>{unseenLabel(todayUnseen)}</small></span></button>
        <button className={reviewScope==='new'?'active':''} onClick={()=>toggleScope('new')} aria-pressed={reviewScope==='new'}><b>New since last scan</b><span>{inbox.newSinceLastScan.length}<small>{unseenLabel(newUnseen)}</small></span></button>
        <button className={whyOpen?'active':''} disabled={!selected} onClick={()=>{setWhyOpen(open=>!open);if(whyOpen)setQueueMode(null)}}><b>Why this stock?</b><span>{selected?.ticker||'—'}</span></button>
      </div>
      <div className="p4-rapid-note"><b>Rapid Review</b><span>Grid now favors continuous mobile review; scroll to keep loading matches.</span></div>
      {reviewScope&&<div className="p4-review-scope" role="status">
        <div><b>Review scope · {reviewScopeLabel(reviewScope)}</b><span>{scopeRows.length.toLocaleString()} candidates · saved-screen membership paused · sort preserved</span></div>
        <button onClick={()=>setInboxMode(reviewScope)}>List</button>
        <button className="clear" aria-label="Clear review scope" onClick={clearScope}>×</button>
      </div>}
    </div>

    {inboxMode&&<div className="p4-inbox-drawer"><header><div><b>{reviewScopeLabel(inboxMode)}</b><span>{rows.length} candidates · {unseenLabel(inboxMode==='today'?todayUnseen:newUnseen)} · click to start a review queue</span></div><button aria-label="Close review inbox" onClick={()=>setInboxMode(null)}>×</button></header><div className="p4-inbox-list">{rows.slice(0,24).map(stock=><button className={reviewedSet.has(stock.ticker)?'reviewed':''} key={stock.ticker} onClick={()=>openTicker(stock.ticker,inboxMode)}><b>{stock.ticker}</b><span>{stock.primarySetup||stock.setup||stock.stageName||'Setup'}</span><em>{reviewedSet.has(stock.ticker)?'✓ reviewed · ':''}{stock.changeLabels?.[0]||stock.opportunityTier||''}</em><strong>{Math.round(stock.opportunityScore??0)}</strong></button>)}{!rows.length&&<p>No candidates in this snapshot.</p>}</div>{rows.length>24&&<footer>Showing the first 24 of {rows.length}; queue navigation can continue through the full inbox.</footer>}</div>}

    {whyOpen&&selected&&<aside className="p4-why"><header><div><b>WHY {selected.ticker}?</b><span>transparent decomposition · no new score{queueMode&&queueIndex>=0?` · Review ${queueIndex+1} / ${queueRows.length}`:''}</span></div><div className="p4-why-actions">{queueMode&&queueIndex>=0&&<><button className="p4-prev" aria-label="Previous review candidate" disabled={queueIndex===0} onClick={()=>moveQueue(-1)}>←</button><button className="p4-next" aria-label="Next review candidate" disabled={queueIndex===queueRows.length-1} onClick={()=>moveQueue(1)}>→</button></>}<button aria-label="Close why panel" onClick={closeWhy}>×</button></div></header><ol>{why.map((line,index)=><li key={`${line}-${index}`}>{line}</li>)}</ol></aside>}
  </section>
}
