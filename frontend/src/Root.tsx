import {lazy,Suspense,useEffect,useState} from 'react'
import DeepVueTerminal from './DeepVueTerminal'
import GroupsPage from './GroupsPage'
import OwnerAccess from './owner/OwnerAccess'
import {useStockScoutData} from './data/StockScoutDataProvider'
import {MODES,useMode} from './modes/ModeProvider'
import './modes/modes.css'

const RyanOriginalDashboard=lazy(()=>import('./RyanOriginalDashboard'))
const FactorRegimePage=lazy(()=>import('./FactorRegimePage'))
const GmliContextPage=lazy(()=>import('./GmliContextPage'))
type NextView='screener'|'groups'|'factors'|'gmli'
const NEXT_VIEWS=new Set<NextView>(['screener','groups','factors','gmli'])

function initialView():NextView{
  const value=new URLSearchParams(location.search).get('view') as NextView|null
  return value&&NEXT_VIEWS.has(value)?value:'screener'
}

export default function Root(){
  const{mode,definition,setMode}=useMode()
  const{selectTicker,manifest}=useStockScoutData()
  const[view,setViewState]=useState<NextView>(initialView)

  const setView=(next:NextView)=>{
    setViewState(next)
    const url=new URL(location.href)
    if(mode==='next')url.searchParams.set('view',next)
    else url.searchParams.delete('view')
    history.replaceState(history.state,'',`${url.pathname}${url.search}${url.hash}`)
  }

  useEffect(()=>{
    const sync=()=>setViewState(mode==='next'?initialView():'screener')
    window.addEventListener('popstate',sync)
    return()=>window.removeEventListener('popstate',sync)
  },[mode])

  const openTicker=(ticker:string)=>{
    selectTicker(ticker)
    setView('screener')
  }
  const openGroup=(type:'sector'|'industry',name:string)=>{
    const url=new URL(location.href)
    url.searchParams.set('mode','next')
    url.searchParams.set('view','screener')
    url.searchParams.set('groupType',type)
    url.searchParams.set('group',name)
    history.replaceState(history.state,'',`${url.pathname}${url.search}${url.hash}`)
    setViewState('screener')
  }

  const selectMode=(nextMode:typeof mode)=>{
    const url=new URL(location.href)
    if(nextMode==='next')url.searchParams.set('view','screener')
    else url.searchParams.delete('view')
    url.searchParams.delete('groupType')
    url.searchParams.delete('group')
    history.replaceState(history.state,'',`${url.pathname}${url.search}${url.hash}`)
    setViewState('screener')
    setMode(nextMode)
  }

  return <div className={`unified-app mode-${mode}`}>
    <header className="mode-header">
      <div className="mode-brand">
        <b>StockScout Unified</b>
        <span>{definition.description}</span>
      </div>
      <nav aria-label="Scanner mode">
        {MODES.map(item=><button
          key={item.id}
          className={item.id===mode?'active':''}
          aria-pressed={item.id===mode}
          onClick={()=>selectMode(item.id)}
        >{item.label}</button>)}
      </nav>
      <div className="mode-meta">
        <span>{manifest?.sessionDate??'No scan'}</span>
        <span>{definition.priceBasis==='split_only'?'Split-only':'Adjusted'}</span>
      </div>
      <OwnerAccess/>
    </header>
    {mode==='next'?<nav className="next-view-nav" aria-label="Next workspace">
      {(['screener','groups','factors','gmli'] as const).map(item=><button key={item} className={view===item?'active':''} aria-current={view===item?'page':undefined} onClick={()=>setView(item)}>{item==='gmli'?'GMLI':item[0].toUpperCase()+item.slice(1)}</button>)}
    </nav>:null}
    <Suspense fallback={<div className="unified-view-loading" role="status">Loading workspace…</div>}>
      {mode==='ryan-original'?<RyanOriginalDashboard/>:mode!=='next'||view==='screener'?<DeepVueTerminal key={mode}/>:view==='groups'?<GroupsPage onOpenTicker={openTicker} onOpenGroup={openGroup}/>:view==='factors'?<FactorRegimePage/>:<GmliContextPage/>}
    </Suspense>
  </div>
}
