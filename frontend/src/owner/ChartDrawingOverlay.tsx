import{useCallback,useEffect,useMemo,useRef,useState,type PointerEvent as ReactPointerEvent}from'react'
import type{IChartApi,ISeriesApi,Logical}from'lightweight-charts'
import{useOwnerData,type OwnerDrawing}from'./OwnerDataProvider'
import{DRAWING_TOOLS,isChartDrawingPayload,moveDrawing,newDrawing,type ChartDrawingPayload,type DrawingPoint,type DrawingTool}from'./chartDrawings'
import{openOwnerAccess}from'./ownerAccessEvent'
import type{DrawingAlertOperator,PriceAlertOperator}from'./alerts'

type ChartBar={time:string;close:number}
type Pixel={x:number;y:number}
type Stored={row:OwnerDrawing;payload:ChartDrawingPayload}
type Drag={drawing:Stored;from:DrawingPoint;preview:ChartDrawingPayload}

const TOOL_LABELS:Record<DrawingTool,string>={cursor:'Select',trendline:'Trend',ray:'Ray',horizontal:'H-line',vertical:'V-line',rectangle:'Box',channel:'Channel',fib:'Fib',text:'Text',measure:'Measure'}

function dash(payload:ChartDrawingPayload){return payload.dash==='dashed'?'6 4':payload.dash==='dotted'?'2 4':undefined}

export default function ChartDrawingOverlay({chart,candle,bars,ticker,interval}:{chart:IChartApi;candle:ISeriesApi<'Candlestick'>;bars:ChartBar[];ticker:string;interval:string}){
  const owner=useOwnerData()
  const[stored,setStored]=useState<Stored[]>([]),[tool,setTool]=useState<DrawingTool>('cursor'),[draft,setDraft]=useState<DrawingPoint|null>(null),[cursor,setCursor]=useState<DrawingPoint|null>(null)
  const[panLocked,setPanLocked]=useState(false),[selectedId,setSelectedId]=useState(''),[busy,setBusy]=useState(false),[message,setMessage]=useState(''),[,setRevision]=useState(0)
  const[drag,setDrag]=useState<Drag|null>(null),lastCreated=useRef<string>('')
  const[textPoint,setTextPoint]=useState<DrawingPoint|null>(null),[textValue,setTextValue]=useState(ticker)
  const[alertDialog,setAlertDialog]=useState<null|{kind:'price';price:string;operator:PriceAlertOperator}|{kind:'drawing';operator:DrawingAlertOperator}>(null)
  const times=useMemo(()=>bars.map(bar=>bar.time),[bars])
  const drawingInterval=interval==='W'?'weekly':'daily'
  const width=chart.timeScale().width(),height=Math.max(0,chart.chartElement().clientHeight-chart.timeScale().height())

  const refresh=useCallback(async()=>{
    if(!owner.user){setStored([]);return}
    const rows=await owner.listDrawings(ticker)
    setStored(rows.filter(row=>row.interval===drawingInterval&&isChartDrawingPayload(row.payload)).map(row=>({row,payload:row.payload as ChartDrawingPayload})))
  },[owner.user?.id,owner.listDrawings,ticker,drawingInterval])

  useEffect(()=>{let live=true;setMessage('');refresh().catch(error=>{if(live)setMessage(error instanceof Error?error.message:String(error))});return()=>{live=false}},[refresh])
  useEffect(()=>{
    const update=()=>setRevision(value=>value+1)
    chart.timeScale().subscribeVisibleLogicalRangeChange(update)
    const observer=new ResizeObserver(update);observer.observe(chart.chartElement())
    return()=>{chart.timeScale().unsubscribeVisibleLogicalRangeChange(update);observer.disconnect()}
  },[chart])

  const project=useCallback((point:DrawingPoint):Pixel|null=>{
    const index=times.indexOf(point.time),y=candle.priceToCoordinate(point.price)
    if(index<0||y==null)return null
    const x=chart.timeScale().logicalToCoordinate(index as Logical)
    return x==null?null:{x:Number(x),y:Number(y)}
  },[candle,chart,times])
  const pointAt=useCallback((x:number,y:number):DrawingPoint|null=>{
    const logical=chart.timeScale().coordinateToLogical(x),price=candle.coordinateToPrice(y)
    if(logical==null||price==null||!times.length)return null
    const index=Math.max(0,Math.min(times.length-1,Math.round(Number(logical))))
    return{time:times[index],price:Number(price)}
  },[candle,chart,times])

  const save=useCallback(async(payload:ChartDrawingPayload,id?:string)=>{
    setBusy(true);setMessage('')
    try{
      await owner.saveDrawing({id,ticker,interval:drawingInterval,payload})
      const rows=await owner.listDrawings(ticker)
      const next=rows.filter(row=>row.interval===drawingInterval&&isChartDrawingPayload(row.payload)).map(row=>({row,payload:row.payload as ChartDrawingPayload}))
      setStored(next)
      if(!id){lastCreated.current=next[0]?.row.id??'';setSelectedId(lastCreated.current)}
    }catch(error){setMessage(error instanceof Error?error.message:String(error))}finally{setBusy(false)}
  },[owner.saveDrawing,owner.listDrawings,ticker,drawingInterval])

  const remove=useCallback(async(id:string)=>{
    setBusy(true);setMessage('')
    try{await owner.deleteDrawing(id);setStored(current=>current.filter(item=>item.row.id!==id));setSelectedId(current=>current===id?'':current)}
    catch(error){setMessage(error instanceof Error?error.message:String(error))}finally{setBusy(false)}
  },[owner.deleteDrawing])

  const commit=useCallback((type:Exclude<DrawingTool,'cursor'>,start:DrawingPoint,end:DrawingPoint)=>{
    let points=[start,end]
    if(type==='channel')points=[start,end,{time:end.time,price:end.price-Math.max(Math.abs(end.price-start.price)*.25,end.price*.02)}]
    if(type==='text'){setTextPoint(start);setTextValue(ticker);return}
    void save(newDrawing(type,points));setDraft(null);setCursor(null);setTool('cursor');setPanLocked(true)
  },[save,ticker])

  const local=(event:ReactPointerEvent<SVGSVGElement>)=>{const rect=event.currentTarget.getBoundingClientRect();return{x:event.clientX-rect.left,y:event.clientY-rect.top}}
  const onPointerDown=(event:ReactPointerEvent<SVGSVGElement>)=>{
    if(!owner.user)return
    const pixel=local(event),point=pointAt(pixel.x,pixel.y)
    if(!point)return
    if(tool!=='cursor'){
      event.preventDefault();event.currentTarget.setPointerCapture(event.pointerId)
      if(tool==='horizontal'||tool==='vertical'||tool==='text')commit(tool,point,point)
      else if(draft)commit(tool,draft,point)
      else{setDraft(point);setCursor(point)}
      return
    }
    if(!panLocked)return
    const target=(event.target as SVGElement).closest<SVGElement>('[data-drawing-id]')?.dataset.drawingId
    if(!target)return
    const drawing=stored.find(item=>item.row.id===target)
    if(!drawing)return
    setSelectedId(target)
    if(!drawing.payload.locked){event.preventDefault();event.currentTarget.setPointerCapture(event.pointerId);setDrag({drawing,from:point,preview:drawing.payload})}
  }
  const onPointerMove=(event:ReactPointerEvent<SVGSVGElement>)=>{
    const pixel=local(event),point=pointAt(pixel.x,pixel.y)
    if(!point)return
    if(draft)setCursor(point)
    if(drag)setDrag({...drag,preview:moveDrawing(drag.drawing.payload,times,drag.from,point)})
  }
  const onPointerUp=(event:ReactPointerEvent<SVGSVGElement>)=>{
    if(event.currentTarget.hasPointerCapture(event.pointerId))event.currentTarget.releasePointerCapture(event.pointerId)
    if(drag){void save(drag.preview,drag.drawing.row.id);setDrag(null)}
  }

  const render=(item:Stored|{row:{id:string};payload:ChartDrawingPayload})=>{
    const payload=drag?.drawing.row.id===item.row.id?drag.preview:item.payload
    if(payload.hidden)return null
    const a=project(payload.points[0]),b=project(payload.points[1]??payload.points[0])
    if(!a||!b)return null
    const color=payload.color,widthPx=selectedId===item.row.id?payload.width+1:payload.width,strokeDasharray=dash(payload)
    const common={stroke:color,strokeWidth:widthPx,strokeDasharray,fill:'transparent',style:{pointerEvents:'stroke' as const,cursor:payload.locked?'default':'move'}}
    const selectProps={'data-drawing-id':item.row.id}
    if(payload.type==='rectangle')return<rect key={item.row.id}{...selectProps} x={Math.min(a.x,b.x)} y={Math.min(a.y,b.y)} width={Math.abs(a.x-b.x)} height={Math.abs(a.y-b.y)} {...common}/>
    if(payload.type==='fib')return<g key={item.row.id}{...selectProps}>{(payload.fibLevels??[0,.236,.382,.5,.618,.786,1]).map(level=>{const price=payload.points[0].price+(payload.points[1].price-payload.points[0].price)*level,y=candle.priceToCoordinate(price);return y==null?null:<g key={level}><line x1={Math.min(a.x,b.x)} y1={Number(y)} x2={Math.max(a.x,b.x)} y2={Number(y)} {...common}/><text x={Math.max(a.x,b.x)+3} y={Number(y)-2} fill={color} fontSize="9">{Math.round(level*1000)/10}%</text></g>})}</g>
    if(payload.type==='text')return<text key={item.row.id}{...selectProps} x={a.x} y={a.y} fill={color} fontSize="12" style={{pointerEvents:'all',cursor:payload.locked?'default':'move'}}>{payload.text??ticker}</text>
    let start=a,end=b
    if(payload.type==='horizontal'){start={x:0,y:a.y};end={x:width,y:a.y}}
    if(payload.type==='vertical'){start={x:a.x,y:0};end={x:a.x,y:height}}
    if((payload.type==='trendline'||payload.type==='ray')&&Math.abs(b.x-a.x)>.1){const slope=(b.y-a.y)/(b.x-a.x),left=payload.type==='trendline'?0:a.x,right=b.x>=a.x?width:0;start=payload.type==='trendline'?{x:left,y:a.y+slope*(left-a.x)}:a;end={x:right,y:a.y+slope*(right-a.x)}}
    const extra=payload.type==='channel'&&payload.points[2]?project(payload.points[2]):null
    return<g key={item.row.id}{...selectProps}><line x1={start.x} y1={start.y} x2={end.x} y2={end.y}{...common}/>{extra&&<line x1={start.x} y1={start.y+(extra.y-b.y)} x2={end.x} y2={end.y+(extra.y-b.y)}{...common}/>} {payload.type==='measure'&&<text x={(a.x+b.x)/2} y={(a.y+b.y)/2-5} fill={color} fontSize="10">{((payload.points[1].price/payload.points[0].price-1)*100).toFixed(1)}%</text>}</g>
  }

  const draftPayload=draft&&cursor&&tool!=='cursor'?newDrawing(tool,[draft,cursor]):null
  const selected=stored.find(item=>item.row.id===selectedId)
  const updateSelected=(patch:Partial<ChartDrawingPayload>)=>{if(selected)void save({...selected.payload,...patch},selected.row.id)}
  const savePriceAlert=()=>{
    if(alertDialog?.kind!=='price')return
    const level=Number(alertDialog.price)
    if(!Number.isFinite(level)||level<=0)return
    setBusy(true);setMessage('')
    owner.saveAlert({name:`${ticker} ${alertDialog.operator.replaceAll('_',' ')} ${level.toFixed(2)}`,ticker,enabled:true,payload:{version:1,kind:'price',operator:alertDialog.operator,price:level}})
      .then(()=>{setMessage('Price alert synced for the next verified EOD run.');setAlertDialog(null)}).catch(error=>setMessage(error instanceof Error?error.message:String(error))).finally(()=>setBusy(false))
  }
  const saveDrawingAlert=()=>{
    if(!selected||alertDialog?.kind!=='drawing')return
    setBusy(true);setMessage('')
    owner.saveAlert({name:`${ticker} ${selected.payload.type} ${alertDialog.operator.replaceAll('_',' ')}`,ticker,enabled:true,payload:{version:1,kind:'drawing',operator:alertDialog.operator,drawing:selected.payload}})
      .then(()=>{setMessage('Drawing alert synced for the next verified EOD run.');setAlertDialog(null)}).catch(error=>setMessage(error instanceof Error?error.message:String(error))).finally(()=>setBusy(false))
  }

  return<>
    <div className="chart-drawing-toolbar" aria-label="Chart drawing tools">
      {!owner.user?<button type="button" className="chart-owner-cta" onClick={openOwnerAccess}>{owner.configured?'Sign in to draw & alert':'Enable owner drawings & alerts'}</button>:<>
        <button type="button" className={!panLocked&&tool==='cursor'?'active':''} onClick={()=>{setTool('cursor');setPanLocked(false);setDraft(null)}}>Pan</button>
        {DRAWING_TOOLS.map(item=><button type="button" key={item} className={tool===item&&panLocked?'active':''} disabled={busy} onClick={()=>{setTool(item);setPanLocked(true);setDraft(null)}}>{TOOL_LABELS[item]}</button>)}
        <select aria-label="Saved chart drawing" value={selectedId} onChange={event=>{setSelectedId(event.target.value);setTool('cursor');setPanLocked(true)}}><option value="">{stored.length} drawings</option>{stored.map(item=><option key={item.row.id} value={item.row.id}>{item.payload.hidden?'◌':'●'} {item.payload.type}</option>)}</select>
        <button type="button" disabled={busy} onClick={()=>setAlertDialog({kind:'price',price:String(bars.at(-1)?.close??''),operator:'crossing_up'})}>Price alert</button>
        {selected&&<><input aria-label="Drawing color" type="color" value={selected.payload.color} onChange={event=>updateSelected({color:event.target.value})}/><button type="button" onClick={()=>updateSelected({dash:selected.payload.dash==='solid'?'dashed':selected.payload.dash==='dashed'?'dotted':'solid'})}>{selected.payload.dash}</button><button type="button" onClick={()=>updateSelected({locked:!selected.payload.locked})}>{selected.payload.locked?'Unlock':'Lock'}</button><button type="button" onClick={()=>updateSelected({hidden:!selected.payload.hidden})}>{selected.payload.hidden?'Show':'Hide'}</button><button type="button" onClick={()=>setAlertDialog({kind:'drawing',operator:['rectangle','channel'].includes(selected.payload.type)?'entering':'crossing_up'})}>Alert</button><button type="button" className="danger" onClick={()=>void remove(selected.row.id)}>Delete</button></>}
        <button type="button" disabled={!lastCreated.current||busy} onClick={()=>void remove(lastCreated.current)}>Undo</button>
      </>}
    </div>
    {message&&<div className="chart-drawing-message" role={message.includes('synced')?'status':'alert'}>{message}</div>}
    {textPoint?<div className="chart-dialog-backdrop"><form className="chart-dialog" role="dialog" aria-modal="true" aria-labelledby="drawing-text-title" onSubmit={event=>{event.preventDefault();void save(newDrawing('text',[textPoint,textPoint],textValue.trim()||ticker));setTextPoint(null);setDraft(null);setCursor(null);setTool('cursor');setPanLocked(true)}}><b id="drawing-text-title">Chart label</b><label>Text<input autoFocus maxLength={80} value={textValue} onChange={event=>setTextValue(event.target.value)}/></label><div><button type="button" onClick={()=>{setTextPoint(null);setTool('cursor');setPanLocked(false)}}>Cancel</button><button type="submit" disabled={busy}>Add label</button></div></form></div>:null}
    {alertDialog?<div className="chart-dialog-backdrop"><form className="chart-dialog" role="dialog" aria-modal="true" aria-labelledby="chart-alert-title" onSubmit={event=>{event.preventDefault();alertDialog.kind==='price'?savePriceAlert():saveDrawingAlert()}}><b id="chart-alert-title">{alertDialog.kind==='price'?'Price alert':'Drawing alert'}</b><p>Evaluated after the next verified EOD deployment and delivered through Telegram when triggered.</p>{alertDialog.kind==='price'?<><label>Condition<select value={alertDialog.operator} onChange={event=>setAlertDialog({...alertDialog,operator:event.target.value as PriceAlertOperator})}><option value="crossing_up">Crossing up</option><option value="crossing_down">Crossing down</option><option value="crossing">Crossing either way</option><option value="touch">Touches</option><option value="above">Closes above</option><option value="below">Closes below</option></select></label><label>Price<input autoFocus inputMode="decimal" required value={alertDialog.price} onChange={event=>setAlertDialog({...alertDialog,price:event.target.value})}/></label></>:<label>Condition<select value={alertDialog.operator} onChange={event=>setAlertDialog({...alertDialog,operator:event.target.value as DrawingAlertOperator})}>{['rectangle','channel'].includes(selected?.payload.type??'')?<><option value="entering">Entering</option><option value="exiting">Exiting</option><option value="inside">Inside</option><option value="outside">Outside</option></>:<><option value="crossing_up">Crossing up</option><option value="crossing_down">Crossing down</option><option value="crossing">Crossing either way</option><option value="touch">Touches</option></>}</select></label>}<div><button type="button" onClick={()=>setAlertDialog(null)}>Cancel</button><button type="submit" disabled={busy}>Save alert</button></div></form></div>:null}
    <svg className={`chart-drawing-layer ${panLocked?'locked':''}`} width="100%" height="100%" onPointerDown={onPointerDown} onPointerMove={onPointerMove} onPointerUp={onPointerUp} onPointerCancel={()=>{setDraft(null);setDrag(null)}}>
      {stored.map(render)}
      {draftPayload&&render({row:{id:'__draft__'},payload:draftPayload})}
    </svg>
  </>
}
