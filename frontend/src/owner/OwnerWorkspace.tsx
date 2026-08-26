import {useCallback,useEffect,useState,type FormEvent} from 'react'
import {
  useOwnerData,
  type OwnerAlert,
  type OwnerDrawing,
  type OwnerSavedScreen,
} from './OwnerDataProvider'
import {normalizeOwnerTicker,parseOwnerJsonObject} from './ownerState'
import './owner.css'

const EMPTY_OBJECT='{}'

function prettyJson(value:Record<string,unknown>){
  return JSON.stringify(value,null,2)
}

function errorMessage(value:unknown){
  return value instanceof Error?value.message:String(value)
}

export default function OwnerWorkspace({ticker=''}:{ticker?:string}){
  const owner=useOwnerData()
  const[screens,setScreens]=useState<OwnerSavedScreen[]>([])
  const[alerts,setAlerts]=useState<OwnerAlert[]>([])
  const[drawings,setDrawings]=useState<OwnerDrawing[]>([])
  const[loadedForUser,setLoadedForUser]=useState<string|null>(null)
  const[drawingsForUser,setDrawingsForUser]=useState<string|null>(null)
  const[busy,setBusy]=useState(false)
  const[message,setMessage]=useState('')

  const[screenId,setScreenId]=useState<string|undefined>()
  const[screenName,setScreenName]=useState('')
  const[screenDefinition,setScreenDefinition]=useState(EMPTY_OBJECT)

  const[drawingId,setDrawingId]=useState<string|undefined>()
  const[drawingTicker,setDrawingTicker]=useState(ticker.trim().toUpperCase())
  const[drawingInterval,setDrawingInterval]=useState('daily')
  const[drawingPayload,setDrawingPayload]=useState(EMPTY_OBJECT)

  const[alertId,setAlertId]=useState<string|undefined>()
  const[alertName,setAlertName]=useState('')
  const[alertTicker,setAlertTicker]=useState(ticker.trim().toUpperCase())
  const[alertEnabled,setAlertEnabled]=useState(true)
  const[alertPayload,setAlertPayload]=useState(EMPTY_OBJECT)

  useEffect(()=>{
    if(!ticker.trim())return
    const normalized=ticker.trim().toUpperCase()
    setDrawingTicker(normalized);setAlertTicker(normalized)
  },[ticker])

  useEffect(()=>{
    if(!owner.user){
      setScreens([]);setAlerts([]);setDrawings([]);setLoadedForUser(null);setDrawingsForUser(null);setMessage('')
      return
    }
    let live=true
    const userId=owner.user.id
    setBusy(true);setLoadedForUser(null);setMessage('')
    Promise.all([owner.listSavedScreens(),owner.listAlerts()]).then(([nextScreens,nextAlerts])=>{
      if(!live)return
      setScreens(nextScreens);setAlerts(nextAlerts);setLoadedForUser(userId)
    }).catch(next=>{if(live)setMessage(errorMessage(next))}).finally(()=>{if(live)setBusy(false)})
    return()=>{live=false}
  },[owner.user?.id,owner.listSavedScreens,owner.listAlerts])

  const refreshScreens=useCallback(async()=>{
    if(!owner.user)return
    const userId=owner.user.id,next=await owner.listSavedScreens()
    if(owner.user?.id===userId){setScreens(next);setLoadedForUser(userId)}
  },[owner.user,owner.listSavedScreens])

  const refreshAlerts=useCallback(async()=>{
    if(!owner.user)return
    const userId=owner.user.id,next=await owner.listAlerts()
    if(owner.user?.id===userId){setAlerts(next);setLoadedForUser(userId)}
  },[owner.user,owner.listAlerts])

  const refreshDrawings=useCallback(async()=>{
    if(!owner.user)return
    const normalized=normalizeOwnerTicker(drawingTicker)
    const userId=owner.user.id,next=await owner.listDrawings(normalized)
    if(owner.user?.id===userId){setDrawingTicker(normalized);setDrawings(next);setDrawingsForUser(userId)}
  },[owner.user,owner.listDrawings,drawingTicker])

  const run=useCallback(async(action:()=>Promise<void>,success:string)=>{
    setBusy(true);setMessage('')
    try{await action();setMessage(success)}catch(next){setMessage(errorMessage(next))}finally{setBusy(false)}
  },[])

  const submitScreen=(event:FormEvent)=>{
    event.preventDefault()
    void run(async()=>{
      await owner.saveSavedScreen({id:screenId,name:screenName,definition:parseOwnerJsonObject(screenDefinition,'Screen definition')})
      setScreenId(undefined);setScreenName('');setScreenDefinition(EMPTY_OBJECT)
      await refreshScreens()
    },'Saved screen synced.')
  }

  const submitDrawing=(event:FormEvent)=>{
    event.preventDefault()
    void run(async()=>{
      await owner.saveDrawing({id:drawingId,ticker:drawingTicker,interval:drawingInterval,payload:parseOwnerJsonObject(drawingPayload,'Drawing payload')})
      setDrawingId(undefined);setDrawingPayload(EMPTY_OBJECT)
      await refreshDrawings()
    },'Drawing synced.')
  }

  const submitAlert=(event:FormEvent)=>{
    event.preventDefault()
    void run(async()=>{
      await owner.saveAlert({id:alertId,name:alertName,ticker:alertTicker||null,enabled:alertEnabled,payload:parseOwnerJsonObject(alertPayload,'Alert payload')})
      setAlertId(undefined);setAlertName('');setAlertPayload(EMPTY_OBJECT);setAlertEnabled(true)
      await refreshAlerts()
    },'Alert synced.')
  }

  if(!owner.user)return null
  const visibleScreens=loadedForUser===owner.user.id?screens:[]
  const visibleAlerts=loadedForUser===owner.user.id?alerts:[]
  const visibleDrawings=drawingsForUser===owner.user.id?drawings:[]

  return <section className="owner-workspace" aria-label="Owner workspace">
    <header><div><b>Owner workspace</b><span>Private Supabase state · {owner.user.email}</span></div>{busy?<span aria-live="polite">Syncing…</span>:null}</header>
    {message?<p className="owner-workspace-message" role={/synced|removed/i.test(message)?'status':'alert'}>{message}</p>:null}

    <details open>
      <summary>Watchlist <span>{owner.watchlist.length}</span></summary>
      {owner.watchlist.length?<ul className="owner-record-list">{owner.watchlist.map(item=><li key={item}><b>{item}</b><button type="button" disabled={busy} onClick={()=>void run(()=>owner.toggleWatch(item),`${item} removed from watchlist.`)}>Remove</button></li>)}</ul>:<p className="owner-empty">No synced owner tickers.</p>}
    </details>

    <details>
      <summary>Saved screens <span>{visibleScreens.length}</span></summary>
      <form className="owner-editor" onSubmit={submitScreen}>
        <label>Name<input required maxLength={80} value={screenName} onChange={event=>setScreenName(event.target.value)}/></label>
        <label>Definition (JSON object)<textarea required rows={5} spellCheck={false} value={screenDefinition} onChange={event=>setScreenDefinition(event.target.value)}/></label>
        <div><button type="submit" disabled={busy}>{screenId?'Update screen':'Save screen'}</button>{screenId?<button type="button" onClick={()=>{setScreenId(undefined);setScreenName('');setScreenDefinition(EMPTY_OBJECT)}}>Cancel edit</button>:null}</div>
      </form>
      {visibleScreens.length?<ul className="owner-record-list">{visibleScreens.map(screen=><li key={screen.id}><div><b>{screen.name}</b><code>{prettyJson(screen.definition)}</code></div><span><button type="button" onClick={()=>{setScreenId(screen.id);setScreenName(screen.name);setScreenDefinition(prettyJson(screen.definition))}}>Edit</button><button type="button" disabled={busy} onClick={()=>void run(async()=>{await owner.deleteSavedScreen(screen.id);await refreshScreens()},`${screen.name} removed.`)}>Delete</button></span></li>)}</ul>:<p className="owner-empty">No saved screens.</p>}
    </details>

    <details>
      <summary>Drawings <span>{visibleDrawings.length}</span></summary>
      <form className="owner-editor" onSubmit={submitDrawing}>
        <div className="owner-editor-row"><label>Ticker<input required maxLength={20} value={drawingTicker} onChange={event=>setDrawingTicker(event.target.value.toUpperCase())}/></label><label>Interval<input required maxLength={20} value={drawingInterval} onChange={event=>setDrawingInterval(event.target.value)}/></label></div>
        <label>Drawing payload (JSON object)<textarea required rows={5} spellCheck={false} value={drawingPayload} onChange={event=>setDrawingPayload(event.target.value)}/></label>
        <div><button type="submit" disabled={busy}>{drawingId?'Update drawing':'Save drawing'}</button><button type="button" disabled={busy||!drawingTicker.trim()} onClick={()=>void run(refreshDrawings,'Drawings refreshed.')}>Load ticker</button>{drawingId?<button type="button" onClick={()=>{setDrawingId(undefined);setDrawingPayload(EMPTY_OBJECT)}}>Cancel edit</button>:null}</div>
      </form>
      {visibleDrawings.length?<ul className="owner-record-list">{visibleDrawings.map(drawing=><li key={drawing.id}><div><b>{drawing.ticker} · {drawing.interval}</b><code>{prettyJson(drawing.payload)}</code></div><span><button type="button" onClick={()=>{setDrawingId(drawing.id);setDrawingTicker(drawing.ticker);setDrawingInterval(drawing.interval);setDrawingPayload(prettyJson(drawing.payload))}}>Edit</button><button type="button" disabled={busy} onClick={()=>void run(async()=>{await owner.deleteDrawing(drawing.id);await refreshDrawings()},`${drawing.ticker} drawing removed.`)}>Delete</button></span></li>)}</ul>:<p className="owner-empty">Load a ticker to view its drawings.</p>}
    </details>

    <details>
      <summary>Alerts <span>{visibleAlerts.length}</span></summary>
      <form className="owner-editor" onSubmit={submitAlert}>
        <div className="owner-editor-row"><label>Name<input required maxLength={120} value={alertName} onChange={event=>setAlertName(event.target.value)}/></label><label>Ticker (optional)<input maxLength={20} value={alertTicker} onChange={event=>setAlertTicker(event.target.value.toUpperCase())}/></label></div>
        <label className="owner-checkbox"><input type="checkbox" checked={alertEnabled} onChange={event=>setAlertEnabled(event.target.checked)}/> Enabled</label>
        <label>Alert payload (JSON object)<textarea required rows={5} spellCheck={false} value={alertPayload} onChange={event=>setAlertPayload(event.target.value)}/></label>
        <div><button type="submit" disabled={busy}>{alertId?'Update alert':'Save alert'}</button>{alertId?<button type="button" onClick={()=>{setAlertId(undefined);setAlertName('');setAlertTicker('');setAlertPayload(EMPTY_OBJECT);setAlertEnabled(true)}}>Cancel edit</button>:null}</div>
      </form>
      {visibleAlerts.length?<ul className="owner-record-list">{visibleAlerts.map(alert=><li key={alert.id}><div><b>{alert.name} · {alert.ticker??'market'} · {alert.enabled?'enabled':'disabled'}</b><code>{prettyJson(alert.payload)}</code></div><span><button type="button" onClick={()=>{setAlertId(alert.id);setAlertName(alert.name);setAlertTicker(alert.ticker??'');setAlertEnabled(alert.enabled);setAlertPayload(prettyJson(alert.payload))}}>Edit</button><button type="button" disabled={busy} onClick={()=>void run(async()=>{await owner.deleteAlert(alert.id);await refreshAlerts()},`${alert.name} removed.`)}>Delete</button></span></li>)}</ul>:<p className="owner-empty">No owner alerts.</p>}
    </details>
  </section>
}
