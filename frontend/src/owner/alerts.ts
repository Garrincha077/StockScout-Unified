import type{ChartDrawingPayload}from'./chartDrawings'
import type{GeometryAlertCondition,GeometryAlertTarget}from'../../../supabase/functions/_shared/chartGeometry.ts'

export type PriceAlertOperator='crossing_up'|'crossing_down'|'crossing'|'touch'|'above'|'below'
export type DrawingAlertOperator='crossing_up'|'crossing_down'|'crossing'|'touch'|'entering'|'exiting'|'inside'|'outside'
export type ScreenAlertOperator='eq'|'ne'|'gt'|'gte'|'lt'|'lte'|'contains'
export type IndicatorDirection='up'|'down'|'either'
export type IndicatorSignal='daily_ema_10_20'|'weekly_sma_10_20'
export type IndicatorConfirmation='sma50_daily_up'|'sma30_weekly_up'
export type OwnerIndicatorAlertPayloadV1={
  version:1
  kind:'indicator'
  signal:IndicatorSignal
  direction:IndicatorDirection
  confirmations:{mode:'all'|'any';conditions:IndicatorConfirmation[]}
  evaluationInterval:'daily'|'weekly'
  rearm:'after_clear'
}
export type OwnerAlertPayloadV1=
  |{version:1;kind:'price';operator:PriceAlertOperator;price:number}
  |{version:1;kind:'drawing';operator:DrawingAlertOperator;drawing:ChartDrawingPayload}
  |{version:1;kind:'screen';filters:Array<{field:string;op:ScreenAlertOperator;value:string|number|boolean}>}
  |{version:1;kind:'watchlist'}
  |OwnerIndicatorAlertPayloadV1
export type OwnerDrawingAlertPayloadV2={version:2;kind:'drawing';drawingId:string;condition:GeometryAlertCondition;target:GeometryAlertTarget;evaluationInterval:'daily';rearm:'after_clear'}
export type OwnerAlertPayloadV2=OwnerAlertPayloadV1|OwnerDrawingAlertPayloadV2

const PRICE_OPERATORS=new Set<PriceAlertOperator>(['crossing_up','crossing_down','crossing','touch','above','below'])
const DRAWING_OPERATORS=new Set<DrawingAlertOperator>(['crossing_up','crossing_down','crossing','touch','entering','exiting','inside','outside'])
const SCREEN_OPERATORS=new Set<ScreenAlertOperator>(['eq','ne','gt','gte','lt','lte','contains'])
const INDICATOR_SIGNALS=new Set<IndicatorSignal>(['daily_ema_10_20','weekly_sma_10_20'])
const INDICATOR_DIRECTIONS=new Set<IndicatorDirection>(['up','down','either'])
const INDICATOR_CONFIRMATIONS=new Set<IndicatorConfirmation>(['sma50_daily_up','sma30_weekly_up'])

export function normalizeAlertPayload(value:Record<string,unknown>):OwnerAlertPayloadV2|null{
  const kind=String(value.kind??'price')
  if(value.version===2&&kind==='drawing'){
    const drawingId=String(value.drawingId??''),condition=String(value.condition??'touch')as GeometryAlertCondition,target=value.target
    if(/^[0-9a-f-]{36}$/i.test(drawingId)&&['crossing_up','crossing_down','touch','entering','exiting','break_up','break_down'].includes(condition)&&target&&typeof target==='object'&&!Array.isArray(target))return{version:2,kind:'drawing',drawingId,condition,target:target as GeometryAlertTarget,evaluationInterval:'daily',rearm:'after_clear'}
    return null
  }
  if(kind==='price'){
    const operator=String(value.operator??'crossing_up')as PriceAlertOperator,price=Number(value.price)
    return PRICE_OPERATORS.has(operator)&&Number.isFinite(price)&&price>0?{version:1,kind,operator,price}:null
  }
  if(kind==='drawing'){
    const operator=String(value.operator??'crossing')as DrawingAlertOperator,drawing=value.drawing
    return DRAWING_OPERATORS.has(operator)&&drawing&&typeof drawing==='object'&&!Array.isArray(drawing)?{version:1,kind,operator,drawing:drawing as ChartDrawingPayload}:null
  }
  if(kind==='screen'&&Array.isArray(value.filters)){
    const filters=value.filters.slice(0,12).flatMap(item=>{
      if(!item||typeof item!=='object'||Array.isArray(item))return[]
      const row=item as Record<string,unknown>,field=String(row.field??'').trim(),op=String(row.op??'eq')as ScreenAlertOperator,next=row.value
      return field&&SCREEN_OPERATORS.has(op)&&(typeof next==='string'||typeof next==='number'||typeof next==='boolean')?[{field,op,value:next}]:[]
    })
    return filters.length?{version:1,kind,filters}:null
  }
  if(kind==='indicator'){
    const signal=String(value.signal??'')as IndicatorSignal
    const direction=String(value.direction??'up')as IndicatorDirection
    const evaluationInterval=String(value.evaluationInterval??(signal==='weekly_sma_10_20'?'weekly':'daily'))as'daily'|'weekly'
    const rearm=String(value.rearm??'after_clear')
    const raw=value.confirmations
    const confirmationObject=raw&&typeof raw==='object'&&!Array.isArray(raw)?raw as Record<string,unknown>:{}
    const mode=String(confirmationObject.mode??'all')as'all'|'any'
    const conditions=Array.isArray(confirmationObject.conditions)
      ?[...new Set(confirmationObject.conditions.map(item=>String(item)as IndicatorConfirmation))].filter(item=>INDICATOR_CONFIRMATIONS.has(item))
      :[]
    const expectedInterval=signal==='weekly_sma_10_20'?'weekly':'daily'
    if(INDICATOR_SIGNALS.has(signal)&&INDICATOR_DIRECTIONS.has(direction)&&evaluationInterval===expectedInterval&&rearm==='after_clear'&&conditions.length===(Array.isArray(confirmationObject.conditions)?new Set(confirmationObject.conditions).size:0)&&['all','any'].includes(mode))return{version:1,kind:'indicator',signal,direction,confirmations:{mode,conditions},evaluationInterval,rearm:'after_clear'}
    return null
  }
  if(kind==='watchlist')return{version:1,kind}
  return null
}

export function alertSummary(value:Record<string,unknown>){
  const payload=normalizeAlertPayload(value)
  if(!payload)return'Unsupported legacy alert payload'
  if(payload.kind==='price')return`Price ${payload.operator.replaceAll('_',' ')} $${payload.price.toFixed(2)}`
  if(payload.kind==='drawing')return payload.version===2?`Linked drawing · ${payload.condition.replaceAll('_',' ')}`:`Detached ${payload.drawing.type} · ${payload.operator.replaceAll('_',' ')}`
  if(payload.kind==='screen')return`${payload.filters.length} screen filter${payload.filters.length===1?'':'s'}`
  if(payload.kind==='indicator'){
    const signal=payload.signal==='daily_ema_10_20'?'10/20 EMA · Daily':'10/20 SMA · Weekly'
    const direction=payload.direction==='either'?'cross':`${payload.direction} cross`
    const confirmations=payload.confirmations.conditions.map(condition=>condition==='sma50_daily_up'?'50D SMA ↑':'30W SMA ↑')
    return`${signal} ${direction}${confirmations.length?` · ${confirmations.join(payload.confirmations.mode==='all'?' + ':' / ')}`:''}`
  }
  return'Watchlist membership'
}
