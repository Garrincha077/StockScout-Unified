export type ChartShardDescriptor={name:string;sha256:string;bytes:number;tickerCount:number}

export type ChartManifest={
  schemaVersion:string
  runId:string
  storageBaseUrl:string
  shards:ChartShardDescriptor[]
  shardsByTicker:Record<string,string>
}

export function chartShard(manifest:ChartManifest,ticker:string):string|null{
  const normalized=ticker.trim().toUpperCase()
  const value=manifest?.shardsByTicker?.[normalized]
  return typeof value==='string'&&value?value:null
}

export function chartShardDescriptor(manifest:ChartManifest,shard:string):ChartShardDescriptor|null{
  return manifest.shards.find(item=>item.name===shard)??null
}

export function chartPath(manifest:ChartManifest,shard:string){
  const filename=shard.endsWith('.json.gz')?shard:`${shard}.json.gz`
  return `${manifest.storageBaseUrl.replace(/\/$/,'')}/shards/${filename}`
}

export function chartRows(payload:any,ticker:string):unknown[]|null{
  const normalized=ticker.trim().toUpperCase()
  const candidate=payload?.[normalized]??payload?.byTicker?.[normalized]??payload?.candidates?.[normalized]
  if(Array.isArray(candidate))return candidate
  if(Array.isArray(candidate?.daily))return candidate.daily
  if(Array.isArray(candidate?.rows))return candidate.rows
  return null
}

export function publicChartManifestUrl(supabaseUrl:string,runId:string){
  const base=supabaseUrl.trim().replace(/\/$/,'')
  return `${base}/storage/v1/object/public/stockscout-eod-charts/${encodeURIComponent(runId)}/manifest.json`
}

export function validateChartManifest(value:unknown,runId:string):ChartManifest{
  if(!value||typeof value!=='object'||Array.isArray(value))throw new Error('Chart manifest is invalid')
  const manifest=value as Partial<ChartManifest>
  if(manifest.runId!==runId)throw new Error('Chart manifest belongs to another scan')
  if(!manifest.storageBaseUrl||!Array.isArray(manifest.shards)||!manifest.shardsByTicker)throw new Error('Chart manifest is incomplete')
  const url=new URL(manifest.storageBaseUrl)
  const validPath=url.pathname.endsWith(`/storage/v1/object/public/stockscout-eod-charts/${runId}`)||url.pathname.endsWith(`/runs/${runId}/charts`)
  if(url.protocol!=='https:'||url.username||url.password||url.search||url.hash||!validPath)throw new Error('Chart storage URL is invalid')
  return manifest as ChartManifest
}
