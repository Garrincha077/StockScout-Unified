import{drawingTriggered,priceBar,triggered,type AlertRow}from'./index.ts'
import{evaluateDrawingGeometry,lineValueAt,rearmAfterClear,utcTimeAtLogicalIndex}from'../_shared/chartGeometry.ts'

function assert(value:unknown,message:string){if(!value)throw new Error(message)}

const bars=[
  {time:'2026-08-24',open:9,high:10,low:8,close:9},
  {time:'2026-08-25',open:9,high:12,low:9,close:11},
]
const candidate={ticker:'AAA',price:11,tradeStatus:'entry_ready'}
const alert=(payload:Record<string,unknown>):AlertRow=>({id:'a',user_id:'u',name:'fixture',ticker:'AAA',mode:'bottom-fishing',price_basis:'split_only',payload})

Deno.test('chart rows normalize epoch and object bars',()=>{
  assert(priceBar([1787616000,9,12,8,11,1000])?.close===11,'array bar was not normalized')
  assert(priceBar({time:'2026-08-25',open:9,high:12,low:8,close:11})?.time==='2026-08-25','object bar was not normalized')
})

Deno.test('price and screen alerts use exact close history and allowlisted fields',()=>{
  assert(triggered(alert({kind:'price',operator:'crossing_up',price:10}),candidate,bars),'crossing-up alert did not fire')
  assert(triggered(alert({kind:'price',operator:'touch',price:11.5}),candidate,bars),'intrabar touch did not fire')
  assert(triggered(alert({kind:'screen',filters:[{field:'tradeStatus',op:'eq',value:'entry_ready'}]}),candidate,[]),'screen alert did not fire')
  assert(!triggered(alert({kind:'screen',filters:[{field:'constructor.name',op:'eq',value:'Object'}]}),candidate,[]),'unsafe field path was accepted')
})

Deno.test('drawing alerts support projected lines, fibs and box transitions',()=>{
  assert(drawingTriggered({kind:'drawing',operator:'crossing_up',type:'horizontal',points:[{time:'2026-08-24',price:10},{time:'2026-08-25',price:10}]},bars),'horizontal crossing did not fire')
  assert(drawingTriggered({kind:'drawing',operator:'touch',type:'fib',points:[{time:'2026-08-24',price:8},{time:'2026-08-25',price:12}],fibLevels:[.5]},bars),'fib touch did not fire')
  assert(drawingTriggered({kind:'drawing',operator:'entering',type:'rectangle',points:[{time:'2026-08-24',price:10},{time:'2026-08-25',price:12}]},bars),'box entry did not fire')
})

Deno.test('UTC geometry is interval independent and uses previous and current sloped levels',()=>{
  const drawing={type:'trendline' as const,extend:'both' as const,points:[{time:'2026-08-20',price:8},{time:'2026-08-24',price:12}]}
  assert(lineValueAt(drawing,'2026-08-22')===10,'calendar projection changed between chart intervals')
  assert(utcTimeAtLogicalIndex(['2026-08-20','2026-08-21','2026-08-24'],3)==='2026-08-27','future chart margin did not follow the final market-bar spacing')
  const result=evaluateDrawingGeometry(drawing,{kind:'line'},'crossing_up',[
    {time:'2026-08-23',open:10,high:12,low:9,close:10.5},
    {time:'2026-08-25',open:12,high:14,low:11,close:13.5},
  ])
  assert(result.previousLevel===11&&result.currentLevel===13,'sloped levels were not evaluated at both bars')
  assert(result.condition,'sloped crossing did not fire')
})

Deno.test('ray waits for its UTC anchor and linked drawings do not require a candidate row',()=>{
  const payload={kind:'drawing',condition:'touch',target:{kind:'line'},type:'ray',extend:'right',points:[{time:'2026-08-26',price:10},{time:'2026-08-27',price:11}]}
  assert(!drawingTriggered(payload,bars),'ray fired before its first anchor')
  const horizontal={kind:'drawing',operator:'crossing_up',type:'horizontal',points:[{time:'2026-08-20',price:10},{time:'2026-08-21',price:10}]}
  assert(triggered(alert(horizontal),undefined,bars),'drawing alert incorrectly required a screener candidate')
})

Deno.test('rearm fires once per continuous episode and rearms only after clear',()=>{
  const first=rearmAfterClear(true,true),repeat=rearmAfterClear(first.armed,true),clear=rearmAfterClear(repeat.armed,false),again=rearmAfterClear(clear.armed,true)
  assert(first.triggered&&!repeat.triggered&&!clear.triggered&&again.triggered,'rearm state machine emitted duplicates or failed to rearm')
})
