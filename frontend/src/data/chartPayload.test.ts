import test from 'node:test'
import assert from 'node:assert/strict'
import {chartPath,chartRows,chartShard,publicChartManifestUrl,validateChartManifest} from './chartPayload.ts'

const manifest={
  schemaVersion:'stockscout-eod/charts-v1',runId:'run-1',
  storageBaseUrl:'https://example.supabase.co/storage/v1/object/public/stockscout-eod-charts/run-1',
  shards:[{name:'006',sha256:'a'.repeat(64),bytes:10,tickerCount:1}],shardsByTicker:{AAA:'006'},
}

test('public chart manifest resolves one immutable shard without owner identity',()=>{
  const parsed=validateChartManifest(manifest,'run-1')
  const shard=chartShard(parsed,'aaa')
  assert.equal(shard,'006')
  assert.equal(chartPath(parsed,shard!),'https://example.supabase.co/storage/v1/object/public/stockscout-eod-charts/run-1/shards/006.json.gz')
  assert.equal(publicChartManifestUrl('https://example.supabase.co/','run-1'),'https://example.supabase.co/storage/v1/object/public/stockscout-eod-charts/run-1/manifest.json')
})

test('public chart shard exposes only the requested ticker rows',()=>{
  const payload={AAA:{daily:[['2026-08-21',1,2,1,2,100,1]]},BBB:{daily:[['other-row']]}}
  assert.deepEqual(chartRows(payload,'AAA'),payload.AAA.daily)
  assert.equal(chartRows(payload,'CCC'),null)
})

test('chart manifest rejects a mismatched run or non-public path',()=>{
  assert.throws(()=>validateChartManifest(manifest,'run-2'),/another scan/)
  assert.throws(()=>validateChartManifest({...manifest,storageBaseUrl:'https://example.supabase.co/storage/v1/object/private/stockscout-eod-charts/run-1'},'run-1'),/invalid/)
})

test('chart manifest accepts immutable GitHub Pages chart shards',()=>{
  const pages={...manifest,storageBaseUrl:'https://garrincha077.github.io/StockScout-Unified/data/modes/bottom-fishing/runs/run-1/charts'}
  assert.equal(chartPath(validateChartManifest(pages,'run-1'),'006'),`${pages.storageBaseUrl}/shards/006.json.gz`)
})
