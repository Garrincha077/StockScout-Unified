import {createContext,useCallback,useContext,useEffect,useMemo,useState,type ReactNode} from 'react'
import type {ReviewScope} from '../phase4Review'
import {useMode,type ModeId} from '../modes/ModeProvider'
import {chartPath,chartRows,chartShard,chartShardDescriptor,validateChartManifest,type ChartManifest} from './chartPayload'
import {
  detailShardFor,isManifestV1,normalizeCore,parseManifest,parseUnifiedManifest,
  type AssetDescriptor,type CandidateCoreV1,type CandidateDetailV1,
  type ScanHistoryItemV1,type StockScoutManifest,
} from './contracts'

export type {AssetDescriptor,CandidateDetailV1,CandidateSummaryV1,ScanHistoryItemV1,StockScoutManifest} from './contracts'
export type StockScoutRow={ticker:string;[key:string]:any}
export type StockScoutCore=CandidateCoreV1
export type LegacyIndex={generatedAt:string;market:Record<string,any>;layers?:Record<string,any>;universe:StockScoutRow[];[key:string]:any}
export type ChartState=
  |{status:'ready';rows:any[]}
  |{status:'unavailable';rows:[];reason?:'missing'}
  |{status:'error';rows:[];error:string}

type LoadOptions={cache?:RequestCache;force?:boolean;cacheBust?:boolean}
type FetchLike=(input:RequestInfo|URL,init?:RequestInit)=>Promise<Response>

export class JsonPromiseCache{
  private pending=new Map<string,Promise<unknown>>()
  constructor(private fetcher:FetchLike=(input,init)=>fetch(input,init)){}

  load<T>(key:string,url:string,options:LoadOptions={}):Promise<T>{
    if(options.force)this.pending.delete(key)
    const existing=this.pending.get(key)
    if(existing)return existing as Promise<T>
    const requestUrl=options.cacheBust?`${url}${url.includes('?')?'&':'?'}retry=${Date.now()}`:url
    const request=this.fetcher(requestUrl,{cache:options.cache??'default'})
      .then(response=>{
        if(!response.ok)throw new Error(`HTTP ${response.status}`)
        if(!response.headers.get('content-type')?.includes('json'))throw new Error('Published scan data is not available yet')
        return response.json() as Promise<T>
      })
      .catch(error=>{this.pending.delete(key);throw error})
    this.pending.set(key,request)
    return request
  }

  delete(key:string){this.pending.delete(key)}
  clear(){this.pending.clear()}
}

export const sharedDataCache=new JsonPromiseCache()
const chartShardCache=new Map<string,Promise<unknown>>()
const contextAssetCache=new Map<string,Promise<unknown>>()

async function sha256Hex(payload:ArrayBuffer){
  const digest=await crypto.subtle.digest('SHA-256',payload)
  return Array.from(new Uint8Array(digest),byte=>byte.toString(16).padStart(2,'0')).join('')
}
async function decodeGzipJson(payload:ArrayBuffer){
  if(typeof DecompressionStream==='undefined')throw new Error('This browser cannot decompress chart data')
  const stream=new Blob([payload]).stream().pipeThrough(new DecompressionStream('gzip'))
  return JSON.parse(await new Response(stream).text())
}
function loadChartShard(manifest:ChartManifest,shard:string,retry:boolean){
  const descriptor=chartShardDescriptor(manifest,shard)
  if(!descriptor)return Promise.reject(new Error('Chart shard is missing from its manifest'))
  const key=`${manifest.runId}:${shard}:${descriptor.sha256}`
  if(retry)chartShardCache.delete(key)
  const existing=chartShardCache.get(key)
  if(existing)return existing
  const request=fetch(`${chartPath(manifest,shard)}${retry?`?retry=${Date.now()}`:''}`,{cache:retry?'no-store':'default'})
    .then(async response=>{
      if(!response.ok)throw new Error(`Chart request failed with HTTP ${response.status}`)
      const payload=await response.arrayBuffer()
      if(payload.byteLength!==descriptor.bytes)throw new Error('Chart shard byte count does not match its manifest')
      if(await sha256Hex(payload)!==descriptor.sha256)throw new Error('Chart shard hash does not match its manifest')
      return decodeGzipJson(payload)
    })
    .catch(error=>{chartShardCache.delete(key);throw error})
  chartShardCache.set(key,request)
  return request
}

const initialPath=location.pathname
const tickerRouteIndex=initialPath.toLowerCase().indexOf('/ticker/')
const APP_ROOT=tickerRouteIndex>=0?`${initialPath.slice(0,tickerRouteIndex)}/`:initialPath.endsWith('/')?initialPath:initialPath.replace(/[^/]+$/,'')
function dataUrl(mode:ModeId,path:string){
  const root=new URL(`${APP_ROOT}data/modes/${mode}/`,location.origin)
  return new URL(path.replace(/^\.?\/?data\//,'').replace(/^\//,''),root).toString()
}
function unifiedDataUrl(path:string){return new URL(`${APP_ROOT}data/${path.replace(/^\//,'')}`,location.origin).toString()}
function versionedUrl(mode:ModeId,asset:AssetDescriptor,path=asset.path){return`${dataUrl(mode,path)}?v=${encodeURIComponent(asset.sha256)}`}
function tickerUrl(ticker:string,manifest:StockScoutManifest|null,mode:ModeId){
  const query=new URLSearchParams(location.search)
  query.delete('ticker')
  query.set('mode',mode)
  if(mode!=='next')query.delete('view')
  if(manifest&&isManifestV1(manifest))query.set('run',manifest.runId)
  return`${APP_ROOT}ticker/${encodeURIComponent(ticker)}?${query}`
}
function initialTicker(){
  const route=location.pathname.match(/\/ticker\/([^/]+)/i)?.[1]
  const query=new URLSearchParams(location.search).get('ticker')
  const hash=location.hash.replace(/^#/,'')
  return decodeURIComponent(route||query||hash||'').trim().toUpperCase()
}
function detailFromPayload(value:unknown,ticker:string):StockScoutRow|null{
  if(!value||typeof value!=='object')return null
  const payload=value as Record<string,any>
  const row=(payload.ticker===ticker?payload:null)??payload[ticker]??payload.byTicker?.[ticker]??payload.candidates?.find?.((item:any)=>item?.ticker===ticker)
  return row&&typeof row==='object'?row as StockScoutRow:null
}

function retainValidatedScan(urls:string[]){
  if(!('serviceWorker'in navigator))return
  navigator.serviceWorker.ready
    .then(registration=>registration.active?.postMessage({type:'CACHE_SCAN',urls}))
    .catch(()=>undefined)
}
function expandCompactFundamentals(core:StockScoutCore):StockScoutCore{
  return{...core,universe:core.universe.map(row=>{
    const dims=row.fundamentalDims
    if(!Array.isArray(dims))return row
    return{...row,
      fundamentalGrowthScore:row.fundamentalGrowthScore??dims[0]??null,
      fundamentalMarginScore:row.fundamentalMarginScore??dims[1]??null,
      fundamentalInventoryScore:row.fundamentalInventoryScore??dims[2]??null,
    }
  })}
}

type DataContextValue={
  manifest:StockScoutManifest|null
  core:StockScoutCore|null
  loading:boolean
  error:string
  selectedTicker:string
  selectTicker:(ticker:string)=>void
  reviewScope:ReviewScope
  setReviewScope:(scope:ReviewScope)=>void
  reload:()=>void
  loadCandidateDetail:(ticker:string,force?:boolean)=>Promise<CandidateDetailV1|null>
  loadExcluded:()=>Promise<StockScoutRow[]>
  loadHistory:()=>Promise<ScanHistoryItemV1[]>
  loadLegacyIndex:()=>Promise<LegacyIndex>
  loadLegacyDetail:(ticker:string,force?:boolean)=>Promise<StockScoutRow|null>
  loadChart:(ticker:string,retry?:boolean)=>Promise<ChartState>
  loadContextAsset:<T>(kind:'factorRegime'|'gmliContext'|'bottomScreener',retry?:boolean)=>Promise<T>
  loadOptional:<T>(path:string)=>Promise<T|null>
}

const DataContext=createContext<DataContextValue|null>(null)

export function StockScoutDataProvider({children}:{children:ReactNode}){
  const{mode}=useMode()
  const[manifest,setManifest]=useState<StockScoutManifest|null>(null)
  const[core,setCore]=useState<StockScoutCore|null>(null)
  const[loading,setLoading]=useState(true)
  const[error,setError]=useState('')
  const[selectedTicker,setSelectedTicker]=useState(initialTicker)
  const[reviewScope,setReviewScope]=useState<ReviewScope>(null)
  const[revision,setRevision]=useState(0)

  useEffect(()=>{
    let live=true
    setLoading(true)
    ;(async()=>{
      const unifiedUrl=unifiedDataUrl('manifest.json')
      const unified=parseUnifiedManifest(await sharedDataCache.load<unknown>('unified-manifest',unifiedUrl,{cache:'no-cache',force:true}))
      const pointer=unified.modes[mode]
      const manifestUrl=dataUrl(mode,'manifest.json')
      const manifestResponse=await fetch(manifestUrl,{cache:'no-cache'})
      if(!manifestResponse.ok)throw new Error(`Mode manifest request failed with HTTP ${manifestResponse.status}`)
      const manifestBytes=await manifestResponse.arrayBuffer()
      if(await sha256Hex(manifestBytes)!==pointer.manifestSha256)throw new Error(`${mode} manifest hash does not match unified activation`)
      const rawManifest=JSON.parse(new TextDecoder().decode(manifestBytes)) as unknown
      const nextManifest=parseManifest(rawManifest)
      if(!isManifestV1(nextManifest)||nextManifest.runId!==unified.runId||nextManifest.sessionDate!==unified.sessionDate||nextManifest.mode!==mode)throw new Error(`${mode} manifest identity does not match unified activation`)
      if(nextManifest.manifestVersion===1&&nextManifest.status==='failed')throw new Error('Latest scan failed its health gate')
      const coreAsset=nextManifest.assets.core
      const coreUrl=versionedUrl(mode,coreAsset)
      const rawCore=await sharedDataCache.load<unknown>(`core:${mode}:${coreAsset.sha256}`,coreUrl,{cache:'default'})
      const nextCore=expandCompactFundamentals(normalizeCore(rawCore,nextManifest))
      retainValidatedScan([unifiedUrl,manifestUrl,coreUrl])
      if(live){
        setManifest(nextManifest);setCore(nextCore);setError('')
        setSelectedTicker(current=>{
          const resolved=current||nextCore.universe[0]?.ticker||''
          if(resolved)history.replaceState(null,'',tickerUrl(resolved,nextManifest,mode))
          return resolved
        })
      }
    })().catch(nextError=>{if(live)setError(nextError instanceof Error?nextError.message:String(nextError))}).finally(()=>{if(live)setLoading(false)})
    return()=>{live=false}
  },[revision,mode])

  useEffect(()=>{
    const sync=()=>setSelectedTicker(initialTicker())
    window.addEventListener('hashchange',sync);window.addEventListener('popstate',sync)
    return()=>{window.removeEventListener('hashchange',sync);window.removeEventListener('popstate',sync)}
  },[])

  const selectTicker=useCallback((ticker:string)=>{
    const next=ticker.trim().toUpperCase()
    if(!next)return
    setSelectedTicker(next)
    history.replaceState(null,'',tickerUrl(next,manifest,mode))
  },[manifest,mode])

  const reload=useCallback(()=>{
    sharedDataCache.clear();setManifest(null);setCore(null);setError('');setRevision(value=>value+1)
  },[])

  const loadCandidateDetail=useCallback(async(ticker:string,force=false):Promise<CandidateDetailV1|null>=>{
    if(!manifest||!core)throw new Error('Manifest is not ready')
    const normalized=ticker.trim().toUpperCase()
    const asset=isManifestV1(manifest)?manifest.assets.details:manifest.assets.details??manifest.assets.legacyDetails
    const explicit=core.detailShards?.[normalized]
    const shard=explicit??detailShardFor(normalized,asset.shardCount??asset.bucketCount)
    const suffix=shard.endsWith('.json')?shard:`${shard}.json`
    const expanded=asset.pattern?.replace('{bucket}',shard).replace('{ticker}',normalized)
    const path=expanded
      ?(expanded.includes('/')?expanded:`${asset.path.replace(/\/$/,'')}/${expanded}`)
      :`${asset.path.replace(/\/$/,'')}/${suffix}`
    const payload=await sharedDataCache.load<unknown>(
      `detail:${mode}:${asset.sha256}:${shard}`,versionedUrl(mode,asset,path),
      {cache:force?'no-store':'default',force,cacheBust:force},
    )
    return detailFromPayload(payload,normalized) as CandidateDetailV1|null
  },[manifest,core,mode])

  const loadExcluded=useCallback(async()=>{
    if(!manifest||!isManifestV1(manifest))return[]
    const asset=manifest.assets.excluded
    const payload=await sharedDataCache.load<unknown>(`excluded:${mode}:${asset.sha256}`,versionedUrl(mode,asset),{cache:'default'})
    if(Array.isArray(payload))return payload as StockScoutRow[]
    if(payload&&typeof payload==='object')return((payload as any).rows??(payload as any).excluded??(payload as any).universe??[]) as StockScoutRow[]
    return[]
  },[manifest,mode])

  const loadHistory=useCallback(async()=>{
    if(!manifest||!isManifestV1(manifest))return[]
    const asset=manifest.assets.history
    const payload=await sharedDataCache.load<unknown>(`history:${mode}:${asset.sha256}`,versionedUrl(mode,asset),{cache:'default'})
    if(Array.isArray(payload))return payload as ScanHistoryItemV1[]
    if(payload&&typeof payload==='object')return((payload as any).sessions??(payload as any).runs??(payload as any).history??[]) as ScanHistoryItemV1[]
    return[]
  },[manifest,mode])

  const loadLegacyIndex=useCallback(async()=>{
    if(!manifest||!core)throw new Error('Manifest is not ready')
    const asset=manifest.assets.legacyIndex
    if(!asset)return{generatedAt:core.generatedAt,market:core.market,universe:core.universe as StockScoutRow[]}
    return sharedDataCache.load<LegacyIndex>(`legacy-index:${mode}:${asset.sha256}`,versionedUrl(mode,asset),{cache:'default'})
  },[manifest,core,mode])

  const loadLegacyDetail=useCallback(async(ticker:string,force=false)=>{
    if(!manifest)throw new Error('Manifest is not ready')
    if(isManifestV1(manifest)&&!manifest.assets.legacyDetails)return loadCandidateDetail(ticker,force)
    const normalized=ticker.trim().toUpperCase()
    const asset=manifest.assets.legacyDetails!
    const shard=detailShardFor(normalized,asset.shardCount)
    const path=`${asset.path.replace(/\/$/,'')}/${shard}.json`
    const rows=await sharedDataCache.load<unknown>(
      `legacy-detail:${mode}:${asset.sha256}:${shard}`,versionedUrl(mode,asset,path),
      {cache:force?'no-store':'default',force,cacheBust:force},
    )
    return detailFromPayload(rows,normalized)
  },[manifest,loadCandidateDetail,mode])

  const loadChart=useCallback(async(ticker:string,retry=false):Promise<ChartState>=>{
    if(!manifest||!core)return{status:'error',rows:[],error:'Dataset is not ready'}
    const asset=manifest.assets.charts
    const normalized=ticker.trim().toUpperCase()
    const legacyShard=core.chartShards?.[normalized]
    if(asset&&legacyShard&&!asset.path.endsWith('manifest.json')){
      try{
        const rows=await sharedDataCache.load<Record<string,any[]>>(
          `chart:${mode}:${asset.sha256}:${legacyShard}`,versionedUrl(mode,asset,`${asset.path.replace(/\/$/,'')}/${legacyShard}`),
          {cache:retry?'no-store':'default',force:retry,cacheBust:retry},
        )
        return rows[normalized]?.length?{status:'ready',rows:rows[normalized]}:{status:'unavailable',rows:[],reason:'missing'}
      }catch(nextError){return{status:'error',rows:[],error:String(nextError)}}
    }
    if(!isManifestV1(manifest))return{status:'unavailable',rows:[],reason:'missing'}
    try{
      const status=manifest.chartStatus??manifest.ownerChartStatus
      if(!asset&&status!=='ready')return{status:'unavailable',rows:[],reason:'missing'}
      if(!asset)return{status:'error',rows:[],error:'Chart asset is not published'}
      const chartManifestUrl=versionedUrl(mode,asset)
      const raw=await sharedDataCache.load<unknown>(
        `chart-manifest:${manifest.runId}:${asset?.sha256??'storage'}`,chartManifestUrl,
        {cache:retry?'no-store':'default',force:retry,cacheBust:retry},
      )
      const chartManifest=validateChartManifest(raw,manifest.runId)
      const shard=chartShard(chartManifest,normalized)
      if(!shard)return{status:'unavailable',rows:[],reason:'missing'}
      const payload=await loadChartShard(chartManifest,shard,retry)
      const rows=chartRows(payload,normalized)
      return rows?.length?{status:'ready',rows:rows as any[]}:{status:'unavailable',rows:[],reason:'missing'}
    }catch(nextError){return{status:'error',rows:[],error:String(nextError)}}
  },[manifest,core,mode])

  const loadContextAsset=useCallback(async<T,>(kind:'factorRegime'|'gmliContext'|'bottomScreener',retry=false):Promise<T>=>{
    const expectedMode=kind==='bottomScreener'?'bottom-fishing':'next'
    if(mode!==expectedMode)throw new Error(`${kind} is available only in ${expectedMode==='next'?'Next':'Bottom Fishing'}`)
    if(!manifest||!isManifestV1(manifest))throw new Error(`${kind} manifest is still loading`)
    const asset=manifest.assets[kind]
    if(!asset)throw new Error(`${kind} is not published for this run`)
    const key=`context:${kind}:${asset.sha256}`
    if(retry)contextAssetCache.delete(key)
    const existing=contextAssetCache.get(key)
    if(existing)return existing as Promise<T>
    const request=fetch(`${versionedUrl(mode,asset)}${retry?'&retry='+Date.now():''}`,{cache:retry?'no-store':'default'})
      .then(async response=>{
        if(!response.ok)throw new Error(`${kind} request failed with HTTP ${response.status}`)
        const bytes=await response.arrayBuffer()
        if(bytes.byteLength!==asset.bytes)throw new Error(`${kind} byte count does not match its manifest`)
        if(await sha256Hex(bytes)!==asset.sha256)throw new Error(`${kind} hash does not match its manifest`)
        const payload=JSON.parse(new TextDecoder().decode(bytes)) as Record<string,unknown>
        const expectedSchema=kind==='bottomScreener'?'stockscout-unified/bottom-screener-v1':1
        if(!payload||typeof payload!=='object'||payload.schemaVersion!==expectedSchema)throw new Error(`${kind} schema is unsupported`)
        if(kind==='factorRegime'&&(!Array.isArray(payload.factors)||payload.factors.length!==6))throw new Error('Factor regime must contain six factors')
        const contract=payload.consumerContract as Record<string,unknown>|undefined
        if(kind==='gmliContext'&&(payload.status!=='OK'||contract?.mode!=='READ_ONLY_SIDECAR'||contract?.mutatesStockScoutScoring!==false))throw new Error('GMLI read-only contract is invalid')
        if(kind==='bottomScreener'&&(!Array.isArray(payload.rows)||payload.runId!==manifest.runId||payload.priceBasis!=='split_only'||!Array.isArray(payload.fields)||payload.fields.length<60))throw new Error('Bottom screener contract is invalid')
        return payload as T
      })
      .catch(error=>{contextAssetCache.delete(key);throw error})
    contextAssetCache.set(key,request)
    return request
  },[manifest,mode])

  const loadOptional=useCallback(async<T,>(path:string):Promise<T|null>=>{
    try{return await sharedDataCache.load<T>(`optional:${mode}:${path}`,dataUrl(mode,path),{cache:'no-cache'})}
    catch{return null}
  },[mode])

  const value=useMemo<DataContextValue>(()=>({
    manifest,core,loading,error,selectedTicker,selectTicker,reviewScope,setReviewScope,reload,
    loadCandidateDetail,loadExcluded,loadHistory,loadLegacyIndex,loadLegacyDetail,loadChart,loadContextAsset,loadOptional,
  }),[manifest,core,loading,error,selectedTicker,selectTicker,reviewScope,reload,loadCandidateDetail,loadExcluded,loadHistory,loadLegacyIndex,loadLegacyDetail,loadChart,loadContextAsset,loadOptional])

  return <DataContext.Provider value={value}>{children}</DataContext.Provider>
}

export function useStockScoutData(){
  const value=useContext(DataContext)
  if(!value)throw new Error('useStockScoutData must be used within StockScoutDataProvider')
  return value
}
