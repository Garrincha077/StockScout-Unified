import {createContext,useCallback,useContext,useMemo,useState,type ReactNode} from 'react'

export type ModeId='bottom-fishing'|'next'|'ryan-original'

export type ModeDefinition={
  id:ModeId
  label:string
  shortLabel:string
  description:string
  priceBasis:'split_only'|'split_div'
}
export const MODES:readonly ModeDefinition[]=[
  {id:'bottom-fishing',label:'Bottom Fishing',shortLabel:'Bottom',description:'Stage 1 → Stage 2 · Crash Base · RWB · Thrust · MA cluster/RVOL',priceBasis:'split_only'},
  {id:'next',label:'Next',shortLabel:'Next',description:'Current StockScreener-next workflow',priceBasis:'split_div'},
  {id:'ryan-original',label:'Ryan Original',shortLabel:'Ryan',description:'Frozen RyanJHamby Stage/Minervini workflow',priceBasis:'split_div'},
] as const

const MODE_KEY='stockscout-unified-mode-v1'
const MODE_IDS=new Set<ModeId>(MODES.map(mode=>mode.id))

export function isModeId(value:unknown):value is ModeId{
  return typeof value==='string'&&MODE_IDS.has(value as ModeId)
}

function initialMode():ModeId{
  const query=new URLSearchParams(location.search).get('mode')
  if(isModeId(query))return query
  try{
    const stored=localStorage.getItem(MODE_KEY)
    if(isModeId(stored))return stored
  }catch{}
  return'bottom-fishing'
}

type ModeContextValue={mode:ModeId;definition:ModeDefinition;setMode:(mode:ModeId)=>void}
const ModeContext=createContext<ModeContextValue|null>(null)

export function ModeProvider({children}:{children:ReactNode}){
  const[mode,setModeState]=useState<ModeId>(initialMode)
  const setMode=useCallback((next:ModeId)=>{
    setModeState(next)
    try{localStorage.setItem(MODE_KEY,next)}catch{}
    const url=new URL(location.href)
    url.searchParams.set('mode',next)
    url.searchParams.delete('run')
    history.replaceState(history.state,'',`${url.pathname}${url.search}${url.hash}`)
  },[])
  const definition=MODES.find(candidate=>candidate.id===mode)??MODES[0]
  const value=useMemo(()=>({mode,definition,setMode}),[mode,definition,setMode])
  return<ModeContext.Provider value={value}>{children}</ModeContext.Provider>
}

export function useMode(){
  const value=useContext(ModeContext)
  if(!value)throw new Error('useMode must be used within ModeProvider')
  return value
}
