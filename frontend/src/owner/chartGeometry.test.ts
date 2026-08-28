import assert from'node:assert/strict'
import test from'node:test'
import{evaluateDrawingGeometry,lineValueAt,logicalIndexAtTime,rearmAfterClear}from'../../../supabase/functions/_shared/chartGeometry.ts'

const drawing={type:'trendline' as const,extend:'both' as const,points:[{time:'2026-08-20',price:8},{time:'2026-08-24',price:12}]}

test('the same UTC anchors project identically on daily and weekly charts',()=>{
  assert.equal(lineValueAt(drawing,'2026-08-22'),10)
  assert.equal(logicalIndexAtTime(['2026-08-17','2026-08-24','2026-08-31'],'2026-08-20'),3/7)
  assert.equal(logicalIndexAtTime(['2026-08-20','2026-08-21','2026-08-24'],'2026-08-22'),1+1/3)
})

test('crossing compares each close to its matching sloped level',()=>{
  const result=evaluateDrawingGeometry(drawing,{kind:'line'},'crossing_up',[
    {time:'2026-08-23',open:10,high:12,low:9,close:10.5},
    {time:'2026-08-25',open:12,high:14,low:11,close:13.5},
  ])
  assert.deepEqual([result.previousLevel,result.currentLevel,result.condition],[11,13,true])
})

test('rearm suppresses a continuous episode',()=>{
  const first=rearmAfterClear(true,true),repeat=rearmAfterClear(first.armed,true),clear=rearmAfterClear(repeat.armed,false),again=rearmAfterClear(clear.armed,true)
  assert.deepEqual([first.triggered,repeat.triggered,clear.armed,again.triggered],[true,false,true,true])
})
