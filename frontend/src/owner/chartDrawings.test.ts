import assert from'node:assert/strict'
import test from'node:test'
import{isChartDrawingPayload,moveDrawing,newDrawing,normalizeChartDrawingPayload,type ChartDrawingPayloadV1}from'./chartDrawings.ts'

test('horizontal and vertical drawings normalize their fixed axis',()=>{
  const points=[{time:'2026-08-20',price:10},{time:'2026-08-21',price:12}]
  assert.deepEqual(newDrawing('horizontal',points).points,[points[0],{time:'2026-08-21',price:10}])
  assert.deepEqual(newDrawing('vertical',points).points,[points[0],{time:'2026-08-20',price:12}])
})

test('new trendlines project right while legacy drawings keep their original both-side behavior',()=>{
  const points=[{time:'2026-08-20',price:10},{time:'2026-08-21',price:12}]
  assert.equal(newDrawing('trendline',points).extend,'right')
  const legacy:ChartDrawingPayloadV1={version:1,type:'trendline',points,color:'#f0b94f',width:2,dash:'solid'}
  assert.equal(normalizeChartDrawingPayload(legacy)?.extend,'both')
})

test('moving a drawing shifts time and price without leaving available bars',()=>{
  const drawing=newDrawing('trendline',[{time:'2026-08-20',price:10},{time:'2026-08-21',price:12}])
  const moved=moveDrawing(drawing,['2026-08-20','2026-08-21','2026-08-24'],{time:'2026-08-20',price:10},{time:'2026-08-21',price:11})
  assert.deepEqual(moved.points,[{time:'2026-08-21',price:11},{time:'2026-08-24',price:13}])
  assert.equal(isChartDrawingPayload(moved),true)
})
