export type DrawingTool=
  |'cursor'|'trendline'|'ray'|'horizontal'|'vertical'|'rectangle'|'channel'|'fib'|'text'|'measure'

import{utcDay,utcDayString,type GeometryExtension,type GeometryInterval,type GeometryPoint}from'../../../supabase/functions/_shared/chartGeometry.ts'

export type DrawingPoint=GeometryPoint
export type DrawingDash='solid'|'dashed'|'dotted'
export type ChartDrawingPayloadV1={
  version:1
  type:Exclude<DrawingTool,'cursor'>
  points:DrawingPoint[]
  color:string
  width:number
  dash:DrawingDash
  text?:string
  locked?:boolean
  hidden?:boolean
  fibLevels?:number[]
}
export type ChartDrawingPayloadV2={
  version:2
  type:Exclude<DrawingTool,'cursor'>
  points:DrawingPoint[]
  color:string
  width:number
  dash:DrawingDash
  sourceInterval:GeometryInterval
  visibleIntervals:GeometryInterval[]
  xBasis:'utc-day'
  extend:GeometryExtension
  text?:string
  locked?:boolean
  hidden?:boolean
  fibLevels?:number[]
}
export type ChartDrawingPayload=ChartDrawingPayloadV1|ChartDrawingPayloadV2

export const DRAWING_TOOLS:readonly DrawingTool[]=[
  'cursor','trendline','ray','horizontal','vertical','rectangle','channel','fib','text','measure',
]
export const DEFAULT_FIB_LEVELS=[0,.236,.382,.5,.618,.786,1] as const

export function isChartDrawingPayload(value:unknown):value is ChartDrawingPayload{
  if(!value||typeof value!=='object'||Array.isArray(value))return false
  const row=value as Record<string,unknown>
  return(row.version===1||row.version===2)&&DRAWING_TOOLS.includes(row.type as DrawingTool)&&row.type!=='cursor'&&Array.isArray(row.points)&&row.points.every(point=>Boolean(point)&&typeof point==='object'&&!Array.isArray(point)&&typeof(point as Record<string,unknown>).time==='string'&&Number.isFinite(Number((point as Record<string,unknown>).price)))
}

const extensionFor=(type:Exclude<DrawingTool,'cursor'>):GeometryExtension=>type==='ray'?'right':['trendline','horizontal','channel'].includes(type)?'both':'none'

export function normalizeChartDrawingPayload(value:unknown,legacyInterval:GeometryInterval='daily'):ChartDrawingPayloadV2|null{
  if(!isChartDrawingPayload(value))return null
  if(value.version===2)return{...value,sourceInterval:value.sourceInterval==='weekly'?'weekly':'daily',visibleIntervals:value.visibleIntervals?.filter(interval=>interval==='daily'||interval==='weekly').length?value.visibleIntervals.filter(interval=>interval==='daily'||interval==='weekly'):['daily','weekly'],xBasis:'utc-day',extend:value.extend??extensionFor(value.type)}
  return{...value,version:2,sourceInterval:legacyInterval,visibleIntervals:['daily','weekly'],xBasis:'utc-day',extend:extensionFor(value.type)}
}

export function newDrawing(type:Exclude<DrawingTool,'cursor'>,points:DrawingPoint[],text?:string,sourceInterval:GeometryInterval='daily'):ChartDrawingPayloadV2{
  const normalized=type==='horizontal'
    ?[points[0],{...points.at(-1)!,price:points[0].price}]
    :type==='vertical'
      ?[points[0],{...points.at(-1)!,time:points[0].time}]
      :points
  return{
    version:2,type,points:normalized,color:type==='fib'?'#2dd4bf':'#f0b94f',width:2,dash:'solid',sourceInterval,visibleIntervals:['daily','weekly'],xBasis:'utc-day',extend:extensionFor(type),
    text:type==='text'?(text?.trim()||'Note'):undefined,
    fibLevels:type==='fib'?[...DEFAULT_FIB_LEVELS]:undefined,
  }
}

export function moveDrawing(payload:ChartDrawingPayloadV2,barTimes:string[],from:DrawingPoint,to:DrawingPoint):ChartDrawingPayloadV2{
  const fromIndex=barTimes.indexOf(from.time),toIndex=barTimes.indexOf(to.time)
  const offset=(fromIndex>=0&&toIndex>=0)?toIndex-fromIndex:0
  const priceOffset=to.price-from.price
  return{
    ...payload,
    points:payload.points.map(point=>{
      const index=barTimes.indexOf(point.time)
      const moved=index<0?point.time:barTimes[Math.max(0,Math.min(barTimes.length-1,index+offset))]
      return{time:moved,price:point.price+priceOffset}
    }),
  }
}

export function moveDrawingPoint(payload:ChartDrawingPayloadV2,index:number,to:DrawingPoint):ChartDrawingPayloadV2{
  if(index<0||index>=payload.points.length)return payload
  const points=payload.points.map((point,pointIndex)=>pointIndex===index?to:point)
  if(payload.type==='horizontal'&&index===0&&points[1])points[1]={...points[1],price:to.price}
  if(payload.type==='vertical'&&index===0&&points[1])points[1]={...points[1],time:to.time}
  return{...payload,points}
}

export function duplicateDrawing(payload:ChartDrawingPayloadV2):ChartDrawingPayloadV2{
  return{...payload,points:payload.points.map(point=>{const day=utcDay(point.time);return{time:day===null?point.time:utcDayString(day+2),price:point.price}})}
}
