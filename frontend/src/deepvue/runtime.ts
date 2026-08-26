export const GRID_STEP=16
export const CHART_SHARD_COUNT=128

export function marketRegimeLabel(market:unknown,fallback='UNKNOWN'){
  if(!market||typeof market!=='object')return fallback
  const regime=(market as Record<string,unknown>).regime
  const values=typeof regime==='string'
    ?[regime]
    :regime&&typeof regime==='object'
      ?['state','label','summary','guppy_state'].map(key=>(regime as Record<string,unknown>)[key])
      :[]
  const label=values.find(value=>typeof value==='string'&&value.trim())
  return typeof label==='string'?label.trim().replaceAll('_',' ').toUpperCase():fallback
}

export type LegacyConfirmationStatus='CONFIRMED'|'EARLY'|'NEUTRAL'|'CONFLICT'|'RISK'|'UNAVAILABLE'
export type LegacyConfirmationSidecar={
  affectsStockScout:false
  source?:{generatedAt?:string|null}
  byTicker?:Record<string,{status:LegacyConfirmationStatus;available:boolean;reasons?:string[]}>
}

export function mergeLegacyConfirmationSidecar<T extends {ticker:string}>(
  universe:T[],
  sidecar:LegacyConfirmationSidecar|null|undefined,
  generatedAt:string,
):Array<T&{legacyConfirmationStatus?:LegacyConfirmationStatus;legacyConfirmationReasons?:string[]}>
{
  if(!sidecar||sidecar.affectsStockScout!==false)return universe
  if(!sidecar.source?.generatedAt||sidecar.source.generatedAt!==generatedAt)return universe
  const entries=sidecar.byTicker||{}
  return universe.map(stock=>{
    const confirmation=entries[stock.ticker.toUpperCase()]
    if(!confirmation)return stock
    return {
      ...stock,
      legacyConfirmationStatus:confirmation.status,
      legacyConfirmationReasons:[...(confirmation.reasons||[])],
    }
  })
}

export function chartShardFor(ticker:string,shardCount=CHART_SHARD_COUNT){
  const normalized=ticker.trim().toUpperCase()
  let value=0
  for(let i=0;i<normalized.length;i++)value+=(i+1)*normalized.charCodeAt(i)
  return `${String(value%Math.max(1,Math.floor(shardCount))).padStart(3,'0')}.json`
}

export function nextGridCount(current:number,total:number,step=GRID_STEP){
  const safeTotal=Math.max(0,Math.floor(total))
  const safeCurrent=Math.max(0,Math.floor(current))
  const safeStep=Math.max(1,Math.floor(step))
  return Math.min(safeTotal,safeCurrent+safeStep)
}

type FetchLike=(input:RequestInfo|URL,init?:RequestInit)=>Promise<Response>
type SleepLike=(ms:number)=>Promise<void>

type RetryOptions={
  attempts?:number
  baseDelayMs?:number
  cache?:RequestCache
}

const sleep:SleepLike=(ms)=>new Promise(resolve=>setTimeout(resolve,ms))

function cacheBust(url:string,attempt:number){
  const join=url.includes('?')?'&':'?'
  return `${url}${join}_cb=${Date.now()}-${attempt}`
}

export async function fetchJsonWithRetry<T>(
  url:string,
  options:RetryOptions={},
  fetcher:FetchLike=fetch,
  sleeper:SleepLike=sleep,
):Promise<T>{
  const attempts=Math.max(1,Math.floor(options.attempts??3))
  const baseDelayMs=Math.max(0,Math.floor(options.baseDelayMs??250))
  const cacheMode=options.cache??'default'
  let lastError:unknown=new Error('request failed')
  for(let attempt=1;attempt<=attempts;attempt++){
    try{
      // Versioned successful shards remain cacheable. Only a retry after an
      // observed failure bypasses a potentially stale browser/CDN response.
      const isRetry=attempt>1
      const requestUrl=isRetry?cacheBust(url,attempt):url
      const response=await fetcher(requestUrl,{cache:isRetry?'no-store':cacheMode})
      if(!response.ok)throw new Error(`HTTP ${response.status}`)
      return await response.json() as T
    }catch(error){
      lastError=error
      if(attempt<attempts&&baseDelayMs>0)await sleeper(baseDelayMs*attempt)
    }
  }
  throw lastError
}

export class RetryJsonCache<T>{
  private pending=new Map<string,Promise<T>>()
  private fetcher:FetchLike
  private sleeper:SleepLike
  private options:RetryOptions

  constructor(
    fetcher:FetchLike=(input,init)=>fetch(input,init),
    sleeper:SleepLike=sleep,
    options:RetryOptions={attempts:3,baseDelayMs:250,cache:'default'},
  ){
    this.fetcher=fetcher
    this.sleeper=sleeper
    this.options=options
  }

  load(key:string,url:string):Promise<T>{
    const existing=this.pending.get(key)
    if(existing)return existing
    const request=fetchJsonWithRetry<T>(url,this.options,this.fetcher,this.sleeper).catch(error=>{
      this.pending.delete(key)
      throw error
    })
    this.pending.set(key,request)
    return request
  }

  clear(){this.pending.clear()}
  delete(key:string){this.pending.delete(key)}
}
