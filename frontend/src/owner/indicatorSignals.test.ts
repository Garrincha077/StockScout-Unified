import assert from'node:assert/strict'
import test from'node:test'
import{aggregateWeeklyBars,buildIndicatorSeries,confirmationsPass,crossed,exponentialMovingAverage,isUpsloping,linearRegressionSlope,normalizedSlope,simpleMovingAverage,weekStartUtc}from'../../../supabase/functions/_shared/indicatorSignals.ts'

test('moving averages use deterministic rolling and SMA-seeded EMA values',()=>{
  assert.deepEqual(simpleMovingAverage([1,2,3,4],3),[null,null,2,3])
  assert.deepEqual(exponentialMovingAverage([1,2,3,4],3),[null,null,2,3])
})

test('weekly aggregation uses the UTC Monday session key and keeps the latest close',()=>{
  assert.equal(weekStartUtc('2026-08-21'),'2026-08-17')
  assert.deepEqual(aggregateWeeklyBars([
    {time:'2026-08-21',close:10},
    {time:'2026-08-24',close:12},
    {time:'2026-08-25',close:13},
  ]),[
    {time:'2026-08-17',close:10},
    {time:'2026-08-24',close:13},
  ])
})

test('slope is normalized and positive only for a rising window',()=>{
  assert.equal(linearRegressionSlope([1,2,3,4]),1)
  assert.equal(normalizedSlope([1,2,3,4],4),.25)
  assert.equal(isUpsloping([1,2,3,4],4),true)
  assert.equal(isUpsloping([4,3,2,1],4),false)
  assert.equal(isUpsloping([2,2,2,2],4),false)
  assert.equal(isUpsloping([1,2],4),null)
})

test('cross detection compares matching previous and current levels',()=>{
  assert.equal(crossed(9,10,11,10,'up'),true)
  assert.equal(crossed(11,10,9,10,'down'),true)
  assert.equal(crossed(9,10,11,10,'either'),true)
  assert.equal(crossed(9,10,9.5,10,'up'),false)
})

test('indicator confirmations support all/any and report missing history',()=>{
  const bars=Array.from({length:420},(_,index)=>({time:new Date(Date.UTC(2026,0,1+index)).toISOString().slice(0,10),close:100+index}))
  const series=buildIndicatorSeries(bars)
  assert.equal(confirmationsPass(series,['sma50_daily_up'],'all'),true)
  assert.equal(confirmationsPass(series,['sma50_daily_up','sma30_weekly_up'],'all'),true)
  assert.equal(confirmationsPass(series,['sma50_daily_up','sma30_weekly_up'],'any'),true)
  assert.equal(confirmationsPass(buildIndicatorSeries(bars.slice(0,30)),['sma50_daily_up'],'all'),null)
})
