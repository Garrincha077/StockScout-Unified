import test from 'node:test'
import assert from 'node:assert/strict'
import {buildReviewInbox,dataHealth,explainStock,lastCompletedMarketSession,matchesReviewScope,reviewScopeLabel} from './phase4Review.ts'

test('review inbox separates today changes from new universe members',()=>{
  const universe=[
    {ticker:'AAA',changedToday:true,changeImpact:2,opportunityScore:70},
    {ticker:'BBB',changedToday:true,newUniverseMember:true,changeImpact:8,opportunityScore:55},
    {ticker:'CCC',newUniverseMember:true,opportunityScore:90},
  ]
  const inbox=buildReviewInbox(universe)
  assert.deepEqual(inbox.today.map(x=>x.ticker),['BBB','AAA'])
  assert.deepEqual(inbox.newSinceLastScan.map(x=>x.ticker),['CCC','BBB'])
})

test('review scope uses the same transparent membership flags as the inbox',()=>{
  const today={ticker:'AAA',changedToday:true,newUniverseMember:false}
  const fresh={ticker:'BBB',changedToday:false,newUniverseMember:true}
  assert.equal(matchesReviewScope(today,'today'),true)
  assert.equal(matchesReviewScope(today,'new'),false)
  assert.equal(matchesReviewScope(fresh,'new'),true)
  assert.equal(matchesReviewScope(fresh,'today'),false)
  assert.equal(matchesReviewScope(today,null),true)
  assert.equal(reviewScopeLabel('today'),'Today / changed')
  assert.equal(reviewScopeLabel('new'),'New since last scan')
})

test('why this stock is a transparent decomposition of existing fields',()=>{
  const lines=explainStock({
    ticker:'AAA',primarySetup:'Fresh Breakout',stage:2,opportunityScore:84,opportunityTier:'READY',opportunityRank:96,
    opportunityPotential:88,opportunityTiming:79,rsRank:94,groupRank:81,fundamentalEvidenceScore:76,volumeRatio:1.8,
    changeLabels:['New setup: Fresh Breakout'],
  })
  assert.equal(lines[0],'Fresh Breakout · Stage 2')
  assert.ok(lines.some(line=>line.includes('Opportunity 84')&&line.includes('READY')&&line.includes('rank 96')))
  assert.ok(lines.some(line=>line.includes('RS 94')&&line.includes('group 81')&&line.includes('fundamentals 76')))
  assert.ok(lines.some(line=>line.startsWith('Since last scan:')))
  assert.equal(lines.length<=5,true)
})

test('health reports exact snapshot mismatch before freshness or validation',()=>{
  const health=dataHealth(
    {generatedAt:'2026-08-20T08:00:00Z',universe:[{ticker:'AAA'}]},
    {generatedAt:'2026-08-20T09:00:00Z',universe:1,marketSession:{date:'2026-08-19',status:'closed'},provenance:{source:{sha256:'a'},publication:{sourceSha256:'a'}}},
    {conclusion:'success',run_id:123},
    Date.parse('2026-08-20T10:00:00Z'),
  )
  assert.equal(health.level,'warn')
  assert.equal(health.label,'Snapshot mismatch')
})

test('v1 health trusts the activated manifest contract without legacy provenance fields',()=>{
  const health=dataHealth(
    {generatedAt:'2026-08-21T22:00:00Z',universe:[{ticker:'AAA'}]},
    {manifestVersion:1,generatedAt:'2026-08-21T22:00:00Z',sessionDate:'2026-08-21',status:'healthy',counts:{candidates:1},health:{status:'healthy',coveragePct:99}},
    null,
    Date.parse('2026-08-22T12:00:00Z'),
  )
  assert.equal(health.level,'ok')
  assert.equal(health.label,'Healthy')
  assert.match(health.detail,/prices are not live/i)
})

test('health does not claim validation green when client status is unavailable',()=>{
  const health=dataHealth(
    {generatedAt:'2026-08-20T08:00:00Z',universe:[{ticker:'AAA'}]},
    {generatedAt:'2026-08-20T08:00:00Z',universe:1,marketSession:{date:'2026-08-19',status:'closed'},provenance:{source:{sha256:'a'},publication:{sourceSha256:'a'}}},
    null,
    Date.parse('2026-08-20T10:00:00Z'),
  )
  assert.equal(health.level,'neutral')
  assert.equal(health.label,'Validation unknown')
  assert.match(health.detail,/validation status is not published/i)
})

test('health exposes a published successful validation when available',()=>{
  const health=dataHealth(
    {generatedAt:'2026-08-20T08:00:00Z',universe:[{ticker:'AAA'}]},
    {generatedAt:'2026-08-20T08:00:00Z',universe:1,marketSession:{date:'2026-08-19',status:'closed'},provenance:{source:{sha256:'a'},publication:{sourceSha256:'a'}}},
    {conclusion:'success',run_id:32355794186},
    Date.parse('2026-08-20T10:00:00Z'),
  )
  assert.equal(health.level,'ok')
  assert.equal(health.label,'Healthy')
  assert.match(health.detail,/32355794186/)
})

test('last completed session skips an observed NYSE holiday',()=>{
  assert.equal(lastCompletedMarketSession(Date.parse('2026-07-03T21:00:00Z')),'2026-07-02')
})
