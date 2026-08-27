import{Children,useEffect,useRef,useState,type CSSProperties,type PointerEvent as ReactPointerEvent,type ReactNode}from'react'
import'./resizable-explicit.css'

const STORAGE_PREFIX='stockscout-layout-v3:'
const readSize=(id:string,fallback:number)=>{try{const value=Number(localStorage.getItem(`${STORAGE_PREFIX}${id}`));return Number.isFinite(value)&&value>0?value:fallback}catch{return fallback}}
const saveSize=(id:string,value:number)=>{try{localStorage.setItem(`${STORAGE_PREFIX}${id}`,String(Math.round(value)))}catch{}}
const clamp=(value:number,min:number,max:number)=>Math.max(min,Math.min(max,value))

export function ResizableWorkspace({id,className='',children,defaultSecondary=520,minPrimary=420,minSecondary=340}:{id:string;className?:string;children:ReactNode;defaultSecondary?:number;minPrimary?:number;minSecondary?:number}){
  const parts=Children.toArray(children),host=useRef<HTMLElement>(null),drag=useRef<{x:number;width:number}|null>(null)
  const[secondary,setSecondary]=useState(()=>readSize(`${id}:secondary`,defaultSecondary)),secondaryRef=useRef(secondary)
  const available=()=>Math.max(minSecondary,(host.current?.getBoundingClientRect().width??minPrimary+minSecondary+14)-minPrimary-14)
  const resize=(value:number,persist=false)=>{const next=Math.round(clamp(value,minSecondary,available()));secondaryRef.current=next;setSecondary(next);if(persist)saveSize(`${id}:secondary`,next)}
  const reset=()=>{secondaryRef.current=defaultSecondary;setSecondary(defaultSecondary);try{localStorage.removeItem(`${STORAGE_PREFIX}${id}:secondary`)}catch{}}
  const pointerDown=(event:ReactPointerEvent<HTMLDivElement>)=>{drag.current={x:event.clientX,width:secondaryRef.current};event.currentTarget.setPointerCapture(event.pointerId);event.preventDefault()}
  const pointerMove=(event:ReactPointerEvent<HTMLDivElement>)=>{if(drag.current)resize(drag.current.width-(event.clientX-drag.current.x))}
  const pointerUp=(event:ReactPointerEvent<HTMLDivElement>)=>{if(!drag.current)return;event.currentTarget.releasePointerCapture(event.pointerId);drag.current=null;resize(secondaryRef.current,true)}
  useEffect(()=>{const onResize=()=>resize(secondaryRef.current);window.addEventListener('resize',onResize);return()=>window.removeEventListener('resize',onResize)},[])
  if(parts.length<2)return <section className={className}>{parts}</section>
  return <section ref={host} className={`ss-explicit-split ${className}`} style={{'--ss-secondary':`${secondary}px`}as CSSProperties}>
    {parts[0]}
    <div className="ss-explicit-splitter" role="separator" aria-label="Resize table and detail panes" aria-orientation="vertical" aria-valuemin={minSecondary} aria-valuenow={secondary} tabIndex={0} title="Drag or use arrow keys · double-click to reset" onPointerDown={pointerDown} onPointerMove={pointerMove} onPointerUp={pointerUp} onPointerCancel={pointerUp} onDoubleClick={reset} onKeyDown={event=>{if(!['ArrowLeft','ArrowRight','Home'].includes(event.key))return;event.preventDefault();if(event.key==='Home')reset();else resize(secondaryRef.current+(event.key==='ArrowLeft'?(event.shiftKey?120:40):-(event.shiftKey?120:40)),true)}}><span/></div>
    {parts.slice(1)}
  </section>
}

export function ResizableHeight({id,className='',children,defaultHeight=430,minHeight=260}:{id:string;className?:string;children:ReactNode;defaultHeight?:number;minHeight?:number}){
  const drag=useRef<{y:number;height:number}|null>(null),[height,setHeight]=useState(()=>readSize(`${id}:height`,defaultHeight)),heightRef=useRef(height)
  const[collapsed,setCollapsed]=useState(false),[focused,setFocused]=useState(false)
  const maximum=()=>Math.max(minHeight,window.innerHeight-100)
  const resize=(value:number,persist=false)=>{const next=Math.round(clamp(value,minHeight,maximum()));heightRef.current=next;setHeight(next);if(persist)saveSize(`${id}:height`,next)}
  const reset=()=>{heightRef.current=defaultHeight;setHeight(defaultHeight);setCollapsed(false);try{localStorage.removeItem(`${STORAGE_PREFIX}${id}:height`)}catch{}}
  const pointerDown=(event:ReactPointerEvent<HTMLDivElement>)=>{drag.current={y:event.clientY,height:heightRef.current};event.currentTarget.setPointerCapture(event.pointerId);event.preventDefault()}
  const pointerMove=(event:ReactPointerEvent<HTMLDivElement>)=>{if(drag.current)resize(drag.current.height+(event.clientY-drag.current.y))}
  const pointerUp=(event:ReactPointerEvent<HTMLDivElement>)=>{if(!drag.current)return;event.currentTarget.releasePointerCapture(event.pointerId);drag.current=null;resize(heightRef.current,true)}
  const pointerCancel=(event:ReactPointerEvent<HTMLDivElement>)=>{if(!drag.current)return;drag.current=null;if(event.currentTarget.hasPointerCapture(event.pointerId))event.currentTarget.releasePointerCapture(event.pointerId)}
  useEffect(()=>{if(!focused)return;const escape=(event:KeyboardEvent)=>{if(event.key==='Escape')setFocused(false)};window.addEventListener('keydown',escape);return()=>window.removeEventListener('keydown',escape)},[focused])
  return <section className={`ss-height-panel ${collapsed?'is-collapsed':''} ${focused?'is-focused':''} ${className}`} style={{'--ss-panel-height':`${height}px`}as CSSProperties}>
    <div className="ss-height-actions"><button type="button" onClick={()=>setCollapsed(value=>!value)} aria-label={collapsed?'Expand panel':'Collapse panel'} title={collapsed?'Expand':'Collapse'}>{collapsed?'＋':'−'}</button><button type="button" onClick={()=>setFocused(value=>!value)} aria-label={focused?'Exit focus mode':'Focus panel'} title={focused?'Exit focus':'Focus'}>{focused?'↙':'↗'}</button><button type="button" onClick={reset} aria-label="Reset panel size" title="Reset size">↺</button></div>
    <div className="ss-height-content">{children}</div>
    <div className="ss-height-splitter" role="separator" aria-label="Resize panel height" aria-orientation="horizontal" aria-valuemin={minHeight} aria-valuenow={height} tabIndex={0} title="Drag or use arrow keys · double-click to reset" onPointerDown={pointerDown} onPointerMove={pointerMove} onPointerUp={pointerUp} onPointerCancel={pointerCancel} onDoubleClick={reset} onKeyDown={event=>{if(!['ArrowUp','ArrowDown','Home'].includes(event.key))return;event.preventDefault();if(event.key==='Home')reset();else resize(heightRef.current+(event.key==='ArrowDown'?(event.shiftKey?100:32):-(event.shiftKey?100:32)),true)}}><span/></div>
  </section>
}
