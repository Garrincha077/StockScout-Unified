import{createHash}from'node:crypto'
import{expect,test,type Page}from'@playwright/test'

const runId='2026-08-24-eod-e2e',sessionDate='2026-08-24',generatedAt='2026-08-24T22:00:00Z'
const modes=['bottom-fishing','next','ryan-original']as const
type Mode=typeof modes[number]

const rows:Record<Mode,Record<string,unknown>>={
  'bottom-fishing':{ticker:'AAA',price:50,stage:2,stageName:'Stage 1 → 2',score:88,focusBlend:91,primarySetup:'RWB squeeze thrust',setupTags:['RWB','Crash Base'],tradeStatus:'entry_ready',entryRiskPct:8,tacticalStopLevel:46,tradePlan:{status:'entry_ready',reasonCodes:['fresh_breakout'],triggerState:'fresh',triggerReferenceLevel:51,entryReferenceLevel:50,structuralInvalidationLevel:46,entryRiskPct:8,extensionAtr:.2,tacticalStopLevel:46,tacticalRiskPct:8,source:'primary',version:1}},
  next:{ticker:'AAA',price:50,stage:2,stageName:'Stage 2',score:73,opportunityScore:96,primarySetup:'Fresh Stage 2',setupTags:['Fresh Stage 2']},
  'ryan-original':{ticker:'AAA',price:50,stage:2,stageName:'Stage 2',score:84,originalBuyScore:84,originalBuy:true,originalRunBuySignal:true,originalRR:3,originalStopLoss:45,originalRiskPct:10,originalEntryQuality:'excellent',originalTTPasses:8,originalVcpQuality:78,originalSellScore:82,originalSell:true,originalRunSellSignal:true,originalSellSeverity:'high',primarySetup:'ryan_original_buy',setupTags:['Original buy','Minervini trend template'],originalEngine:{model:'original-signal-engine-v1',phase:2,phaseConfidence:88,sourceInputs:{quarterlyData:{revenue:[10,12]}},sourceOutputs:{buy:{reason:'breakout'},sell:{reason:'breakdown'}},phaseInfo:{phase:2,confidence:88},minervini:{passed:8,passes:true},vcp:{quality:78,isVcp:true},breakout:{breakout_type:'Base Breakout',breakout_level:50,volume_confirmed:true},buy:{score:84,isBuy:true,emittedByOriginalRun:true,entryQuality:'excellent',stopLoss:45,riskPct:10,riskReward:3,rewardTarget:65,components:{trend:32,fundamental:30}},sell:{score:82,isSell:true,emittedByOriginalRun:true,severity:'high',breakdownLevel:45,reasons:['Breakdown']}}},
}

function modeFixture(mode:Mode){
  const priceMode=mode==='bottom-fishing'?'split_only':'split_div',row={...rows[mode],id:`scan:${runId}:mode:${mode}:candidate:AAA`,mode,priceBasis:priceMode,scanOrder:0,asOf:sessionDate}
  const core={schemaVersion:'stockscout-unified/core-v1',runId,sessionDate,generatedAt,market:{scanDate:sessionDate,regime:{state:'confirmed_uptrend',summary:'Confirmed uptrend'}},universe:[row],detailShards:{AAA:'000'},chartShards:{AAA:'000.json'}}
  const assets={
    core:{path:`runs/${runId}/core.json`,sha256:`${mode}-core`,bytes:1,count:1},
    details:{path:`runs/${runId}/details`,sha256:`${mode}-details`,bytes:1,count:1,pattern:'{bucket}.json',bucketCount:128},
    excluded:{path:`runs/${runId}/excluded.json`,sha256:`${mode}-excluded`,bytes:1,count:0},
    history:{path:`runs/${runId}/history.json`,sha256:`${mode}-history`,bytes:1,count:1},
    charts:{path:`runs/${runId}/charts`,sha256:`${mode}-charts`,bytes:1,count:1,pattern:'{bucket}.json',bucketCount:128,coveragePct:100},
  }
  const manifest={manifestVersion:1,schemaVersion:'stockscout-eod/v1',mode,runId,sessionDate,marketDataDate:sessionDate,generatedAt,status:'healthy',priceMode,chartStatus:'ready',counts:{universe:1,candidates:1,excluded:0,failed:0,total:1},health:{status:'healthy',coveragePct:100,checks:[]},provenance:{fixture:true},versions:{ranking:mode,detectors:'fixture',tradePlan:mode==='bottom-fishing'?'v1':'not-applicable'},assets}
  const manifestText=JSON.stringify(manifest),manifestHash=createHash('sha256').update(manifestText).digest('hex')
  return{row,core,manifest,manifestText,manifestHash}
}
const fixtures=Object.fromEntries(modes.map(mode=>[mode,modeFixture(mode)]))as Record<Mode,ReturnType<typeof modeFixture>>
const unified={manifestVersion:1,schemaVersion:'stockscout-unified/v1',runId,sessionDate,generatedAt,status:'healthy',defaultMode:'bottom-fishing',modes:Object.fromEntries(modes.map(mode=>[mode,{mode,label:mode,priceBasis:mode==='bottom-fishing'?'split_only':'split_div',status:'healthy',manifestPath:`modes/${mode}/manifest.json`,manifestSha256:fixtures[mode].manifestHash,manifestBytes:Buffer.byteLength(fixtures[mode].manifestText),candidates:1,excluded:0,chartCoveragePct:100,ranking:mode}]))}

async function installRoutes(page:Page){
  await page.route('**/data/manifest.json*',route=>route.fulfill({json:unified}))
  for(const mode of modes){
    const fixture=fixtures[mode]
    await page.route(`**/data/modes/${mode}/manifest.json*`,route=>route.fulfill({status:200,contentType:'application/json',body:fixture.manifestText}))
    await page.route(`**/data/modes/${mode}/runs/${runId}/core.json*`,route=>route.fulfill({json:fixture.core}))
    await page.route(`**/data/modes/${mode}/runs/${runId}/details/*.json*`,route=>route.fulfill({json:{AAA:fixture.row}}))
    await page.route(`**/data/modes/${mode}/runs/${runId}/charts/000.json*`,route=>route.fulfill({json:{AAA:[['2026-08-20',48,50,47,49,100000,1],['2026-08-21',49,51,48,50,180000,1.1],['2026-08-24',50,52,49,51,220000,1.2]]}}))
    await page.route(`**/data/modes/${mode}/runs/${runId}/excluded.json*`,route=>route.fulfill({json:{rows:[]}}))
    await page.route(`**/data/modes/${mode}/runs/${runId}/history.json*`,route=>route.fulfill({json:{sessions:[{runId,sessionDate,generatedAt,status:'healthy',candidateCount:1,excludedCount:0}]}}))
  }
  await page.route('**/data/validation-status.json*',route=>route.fulfill({status:404,body:''}))
}

test('three modes stay isolated and remain usable in mobile and desktop views',async({page})=>{
  await installRoutes(page)
  await page.goto(`/StockScout-Unified/ticker/AAA?run=${runId}&mode=bottom-fishing`)
  await expect(page.locator('.mode-header')).toContainText('Bottom Fishing')
  await expect(page.locator('.dv-detailhead')).toContainText('STOCKSCOUT')
  await expect(page.locator('.trade-plan')).toContainText('Entry ready')
  await expect(page.locator('.dv-chart canvas').first()).toBeVisible()
  await expect(page.locator('.chart-drawing-toolbar')).toContainText(/Sign in to draw|not configured/)

  await page.locator('.mode-header nav button').filter({hasText:'Next'}).click()
  await expect(page.locator('.dv-detailhead')).toContainText('NEXT SCORE')
  await expect(page.locator('.dv-detailhead')).toContainText('96.0')
  await expect(page.locator('.trade-plan')).toHaveCount(0)
  await expect(page).toHaveURL(/mode=next/)

  await page.locator('.mode-header nav button').filter({hasText:'Ryan Original'}).click()
  await expect(page.locator('.ryan-dashboard')).toContainText('Ryan Original')
  await expect(page.locator('.ryan-summary')).toContainText('Buy Signals')
  await expect(page.locator('.ryan-summary')).toContainText('Sell Signals')
  await expect(page.locator('.ryan-detail')).toContainText('Source inputs')
  await expect(page.locator('.ryan-detail')).toContainText('BUY SCORE ANATOMY')
  await page.locator('.ryan-tabs button').filter({hasText:'SELL'}).click()
  await expect(page.locator('.ryan-table')).toContainText('AAA')
  await expect(page.locator('.ryan-table')).toContainText('high')
  await expect(page).toHaveURL(/mode=ryan-original/)
})

test('a mismatched mode manifest hash never activates partial data',async({page})=>{
  await installRoutes(page)
  await page.route('**/data/modes/next/manifest.json*',route=>route.fulfill({json:{...fixtures.next.manifest,runId:'wrong-run'}}))
  await page.goto('/?mode=next')
  await expect(page.locator('.dv-loading')).toContainText(/hash does not match|identity does not match/)
})
