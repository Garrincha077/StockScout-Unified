export type Logic='ALL'|'ANY'
export type RuleOp='>'|'>='|'<'|'<='|'='|'!='|'between'|'contains'|'true'|'false'
export type Rule={id:string;field:string;op:RuleOp;value:string}
export type RuleGroup={id:string;logic:Logic;rules:Rule[]}
export type ScreenState={
  name:string
  rootLogic:Logic
  groups:RuleGroup[]
  sorting:{id:string;desc:boolean}[]
  visibility:Record<string,boolean>
  recipe:string
  query:string
  pageSize:number
}
export type FieldDef={id:string;label:string;kind:'number'|'text'|'boolean';defaultOp:RuleOp;placeholder?:string}

export function stockScoutFocusBlend(stock:Record<string,unknown>){
  const value=stock.focusBlend??stock.focus_blend??stock.score
  const numeric=Number(value)
  return value!==null&&value!==undefined&&Number.isFinite(numeric)?numeric:null
}

export function preserveScanOrder<T extends {scanOrder?:unknown;scan_order?:unknown}>(rows:T[]):T[]{
  return rows.map((row,index)=>{
    const raw=row.scanOrder??row.scan_order
    const numeric=Number(raw)
    return{row,index,order:raw!==null&&raw!==undefined&&Number.isFinite(numeric)?numeric:index}
  }).sort((left,right)=>left.order-right.order||left.index-right.index).map(item=>item.row)
}

export function normalizeEodDate(value:unknown):string|null{
  if(value instanceof Date&&!Number.isNaN(value.getTime()))return value.toISOString().slice(0,10)
  if(typeof value==='string'&&/^\d{4}-\d{2}-\d{2}(?:[T ].*)?$/.test(value)){
    const parsed=new Date(`${value.slice(0,10)}T00:00:00Z`)
    return Number.isNaN(parsed.getTime())?null:parsed.toISOString().slice(0,10)
  }
  const numeric=typeof value==='number'?value:typeof value==='string'&&/^\d+(?:\.\d+)?$/.test(value.trim())?Number(value):NaN
  if(Number.isFinite(numeric)){
    const milliseconds=Math.abs(numeric)<100_000_000_000?numeric*1000:numeric
    const parsed=new Date(milliseconds)
    return Number.isNaN(parsed.getTime())?null:parsed.toISOString().slice(0,10)
  }
  if(typeof value!=='string'||!value.trim())return null
  const parsed=new Date(value)
  return Number.isNaN(parsed.getTime())?null:parsed.toISOString().slice(0,10)
}

export function weekStartUtc(value:unknown):string|null{
  const normalized=normalizeEodDate(value)
  if(!normalized)return null
  const date=new Date(`${normalized}T00:00:00Z`)
  const day=(date.getUTCDay()+6)%7
  date.setUTCDate(date.getUTCDate()-day)
  return date.toISOString().slice(0,10)
}

export function expandedCandleBounds(rows:Array<{low:unknown;high:unknown}>,paddingRatio=.1){
  const lows=rows.map(row=>Number(row.low)).filter(Number.isFinite)
  const highs=rows.map(row=>Number(row.high)).filter(Number.isFinite)
  if(!lows.length||!highs.length)return null
  const minimum=Math.min(...lows),maximum=Math.max(...highs),padding=(maximum-minimum)*Math.max(0,paddingRatio)
  return{minimum:minimum-padding,maximum:maximum+padding}
}

export function levelFitsExpandedCandleBounds(level:unknown,rows:Array<{low:unknown;high:unknown}>,paddingRatio=.1){
  const numeric=Number(level),bounds=expandedCandleBounds(rows,paddingRatio)
  return Number.isFinite(numeric)&&Boolean(bounds)&&numeric>=bounds!.minimum&&numeric<=bounds!.maximum
}

export type PositionSizeResult={
  enabled:boolean
  reason:string
  riskBudget:number
  riskPerShare:number
  shares:number
  positionValue:number
  positionPctNav:number
  riskUsed:number
  cashLimited:boolean
}

const disabledPosition=(reason:string):PositionSizeResult=>({enabled:false,reason,riskBudget:0,riskPerShare:0,shares:0,positionValue:0,positionPctNav:0,riskUsed:0,cashLimited:false})

export function calculatePositionSize(status:string,nav:unknown,riskPct:unknown,entry:unknown,tacticalStop:unknown):PositionSizeResult{
  if(status!=='entry_ready')return disabledPosition('Sizing is available only for entry-ready setups.')
  const stop=Number(tacticalStop)
  if(tacticalStop===null||tacticalStop===undefined||!Number.isFinite(stop)||stop<=0)return disabledPosition('A valid tactical stop is required; structural invalidation is never used as fallback.')
  const account=Number(nav),riskPercent=Number(riskPct),entryPrice=Number(entry)
  if(!Number.isFinite(account)||account<=0||!Number.isFinite(riskPercent)||riskPercent<=0||!Number.isFinite(entryPrice)||entryPrice<=0)return disabledPosition('Enter a positive NAV, risk percentage and entry price.')
  if(entryPrice<=stop)return disabledPosition('Entry price must be above the tactical stop.')
  const riskBudget=account*riskPercent/100,riskPerShare=entryPrice-stop
  const riskShares=Math.floor(riskBudget/riskPerShare),cashShares=Math.floor(account/entryPrice)
  const shares=Math.max(0,Math.min(riskShares,cashShares)),positionValue=shares*entryPrice,riskUsed=shares*riskPerShare
  return{enabled:shares>0,reason:shares>0?'':'Risk budget is too small for one share.',riskBudget,riskPerShare,shares,positionValue,positionPctNav:positionValue/account*100,riskUsed,cashLimited:cashShares<riskShares}
}

export const fieldDefs:FieldDef[]=[
  {id:'stage',label:'Stage',kind:'number',defaultOp:'='},
  {id:'scoutTier',label:'Scout Tier',kind:'text',defaultOp:'='},
  {id:'scoutTierRank',label:'Scout Tier rank',kind:'number',defaultOp:'>='},
  {id:'scoutTierLabel',label:'Scout Tier / phase',kind:'text',defaultOp:'contains'},
  {id:'scoutQualityConfirmed',label:'Scout quality confirmed',kind:'boolean',defaultOp:'true'},
  {id:'scoutTierReasons',label:'Scout Tier reason',kind:'text',defaultOp:'contains'},
  {id:'maClusterPhase',label:'MA Cluster phase',kind:'text',defaultOp:'='},
  {id:'maClusterTier',label:'Timing Tier',kind:'text',defaultOp:'='},
  {id:'maClusterTierRank',label:'Timing Tier rank',kind:'number',defaultOp:'>='},
  {id:'maClusterTierLabel',label:'Timing Tier / phase',kind:'text',defaultOp:'contains'},
  {id:'maClusterWatch',label:'MA Cluster WATCH',kind:'boolean',defaultOp:'true'},
  {id:'maClusterReady',label:'MA Cluster READY',kind:'boolean',defaultOp:'true'},
  {id:'maClusterEntrySignal',label:'MA Cluster ENTRY',kind:'boolean',defaultOp:'true'},
  {id:'maClusterScore',label:'MA Cluster timing 0-100',kind:'number',defaultOp:'>='},
  {id:'maClusterSpreadPct',label:'10W/30W spread %',kind:'number',defaultOp:'<=',placeholder:'3.5'},
  {id:'ma10wSlope4w',label:'10W slope 4W %',kind:'number',defaultOp:'>='},
  {id:'ma30wSlope4w',label:'30W slope 4W %',kind:'number',defaultOp:'>='},
  {id:'maClusterTurnCount',label:'Rising MAs 0-2',kind:'number',defaultOp:'>='},
  {id:'maClusterPricePct',label:'Price vs MA cluster %',kind:'number',defaultOp:'between',placeholder:'-3,5'},
  {id:'maClusterVolumePace',label:'Weekly volume pace x',kind:'number',defaultOp:'>='},
  {id:'maClusterVeryTight',label:'Very tight 10W/30W',kind:'boolean',defaultOp:'true'},
  {id:'maClusterReasons',label:'MA Cluster reason',kind:'text',defaultOp:'contains'},
  {id:'ema10d20dState',label:'Daily EMA 10/20 state',kind:'text',defaultOp:'='},
  {id:'ema10d20dCross',label:'Daily EMA 10/20 last cross',kind:'text',defaultOp:'='},
  {id:'ema10d20dCrossAge',label:'Daily EMA 10/20 cross age (sessions)',kind:'number',defaultOp:'<='},
  {id:'ema10d20dSpreadPct',label:'Daily EMA 10/20 spread %',kind:'number',defaultOp:'between',placeholder:'-1,1'},
  {id:'sma10w20wState',label:'Weekly SMA 10/20 state',kind:'text',defaultOp:'='},
  {id:'sma10w20wCross',label:'Weekly SMA 10/20 last cross',kind:'text',defaultOp:'='},
  {id:'sma10w20wCrossAge',label:'Weekly SMA 10/20 cross age (weeks)',kind:'number',defaultOp:'<='},
  {id:'sma10w20wSpreadPct',label:'Weekly SMA 10/20 spread %',kind:'number',defaultOp:'between',placeholder:'-2,2'},
  {id:'focusBlend',label:'StockScout focus blend',kind:'number',defaultOp:'>='},
  {id:'opportunityScore',label:'Opportunity analysis 0-100',kind:'number',defaultOp:'>='},
  {id:'opportunityRank',label:'Opportunity percentile 1-99',kind:'number',defaultOp:'>='},
  {id:'opportunityTier',label:'Opportunity Tier',kind:'text',defaultOp:'='},
  {id:'opportunityPotential',label:'Opportunity Potential 0-100',kind:'number',defaultOp:'>='},
  {id:'opportunityTiming',label:'Opportunity Timing 0-100',kind:'number',defaultOp:'>='},
  {id:'opportunityGroupModifier',label:'Opportunity Group modifier',kind:'number',defaultOp:'>='},
  {id:'opportunityFundModifier',label:'Opportunity Fundamental modifier',kind:'number',defaultOp:'>='},
  {id:'opportunityPenalty',label:'Opportunity penalty',kind:'number',defaultOp:'<='},
  {id:'emergingLeaderScore',label:'Emerging discovery score 0-100',kind:'number',defaultOp:'>='},
  {id:'emergingArchetype',label:'Emerging archetype',kind:'text',defaultOp:'contains'},
  {id:'emergingEvidenceCount',label:'Evidence 0-5',kind:'number',defaultOp:'>='},
  {id:'emergingLeaderCandidate',label:'Emerging Leader candidate',kind:'boolean',defaultOp:'true'},
  {id:'aPlusEmergingSetup',label:'A+ Emerging setup',kind:'boolean',defaultOp:'true'},
  {id:'neglectedEmergingScore',label:'Neglected Emerging 0-100',kind:'number',defaultOp:'>='},
  {id:'resetReawakeningScore',label:'Reset Reawakening 0-100',kind:'number',defaultOp:'>='},
  {id:'resetScore',label:'Base / Reset 0-100',kind:'number',defaultOp:'>='},
  {id:'rsTurnScore',label:'RS Turn 0-100',kind:'number',defaultOp:'>='},
  {id:'neglectHistoryScore',label:'Neglect History 0-100',kind:'number',defaultOp:'>='},
  {id:'triggerReadinessScore',label:'Trigger Readiness 0-100',kind:'number',defaultOp:'>='},
  {id:'reawakeningStructureScore',label:'Reawakening Structure 0-100',kind:'number',defaultOp:'>='},
  {id:'ignitionScore',label:'Ignition 0-100',kind:'number',defaultOp:'>='},
  {id:'recoveryScore',label:'Recovery 0-100',kind:'number',defaultOp:'>='},
  {id:'dryResetScore',label:'Dry Reset 0-100',kind:'number',defaultOp:'>='},
  {id:'stageFreshnessScore',label:'Stage Freshness 0-100',kind:'number',defaultOp:'>='},
  {id:'emergingReasons',label:'Emerging reason',kind:'text',defaultOp:'contains'},
  {id:'leadershipScore',label:'Group-adjusted Emerging',kind:'number',defaultOp:'>='},
  {id:'groupRank',label:'Group Rank (confidence-weighted)',kind:'number',defaultOp:'>='},
  {id:'groupRS',label:'Group RS % (confidence-weighted)',kind:'number',defaultOp:'>='},
  {id:'groupConfidence',label:'Group Confidence %',kind:'number',defaultOp:'>='},
  {id:'groupLeadership',label:'Group leadership (compat)',kind:'number',defaultOp:'>='},
  {id:'sectorRank',label:'Sector proxy raw rank',kind:'number',defaultOp:'>='},
  {id:'sectorProxyConfidence',label:'Sector proxy confidence %',kind:'number',defaultOp:'>='},
  {id:'sectorCorrelationStability',label:'Sector correlation stability %',kind:'number',defaultOp:'>='},
  {id:'industryRank',label:'Industry proxy raw rank',kind:'number',defaultOp:'>='},
  {id:'industryProxyConfidence',label:'Industry proxy confidence %',kind:'number',defaultOp:'>='},
  {id:'industryCorrelationStability',label:'Industry correlation stability %',kind:'number',defaultOp:'>='},
  {id:'sectorProxy',label:'Sector proxy',kind:'text',defaultOp:'contains'},
  {id:'industryProxy',label:'Industry proxy',kind:'text',defaultOp:'contains'},
  {id:'rsRank',label:'RS Rank',kind:'number',defaultOp:'>='},
  {id:'rsAcceleration',label:'RS Δ',kind:'number',defaultOp:'>'},
  {id:'confluence',label:'Evidence 0-5 (compat)',kind:'number',defaultOp:'>='},
  {id:'freshnessScore',label:'Legacy Freshness',kind:'number',defaultOp:'>='},
  {id:'neglectedScore',label:'Legacy Neglected',kind:'number',defaultOp:'>='},
  {id:'lateralBaseScore',label:'Lateral Base 0-100',kind:'number',defaultOp:'>='},
  {id:'contractionQuality',label:'Contraction Quality 0-100',kind:'number',defaultOp:'>='},
  {id:'launchReadiness',label:'Launch Readiness 0-100',kind:'number',defaultOp:'>='},
  {id:'neglectedLaunchScore',label:'Lateral Neglect Launch 0-100',kind:'number',defaultOp:'>='},
  {id:'lateralBaseCandidate',label:'Lateral Base candidate',kind:'boolean',defaultOp:'true'},
  {id:'lateralBaseReasons',label:'Lateral Base reason',kind:'text',defaultOp:'contains'},
  {id:'volumeRatio',label:'Volume x',kind:'number',defaultOp:'>='},
  {id:'breakoutPct',label:'Breakout %',kind:'number',defaultOp:'>='},
  {id:'distance10w',label:'10W distance %',kind:'number',defaultOp:'between',placeholder:'-3,8'},
  {id:'distance30w',label:'30W distance %',kind:'number',defaultOp:'between',placeholder:'-5,15'},
  {id:'trendTemplatePasses',label:'Trend Template /8',kind:'number',defaultOp:'>='},
  {id:'stage2AgeWeeks',label:'Stage 2 age weeks',kind:'number',defaultOp:'<='},
  {id:'baseWeeks',label:'Local base weeks',kind:'number',defaultOp:'>='},
  {id:'tightRange20',label:'20D range %',kind:'number',defaultOp:'<='},
  {id:'atrCompression',label:'ATR compression %',kind:'number',defaultOp:'>='},
  {id:'prior9mReturn',label:'Prior 9M %',kind:'number',defaultOp:'<='},
  {id:'return3m',label:'Recent 3M %',kind:'number',defaultOp:'>='},
  {id:'avgDollarVolume20',label:'Avg $ Volume',kind:'number',defaultOp:'>='},
  {id:'setupTags',label:'Setup tag',kind:'text',defaultOp:'contains'},
  {id:'fundamentalSupport',label:'Fundamental support',kind:'boolean',defaultOp:'true'},
  {id:'fundamentalEvidenceScore',label:'Fundamental Evidence 0-100',kind:'number',defaultOp:'>='},
  {id:'fundamentalEvidenceConfidence',label:'Fundamental confidence %',kind:'number',defaultOp:'>='},
  {id:'fundamentalEvidenceCoverage',label:'Fundamental coverage %',kind:'number',defaultOp:'>='},
  {id:'revenueYoY',label:'Revenue YoY %',kind:'number',defaultOp:'>='},
  {id:'epsYoY',label:'EPS YoY %',kind:'number',defaultOp:'>='},
  {id:'operatingCashFlowYoY',label:'Operating cash flow YoY %',kind:'number',defaultOp:'>='},
  {id:'freeCashFlowYoY',label:'Free cash flow YoY %',kind:'number',defaultOp:'>='},
  {id:'freeCashFlowMargin',label:'Free cash flow margin %',kind:'number',defaultOp:'>='},
  {id:'totalDebtYoY',label:'Total debt YoY % (lower better)',kind:'number',defaultOp:'<='},
  {id:'netDebt',label:'Net debt (lower better)',kind:'number',defaultOp:'<='},
  {id:'shareDilutionYoY',label:'Share dilution YoY % (lower better)',kind:'number',defaultOp:'<='},
  {id:'extended',label:'Extended',kind:'boolean',defaultOp:'false'},
  {id:'changedToday',label:'Changed since last scan',kind:'boolean',defaultOp:'true'},
  {id:'stageChanged',label:'Stage changed',kind:'boolean',defaultOp:'true'},
  {id:'newSetupTags',label:'New setup since last scan',kind:'text',defaultOp:'contains'},
  {id:'changeImpact',label:'Change impact',kind:'number',defaultOp:'>='},
  {id:'opportunityDelta',label:'Δ Opportunity Score',kind:'number',defaultOp:'>='},
  {id:'rsRankDelta',label:'Δ RS Rank',kind:'number',defaultOp:'>='},

  // Rich shared evidence generated after the nightly scan from the same 5Y cache.
  {id:'return1w',label:'RICH return 1W %',kind:'number',defaultOp:'>='},
  {id:'return1m',label:'RICH return 1M %',kind:'number',defaultOp:'>='},
  {id:'return2y',label:'RICH return 2Y %',kind:'number',defaultOp:'>='},
  {id:'return3y',label:'RICH return 3Y %',kind:'number',defaultOp:'>='},
  {id:'return5y',label:'RICH return 5Y %',kind:'number',defaultOp:'>='},
  {id:'distance20',label:'RICH distance 20DMA %',kind:'number',defaultOp:'between',placeholder:'-5,10'},
  {id:'distance100',label:'RICH distance 100DMA %',kind:'number',defaultOp:'between',placeholder:'-10,20'},
  {id:'distance150',label:'RICH distance 150DMA %',kind:'number',defaultOp:'between',placeholder:'-10,25'},
  {id:'atr14Pct',label:'RICH ATR14 %',kind:'number',defaultOp:'<='},
  {id:'realizedVol20',label:'RICH realized vol 20D %',kind:'number',defaultOp:'<='},
  {id:'realizedVol60',label:'RICH realized vol 60D %',kind:'number',defaultOp:'<='},
  {id:'avgDollarVolume50',label:'RICH avg $ volume 50D',kind:'number',defaultOp:'>='},
  {id:'upDownVolume20',label:'RICH up/down volume 20D',kind:'number',defaultOp:'>='},
  {id:'upDownVolume50',label:'RICH up/down volume 50D',kind:'number',defaultOp:'>='},
  {id:'rs1m',label:'RICH relative strength 1M %',kind:'number',defaultOp:'>='},
  {id:'richRs3m',label:'RICH relative strength 3M %',kind:'number',defaultOp:'>='},
  {id:'richRs6m',label:'RICH relative strength 6M %',kind:'number',defaultOp:'>='},
  {id:'richRs12m',label:'RICH relative strength 12M %',kind:'number',defaultOp:'>='},
  {id:'distance2yHigh',label:'RICH distance 2Y high %',kind:'number',defaultOp:'>='},
  {id:'maxDrawdown1y',label:'RICH max drawdown 1Y %',kind:'number',defaultOp:'>='},
  {id:'revenueQoQ',label:'RICH revenue QoQ %',kind:'number',defaultOp:'>='},
  {id:'epsQoQ',label:'RICH EPS QoQ %',kind:'number',defaultOp:'>='},
  {id:'operatingMargin',label:'RICH operating margin %',kind:'number',defaultOp:'>='},
  {id:'inventoryQoQ',label:'RICH inventory QoQ %',kind:'number',defaultOp:'<='},
  {id:'inventoryToSales',label:'RICH inventory / sales',kind:'number',defaultOp:'<='},
  {id:'fundamentalsAgeDays',label:'RICH fundamentals age days',kind:'number',defaultOp:'<='},
]

export const opsByKind={
  number:['>','>=','<','<=','=','!=','between'] as RuleOp[],
  text:['contains','=','!='] as RuleOp[],
  boolean:['true','false'] as RuleOp[],
}

export const uid=()=>Math.random().toString(36).slice(2,9)
export const makeRule=(field='rsRank'):Rule=>{
  const def=fieldDefs.find(x=>x.id===field)||fieldDefs[0]
  return {id:uid(),field:def.id,op:def.defaultOp,value:''}
}
export const makeGroup=(logic:Logic='ALL',rules:Rule[]=[makeRule()]):RuleGroup=>({id:uid(),logic,rules})

const scalar=(stock:any,field:string)=>{
  if(field==='focusBlend')return stockScoutFocusBlend(stock)
  if(field==='opportunityScore')return stock.opportunityScore??stock.emergingLeaderScore??stock.score
  return stock[field]
}
const textValue=(value:any)=>Array.isArray(value)?value.join(' | '):String(value??'')
const asNumber=(value:any)=>{const n=Number(value);return Number.isFinite(n)?n:NaN}

export function validateRule(rule:Rule):string|null{
  const def=fieldDefs.find(field=>field.id===rule.field)
  if(!def)return'Unknown field'
  if(def.kind==='boolean')return null
  const input=rule.value.trim()
  if(!input)return'Enter a value'
  if(def.kind==='text')return null
  if(rule.op==='between'){
    const parts=input.split(',').map(value=>value.trim())
    if(parts.length!==2||parts.some(value=>!value||!Number.isFinite(Number(value))))return'Use two numbers separated by a comma'
    return null
  }
  if(!Number.isFinite(Number(input)))return'Enter a valid number'
  return null
}

export function invalidRules(groups:RuleGroup[]){
  return groups.flatMap(group=>group.rules.filter(rule=>validateRule(rule)!==null))
}

export function matchesRule(stock:any,rule:Rule):boolean{
  if(validateRule(rule))return false
  const raw=scalar(stock,rule.field)
  if(rule.op==='true')return raw===true
  if(rule.op==='false')return raw===false
  if(rule.op==='contains')return textValue(raw).toLowerCase().includes(rule.value.trim().toLowerCase())
  const def=fieldDefs.find(x=>x.id===rule.field)
  if(def?.kind==='text'){
    const left=textValue(raw).toLowerCase(),right=rule.value.trim().toLowerCase()
    return rule.op==='!='?left!==right:left===right
  }
  const left=asNumber(raw)
  if(!Number.isFinite(left))return false
  if(rule.op==='between'){
    const parts=rule.value.split(',').map(x=>x.trim())
    if(parts.length!==2||parts.some(x=>!x))return false
    const [a,b]=parts.map(Number)
    if(!Number.isFinite(a)||!Number.isFinite(b))return false
    return left>=Math.min(a,b)&&left<=Math.max(a,b)
  }
  const right=Number(rule.value)
  if(!Number.isFinite(right))return false
  if(rule.op==='>')return left>right
  if(rule.op==='>=')return left>=right
  if(rule.op==='<')return left<right
  if(rule.op==='<=')return left<=right
  if(rule.op==='!=')return left!==right
  return left===right
}

export function matchesGroups(stock:any,groups:RuleGroup[],rootLogic:Logic):boolean{
  const active=groups.map(group=>({...group,rules:group.rules.filter(rule=>!validateRule(rule))})).filter(group=>group.rules.length)
  if(!active.length)return true
  const groupMatch=(g:RuleGroup)=>g.logic==='ALL'?g.rules.every(r=>matchesRule(stock,r)):g.rules.some(r=>matchesRule(stock,r))
  return rootLogic==='ALL'?active.every(groupMatch):active.some(groupMatch)
}

const rule=(field:string,op:RuleOp,value=''):Rule=>({id:uid(),field,op,value})
const group=(logic:Logic,rules:Rule[]):RuleGroup=>({id:uid(),logic,rules})

export const builtInScreens:ScreenState[]=[
  {
    name:'Prime / Ready Opportunities',rootLogic:'ALL',recipe:'All',query:'',pageSize:100,
    visibility:{opportunityTier:true,opportunityRank:true,opportunityPotential:true,opportunityTiming:true,rsRank:true,volumeRatio:true,fundamentalEvidenceScore:true},
    sorting:[{id:'opportunityScore',desc:true},{id:'opportunityRank',desc:true},{id:'opportunityTiming',desc:true},{id:'opportunityPotential',desc:true}],
    groups:[group('ALL',[rule('opportunityScore','>=','80'),rule('opportunityRank','>=','90'),rule('extended','false')])],
  },
  {
    name:'Scout Tier A',rootLogic:'ALL',recipe:'All',query:'',pageSize:100,
    visibility:{scoutTierLabel:true,maClusterTierLabel:true,maClusterScore:true,maClusterSpreadPct:true,maClusterVolumePace:true,opportunityScore:true,emergingArchetype:true,emergingEvidenceCount:true},
    sorting:[{id:'scoutTierRank',desc:true},{id:'maClusterEntrySignal',desc:true},{id:'opportunityScore',desc:true},{id:'rsRank',desc:true},{id:'maClusterScore',desc:true}],
    groups:[group('ALL',[rule('scoutTier','=','A')])],
  },
  {
    name:'Scout Tier A+B',rootLogic:'ALL',recipe:'All',query:'',pageSize:100,
    visibility:{scoutTierLabel:true,maClusterTierLabel:true,maClusterScore:true,maClusterSpreadPct:true,maClusterVolumePace:true,opportunityScore:true,emergingArchetype:true,emergingEvidenceCount:true},
    sorting:[{id:'scoutTierRank',desc:true},{id:'maClusterEntrySignal',desc:true},{id:'opportunityScore',desc:true},{id:'rsRank',desc:true},{id:'maClusterScore',desc:true}],
    groups:[group('ALL',[rule('scoutTierRank','>=','2')])],
  },
  {
    name:'MA Cluster ENTRY',rootLogic:'ALL',recipe:'All',query:'',pageSize:100,
    visibility:{scoutTierLabel:true,maClusterTierLabel:true,maClusterScore:true,maClusterSpreadPct:true,maClusterVolumePace:true,ma10wSlope4w:true,ma30wSlope4w:true,emergingArchetype:true,emergingEvidenceCount:true},
    sorting:[{id:'scoutTierRank',desc:true},{id:'maClusterTierRank',desc:true},{id:'maClusterScore',desc:true},{id:'opportunityScore',desc:true}],
    groups:[group('ALL',[rule('maClusterPhase','=','ENTRY')])],
  },
  {
    name:'MA Cluster Ready',rootLogic:'ALL',recipe:'All',query:'',pageSize:100,
    visibility:{scoutTierLabel:true,maClusterTierLabel:true,maClusterScore:true,maClusterSpreadPct:true,maClusterPricePct:true,ma10wSlope4w:true,ma30wSlope4w:true,opportunityScore:true,emergingArchetype:true},
    sorting:[{id:'scoutTierRank',desc:true},{id:'maClusterTierRank',desc:true},{id:'opportunityScore',desc:true},{id:'maClusterScore',desc:true},{id:'rsRank',desc:true}],
    groups:[group('ALL',[rule('maClusterPhase','=','READY')])],
  },
  {
    name:'MA Cluster Watch',rootLogic:'ALL',recipe:'All',query:'',pageSize:100,
    visibility:{scoutTierLabel:true,maClusterTierLabel:true,maClusterScore:true,maClusterSpreadPct:true,maClusterPricePct:true,ma10wSlope4w:true,ma30wSlope4w:true,opportunityScore:true},
    sorting:[{id:'scoutTierRank',desc:true},{id:'maClusterScore',desc:true},{id:'opportunityScore',desc:true},{id:'rsRank',desc:true}],
    groups:[group('ALL',[rule('maClusterPhase','=','WATCH')])],
  },
  {
    name:'Tier A Cluster Timing',rootLogic:'ALL',recipe:'All',query:'',pageSize:100,
    visibility:{scoutTierLabel:true,maClusterTierLabel:true,maClusterScore:true,maClusterSpreadPct:true,maClusterVolumePace:true,maClusterPricePct:true,opportunityScore:true,emergingEvidenceCount:true},
    sorting:[{id:'scoutTierRank',desc:true},{id:'maClusterScore',desc:true},{id:'opportunityScore',desc:true},{id:'rsRank',desc:true}],
    groups:[group('ALL',[rule('maClusterTier','=','A')])],
  },
  {
    name:'A+ Emerging',rootLogic:'ALL',recipe:'All',query:'',pageSize:100,
    visibility:{emergingArchetype:true,emergingEvidenceCount:true,neglectedEmergingScore:true,resetReawakeningScore:true},
    sorting:[{id:'opportunityScore',desc:true},{id:'emergingEvidenceCount',desc:true},{id:'rsRank',desc:true}],
    groups:[group('ALL',[rule('aPlusEmergingSetup','true')])],
  },
  {
    name:'Emerging Leaders',rootLogic:'ALL',recipe:'All',query:'',pageSize:100,
    visibility:{emergingArchetype:true,emergingEvidenceCount:true},
    sorting:[{id:'opportunityScore',desc:true},{id:'emergingEvidenceCount',desc:true},{id:'rsRank',desc:true},{id:'volumeRatio',desc:true}],
    groups:[group('ALL',[rule('emergingLeaderCandidate','true')])],
  },
  {
    name:'Reset → Reawakening',rootLogic:'ALL',recipe:'All',query:'',pageSize:100,
    visibility:{emergingArchetype:true,emergingEvidenceCount:true,resetReawakeningScore:true,reawakeningStructureScore:true,ignitionScore:true,recoveryScore:true},
    sorting:[{id:'resetReawakeningScore',desc:true},{id:'emergingEvidenceCount',desc:true},{id:'ignitionScore',desc:true},{id:'rsRank',desc:true}],
    groups:[group('ALL',[rule('emergingLeaderCandidate','true'),rule('emergingArchetype','contains','Reset Reawakening')])],
  },
  {
    name:'Neglected → Emerging',rootLogic:'ALL',recipe:'All',query:'',pageSize:100,
    visibility:{emergingArchetype:true,emergingEvidenceCount:true,neglectedEmergingScore:true,resetScore:true,neglectHistoryScore:true,triggerReadinessScore:true},
    sorting:[{id:'neglectedEmergingScore',desc:true},{id:'emergingEvidenceCount',desc:true},{id:'rsTurnScore',desc:true},{id:'rsRank',desc:true}],
    groups:[group('ALL',[rule('emergingLeaderCandidate','true'),rule('emergingArchetype','contains','Neglected Emerging')])],
  },
  {
    name:'Fundamental Confirmed Emerging',rootLogic:'ALL',recipe:'All',query:'',pageSize:100,
    visibility:{emergingArchetype:true,emergingEvidenceCount:true,fundamentalEvidenceScore:true,fundamentalEvidenceConfidence:true,revenueYoY:true,epsYoY:true,freeCashFlowYoY:true,freeCashFlowMargin:true,totalDebtYoY:true,shareDilutionYoY:true},
    sorting:[{id:'opportunityScore',desc:true},{id:'fundamentalEvidenceScore',desc:true},{id:'fundamentalEvidenceConfidence',desc:true},{id:'rsRank',desc:true}],
    groups:[group('ALL',[rule('emergingLeaderCandidate','true'),rule('fundamentalEvidenceScore','>=','65'),rule('fundamentalEvidenceConfidence','>=','50')])],
  },
  {
    name:'Fundamental Quality',rootLogic:'ALL',recipe:'All',query:'',pageSize:100,
    visibility:{fundamentalEvidenceScore:true,fundamentalEvidenceConfidence:true,fundamentalEvidenceCoverage:true,revenueYoY:true,epsYoY:true,operatingCashFlowYoY:true,freeCashFlowYoY:true,freeCashFlowMargin:true,totalDebtYoY:true,netDebt:true,shareDilutionYoY:true},
    sorting:[{id:'fundamentalEvidenceScore',desc:true},{id:'fundamentalEvidenceConfidence',desc:true},{id:'revenueYoY',desc:true},{id:'freeCashFlowYoY',desc:true}],
    groups:[group('ALL',[rule('fundamentalEvidenceScore','>=','65'),rule('fundamentalEvidenceConfidence','>=','70')])],
  },
  {
    name:'Group-confirmed Emerging',rootLogic:'ALL',recipe:'All',query:'',pageSize:100,
    visibility:{emergingArchetype:true,emergingEvidenceCount:true,groupRank:true,groupRS:true,groupConfidence:true,leadershipScore:true},
    sorting:[{id:'leadershipScore',desc:true},{id:'opportunityScore',desc:true},{id:'groupConfidence',desc:true},{id:'groupRank',desc:true}],
    groups:[group('ALL',[rule('emergingLeaderCandidate','true'),rule('groupConfidence','>=','35'),rule('groupRank','>=','60')])],
  },
  {
    name:'Fresh Breakouts',rootLogic:'ALL',recipe:'All',query:'',pageSize:100,visibility:{},
    sorting:[{id:'volumeRatio',desc:true},{id:'rsRank',desc:true},{id:'breakoutPct',desc:true}],
    groups:[group('ALL',[rule('breakoutPct','between','-1.5,5'),rule('volumeRatio','>=','1.5'),rule('rsRank','>=','70'),rule('rsAcceleration','>','0'),rule('extended','false')])],
  },
  {
    name:'Tight Bases',rootLogic:'ALL',recipe:'All',query:'',pageSize:100,visibility:{lateralBaseScore:true,contractionQuality:true},
    sorting:[{id:'lateralBaseScore',desc:true},{id:'contractionQuality',desc:true},{id:'baseWeeks',desc:true},{id:'rsRank',desc:true}],
    groups:[group('ALL',[rule('tightRange20','<=','12'),rule('atrCompression','>=','20'),rule('baseWeeks','>=','6'),rule('extended','false')])],
  },
  {
    name:'Since Last Scan',rootLogic:'ALL',recipe:'All',query:'',pageSize:100,visibility:{},
    sorting:[{id:'changeImpact',desc:true},{id:'rsRankDelta',desc:true},{id:'opportunityDelta',desc:true},{id:'rsRank',desc:true}],
    groups:[group('ALL',[rule('changedToday','true')])],
  },
]
