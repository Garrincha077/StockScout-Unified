import {useState} from 'react'
import DeepVueTerminal from './DeepVueTerminal'
import GroupsPage from './GroupsPage'
import OwnerAccess from './owner/OwnerAccess'
import {useStockScoutData} from './data/StockScoutDataProvider'
import {MODES,useMode} from './modes/ModeProvider'
import './modes/modes.css'

export default function Root(){
  const{mode,definition,setMode}=useMode()
  const{selectTicker,manifest}=useStockScoutData()
  const[view,setView]=useState<'terminal'|'groups'>('terminal')

  const openTicker=(ticker:string)=>{
    selectTicker(ticker)
    setView('terminal')
  }

  return <div className={`unified-app mode-${mode}`}>
    <OwnerAccess/>
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
          onClick={()=>{setMode(item.id);setView('terminal')}}
        >{item.label}</button>)}
      </nav>
      <div className="mode-meta">
        <span>{manifest?.sessionDate??'No scan'}</span>
        <span>{definition.priceBasis==='split_only'?'Split-only':'Adjusted'}</span>
      </div>
    </header>
    {view==='groups'
      ?<GroupsPage onBack={()=>setView('terminal')} onOpenTicker={openTicker}/>
      :<>
        <DeepVueTerminal/>
        <button className="dv-groups-launch" onClick={()=>setView('groups')}>◎ Groups</button>
      </>}
  </div>
}
