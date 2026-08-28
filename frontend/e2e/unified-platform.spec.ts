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

const factorPayload={schemaVersion:1,generatedAt,source:{provider:'Kenneth R. French Data Library'},method:{windowMonths:120,annualization:'geometric',droughtDefinition:'trailing premium below zero',deltaDefinition:'change in premium',stockScoutImpact:'none; read-only context'},range:{firstMonth:'2010-01',lastMonth:'2026-07',rollingFirstMonth:'2020-01',alignedMonths:190},summary:{mostImproving12m:['MOM'],activeDroughts:1},factors:['MKT_RF','SMB','HML','RMW','CMA','MOM'].map((id,index)=>({id,sourceCode:id,label:`Factor ${id}`,latest:{month:'2026-07',premiumPct:index+1,delta1mPp:.1,delta6mPp:.2,delta12mPp:.3,recent12mPremiumPct:1,historicalPercentile:60,regime:'STRONG'},currentDrought:{active:false,startMonth:null,endMonth:null,duration:'0m',ongoing:false},longestDrought:{active:true,startMonth:'2020-01',endMonth:'2020-02',duration:'2m',ongoing:false},series:[{month:'2026-06',premiumPct:index+.5},{month:'2026-07',premiumPct:index+1}]}))}
const gmliPayload={schemaVersion:1,status:'OK',generatedAt,stockScoutImpact:'none; read-only independent macro context',source:{repository:'Garrincha077/NUEVO',upstreamRefreshStatus:'PASS_FETCH_FIRST'},consumerContract:{mode:'READ_ONLY_SIDECAR',mutatesStockScoutScoring:false,lastGoodFallbackAllowed:true},dataHealth:{status:'PASS'},regime:{label:'EXPANSION',tilt:'supportive',money:{usdScore:70,usdYoYPct:4,usdRegime:'EXPANDING',fxNeutralScore:65,fxNeutralYoYPct:3,fxNeutralRegime:'EXPANDING'},funding:{score:60,regime:'SUPPORTIVE',role:'context'},fiscal:{score:55,regime:'NEUTRAL',role:'context',automaticGlobalConvictionWeight:50},market:{month:'2026-07',positive:3,total:4,assetsPositive:{SPY:true,HYG:true}}},moneyExtremes:{latest:{usd_level:{value_pct:4,z:1,percentile:75},fx_neutral_level:{value_pct:3,z:.5,percentile:60},usd_accel3:{value_pp:.5,z:.2,percentile:55},fx_neutral_accel3:{value_pp:.4,z:.1,percentile:52}}},history:{funding:[{month:'2026-06',score:58},{month:'2026-07',score:60}],fiscal:[{month:'2026-06',score:54},{month:'2026-07',score:55}],market:[{month:'2026-06',positive:2},{month:'2026-07',positive:3}]}}
const factorText=JSON.stringify(factorPayload),gmliText=JSON.stringify(gmliPayload)

function modeFixture(mode:Mode){
  const priceMode=mode==='bottom-fishing'?'split_only':'split_div',row={...rows[mode],id:`scan:${runId}:mode:${mode}:candidate:AAA`,mode,priceBasis:priceMode,scanOrder:0,asOf:sessionDate}
  const extra=mode==='ryan-original'?{...row,id:`scan:${runId}:mode:${mode}:candidate:BBB`,ticker:'BBB',scanOrder:1,originalBuyScore:80,score:80}:null
  const universe=extra?[row,extra]:[row]
  const groups=mode==='next'?{method:'behavioral-proxy-v2-confidence',description:'fixture',sectorCoverage:1,industryCoverage:1,averageConfidence:88,maxLeadershipAdjustmentPoints:5,sectors:[{ticker:'XLK',name:'Technology',rank:92,rel1m:4,rel3m:7,rel6m:12,stocks:1,stage2Pct:100,earlyLeaders:1,medianOpportunity:96,avgConfidence:88,topTickers:['AAA']}],industries:[{ticker:'SOXX',name:'Semiconductors',rank:95,rel1m:5,rel3m:8,rel6m:15,stocks:1,stage2Pct:100,earlyLeaders:1,medianOpportunity:96,avgConfidence:90,topTickers:['AAA']}]}:undefined
  const core={schemaVersion:'stockscout-unified/core-v1',runId,sessionDate,generatedAt,market:{scanDate:sessionDate,regime:{state:'confirmed_uptrend',summary:'Confirmed uptrend'}},universe,detailShards:{AAA:'000',BBB:'000'},chartShards:{AAA:'000.json',BBB:'000.json'},...(groups?{groups}:{})}
  const assets:Record<string,unknown>={
    core:{path:`runs/${runId}/core.json`,sha256:`${mode}-core`,bytes:1,count:universe.length},
    details:{path:`runs/${runId}/details`,sha256:`${mode}-details`,bytes:1,count:universe.length,pattern:'{bucket}.json',bucketCount:128},
    excluded:{path:`runs/${runId}/excluded.json`,sha256:`${mode}-excluded`,bytes:1,count:0},
    history:{path:`runs/${runId}/history.json`,sha256:`${mode}-history`,bytes:1,count:1},
    charts:{path:`runs/${runId}/charts`,sha256:`${mode}-charts`,bytes:1,count:universe.length,pattern:'{bucket}.json',bucketCount:128,coveragePct:100},
  }
  if(mode==='next'){
    assets.factorRegime={path:`runs/${runId}/contexts/factor-regime.json`,sha256:createHash('sha256').update(factorText).digest('hex'),bytes:Buffer.byteLength(factorText),count:6}
    assets.gmliContext={path:`runs/${runId}/contexts/gmli-context.json`,sha256:createHash('sha256').update(gmliText).digest('hex'),bytes:Buffer.byteLength(gmliText),count:1}
  }
  const manifest={manifestVersion:1,schemaVersion:'stockscout-eod/v1',mode,runId,sessionDate,marketDataDate:sessionDate,generatedAt,status:'healthy',priceMode,chartStatus:'ready',counts:{universe:universe.length,candidates:universe.length,excluded:0,failed:0,total:universe.length},health:{status:'healthy',coveragePct:100,checks:[]},provenance:{fixture:true},versions:{ranking:mode,detectors:'fixture',tradePlan:mode==='bottom-fishing'?'v1':'not-applicable'},assets}
  const manifestText=JSON.stringify(manifest),manifestHash=createHash('sha256').update(manifestText).digest('hex')
  return{row,universe,core,manifest,manifestText,manifestHash}
}
const fixtures=Object.fromEntries(modes.map(mode=>[mode,modeFixture(mode)]))as Record<Mode,ReturnType<typeof modeFixture>>
const unified={manifestVersion:1,schemaVersion:'stockscout-unified/v1',runId,sessionDate,generatedAt,status:'healthy',defaultMode:'bottom-fishing',modes:Object.fromEntries(modes.map(mode=>[mode,{mode,label:mode,priceBasis:mode==='bottom-fishing'?'split_only':'split_div',status:'healthy',manifestPath:`modes/${mode}/manifest.json`,manifestSha256:fixtures[mode].manifestHash,manifestBytes:Buffer.byteLength(fixtures[mode].manifestText),candidates:fixtures[mode].universe.length,excluded:0,chartCoveragePct:100,ranking:mode}]))}

async function installRoutes(page:Page){
  await page.route('**/data/manifest.json*',route=>route.fulfill({json:unified}))
  for(const mode of modes){
    const fixture=fixtures[mode]
    await page.route(`**/data/modes/${mode}/manifest.json*`,route=>route.fulfill({status:200,contentType:'application/json',body:fixture.manifestText}))
    await page.route(`**/data/modes/${mode}/runs/${runId}/core.json*`,route=>route.fulfill({json:fixture.core}))
    await page.route(`**/data/modes/${mode}/runs/${runId}/details/*.json*`,route=>route.fulfill({json:Object.fromEntries(fixture.universe.map(row=>[String(row.ticker),row]))}))
    const bars=[['2026-08-20',48,50,47,49,100000,1],['2026-08-21',49,51,48,50,180000,1.1],['2026-08-24',50,52,49,51,220000,1.2]]
    await page.route(`**/data/modes/${mode}/runs/${runId}/charts/000.json*`,route=>route.fulfill({json:Object.fromEntries(fixture.universe.map(row=>[String(row.ticker),bars]))}))
    await page.route(`**/data/modes/${mode}/runs/${runId}/excluded.json*`,route=>route.fulfill({json:{rows:[]}}))
    await page.route(`**/data/modes/${mode}/runs/${runId}/history.json*`,route=>route.fulfill({json:{sessions:[{runId,sessionDate,generatedAt,status:'healthy',candidateCount:1,excludedCount:0}]}}))
  }
  await page.route(`**/data/modes/next/runs/${runId}/contexts/factor-regime.json*`,route=>route.fulfill({status:200,contentType:'application/json',body:factorText}))
  await page.route(`**/data/modes/next/runs/${runId}/contexts/gmli-context.json*`,route=>route.fulfill({status:200,contentType:'application/json',body:gmliText}))
  await page.route('**/data/validation-status.json*',route=>route.fulfill({status:404,body:''}))
}

async function chooseMode(page:Page,mode:Mode,label:string){
  const mobileSelect=page.locator('.mode-select')
  if(await mobileSelect.isVisible())await mobileSelect.selectOption(mode)
  else await page.locator('.mode-header nav button').filter({hasText:label}).click()
}

test('three modes stay isolated and remain usable in mobile and desktop views',async({page})=>{
  await installRoutes(page)
  await page.goto(`/StockScout-Unified/ticker/AAA?run=${runId}&mode=bottom-fishing`)
  await expect(page.locator('.mode-header')).toContainText('Bottom Fishing')
  await expect(page.locator('.dv-detailhead')).toContainText('STOCKSCOUT')
  await expect(page.locator('.trade-plan')).toContainText('Entry ready')
  await expect(page.locator('.dv-chart canvas').first()).toBeVisible()
  await expect(page.locator('.chart-drawing-toolbar')).toContainText(/Sign in to draw|Enable owner drawings/)
  await expect(page.locator('.unified-app')).toHaveClass(/layout-cockpit/)
  await page.locator('.layout-mode-toggle').click()
  await expect(page.locator('.unified-app')).toHaveClass(/layout-classic/)
  await expect(page.locator('.layout-mode-toggle')).toContainText('Cockpit layout')
  await page.reload()
  await expect(page.locator('.unified-app')).toHaveClass(/layout-classic/)
  await page.locator('.layout-mode-toggle').click()
  await expect(page.locator('.unified-app')).toHaveClass(/layout-cockpit/)
  await page.reload()
  await expect(page.locator('.unified-app')).toHaveClass(/layout-cockpit/)

  const initialViewport=page.viewportSize()
  if((initialViewport?.width??0)<=760){
    await expect(page.locator('.chart-drawing-toolbar')).toBeHidden()
    await expect(page.locator('.chart-mobile-dock')).toBeVisible()
    const panelBox=await page.locator('.dv-chart-resizer').boundingBox()
    const contentBox=await page.locator('.dv-chart-resizer>.ss-height-content').boundingBox()
    const canvasBox=await page.locator('.dv-chart canvas').first().boundingBox()
    expect(panelBox?.height??0).toBeGreaterThan(300)
    expect(contentBox?.height??0).toBeGreaterThan((panelBox?.height??0)-4)
    expect(canvasBox?.height??0).toBeGreaterThan(250)
    const dockBox=await page.locator('.chart-mobile-dock').boundingBox()
    const chartBox=await page.locator('.drawing-chart-shell.owner-tools>.dv-chart').boundingBox()
    if(dockBox&&chartBox)expect(dockBox.y).toBeGreaterThanOrEqual(chartBox.y+chartBox.height-2)
  }else await expect(page.locator('.chart-drawing-toolbar')).toBeVisible()
  expect(await page.evaluate(()=>document.body.scrollWidth<=window.innerWidth)).toBe(true)

  await chooseMode(page,'next','Next')
  await expect(page.locator('.dv-detailhead')).toContainText('NEXT SCORE')
  await expect(page.locator('.dv-detailhead')).toContainText('96.0')
  await expect(page.locator('.trade-plan')).toHaveCount(0)
  await expect(page).toHaveURL(/mode=next/)

  await page.locator('.next-view-nav button').filter({hasText:'Groups'}).click()
  await expect(page.locator('.grp-top')).toContainText('1/1 mapped')
  await expect(page.locator('.grp-board')).toContainText('Technology')
  await page.locator('.next-view-nav button').filter({hasText:'Factors'}).click()
  await expect(page.locator('.ctx-factor-grid .ctx-card')).toHaveCount(6)
  await page.locator('.next-view-nav button').filter({hasText:'GMLI'}).click()
  await expect(page.locator('.ctx-page')).toContainText('GMLI Context')

  await chooseMode(page,'ryan-original','Ryan Original')
  await expect(page.locator('.ryan-dashboard')).toContainText('Ryan Original')
  await expect(page.locator('.ryan-summary')).toContainText('Buy Signals')
  await expect(page.locator('.ryan-summary')).toContainText('Sell Signals')
  await expect(page.locator('.ryan-detail')).toContainText('Source inputs')
  await expect(page.locator('.ryan-detail')).toContainText('BUY SCORE ANATOMY')
  await expect(page.locator('.ryan-chart-stage canvas').first()).toBeVisible()
  await page.evaluate(()=>document.activeElement instanceof HTMLElement&&document.activeElement.blur())
  await page.keyboard.press('Space')
  await expect(page).toHaveURL(/ticker\/BBB/)
  await page.locator('.ryan-tabs button').filter({hasText:'SELL'}).click()
  await expect(page.locator('.ryan-table')).toContainText('AAA')
  await expect(page.locator('.ryan-table')).toContainText('high')
  await expect(page).toHaveURL(/mode=ryan-original/)

  const viewport=page.viewportSize()
  const paneSplitter=page.locator('.ryan-workspace > .ss-explicit-splitter')
  if((viewport?.width??0)>1050){
    await expect(paneSplitter).toBeVisible()
    const before=Number(await paneSplitter.getAttribute('aria-valuenow'))
    await paneSplitter.focus()
    await page.keyboard.press('ArrowLeft')
    expect(Number(await paneSplitter.getAttribute('aria-valuenow'))).toBeGreaterThan(before)
    await page.keyboard.press('Home')
    expect(await page.evaluate(()=>localStorage.getItem('stockscout-layout-v3:ryan:secondary'))).toBeNull()
  }else{
    await expect(paneSplitter).toBeHidden()
  }
})

test('Ryan chart exposes a meaningful retry state',async({page})=>{
  await installRoutes(page)
  let attempts=0
  await page.route(`**/data/modes/ryan-original/runs/${runId}/charts/000.json*`,async route=>{
    attempts++
    if(attempts===1)await route.fulfill({status:503,body:'temporarily unavailable'})
    else await route.fallback()
  })
  await page.goto(`/StockScout-Unified/ticker/AAA?run=${runId}&mode=ryan-original`)
  await expect(page.locator('.ryan-chart-state')).toContainText('503')
  await page.locator('.ryan-chart-state button').filter({hasText:'Retry'}).click()
  await expect(page.locator('.ryan-chart-stage canvas').first()).toBeVisible()
})

test('a mismatched mode manifest hash never activates partial data',async({page})=>{
  await installRoutes(page)
  await page.route('**/data/modes/next/manifest.json*',route=>route.fulfill({json:{...fixtures.next.manifest,runId:'wrong-run'}}))
  await page.goto('/?mode=next')
  await expect(page.locator('.dv-loading')).toContainText(/hash does not match|identity does not match/)
})
