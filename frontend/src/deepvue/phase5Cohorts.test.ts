import test from 'node:test'
import assert from 'node:assert/strict'
import {builtInScreens,fieldDefs,matchesGroups} from './filterEngine.ts'
import {installPhase5Cohorts,isPhase5CohortName,phase5CohortScreens} from './phase5Cohorts.ts'

const screen=(name:string)=>{
  const found=builtInScreens.find(candidate=>candidate.name===name)
  assert.ok(found,`missing built-in screen: ${name}`)
  return found
}
const matches=(name:string,stock:Record<string,unknown>)=>{
  const cohort=screen(name)
  return matchesGroups(stock,cohort.groups,cohort.rootLogic)
}
const strong={opportunityScore:85,opportunityRank:95,extended:false}

test('phase 5 installs five discovery screens once and leaves the existing default first',()=>{
  const first=builtInScreens[0]?.name
  installPhase5Cohorts()
  installPhase5Cohorts()
  assert.equal(first,'Prime / Ready Opportunities')
  assert.equal(builtInScreens[0]?.name,'Prime / Ready Opportunities')
  assert.equal(phase5CohortScreens.length,5)
  for(const cohort of phase5CohortScreens){
    assert.equal(isPhase5CohortName(cohort.name),true)
    assert.equal(builtInScreens.filter(existing=>existing.name===cohort.name).length,1)
  }
  for(const field of ['legacyConfirmationStatus','originalTTPasses','originalBreakoutVolumeConfirmed','originalRunSellSignal']){
    assert.equal(fieldDefs.some(def=>def.id===field),true,`missing Phase 5 field ${field}`)
  }
})

test('Confirmed Leaders reuses strong StockScout rules and requires LEGACY confirmation',()=>{
  assert.equal(matches('Confirmed Leaders',{...strong,legacyConfirmationStatus:'CONFIRMED'}),true)
  assert.equal(matches('Confirmed Leaders',{...strong,legacyConfirmationStatus:'EARLY'}),false)
  assert.equal(matches('Confirmed Leaders',{...strong,opportunityScore:79,legacyConfirmationStatus:'CONFIRMED'}),false)
})

test('Early Leaders is strong StockScout without confirmed or risk shadow status',()=>{
  for(const status of ['EARLY','NEUTRAL','CONFLICT'])assert.equal(matches('Early Leaders',{...strong,legacyConfirmationStatus:status}),true)
  assert.equal(matches('Early Leaders',{...strong,legacyConfirmationStatus:'CONFIRMED'}),false)
  assert.equal(matches('Early Leaders',{...strong,legacyConfirmationStatus:'RISK'}),false)
})

test('Ahead of Minervini uses existing Emerging candidate plus original TT below 7/8',()=>{
  const base={emergingLeaderCandidate:true,originalTTPasses:6,extended:false,legacyConfirmationStatus:'EARLY'}
  assert.equal(matches('Ahead of Minervini',base),true)
  assert.equal(matches('Ahead of Minervini',{...base,originalTTPasses:7}),false)
  assert.equal(matches('Ahead of Minervini',{...base,emergingLeaderCandidate:false}),false)
  assert.equal(matches('Ahead of Minervini',{...base,legacyConfirmationStatus:'RISK'}),false)
})

test('Breakout Confirmed requires the existing strong screen plus original volume confirmation',()=>{
  assert.equal(matches('Breakout Confirmed',{...strong,originalBreakoutVolumeConfirmed:true}),true)
  assert.equal(matches('Breakout Confirmed',{...strong,originalBreakoutVolumeConfirmed:false}),false)
})

test('Watchlist Risk is the frozen original-run SELL observation only',()=>{
  assert.equal(matches('Watchlist Risk',{originalRunSellSignal:true}),true)
  assert.equal(matches('Watchlist Risk',{originalRunSellSignal:false}),false)
})

test('cohort sorting does not silently blend LEGACY into leader ranking',()=>{
  for(const name of ['Early Leaders','Confirmed Leaders','Ahead of Minervini','Breakout Confirmed']){
    const ids=screen(name).sorting.map(sort=>sort.id)
    assert.equal(ids.some(id=>id.startsWith('original')||id.startsWith('legacy')),false,`${name} unexpectedly sorts by LEGACY`)
  }
  assert.equal(screen('Watchlist Risk').sorting[0]?.id,'originalSellScore')
})
