import {lazy,Suspense,useEffect,useRef,useState} from 'react'
import {CandlestickSeries,ColorType,HistogramSeries,LineSeries,LineStyle,createChart,type IChartApi,type ISeriesApi} from 'lightweight-charts'
import {levelFitsExpandedCandleBounds,normalizeEodDate,weekStartUtc} from './deepvue/filterEngine'
import type{TradeStatus}from'./data/contracts'

const ChartDrawingOverlay=lazy(()=>import('./owner/ChartDrawingOverlay'))
export type ChartBar={time:string;open:number;high:number;low:number;close:number;volume:number;rs:number}
export type ChartInterval='D'|'W'
export type ChartRange='3M'|'6M'|'1Y'|'2Y'|'5Y'
export type ChartDisplay='Price'|'RS'|'Volume'
export type ChartPriceLine={price:number;title:string;color:string;style?:'solid'|'dashed'|'dotted'}

export function normalizeChartRows(rows:any[]):ChartBar[]{
  return rows.map(row=>{
    const source=Array.isArray(row)?{time:row[0],open:row[1],high:row[2],low:row[3],close:row[4],volume:row[5],rs:row[6]}:row
    const time=normalizeEodDate(source?.time??source?.date),open=Number(source?.open),high=Number(source?.high),low=Number(source?.low),close=Number(source?.close),volume=Number(source?.volume??0),rs=Number(source?.rs??0)
    return time&&[open,high,low,close,volume,rs].every(Number.isFinite)?{time,open,high,low,close,volume,rs}:null
  }).filter((bar):bar is ChartBar=>bar!==null).sort((left,right)=>left.time.localeCompare(right.time))
}

function aggregateWeekly(bars:ChartBar[]){
  const out:ChartBar[]=[]
  for(const bar of bars){const time=weekStartUtc(bar.time);if(!time)continue;const last=out.at(-1);if(!last||last.time!==time)out.push({...bar,time});else{last.high=Math.max(last.high,bar.high);last.low=Math.min(last.low,bar.low);last.close=bar.close;last.volume+=bar.volume;last.rs=bar.rs}}
  return out
}
function average(values:number[],window:number,exponential=false){
  const output:(number|null)[]=[]
  if(exponential){const weight=2/(window+1);let current=values[0]??0;values.forEach((value,index)=>{current=index?value*weight+current*(1-weight):value;output.push(index+1>=window?current:null)});return output}
  let sum=0;values.forEach((value,index)=>{sum+=value;if(index>=window)sum-=values[index-window];output.push(index+1>=window?sum/window:null)});return output
}
function sourceRows(bars:ChartBar[],interval:ChartInterval,range:ChartRange){
  const counts:Record<ChartRange,number>=interval==='W'?{'3M':13,'6M':26,'1Y':52,'2Y':104,'5Y':260}:{'3M':66,'6M':132,'1Y':252,'2Y':504,'5Y':1265}
  return(interval==='W'?aggregateWeekly(bars):bars).slice(-counts[range])
}
function numeric(value:unknown){return value!==null&&value!==undefined&&Number.isFinite(Number(value))?Number(value):null}
function stockLines(stock:any,rows:ChartBar[]):ChartPriceLine[]{
  if(!stock)return[]
  const plan=stock.tradePlan??{},status=(stock.tradeStatus??plan.status??plan.trade_status??'insufficient_data')as TradeStatus
  const trigger=numeric(plan.triggerReferenceLevel??plan.trigger_reference_level),tactical=status==='entry_ready'?numeric(stock.tacticalStopLevel??plan.tacticalStopLevel??plan.tactical_stop_level):null,structural=numeric(plan.structuralInvalidationLevel??plan.structural_invalidation_level)
  const lines:ChartPriceLine[]=[]
  if(trigger!=null)lines.push({price:trigger,title:'Entry trigger',color:'#62a8ff',style:'dashed'})
  if(tactical!=null)lines.push({price:tactical,title:'Tactical stop',color:'#ff6f7d'})
  if(structural!=null&&structural!==tactical&&levelFitsExpandedCandleBounds(structural,rows,.1))lines.push({price:structural,title:'Structural invalidation',color:'#d99a62',style:'dotted'})
  return lines
}
const lineStyle=(style:ChartPriceLine['style'])=>style==='dashed'?LineStyle.Dashed:style==='dotted'?LineStyle.Dotted:LineStyle.Solid

export default function StockChart({bars,interval='W',range='5Y',display='Price',mini=false,stock,ticker,priceLines=[]}:{bars:ChartBar[];interval?:ChartInterval;range?:ChartRange;display?:ChartDisplay;mini?:boolean;stock?:any;ticker?:string;priceLines?:ChartPriceLine[]}){
  const ref=useRef<HTMLDivElement>(null)
  const[drawingApi,setDrawingApi]=useState<{chart:IChartApi;candle:ISeriesApi<'Candlestick'>;bars:ChartBar[]}|null>(null)
  useEffect(()=>{
    if(!ref.current||!bars.length)return
    setDrawingApi(null)
    const source=sourceRows(bars,interval,range)
    const chart=createChart(ref.current,{autoSize:true,layout:{background:{type:ColorType.Solid,color:'#08111d'},textColor:mini?'#63758d':'#8396ae',attributionLogo:false},grid:{vertLines:{color:mini?'transparent':'#142238'},horzLines:{color:mini?'#102033':'#142238'}},timeScale:{borderVisible:!mini,borderColor:'#243248',rightOffset:2,timeVisible:false},rightPriceScale:{borderVisible:!mini,borderColor:'#243248',scaleMargins:mini?{top:.08,bottom:.16}:undefined},handleScroll:!mini,handleScale:!mini})
    if(display==='Price'){
      const candle=chart.addSeries(CandlestickSeries,{upColor:'#20d886',downColor:'#f05d6c',wickUpColor:'#20d886',wickDownColor:'#f05d6c',borderVisible:false,priceLineVisible:!mini,lastValueVisible:!mini})
      candle.setData(source.map(bar=>({time:bar.time,open:bar.open,high:bar.high,low:bar.low,close:bar.close}))as any)
      if(!mini)setDrawingApi({chart,candle,bars:source})
      for(const item of[...stockLines(stock,source),...priceLines])if(Number.isFinite(item.price)&&levelFitsExpandedCandleBounds(item.price,source,.18))candle.createPriceLine({price:item.price,color:item.color,lineWidth:2,lineStyle:lineStyle(item.style),axisLabelVisible:true,title:item.title})
      const closes=source.map(bar=>bar.close),specs=interval==='W'?[[10,'#f3c85b',false],[20,'#4ca3ff',false]]:[[10,'#f3c85b',true],[20,'#4ca3ff',true],[50,'#a36cff',false],[200,'#26c7b7',false]]
      for(const[number,color,exponential]of specs as[number,string,boolean][]){const values=average(closes,number,exponential),line=chart.addSeries(LineSeries,{color,lineWidth:mini?1:2,priceLineVisible:false,lastValueVisible:false});line.setData(source.map((bar,index)=>values[index]==null?null:{time:bar.time,value:values[index]}).filter(Boolean)as any)}
      const volume=chart.addSeries(HistogramSeries,{priceFormat:{type:'volume'},priceScaleId:'',lastValueVisible:false,priceLineVisible:false});volume.priceScale().applyOptions({scaleMargins:{top:.84,bottom:0}});volume.setData(source.map(bar=>({time:bar.time,value:bar.volume,color:bar.close>=bar.open?'rgba(32,216,134,.22)':'rgba(240,93,108,.22)'}))as any)
    }else if(display==='RS'){const line=chart.addSeries(LineSeries,{color:'#54a6ff',lineWidth:2,priceLineVisible:false});line.setData(source.filter(bar=>bar.rs>0).map(bar=>({time:bar.time,value:bar.rs}))as any)}else{const volume=chart.addSeries(HistogramSeries,{priceFormat:{type:'volume'},priceScaleId:'',priceLineVisible:false});volume.setData(source.map(bar=>({time:bar.time,value:bar.volume,color:bar.close>=bar.open?'rgba(32,216,134,.72)':'rgba(240,93,108,.72)'}))as any)}
    chart.timeScale().fitContent()
    return()=>{setDrawingApi(null);chart.remove()}
  },[bars,interval,range,display,mini,stock,priceLines])
  if(mini)return <div className="dv-minichart" ref={ref}/>
  const drawingTicker=ticker??stock?.ticker
  return <div className="drawing-chart-shell"><div className="dv-chart" ref={ref}/>{drawingApi&&display==='Price'&&drawingTicker?<Suspense fallback={null}><ChartDrawingOverlay chart={drawingApi.chart} candle={drawingApi.candle} bars={drawingApi.bars} ticker={drawingTicker} interval={interval}/></Suspense>:null}</div>
}
