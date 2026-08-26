export const STOCKSCOUT_SCHEMA='stockscout-eod/v1' as const
export const UNIFIED_SCHEMA='stockscout-unified/v1' as const
export type UnifiedManifestV1={
  manifestVersion:1;schemaVersion:typeof UNIFIED_SCHEMA;runId:string;sessionDate:string;generatedAt:string;status:'healthy';defaultMode:'bottom-fishing'
  modes:Record<'bottom-fishing'|'next'|'ryan-original',{mode:string;label:string;priceBasis:string;status:'healthy';manifestPath:string;manifestSha256:string;manifestBytes:number;candidates:number;excluded:number;chartCoveragePct:number;ranking:string}>
}

export type TradeStatus='entry_ready'|'trigger_pending'|'wait_for_retest'|'not_tradeable'|'insufficient_data'
export type TriggerState='pending'|'fresh'|'extended'|'unavailable'

export type TradePlanV1={
  status:TradeStatus
  reasonCodes:string[]
  triggerState:TriggerState
  triggerReferenceLevel:number|null
  entryReferenceLevel:number|null
  structuralInvalidationLevel:number|null
  entryRiskPct:number|null
  extensionAtr:number|null
  tacticalStopLevel:number|null
  tacticalRiskPct:number|null
  source:string
  version:number|string
}

export type AssetDescriptor={
  path:string
  sha256:string
  bytes:number
  count?:number
  coverage?:number
  coveragePct?:number
  pattern?:string
  shardCount?:number
  bucketCount?:number
}

export type ScanHealth='healthy'|'degraded'|'failed'

export type ScanManifestV1={
  manifestVersion:1
  schemaVersion:typeof STOCKSCOUT_SCHEMA
  mode?:'bottom-fishing'|'next'|'ryan-original'
  runId:string
  sessionDate:string
  marketDataDate:string
  generatedAt:string
  status:ScanHealth
  priceMode:'split_only'|'split_div'|string
  chartStatus?:'ready'|'stale'|'missing'
  ownerChartStatus?:'ready'|'stale'|'missing'
  counts:{universe:number;candidates:number;excluded:number;failed:number;total:number}
  health?:{status:ScanHealth;coveragePct:number;checks:Array<{code:string;passed:boolean;detail:string}>}
  provenance:Record<string,unknown>
  versions:{ranking:string;detectors:string;tradePlan:string;[key:string]:string}
  assets:{
    core:AssetDescriptor
    details:AssetDescriptor
    excluded:AssetDescriptor
    history:AssetDescriptor
    legacyConfirmation?:AssetDescriptor
    legacyIndex?:AssetDescriptor
    legacyDetails?:AssetDescriptor
    charts?:AssetDescriptor
  }
}

export type LegacyManifestV2={
  manifestVersion:2
  schemaVersion?:string
  model:string
  generatedAt:string
  runId?:string
  sessionDate?:string
  marketSession?:{date?:string|null;status?:string;timezone?:string}
  universe:number
  provenance:Record<string,unknown>
  assets:{
    core:AssetDescriptor
    legacyIndex:AssetDescriptor
    legacyDetails:AssetDescriptor
    legacyConfirmation:AssetDescriptor
    charts:AssetDescriptor
    details?:AssetDescriptor
    excluded?:AssetDescriptor
    history?:AssetDescriptor
  }
}

export type StockScoutManifest=ScanManifestV1|LegacyManifestV2

export type CandidateSummaryV1={
  id:string
  canonicalUrl?:string
  ticker:string
  mode?:'bottom-fishing'|'next'|'ryan-original'
  priceBasis?:'split_only'|'split_div'
  scanOrder:number
  focusBlend?:number
  tradeStatus?:TradeStatus
  entryRiskPct?:number|null
  tacticalStopLevel?:number|null
  excluded?:boolean
  [key:string]:any
}

export type CandidateDetailV1=CandidateSummaryV1&{
  tradePlan?:TradePlanV1|Record<string,unknown>|null
  setupHits?:Record<string,unknown>
  rawFeatures?:Record<string,unknown>
  reasons?:string[]
}

export type CandidateCoreV1={
  schemaVersion?:string
  runId?:string
  sessionDate?:string
  generatedAt:string
  market:Record<string,any>
  universe:CandidateSummaryV1[]
  detailShards?:Record<string,string>
  chartShards?:Record<string,string>
  [key:string]:any
}

export type ScanHistoryItemV1={
  runId:string
  sessionDate:string
  generatedAt:string
  status:ScanHealth
  coveragePct?:number
  candidateCount?:number
  excludedCount?:number
  counts?:ScanManifestV1['counts']
  manifestSha256?:string
}

function isRecord(value:unknown):value is Record<string,unknown>{
  return Boolean(value)&&typeof value==='object'&&!Array.isArray(value)
}

export function parseUnifiedManifest(value:unknown):UnifiedManifestV1{
  if(!isRecord(value)||value.schemaVersion!==UNIFIED_SCHEMA||value.manifestVersion!==1||value.status!=='healthy')throw new Error('Unified activation manifest is invalid')
  const modes=isRecord(value.modes)?value.modes:{}
  for(const mode of ['bottom-fishing','next','ryan-original'] as const){
    const pointer=modes[mode]
    if(!isRecord(pointer)||pointer.mode!==mode||pointer.status!=='healthy'||typeof pointer.manifestSha256!=='string'||!/^[0-9a-f]{64}$/.test(pointer.manifestSha256))throw new Error(`Unified mode pointer is invalid: ${mode}`)
  }
  return value as unknown as UnifiedManifestV1
}

function requiredText(value:unknown,label:string){
  if(typeof value!=='string'||!value.trim())throw new Error(`${label} is missing`)
  return value
}

function requiredAsset(value:unknown,label:string):AssetDescriptor{
  if(!isRecord(value))throw new Error(`${label} asset is missing`)
  return{
    ...value,
    path:requiredText(value.path,`${label}.path`),
    sha256:requiredText(value.sha256,`${label}.sha256`),
    bytes:Number(value.bytes??0),
  } as AssetDescriptor
}

export function parseManifest(value:unknown):StockScoutManifest{
  if(!isRecord(value))throw new Error('Manifest is not an object')
  if(value.manifestVersion===1){
    if(value.schemaVersion!==STOCKSCOUT_SCHEMA)throw new Error(`Unsupported schema ${String(value.schemaVersion??'unknown')}`)
    const assets=isRecord(value.assets)?value.assets:{}
    const counts=isRecord(value.counts)?value.counts:{}
    const status=value.status
    if(status!=='healthy'&&status!=='degraded'&&status!=='failed')throw new Error(`Unsupported scan status ${String(status)}`)
    return{
      ...value,
      manifestVersion:1,
      schemaVersion:STOCKSCOUT_SCHEMA,
      runId:requiredText(value.runId,'runId'),
      sessionDate:requiredText(value.sessionDate,'sessionDate'),
      marketDataDate:requiredText(value.marketDataDate??value.sessionDate,'marketDataDate'),
      generatedAt:requiredText(value.generatedAt,'generatedAt'),
      status,
      priceMode:requiredText(value.priceMode,'priceMode'),
      counts:{
        universe:Number(counts.universe??Number(counts.candidates??0)+Number(counts.excluded??0)),
        candidates:Number(counts.candidates??0),
        excluded:Number(counts.excluded??0),
        failed:Number(counts.failed??0),
        total:Number(counts.total??Number(counts.candidates??0)+Number(counts.excluded??0)),
      },
      provenance:isRecord(value.provenance)?value.provenance:{},
      versions:isRecord(value.versions)?value.versions as ScanManifestV1['versions']:{ranking:'unknown',detectors:'unknown',tradePlan:'unknown'},
      assets:{
        ...assets,
        core:requiredAsset(assets.core,'core'),
        details:requiredAsset(assets.details,'details'),
        excluded:requiredAsset(assets.excluded,'excluded'),
        history:requiredAsset(assets.history,'history'),
      },
    } as ScanManifestV1
  }
  if(value.manifestVersion===2){
    const assets=isRecord(value.assets)?value.assets:{}
    return{
      ...value,
      manifestVersion:2,
      model:requiredText(value.model,'model'),
      generatedAt:requiredText(value.generatedAt,'generatedAt'),
      universe:Number(value.universe??0),
      provenance:isRecord(value.provenance)?value.provenance:{},
      assets:{
        ...assets,
        core:requiredAsset(assets.core,'core'),
        legacyIndex:requiredAsset(assets.legacyIndex,'legacyIndex'),
        legacyDetails:requiredAsset(assets.legacyDetails,'legacyDetails'),
        legacyConfirmation:requiredAsset(assets.legacyConfirmation,'legacyConfirmation'),
        charts:requiredAsset(assets.charts,'charts'),
      },
    } as LegacyManifestV2
  }
  throw new Error(`Unsupported manifest v${String(value.manifestVersion??'unknown')}`)
}

export function normalizeCore(value:unknown,manifest:StockScoutManifest):CandidateCoreV1{
  if(!isRecord(value))throw new Error('Core dataset is not an object')
  const universe=Array.isArray(value.universe)?value.universe:Array.isArray(value.candidates)?value.candidates:null
  if(!universe)throw new Error('Core dataset has no candidate universe')
  const generatedAt=requiredText(value.generatedAt,'core.generatedAt')
  if(generatedAt!==manifest.generatedAt)throw new Error('Core dataset does not match manifest timestamp')
  const expected=manifest.manifestVersion===1?manifest.counts.candidates:manifest.universe
  if(universe.length!==expected)throw new Error(`Core candidate count ${universe.length} does not match manifest ${expected}`)
  const runId=manifest.manifestVersion===1?manifest.runId:manifest.runId
  return{
    ...value,
    generatedAt,
    runId:typeof value.runId==='string'?value.runId:runId,
    market:isRecord(value.market)?value.market:{},
    universe:universe.map((candidate,index)=>{
      if(!isRecord(candidate))throw new Error(`Candidate ${index} is invalid`)
      const ticker=requiredText(candidate.ticker,`candidate ${index}.ticker`).toUpperCase()
      return{
        ...candidate,
        id:typeof candidate.id==='string'?candidate.id:`scan:${runId??generatedAt}:candidate:${ticker}`,
        ticker,
        scanOrder:Number(candidate.scanOrder??index),
      } as CandidateSummaryV1
    }),
  } as CandidateCoreV1
}

export function isManifestV1(manifest:StockScoutManifest):manifest is ScanManifestV1{
  return manifest.manifestVersion===1
}

export function detailShardFor(ticker:string,count=128){
  const normalized=ticker.trim().toUpperCase()
  let value=0
  for(let index=0;index<normalized.length;index++)value+=(index+1)*normalized.charCodeAt(index)
  return String(value%Math.max(1,Math.floor(count))).padStart(3,'0')
}
