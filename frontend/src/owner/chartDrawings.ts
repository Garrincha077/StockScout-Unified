export type DrawingTool=
  |'cursor'|'trendline'|'ray'|'horizontal'|'vertical'|'rectangle'|'channel'|'fib'|'text'|'measure'

export type DrawingPoint={time:string;price:number}
export type DrawingDash='solid'|'dashed'|'dotted'
export type ChartDrawingPayload={
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

export const DRAWING_TOOLS:readonly DrawingTool[]=[
  'cursor','trendline','ray','horizontal','vertical','rectangle','channel','fib','text','measure',
]
export const DEFAULT_FIB_LEVELS=[0,.236,.382,.5,.618,.786,1] as const

export function isChartDrawingPayload(value:unknown):value is ChartDrawingPayload{
  if(!value||typeof value!=='object'||Array.isArray(value))return false
  const row=value as Record<string,unknown>
  return row.version===1&&DRAWING_TOOLS.includes(row.type as DrawingTool)&&row.type!=='cursor'&&Array.isArray(row.points)&&row.points.every(point=>Boolean(point)&&typeof point==='object'&&!Array.isArray(point)&&typeof(point as Record<string,unknown>).time==='string'&&Number.isFinite(Number((point as Record<string,unknown>).price)))
}

export function newDrawing(type:Exclude<DrawingTool,'cursor'>,points:DrawingPoint[],text?:string):ChartDrawingPayload{
  const normalized=type==='horizontal'
    ?[points[0],{...points.at(-1)!,price:points[0].price}]
    :type==='vertical'
      ?[points[0],{...points.at(-1)!,time:points[0].time}]
      :points
  return{
    version:1,type,points:normalized,color:type==='fib'?'#2dd4bf':'#f0b94f',width:2,dash:'solid',
    text:type==='text'?(text?.trim()||'Note'):undefined,
    fibLevels:type==='fib'?[...DEFAULT_FIB_LEVELS]:undefined,
  }
}

export function moveDrawing(payload:ChartDrawingPayload,barTimes:string[],from:DrawingPoint,to:DrawingPoint):ChartDrawingPayload{
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
