export type ReviewStock={
  ticker:string
  stage?:number
  stageName?:string
  primarySetup?:string
  setup?:string
  setupTags?:string[]
  opportunityScore?:number
  opportunityTier?:string
  opportunityRank?:number
  opportunityPotential?:number
  opportunityTiming?:number
  emergingLeaderScore?:number
  rsRank?:number
  groupRank?:number
  fundamentalEvidenceScore?:number|null
  volumeRatio?:number
  vcpScore?:number
  distance10w?:number
  changedToday?:boolean
  newUniverseMember?:boolean
  changeImpact?:number
  changeLabels?:string[]
  reasons?:string[]
}

export type ReviewScope='today'|'new'|null
export type ReviewPayload={generatedAt:string;universe:ReviewStock[]}
export type ReviewManifest={
  manifestVersion?:number
  generatedAt?:string
  universe?:number
  sessionDate?:string
  marketDataDate?:string
  status?:'healthy'|'degraded'|'failed'
  counts?:{candidates?:number;excluded?:number;universe?:number;failed?:number}
  health?:{status?:string;coveragePct?:number}
  marketSession?:{date?:string|null;status?:string}
  provenance?:{source?:{sha256?:string};publication?:{sourceSha256?:string}}
}
export type ValidationStatus={conclusion?:string;run_id?:number;head_sha?:string;updated_at?:string;run_url?:string}

const finite=(value:unknown):value is number=>typeof value==='number'&&Number.isFinite(value)
const rounded=(value:number)=>Math.round(value)
const setupOf=(stock:ReviewStock)=>stock.primarySetup||stock.setup||stock.setupTags?.[0]||stock.stageName||'Unclassified setup'

export function matchesReviewScope(stock:ReviewStock,scope:ReviewScope){
  if(scope==='today')return stock.changedToday===true
  if(scope==='new')return stock.newUniverseMember===true
  return true
}

export function reviewScopeLabel(scope:Exclude<ReviewScope,null>){
  return scope==='today'?'Today / changed':'New since last scan'
}

export function buildReviewInbox(universe:ReviewStock[]){
  const today=universe.filter(stock=>matchesReviewScope(stock,'today')).sort((a,b)=>(b.changeImpact??0)-(a.changeImpact??0)||a.ticker.localeCompare(b.ticker))
  const newSinceLastScan=universe.filter(stock=>matchesReviewScope(stock,'new')).sort((a,b)=>(b.opportunityScore??0)-(a.opportunityScore??0)||a.ticker.localeCompare(b.ticker))
  return{today,newSinceLastScan}
}

export function explainStock(stock:ReviewStock):string[]{
  const lines:string[]=[]
  const stage=finite(stock.stage)?` · Stage ${stock.stage}`:''
  lines.push(`${setupOf(stock)}${stage}`)

  const opportunity=[finite(stock.opportunityScore)?`Opportunity ${rounded(stock.opportunityScore)}`:null,stock.opportunityTier||null,finite(stock.opportunityRank)?`rank ${rounded(stock.opportunityRank)}`:null].filter(Boolean).join(' · ')
  if(opportunity)lines.push(opportunity)

  const timing=[finite(stock.opportunityPotential)?`potential ${rounded(stock.opportunityPotential)}`:null,finite(stock.opportunityTiming)?`timing ${rounded(stock.opportunityTiming)}`:null,finite(stock.emergingLeaderScore)?`emerging ${rounded(stock.emergingLeaderScore)}`:null].filter(Boolean).join(' · ')
  if(timing)lines.push(timing)

  const leadership=[finite(stock.rsRank)?`RS ${rounded(stock.rsRank)}`:null,finite(stock.groupRank)?`group ${rounded(stock.groupRank)}`:null,finite(stock.fundamentalEvidenceScore)?`fundamentals ${rounded(stock.fundamentalEvidenceScore)}`:null,finite(stock.volumeRatio)?`volume ${stock.volumeRatio.toFixed(1)}x`:null].filter(Boolean).join(' · ')
  if(leadership)lines.push(leadership)

  if(stock.changeLabels?.length)lines.push(`Since last scan: ${stock.changeLabels.slice(0,2).join(' · ')}`)
  else if(stock.reasons?.length)lines.push(stock.reasons.slice(0,2).join(' · '))

  return lines.slice(0,5)
}

const isoDate=(date:Date)=>date.toISOString().slice(0,10)
const observedFixedHoliday=(year:number,month:number,day:number)=>{
  const date=new Date(Date.UTC(year,month,day,12))
  if(date.getUTCDay()===6)date.setUTCDate(date.getUTCDate()-1)
  if(date.getUTCDay()===0)date.setUTCDate(date.getUTCDate()+1)
  return isoDate(date)
}
const nthWeekday=(year:number,month:number,weekday:number,nth:number)=>{
  const date=new Date(Date.UTC(year,month,1,12))
  date.setUTCDate(1+(7+weekday-date.getUTCDay())%7+(nth-1)*7)
  return isoDate(date)
}
const lastWeekday=(year:number,month:number,weekday:number)=>{
  const date=new Date(Date.UTC(year,month+1,0,12))
  date.setUTCDate(date.getUTCDate()-(7+date.getUTCDay()-weekday)%7)
  return isoDate(date)
}
const easterSunday=(year:number)=>{
  const a=year%19,b=Math.floor(year/100),c=year%100,d=Math.floor(b/4),e=b%4
  const f=Math.floor((b+8)/25),g=Math.floor((b-f+1)/3),h=(19*a+b-d-g+15)%30
  const i=Math.floor(c/4),k=c%4,l=(32+2*e+2*i-h-k)%7,m=Math.floor((a+11*h+22*l)/451)
  return new Date(Date.UTC(year,Math.floor((h+l-7*m+114)/31)-1,(h+l-7*m+114)%31+1,12))
}
function marketHolidays(year:number){
  const easter=easterSunday(year);easter.setUTCDate(easter.getUTCDate()-2)
  return new Set([
    observedFixedHoliday(year,0,1),nthWeekday(year,0,1,3),nthWeekday(year,1,1,3),isoDate(easter),
    lastWeekday(year,4,1),observedFixedHoliday(year,5,19),observedFixedHoliday(year,6,4),
    nthWeekday(year,8,1,1),nthWeekday(year,10,4,4),observedFixedHoliday(year,11,25),
  ])
}
function isTradingSession(date:Date){
  if(date.getUTCDay()===0||date.getUTCDay()===6)return false
  const iso=isoDate(date),year=date.getUTCFullYear()
  return ![year-1,year,year+1].some(candidate=>marketHolidays(candidate).has(iso))
}
function previousTradingSession(date:Date){
  do{date.setUTCDate(date.getUTCDate()-1)}while(!isTradingSession(date))
  return date
}

export function lastCompletedMarketSession(nowMs=Date.now()){
  const parts=new Intl.DateTimeFormat('en-CA',{timeZone:'America/New_York',year:'numeric',month:'2-digit',day:'2-digit',weekday:'short',hour:'2-digit',minute:'2-digit',hourCycle:'h23'}).formatToParts(new Date(nowMs))
  const value=(type:string)=>parts.find(part=>part.type===type)?.value||''
  const date=new Date(`${value('year')}-${value('month')}-${value('day')}T12:00:00Z`)
  const weekday=value('weekday')
  const minutes=Number(value('hour'))*60+Number(value('minute'))
  if(weekday==='Sat'||weekday==='Sun'||!isTradingSession(date)||minutes<16*60+30)previousTradingSession(date)
  return isoDate(date)
}

export function dataHealth(payload:ReviewPayload|null,manifest:ReviewManifest|null,validation:ValidationStatus|null,nowMs=Date.now()){
  if(!payload)return{level:'unknown' as const,label:'Data unavailable',ageHours:null,detail:'Core dataset could not be loaded.'}
  const generatedMs=Date.parse(payload.generatedAt)
  const ageHours=Number.isFinite(generatedMs)?Math.max(0,(nowMs-generatedMs)/3600000):null
  const expectedSession=lastCompletedMarketSession(nowMs)
  if(manifest?.manifestVersion===1){
    const manifestMatch=manifest.generatedAt===payload.generatedAt
    const universeMatch=manifest.counts?.candidates===payload.universe.length
    if(!manifestMatch||!universeMatch)return{level:'warn' as const,label:'Snapshot mismatch',ageHours,detail:'Core and v1 manifest are not aligned.'}
    if(manifest.status==='failed'||manifest.health?.status==='failed')return{level:'warn' as const,label:'Scan failed',ageHours,detail:'The latest snapshot did not pass its activation health gate.'}
    if(manifest.sessionDate!==expectedSession)return{level:'warn' as const,label:'Session stale',ageHours,detail:`Published session ${manifest.sessionDate||'unknown'}; expected ${expectedSession}.`}
    if(manifest.status==='degraded'||manifest.health?.status==='degraded')return{level:'warn' as const,label:'Degraded',ageHours,detail:`Published session ${manifest.sessionDate} is usable but its data-health gate is degraded.`}
    if(validation?.conclusion&&validation.conclusion!=='success')return{level:'warn' as const,label:'Validation not green',ageHours,detail:`Last published validation: ${validation.conclusion}.`}
    if(validation?.conclusion==='success')return{level:'ok' as const,label:'Healthy',ageHours,detail:`Healthy EOD scan ${manifest.sessionDate} · validation #${validation.run_id??'—'} green.`}
    return{level:'ok' as const,label:'Healthy',ageHours,detail:`Healthy EOD scan ${manifest.sessionDate} · prices are not live.`}
  }
  const manifestMatch=manifest?.generatedAt===payload.generatedAt
  const universeMatch=manifest?.universe===payload.universe.length
  const provenanceMatch=Boolean(manifest?.provenance?.source?.sha256)&&manifest?.provenance?.source?.sha256===manifest?.provenance?.publication?.sourceSha256
  const sessionMatch=manifest?.marketSession?.status==='closed'&&manifest.marketSession.date===expectedSession
  const validationGreen=validation?.conclusion==='success'
  const validationKnown=Boolean(validation?.conclusion)

  if(!manifestMatch||!universeMatch||!provenanceMatch)return{level:'warn' as const,label:'Snapshot mismatch',ageHours,detail:'Core, manifest, and source provenance are not aligned.'}
  if(!sessionMatch)return{level:'warn' as const,label:'Session stale',ageHours,detail:`Published session ${manifest?.marketSession?.date||'unknown'}; expected ${expectedSession}.`}
  if(validationKnown&&!validationGreen)return{level:'warn' as const,label:'Validation not green',ageHours,detail:`Last published validation: ${validation?.conclusion}.`}
  if(validationGreen)return{level:'ok' as const,label:'Healthy',ageHours,detail:`Snapshot aligned · validation #${validation?.run_id??'—'} green.`}
  return{level:'neutral' as const,label:'Validation unknown',ageHours,detail:'Snapshot and completed session are aligned; validation status is not published.'}
}
