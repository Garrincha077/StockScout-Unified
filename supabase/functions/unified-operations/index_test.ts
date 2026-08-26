import{drawingTriggered,priceBar,triggered,type AlertRow}from'./index.ts'

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
