import assert from'node:assert/strict'
import test from'node:test'
import{alertSummary,normalizeAlertPayload}from'./alerts.ts'
import{indicatorAlertsEnabled}from'./indicatorAlerts.ts'

test('legacy price alerts normalize to typed v1 payloads',()=>{
  assert.deepEqual(normalizeAlertPayload({kind:'price',operator:'crossing',price:42}),{version:1,kind:'price',operator:'crossing',price:42})
  assert.equal(alertSummary({kind:'price',operator:'crossing_down',price:42}),'Price crossing down $42.00')
})

test('invalid alert operators and prices are rejected',()=>{
  assert.equal(normalizeAlertPayload({kind:'price',operator:'execute_trade',price:42}),null)
  assert.equal(normalizeAlertPayload({kind:'price',operator:'above',price:-1}),null)
})

test('indicator alerts normalize with optional confirmations and summarize clearly',()=>{
  const payload=normalizeAlertPayload({kind:'indicator',signal:'daily_ema_10_20',direction:'up',confirmations:{mode:'all',conditions:['sma50_daily_up','sma30_weekly_up']}})
  assert.deepEqual(payload,{version:1,kind:'indicator',signal:'daily_ema_10_20',direction:'up',confirmations:{mode:'all',conditions:['sma50_daily_up','sma30_weekly_up']},evaluationInterval:'daily',rearm:'after_clear'})
  assert.equal(alertSummary(payload as Record<string,unknown>),'10/20 EMA · Daily up cross · 50D SMA ↑ + 30W SMA ↑')
})

test('indicator payloads reject mismatched cadence or unknown confirmations',()=>{
  assert.equal(normalizeAlertPayload({kind:'indicator',signal:'weekly_sma_10_20',evaluationInterval:'daily',confirmations:{mode:'all',conditions:[]}}),null)
  assert.equal(normalizeAlertPayload({kind:'indicator',signal:'daily_ema_10_20',confirmations:{mode:'all',conditions:['unknown']}}),null)
})

test('indicator feature flag is owner-only by default',()=>{
  assert.equal(indicatorAlertsEnabled(false),false)
  assert.equal(indicatorAlertsEnabled(true),true)
})
