import test from 'node:test'
import assert from 'node:assert/strict'
import {normalizeCore,parseManifest,parseUnifiedManifest,STOCKSCOUT_SCHEMA} from './contracts.ts'

const asset=(path:string)=>({path,sha256:`sha-${path}`,bytes:1,count:1})

test('v1 manifest and core enforce immutable activation contract',()=>{
  const manifest=parseManifest({
    manifestVersion:1,schemaVersion:STOCKSCOUT_SCHEMA,runId:'20260821-close',sessionDate:'2026-08-21',
    generatedAt:'2026-08-21T22:00:00Z',status:'healthy',priceMode:'eod',
    counts:{candidates:1,excluded:2,total:3},provenance:{primary:'yahoo'},
    versions:{ranking:'frozen-v1',detectors:'2026.08',tradePlan:'v1'},
    assets:{core:asset('runs/x/core.json'),details:asset('runs/x/details'),excluded:asset('runs/x/excluded.json'),history:asset('runs/x/history.json')},
  })
  const core=normalizeCore({generatedAt:manifest.generatedAt,market:{regime:'confirmed'},universe:[{ticker:'aaa'}]},manifest)
  assert.equal(manifest.manifestVersion,1)
  assert.equal(core.universe[0].ticker,'AAA')
  assert.equal(core.universe[0].id,'scan:20260821-close:candidate:AAA')
  assert.equal(core.universe[0].scanOrder,0)
})

test('legacy v2 remains readable for comparison fixtures',()=>{
  const legacyAsset=(path:string)=>({path,sha256:path,bytes:1,coverage:1,coveragePct:100})
  const manifest=parseManifest({manifestVersion:2,model:'legacy-test',generatedAt:'2026-08-20T00:00:00Z',universe:1,provenance:{},assets:{
    core:legacyAsset('core.json'),legacyIndex:legacyAsset('legacy/index.json'),legacyDetails:{...legacyAsset('legacy/details'),shardCount:128},
    legacyConfirmation:legacyAsset('shadow/legacy-confirmation.json'),charts:legacyAsset('charts'),
  }})
  const core=normalizeCore({generatedAt:manifest.generatedAt,market:{},universe:[{ticker:'MSFT'}]},manifest)
  assert.equal(core.universe[0].ticker,'MSFT')
})

test('failed or inconsistent activation data is rejected',()=>{
  assert.throws(()=>parseManifest({manifestVersion:1,schemaVersion:STOCKSCOUT_SCHEMA}),/status/)
})

test('unified activation requires all three healthy hash-bound modes',()=>{
  const pointer=(mode:string)=>({mode,label:mode,priceBasis:mode==='bottom-fishing'?'split_only':'split_div',status:'healthy',manifestPath:`modes/${mode}/manifest.json`,manifestSha256:'a'.repeat(64),manifestBytes:10,candidates:1,excluded:0,chartCoveragePct:100,ranking:`${mode}-order`})
  const manifest=parseUnifiedManifest({manifestVersion:1,schemaVersion:'stockscout-unified/v1',runId:'run-1',sessionDate:'2026-08-24',generatedAt:'2026-08-24T22:00:00Z',status:'healthy',defaultMode:'bottom-fishing',modes:{'bottom-fishing':pointer('bottom-fishing'),next:pointer('next'),'ryan-original':pointer('ryan-original')}})
  assert.equal(manifest.defaultMode,'bottom-fishing')
  assert.throws(()=>parseUnifiedManifest({...manifest,modes:{...manifest.modes,next:{...manifest.modes.next,status:'failed'}}}),/next/)
})
