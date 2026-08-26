import test from 'node:test'
import assert from 'node:assert/strict'
import {
  calculatePositionSize,
  expandedCandleBounds,
  levelFitsExpandedCandleBounds,
  matchesGroups,
  matchesRule,
  normalizeEodDate,
  preserveScanOrder,
  stockScoutFocusBlend,
  validateRule,
  weekStartUtc,
  type Rule,
  type RuleGroup,
} from './filterEngine.ts'

const rule=(value:string,op:Rule['op']='>='):Rule=>({id:'rule',field:'rsRank',op,value})

test('empty and non-numeric numeric rules are invalid and never match',()=>{
  for(const value of ['', 'not-a-number']){
    const candidate=rule(value)
    assert.ok(validateRule(candidate))
    assert.equal(matchesRule({rsRank:90},candidate),false)
  }
})

test('malformed between rules are invalid and never match',()=>{
  for(const value of ['10','10,','10,nope','10,20,30']){
    const candidate=rule(value,'between')
    assert.ok(validateRule(candidate))
    assert.equal(matchesRule({rsRank:15},candidate),false)
  }
})

test('invalid rules are ignored without widening an existing valid filter',()=>{
  const groups:RuleGroup[]=[{id:'group',logic:'ALL',rules:[rule('80'),{...rule(''),id:'invalid'}]}]
  assert.equal(matchesGroups({rsRank:90},groups,'ALL'),true)
  assert.equal(matchesGroups({rsRank:70},groups,'ALL'),false)
})

test('default ordering follows explicit scanOrder without mutating the scan',()=>{
  const rows=[{ticker:'BBB',scanOrder:2},{ticker:'AAA',scanOrder:0},{ticker:'CCC',scanOrder:1}]
  const ordered=preserveScanOrder(rows)
  assert.deepEqual(ordered.map(row=>row.ticker),['AAA','CCC','BBB'])
  assert.deepEqual(rows.map(row=>row.ticker),['BBB','AAA','CCC'])
})

test('canonical StockScout value never falls back to opportunity analysis',()=>{
  assert.equal(stockScoutFocusBlend({focusBlend:81,opportunityScore:99}),81)
  assert.equal(stockScoutFocusBlend({score:77,opportunityScore:99}),77)
  assert.equal(stockScoutFocusBlend({opportunityScore:99}),null)
  assert.equal(matchesRule({focus_blend:81},{id:'focus',field:'focusBlend',op:'>=',value:'80'}),true)
})

test('private epoch-second bars normalize before weekly aggregation',()=>{
  const friday=Date.UTC(2026,7,21)/1000
  assert.equal(normalizeEodDate(friday),'2026-08-21')
  assert.equal(normalizeEodDate(friday*1000),'2026-08-21')
  assert.equal(weekStartUtc(friday),'2026-08-17')
})

test('structural levels use ten percent of candle range as chart padding',()=>{
  const rows=[{low:90,high:100},{low:95,high:120}]
  assert.deepEqual(expandedCandleBounds(rows),{minimum:87,maximum:123})
  assert.equal(levelFitsExpandedCandleBounds(87,rows),true)
  assert.equal(levelFitsExpandedCandleBounds(86.99,rows),false)
})

test('position sizing requires entry-ready and an actual tactical stop',()=>{
  assert.equal(calculatePositionSize('trigger_pending',10_000_000,.5,100,95).enabled,false)
  assert.match(calculatePositionSize('entry_ready',10_000_000,.5,100,null).reason,/structural invalidation is never used/i)
  const result=calculatePositionSize('entry_ready',10_000_000,.5,100,95)
  assert.equal(result.enabled,true)
  assert.equal(result.riskBudget,50_000)
  assert.equal(result.shares,10_000)
  assert.equal(result.positionValue,1_000_000)
  assert.equal(result.riskUsed,50_000)
})
