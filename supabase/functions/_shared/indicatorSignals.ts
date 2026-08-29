export type IndicatorBar={time:string;close:number}
export type IndicatorDirection='up'|'down'|'either'
export type IndicatorSignal='daily_ema_10_20'|'weekly_sma_10_20'
export type IndicatorConfirmation='sma50_daily_up'|'sma30_weekly_up'

export type IndicatorSeries={
  daily:{bars:IndicatorBar[];ema10:Array<number|null>;ema20:Array<number|null>;sma50:Array<number|null>;sma200:Array<number|null>}
  weekly:{bars:IndicatorBar[];sma10:Array<number|null>;sma20:Array<number|null>;sma30:Array<number|null>;sma50:Array<number|null>;sma200:Array<number|null>}
}

const DAY_MS=86_400_000

function finiteBars(bars:readonly IndicatorBar[]):IndicatorBar[]{
  return bars
    .filter(bar=>Boolean(bar)&&typeof bar.time==='string'&&Number.isFinite(Number(bar.close)))
    .map(bar=>({time:bar.time.slice(0,10),close:Number(bar.close)}))
    .filter(bar=>/^\d{4}-\d{2}-\d{2}$/.test(bar.time))
    .sort((left,right)=>left.time.localeCompare(right.time))
}

export function weekStartUtc(value:string):string|null{
  if(!/^\d{4}-\d{2}-\d{2}$/.test(value))return null
  const parsed=Date.parse(`${value}T00:00:00.000Z`)
  if(!Number.isFinite(parsed))return null
  const day=new Date(parsed).getUTCDay()
  const mondayOffset=(day+6)%7
  return new Date(parsed-mondayOffset*DAY_MS).toISOString().slice(0,10)
}

export function aggregateWeeklyBars(bars:readonly IndicatorBar[]):IndicatorBar[]{
  const output:IndicatorBar[]=[]
  for(const bar of finiteBars(bars)){
    const time=weekStartUtc(bar.time)
    if(!time)continue
    const last=output.at(-1)
    if(!last||last.time!==time)output.push({time,close:bar.close})
    else last.close=bar.close
  }
  return output
}

export function simpleMovingAverage(values:readonly number[],period:number):(number|null)[]{
  const output:Array<number|null>=[]
  if(!Number.isInteger(period)||period<1)return values.map(()=>null)
  let sum=0
  values.forEach((value,index)=>{
    sum+=value
    if(index>=period)sum-=values[index-period]
    output.push(index+1>=period?sum/period:null)
  })
  return output
}

/** EMA seeded with the first complete SMA window for deterministic replay. */
export function exponentialMovingAverage(values:readonly number[],period:number):(number|null)[]{
  const output:Array<number|null>=values.map(()=>null)
  if(!Number.isInteger(period)||period<1||values.length<period)return output
  const seed=values.slice(0,period).reduce((total,value)=>total+value,0)/period
  let current=seed
  output[period-1]=current
  const weight=2/(period+1)
  for(let index=period;index<values.length;index++){
    current=values[index]*weight+current*(1-weight)
    output[index]=current
  }
  return output
}

export function linearRegressionSlope(values:readonly number[]):number|null{
  if(values.length<2||values.some(value=>!Number.isFinite(value)))return null
  const count=values.length,meanX=(count-1)/2,meanY=values.reduce((sum,value)=>sum+value,0)/count
  let numerator=0,denominator=0
  values.forEach((value,index)=>{const dx=index-meanX;numerator+=dx*(value-meanY);denominator+=dx*dx})
  return denominator===0?null:numerator/denominator
}

export function normalizedSlope(values:readonly number[],lookback:number):number|null{
  if(!Number.isInteger(lookback)||lookback<2||values.length<lookback)return null
  const window=values.slice(-lookback),slope=linearRegressionSlope(window),baseline=Math.abs(window.at(-1)??0)
  return slope===null||baseline===0?null:slope/baseline
}

export function isUpsloping(values:readonly number[],lookback:number):boolean|null{
  const slope=normalizedSlope(values,lookback)
  return slope===null?null:slope>0
}

export function crossed(previousFast:number|null,previousSlow:number|null,currentFast:number|null,currentSlow:number|null,direction:IndicatorDirection):boolean{
  if([previousFast,previousSlow,currentFast,currentSlow].some(value=>value===null||!Number.isFinite(value)))return false
  const previousDelta=(previousFast as number)-(previousSlow as number),currentDelta=(currentFast as number)-(currentSlow as number)
  const up=previousDelta<=0&&currentDelta>0,down=previousDelta>=0&&currentDelta<0
  return direction==='up'?up:direction==='down'?down:up||down
}

export function buildIndicatorSeries(input:readonly IndicatorBar[]):IndicatorSeries{
  const daily=finiteBars(input),dailyCloses=daily.map(bar=>bar.close),weekly=aggregateWeeklyBars(daily),weeklyCloses=weekly.map(bar=>bar.close)
  return{
    daily:{bars:daily,ema10:exponentialMovingAverage(dailyCloses,10),ema20:exponentialMovingAverage(dailyCloses,20),sma50:simpleMovingAverage(dailyCloses,50),sma200:simpleMovingAverage(dailyCloses,200)},
    weekly:{bars:weekly,sma10:simpleMovingAverage(weeklyCloses,10),sma20:simpleMovingAverage(weeklyCloses,20),sma30:simpleMovingAverage(weeklyCloses,30),sma50:simpleMovingAverage(weeklyCloses,50),sma200:simpleMovingAverage(weeklyCloses,200)},
  }
}

export function confirmationsPass(series:IndicatorSeries,conditions:readonly IndicatorConfirmation[],mode:'all'|'any'='all'):boolean|null{
  if(!conditions.length)return true
  const values=conditions.map(condition=>condition==='sma50_daily_up'
    ?isUpsloping(series.daily.sma50.filter((value):value is number=>value!==null),20)
    :isUpsloping(series.weekly.sma30.filter((value):value is number=>value!==null),8))
  if(values.some(value=>value===null))return null
  return mode==='any'?values.some(Boolean):values.every(Boolean)
}
