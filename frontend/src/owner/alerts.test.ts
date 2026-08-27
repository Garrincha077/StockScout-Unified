import assert from'node:assert/strict'
import test from'node:test'
import{alertSummary,normalizeAlertPayload}from'./alerts.ts'

test('legacy price alerts normalize to typed v1 payloads',()=>{
  assert.deepEqual(normalizeAlertPayload({kind:'price',operator:'crossing',price:42}),{version:1,kind:'price',operator:'crossing',price:42})
  assert.equal(alertSummary({kind:'price',operator:'crossing_down',price:42}),'Price crossing down $42.00')
})

test('invalid alert operators and prices are rejected',()=>{
  assert.equal(normalizeAlertPayload({kind:'price',operator:'execute_trade',price:42}),null)
  assert.equal(normalizeAlertPayload({kind:'price',operator:'above',price:-1}),null)
})
