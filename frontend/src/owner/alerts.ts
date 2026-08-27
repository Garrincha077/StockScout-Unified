import type{ChartDrawingPayload}from'./chartDrawings'

export type PriceAlertOperator='crossing_up'|'crossing_down'|'crossing'|'touch'|'above'|'below'
export type DrawingAlertOperator='crossing_up'|'crossing_down'|'crossing'|'touch'|'entering'|'exiting'|'inside'|'outside'
export type ScreenAlertOperator='eq'|'ne'|'gt'|'gte'|'lt'|'lte'|'contains'
export type OwnerAlertPayloadV1=
  |{version:1;kind:'price';operator:PriceAlertOperator;price:number}
  |{version:1;kind:'drawing';operator:DrawingAlertOperator;drawing:ChartDrawingPayload}
  |{version:1;kind:'screen';filters:Array<{field:string;op:ScreenAlertOperator;value:string|number|boolean}>}
  |{version:1;kind:'watchlist'}

const PRICE_OPERATORS=new Set<PriceAlertOperator>(['crossing_up','crossing_down','crossing','touch','above','below'])
const DRAWING_OPERATORS=new Set<DrawingAlertOperator>(['crossing_up','crossing_down','crossing','touch','entering','exiting','inside','outside'])
const SCREEN_OPERATORS=new Set<ScreenAlertOperator>(['eq','ne','gt','gte','lt','lte','contains'])

export function normalizeAlertPayload(value:Record<string,unknown>):OwnerAlertPayloadV1|null{
  const kind=String(value.kind??'price')
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
  if(kind==='watchlist')return{version:1,kind}
  return null
}

export function alertSummary(value:Record<string,unknown>){
  const payload=normalizeAlertPayload(value)
  if(!payload)return'Unsupported legacy alert payload'
  if(payload.kind==='price')return`Price ${payload.operator.replaceAll('_',' ')} $${payload.price.toFixed(2)}`
  if(payload.kind==='drawing')return`${payload.drawing.type} · ${payload.operator.replaceAll('_',' ')}`
  if(payload.kind==='screen')return`${payload.filters.length} screen filter${payload.filters.length===1?'':'s'}`
  return'Watchlist membership'
}
