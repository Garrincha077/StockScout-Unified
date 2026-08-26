import test from 'node:test'
import assert from 'node:assert/strict'
import {fieldDefs,matchesRule} from './filterEngine.ts'
import {RetryJsonCache,fetchJsonWithRetry,marketRegimeLabel,mergeLegacyConfirmationSidecar,nextGridCount} from './runtime.ts'

test('marketRegimeLabel accepts scalar and production nested regime payloads',()=>{
  assert.equal(marketRegimeLabel({regime:'confirmed'}),'CONFIRMED')
  assert.equal(marketRegimeLabel({regime:{state:'under_pressure',guppy_state:'RWB'}}),'UNDER PRESSURE')
  assert.equal(marketRegimeLabel({regime:{guppy_state:'RWB'}}),'RWB')
  assert.equal(marketRegimeLabel({regime:{state:{unexpected:true}}}),'UNKNOWN')
})

test('nextGridCount advances in bounded batches',()=>{
  assert.equal(nextGridCount(16,123),32)
  assert.equal(nextGridCount(112,123),123)
  assert.equal(nextGridCount(123,123),123)
})

test('fetchJsonWithRetry recovers from a transient HTTP failure',async()=>{
  let calls=0
  const fetcher=async()=>{
    calls++
    return calls===1?new Response('nope',{status:503}):Response.json({ok:true})
  }
  const value=await fetchJsonWithRetry<{ok:boolean}>('/chart.json',{attempts:2,baseDelayMs:0},fetcher)
  assert.deepEqual(value,{ok:true})
  assert.equal(calls,2)
})

test('versioned chart requests cache normally and only cache-bust after an error',async()=>{
  const requested:string[]=[]
  const cacheModes:(RequestCache|undefined)[]=[]
  let calls=0
  const fetcher=async(input:RequestInfo|URL,init?:RequestInit)=>{
    requested.push(String(input));cacheModes.push(init?.cache);calls++
    return calls===1?new Response('retry',{status:503}):Response.json({ok:true})
  }
  await fetchJsonWithRetry<{ok:boolean}>('/data/charts/001.json?v=snapshot',{attempts:2,baseDelayMs:0},fetcher)
  assert.equal(cacheModes[0],'default')
  assert.equal(requested[0],'/data/charts/001.json?v=snapshot')
  assert.equal(cacheModes[1],'no-store')
  assert.match(requested[1],/\/data\/charts\/001\.json\?v=snapshot&_cb=/)
})

test('RetryJsonCache evicts a rejected promise so a later request can recover',async()=>{
  let calls=0
  const fetcher=async()=>{
    calls++
    return calls===1?new Response('nope',{status:503}):Response.json({rows:[1,2,3]})
  }
  const cache=new RetryJsonCache<{rows:number[]}>(fetcher,async()=>{}, {attempts:1,baseDelayMs:0})
  await assert.rejects(cache.load('snapshot:001','/chart.json'))
  const value=await cache.load('snapshot:001','/chart.json')
  assert.deepEqual(value,{rows:[1,2,3]})
  assert.equal(calls,2)
})

test('LEGACY sidecar merge adds only transparent status fields',()=>{
  const stocks=[{ticker:'AAA',opportunityScore:82},{ticker:'BBB',opportunityScore:75}]
  const merged=mergeLegacyConfirmationSidecar(stocks,{
    affectsStockScout:false,
    source:{generatedAt:'snapshot-1'},
    byTicker:{AAA:{status:'CONFIRMED',available:true,reasons:['ORIGINAL_RUN_BUY']}},
  },'snapshot-1')
  assert.deepEqual(merged[0],{
    ticker:'AAA',opportunityScore:82,
    legacyConfirmationStatus:'CONFIRMED',
    legacyConfirmationReasons:['ORIGINAL_RUN_BUY'],
  })
  assert.deepEqual(merged[1],stocks[1])
  assert.equal(stocks[0].opportunityScore,82)
  assert.equal('legacyConfirmationStatus' in stocks[0],false)
})

test('LEGACY sidecar merge refuses a stale snapshot',()=>{
  const stocks=[{ticker:'AAA',opportunityScore:82}]
  const merged=mergeLegacyConfirmationSidecar(stocks,{
    affectsStockScout:false,
    source:{generatedAt:'old-snapshot'},
    byTicker:{AAA:{status:'RISK',available:true,reasons:['ORIGINAL_RUN_SELL']}},
  },'new-snapshot')
  assert.equal(merged,stocks)
  assert.equal('legacyConfirmationStatus' in merged[0],false)
})

test('LEGACY confirmation values work with existing text filter semantics',()=>{
  const added=[] as string[]
  if(!fieldDefs.some(field=>field.id==='legacyConfirmationStatus')){
    fieldDefs.push({id:'legacyConfirmationStatus',label:'LEGACY confirmation',kind:'text',defaultOp:'='})
    added.push('legacyConfirmationStatus')
  }
  if(!fieldDefs.some(field=>field.id==='legacyConfirmationReasons')){
    fieldDefs.push({id:'legacyConfirmationReasons',label:'LEGACY confirmation reason',kind:'text',defaultOp:'contains'})
    added.push('legacyConfirmationReasons')
  }
  try{
    const stock={
      ticker:'AAA',
      legacyConfirmationStatus:'CONFIRMED',
      legacyConfirmationReasons:['ORIGINAL_RUN_BUY','TREND_TEMPLATE_PASS'],
    }
    assert.equal(matchesRule(stock,{id:'status',field:'legacyConfirmationStatus',op:'=',value:'confirmed'}),true)
    assert.equal(matchesRule(stock,{id:'reason',field:'legacyConfirmationReasons',op:'contains',value:'trend_template'}),true)
    assert.equal(matchesRule(stock,{id:'risk',field:'legacyConfirmationStatus',op:'=',value:'risk'}),false)
  }finally{
    for(const id of added){
      const index=fieldDefs.findIndex(field=>field.id===id)
      if(index>=0)fieldDefs.splice(index,1)
    }
  }
})
