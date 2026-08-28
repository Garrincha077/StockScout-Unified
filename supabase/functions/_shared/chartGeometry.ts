export type GeometryInterval='daily'|'weekly'
export type GeometryPoint={time:string;price:number}
export type GeometryDrawingType='trendline'|'ray'|'horizontal'|'vertical'|'rectangle'|'channel'|'fib'|'text'|'measure'
export type GeometryExtension='none'|'left'|'right'|'both'
export type GeometryDrawing={
  type:GeometryDrawingType
  points:GeometryPoint[]
  extend?:GeometryExtension
  fibLevels?:number[]
}
export type GeometryBar={time:string;open:number;high:number;low:number;close:number}
export type GeometryAlertCondition='crossing_up'|'crossing_down'|'touch'|'entering'|'exiting'|'break_up'|'break_down'
export type GeometryAlertTarget=
  |{kind:'line'}
  |{kind:'zone'}
  |{kind:'channel-boundary';boundary:'upper'|'lower'}
  |{kind:'fib-level';level:number}

export type GeometryEvaluation={
  condition:boolean
  relation:'above'|'below'|'inside'|'outside'|'touching'|'before-anchor'|'invalid'
  previousPrice:number|null
  currentPrice:number|null
  previousLevel:number|null
  currentLevel:number|null
}
export function rearmAfterClear(armed:boolean,condition:boolean){return{triggered:armed&&condition,armed:!condition}}

const DAY_MS=86_400_000

export function utcDay(value:string):number|null{
  if(!/^\d{4}-\d{2}-\d{2}$/.test(value))return null
  const parsed=Date.parse(`${value}T00:00:00.000Z`)
  return Number.isFinite(parsed)?Math.floor(parsed/DAY_MS):null
}

export function utcDayString(day:number):string{
  return new Date(Math.round(day)*DAY_MS).toISOString().slice(0,10)
}

export function logicalIndexAtTime(times:string[],time:string):number|null{
  const target=utcDay(time)
  if(target===null||!times.length)return null
  const days=times.map(utcDay)
  if(days.some(day=>day===null))return null
  const values=days as number[]
  if(values.length===1)return 0
  if(target<=values[0])return(target-values[0])/Math.max(1,values[1]-values[0])
  const last=values.length-1
  if(target>=values[last])return last+(target-values[last])/Math.max(1,values[last]-values[last-1])
  let low=0,high=last
  while(low+1<high){const middle=Math.floor((low+high)/2);if(values[middle]<=target)low=middle;else high=middle}
  return low+(target-values[low])/Math.max(1,values[high]-values[low])
}

export function utcTimeAtLogicalIndex(times:string[],logical:number):string|null{
  if(!times.length||!Number.isFinite(logical))return null
  const days=times.map(utcDay)
  if(days.some(day=>day===null))return null
  const values=days as number[]
  if(values.length===1)return utcDayString(values[0]+logical)
  const last=values.length-1
  if(logical<=0)return utcDayString(values[0]+logical*(values[1]-values[0]))
  if(logical>=last)return utcDayString(values[last]+(logical-last)*(values[last]-values[last-1]))
  const low=Math.floor(logical),high=Math.ceil(logical)
  if(low===high)return utcDayString(values[low])
  return utcDayString(values[low]+(values[high]-values[low])*(logical-low))
}

function baseLineValue(drawing:GeometryDrawing,time:string):number|null{
  const first=drawing.points[0],second=drawing.points[1]
  if(!first||!second)return null
  const at=utcDay(time),start=utcDay(first.time),end=utcDay(second.time)
  if(at===null||start===null||end===null||![first.price,second.price].every(Number.isFinite))return null
  const extension=drawing.extend??(drawing.type==='ray'?'right':drawing.type==='horizontal'?'both':'none')
  const minimum=Math.min(start,end),maximum=Math.max(start,end)
  if((extension==='none'||extension==='right')&&at<minimum)return null
  if((extension==='none'||extension==='left')&&at>maximum)return null
  if(drawing.type==='ray'&&at<start)return null
  if(drawing.type==='horizontal')return first.price
  if(start===end)return first.price
  return first.price+(second.price-first.price)*((at-start)/(end-start))
}

export function lineValueAt(drawing:GeometryDrawing,time:string,boundary:'base'|'upper'|'lower'='base'):number|null{
  const base=baseLineValue(drawing,time)
  if(base===null||drawing.type!=='channel'||boundary==='base')return base
  const second=drawing.points[1],third=drawing.points[2]
  if(!second||!third)return null
  const offset=third.price-second.price
  const other=base+offset
  return boundary==='upper'?Math.max(base,other):Math.min(base,other)
}

export function fibLevelPrice(drawing:GeometryDrawing,level:number):number|null{
  const first=drawing.points[0]?.price,second=drawing.points[1]?.price
  return Number.isFinite(first)&&Number.isFinite(second)&&Number.isFinite(level)?Number(first)+(Number(second)-Number(first))*level:null
}

function invalid():GeometryEvaluation{return{condition:false,relation:'invalid',previousPrice:null,currentPrice:null,previousLevel:null,currentLevel:null}}

export function evaluateDrawingGeometry(drawing:GeometryDrawing,target:GeometryAlertTarget,condition:GeometryAlertCondition,bars:GeometryBar[]):GeometryEvaluation{
  if(bars.length<1||['text','measure','vertical'].includes(drawing.type))return invalid()
  const current=bars.at(-1)!,previous=bars.at(-2)??current
  if(target.kind==='zone'){
    let previousLow:number|null=null,previousHigh:number|null=null,currentLow:number|null=null,currentHigh:number|null=null
    if(drawing.type==='rectangle'){
      const prices=drawing.points.slice(0,2).map(point=>point.price)
      if(prices.length<2||!prices.every(Number.isFinite))return invalid()
      previousLow=currentLow=Math.min(...prices);previousHigh=currentHigh=Math.max(...prices)
    }else if(drawing.type==='channel'){
      previousLow=lineValueAt(drawing,previous.time,'lower');previousHigh=lineValueAt(drawing,previous.time,'upper')
      currentLow=lineValueAt(drawing,current.time,'lower');currentHigh=lineValueAt(drawing,current.time,'upper')
    }else return invalid()
    if([previousLow,previousHigh,currentLow,currentHigh].some(value=>value===null))return{...invalid(),relation:'before-anchor'}
    const before=previous.close>=previousLow!&&previous.close<=previousHigh!,inside=current.close>=currentLow!&&current.close<=currentHigh!
    return{condition:condition==='entering'?inside&&!before:condition==='exiting'?!inside&&before:false,relation:inside?'inside':'outside',previousPrice:previous.close,currentPrice:current.close,previousLevel:previousLow,currentLevel:currentLow}
  }
  let previousLevel:number|null,currentLevel:number|null
  if(target.kind==='fib-level')previousLevel=currentLevel=fibLevelPrice(drawing,target.level)
  else{
    const boundary=target.kind==='channel-boundary'?target.boundary:'base'
    previousLevel=lineValueAt(drawing,previous.time,boundary)
    currentLevel=lineValueAt(drawing,current.time,boundary)
  }
  if(previousLevel===null||currentLevel===null)return{...invalid(),relation:'before-anchor'}
  const previousDelta=previous.close-previousLevel,currentDelta=current.close-currentLevel
  const touching=current.low<=currentLevel&&currentLevel<=current.high
  const isUp=previousDelta<=0&&currentDelta>0,isDown=previousDelta>=0&&currentDelta<0
  const fired=condition==='touch'?touching:condition==='crossing_up'||condition==='break_up'?isUp:condition==='crossing_down'||condition==='break_down'?isDown:false
  return{condition:fired,relation:touching?'touching':currentDelta>0?'above':'below',previousPrice:previous.close,currentPrice:current.close,previousLevel,currentLevel}
}
