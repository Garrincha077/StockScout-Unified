export type SortRule={id:string;desc:boolean}
type SortableStock={ticker:string;[key:string]:any}

const EMA_FRESH_SORT='ema10d20dFresh'
const SMA_FRESH_SORT='sma10w20wFresh'

function opportunity(stock:SortableStock){return stock.opportunityScore??stock.score??0}
function setup(stock:SortableStock){return stock.primarySetup||stock.setup||stock.stageName||'Other'}

function sortValue(stock:SortableStock,id:string):any{
  if(id===EMA_FRESH_SORT){const age=stock.ema10d20dCrossAge,cross=stock.ema10d20dCross;return typeof age==='number'&&cross?-age+(cross==='BULL'?0.25:0):-1e9}
  if(id===SMA_FRESH_SORT){const age=stock.sma10w20wCrossAge,cross=stock.sma10w20wCross;return typeof age==='number'&&cross?-age+(cross==='BULL'?0.25:0):-1e9}
  if(id==='opportunityScore')return opportunity(stock)
  if(id==='primarySetup')return setup(stock)
  return stock[id]
}

function compareValues(a:any,b:any):number{
  const am=a==null||(typeof a==='number'&&!Number.isFinite(a)),bm=b==null||(typeof b==='number'&&!Number.isFinite(b))
  if(am&&bm)return 0
  if(am)return 1
  if(bm)return-1
  if(typeof a==='number'&&typeof b==='number')return a-b
  if(typeof a==='boolean'&&typeof b==='boolean')return Number(a)-Number(b)
  return String(a).localeCompare(String(b),undefined,{numeric:true,sensitivity:'base'})
}

function prioritySort<T extends SortableStock>(rows:T[],sorting:SortRule[]){
  return rows.map((stock,index)=>({stock,index})).sort((a,b)=>{
    for(const rule of sorting){
      const comparison=compareValues(sortValue(a.stock,rule.id),sortValue(b.stock,rule.id))
      if(comparison!==0)return rule.desc?-comparison:comparison
    }
    return a.stock.ticker.localeCompare(b.stock.ticker)||a.index-b.index
  }).map(item=>item.stock)
}

export function applyMultiSort<T extends SortableStock>(rows:T[],sorting:SortRule[]):T[]{
  if(!sorting.length)return rows
  if(sorting.length===1)return prioritySort(rows,sorting)
  const accum=new Map<string,{log:number;arith:number;weight:number}>()
  rows.forEach(stock=>accum.set(stock.ticker,{log:0,arith:0,weight:0}))
  let activeCriteria=0
  sorting.forEach((rule,sortIndex)=>{
    const values=rows.map((stock,index)=>({stock,index,value:sortValue(stock,rule.id)})).filter(item=>typeof item.value==='number'&&Number.isFinite(item.value)) as {stock:T;index:number;value:number}[]
    if(values.length<2)return
    activeCriteria++
    values.sort((a,b)=>(rule.desc?b.value-a.value:a.value-b.value)||(a.index-b.index))
    const percentile=new Map<string,number>()
    let index=0
    while(index<values.length){
      let end=index+1
      while(end<values.length&&values[end].value===values[index].value)end++
      const averagePosition=(index+end-1)/2
      const score=100-(averagePosition/(values.length-1))*99
      for(let cursor=index;cursor<end;cursor++)percentile.set(values[cursor].stock.ticker,score)
      index=end
    }
    const weight=Math.max(.55,1-sortIndex*.10)
    rows.forEach(stock=>{
      const score=Math.max(1,percentile.get(stock.ticker)??1)
      const aggregate=accum.get(stock.ticker)!
      aggregate.log+=Math.log(score)*weight
      aggregate.arith+=score*weight
      aggregate.weight+=weight
    })
  })
  if(activeCriteria<2)return prioritySort(rows,sorting)
  const scores=new Map<string,{mix:number;average:number}>()
  rows.forEach(stock=>{
    const aggregate=accum.get(stock.ticker)!
    scores.set(stock.ticker,{
      mix:aggregate.weight?Math.exp(aggregate.log/aggregate.weight):0,
      average:aggregate.weight?aggregate.arith/aggregate.weight:0,
    })
  })
  return rows.map((stock,index)=>({stock,index})).sort((a,b)=>{
    const left=scores.get(a.stock.ticker)!,right=scores.get(b.stock.ticker)!
    const mix=right.mix-left.mix
    if(Math.abs(mix)>1e-9)return mix
    const average=right.average-left.average
    if(Math.abs(average)>1e-9)return average
    const first=sorting[0]
    const comparison=compareValues(sortValue(a.stock,first.id),sortValue(b.stock,first.id))
    if(comparison!==0)return first.desc?-comparison:comparison
    return a.stock.ticker.localeCompare(b.stock.ticker)||a.index-b.index
  }).map(item=>item.stock)
}
