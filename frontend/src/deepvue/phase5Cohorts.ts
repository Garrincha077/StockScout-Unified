import {builtInScreens,fieldDefs,type FieldDef,type Rule,type RuleGroup,type ScreenState} from './filterEngine.ts'
import {installLegacyConfirmationFields} from './legacyConfirmationUi.ts'

const COHORT_NAMES=[
  'Early Leaders',
  'Confirmed Leaders',
  'Ahead of Minervini',
  'Breakout Confirmed',
  'Watchlist Risk',
] as const

const ensureField=(field:FieldDef)=>{
  if(!fieldDefs.some(existing=>existing.id===field.id))fieldDefs.push(field)
}

const rule=(id:string,field:string,op:Rule['op'],value=''):Rule=>({id,field,op,value})
const group=(id:string,logic:RuleGroup['logic'],rules:Rule[]):RuleGroup=>({id,logic,rules})

// Reuse the exact existing Prime / Ready Opportunities StockScout definition.
// Phase 5 must not invent a second definition of "strong StockScout".
const strongRules=(prefix:string):Rule[]=>[
  rule(`${prefix}-opp`,'opportunityScore','>=','80'),
  rule(`${prefix}-rank`,'opportunityRank','>=','90'),
  rule(`${prefix}-extended`,'extended','false'),
]

export const phase5CohortScreens:ScreenState[]=[
  {
    name:'Early Leaders',rootLogic:'ALL',recipe:'All',query:'',pageSize:100,
    visibility:{opportunityTier:true,opportunityRank:true,opportunityPotential:true,opportunityTiming:true,rsRank:true,originalTTPasses:true,originalBuyScore:true,originalRR:true},
    sorting:[{id:'opportunityScore',desc:true},{id:'opportunityRank',desc:true},{id:'opportunityTiming',desc:true},{id:'opportunityPotential',desc:true}],
    groups:[
      group('p5-early-strong','ALL',strongRules('p5-early')),
      group('p5-early-shadow','ANY',[
        rule('p5-early-status-early','legacyConfirmationStatus','=','EARLY'),
        rule('p5-early-status-neutral','legacyConfirmationStatus','=','NEUTRAL'),
        rule('p5-early-status-conflict','legacyConfirmationStatus','=','CONFLICT'),
      ]),
    ],
  },
  {
    name:'Confirmed Leaders',rootLogic:'ALL',recipe:'All',query:'',pageSize:100,
    visibility:{opportunityTier:true,opportunityRank:true,opportunityPotential:true,opportunityTiming:true,rsRank:true,originalTTPasses:true,originalBuyScore:true,originalRR:true},
    sorting:[{id:'opportunityScore',desc:true},{id:'opportunityRank',desc:true},{id:'opportunityTiming',desc:true},{id:'opportunityPotential',desc:true}],
    groups:[group('p5-confirmed','ALL',[
      ...strongRules('p5-confirmed'),
      rule('p5-confirmed-status','legacyConfirmationStatus','=','CONFIRMED'),
    ])],
  },
  {
    name:'Ahead of Minervini',rootLogic:'ALL',recipe:'All',query:'',pageSize:100,
    visibility:{emergingArchetype:true,emergingEvidenceCount:true,opportunityTier:true,opportunityRank:true,rsRank:true,originalTTPasses:true,originalBuyScore:true},
    sorting:[{id:'opportunityScore',desc:true},{id:'emergingLeaderScore',desc:true},{id:'rsRank',desc:true},{id:'opportunityRank',desc:true}],
    groups:[group('p5-ahead','ALL',[
      rule('p5-ahead-emerging','emergingLeaderCandidate','true'),
      rule('p5-ahead-tt','originalTTPasses','<','7'),
      rule('p5-ahead-extended','extended','false'),
      rule('p5-ahead-risk','legacyConfirmationStatus','!=','RISK'),
    ])],
  },
  {
    name:'Breakout Confirmed',rootLogic:'ALL',recipe:'All',query:'',pageSize:100,
    visibility:{opportunityTier:true,opportunityRank:true,opportunityTiming:true,rsRank:true,volumeRatio:true,breakoutPct:true,originalTTPasses:true,originalBuyScore:true,originalRR:true},
    sorting:[{id:'opportunityScore',desc:true},{id:'opportunityRank',desc:true},{id:'volumeRatio',desc:true},{id:'rsRank',desc:true}],
    groups:[group('p5-breakout','ALL',[
      ...strongRules('p5-breakout'),
      rule('p5-breakout-volume','originalBreakoutVolumeConfirmed','true'),
    ])],
  },
  {
    name:'Watchlist Risk',rootLogic:'ALL',recipe:'All',query:'',pageSize:100,
    visibility:{opportunityTier:true,opportunityRank:true,rsRank:true,originalSellScore:true,originalRiskPct:true,originalTTPasses:true},
    sorting:[{id:'originalSellScore',desc:true},{id:'opportunityScore',desc:true},{id:'rsRank',desc:true}],
    groups:[group('p5-risk','ALL',[
      rule('p5-risk-sell','originalRunSellSignal','true'),
    ])],
  },
]

export function installPhase5Cohorts(){
  installLegacyConfirmationFields()
  ensureField({id:'originalTTPasses',label:'LEGACY Trend Template passes /8',kind:'number',defaultOp:'<'})
  ensureField({id:'originalBreakoutVolumeConfirmed',label:'LEGACY breakout volume confirmed',kind:'boolean',defaultOp:'true'})
  ensureField({id:'originalRunSellSignal',label:'LEGACY original-run SELL',kind:'boolean',defaultOp:'true'})

  const existing=new Set(builtInScreens.map(screen=>screen.name))
  for(const screen of phase5CohortScreens){
    if(!existing.has(screen.name)){
      builtInScreens.push(screen)
      existing.add(screen.name)
    }
  }
}

export function isPhase5CohortName(name:string):boolean{
  return (COHORT_NAMES as readonly string[]).includes(name)
}

installPhase5Cohorts()
