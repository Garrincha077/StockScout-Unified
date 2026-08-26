export type GeometryVectorBar={time:string;open:number;high:number;low:number;close:number}
export type GeometryPoint={time:string;price:number}
export type ProjectionVector={
  name:string
  kind:'projection'
  interval:'D'|'W'
  points:[GeometryPoint,GeometryPoint]
  bars:GeometryVectorBar[]
  atTime:string
  expected:number|null
}
export type TriggerVector={
  name:string
  kind:'trigger'
  interval:'D'|'W'
  points:[GeometryPoint,GeometryPoint]
  bars:GeometryVectorBar[]
  condition:'cross_above'|'cross_below'|'touch'
  basis:'close'|'wick'
  expectedValid:boolean
  expectedFired:boolean
  expectedLine:number|null
  expectedPrevLine:number|null
}
export type GeometryVector=ProjectionVector|TriggerVector

const bar=(time:string,close:number,low=close-.5,high=close+.5):GeometryVectorBar=>({time,open:close,high,low,close})

export const chartAlertGeometryVectors:GeometryVector[]=[
  {
    name:'horizontal line remains flat',kind:'projection',interval:'D',
    points:[{time:'2026-08-03',price:12.5},{time:'2026-08-04',price:12.5}],
    bars:[bar('2026-08-03',12),bar('2026-08-04',12),bar('2026-08-05',12)],
    atTime:'2026-08-05',expected:12.5,
  },
  {
    name:'rising trendline uses trading bars across weekend',kind:'projection',interval:'D',
    points:[{time:'2026-08-03',price:100},{time:'2026-08-04',price:101}],
    bars:[bar('2026-08-03',100),bar('2026-08-04',101),bar('2026-08-05',102),bar('2026-08-06',103),bar('2026-08-07',104),bar('2026-08-10',105)],
    atTime:'2026-08-10',expected:105,
  },
  {
    name:'falling trendline uses trading bars across weekend',kind:'projection',interval:'D',
    points:[{time:'2026-08-03',price:50},{time:'2026-08-04',price:48}],
    bars:[bar('2026-08-03',50),bar('2026-08-04',48),bar('2026-08-05',46),bar('2026-08-06',44),bar('2026-08-07',42),bar('2026-08-10',40)],
    atTime:'2026-08-10',expected:40,
  },
  {
    name:'missing anchor is invalid instead of silently clamped',kind:'projection',interval:'D',
    points:[{time:'2026-07-31',price:100},{time:'2026-08-04',price:101}],
    bars:[bar('2026-08-03',100),bar('2026-08-04',101),bar('2026-08-05',102)],
    atTime:'2026-08-05',expected:null,
  },
  {
    name:'weekly projection advances by weekly bars not calendar days',kind:'projection',interval:'W',
    points:[{time:'2026-07-27',price:100},{time:'2026-08-03',price:102}],
    bars:[
      bar('2026-07-27',99),bar('2026-07-28',100),bar('2026-07-29',100),bar('2026-07-30',101),bar('2026-07-31',101),
      bar('2026-08-03',101),bar('2026-08-04',102),bar('2026-08-05',102),bar('2026-08-06',103),bar('2026-08-07',103),
      bar('2026-08-10',103),bar('2026-08-11',104),bar('2026-08-12',104),bar('2026-08-13',105),bar('2026-08-14',105),
    ],
    atTime:'2026-08-10',expected:104,
  },
  {
    name:'close cross above requires a true transition',kind:'trigger',interval:'D',
    points:[{time:'2026-08-03',price:100},{time:'2026-08-04',price:101}],
    bars:[bar('2026-08-03',99),bar('2026-08-04',100),bar('2026-08-05',102.5)],
    condition:'cross_above',basis:'close',expectedValid:true,expectedFired:true,expectedPrevLine:101,expectedLine:102,
  },
  {
    name:'remaining above does not re-fire cross above',kind:'trigger',interval:'D',
    points:[{time:'2026-08-03',price:100},{time:'2026-08-04',price:101}],
    bars:[bar('2026-08-03',99),bar('2026-08-04',102),bar('2026-08-05',103.5)],
    condition:'cross_above',basis:'close',expectedValid:true,expectedFired:false,expectedPrevLine:101,expectedLine:102,
  },
  {
    name:'close cross below requires a true transition',kind:'trigger',interval:'D',
    points:[{time:'2026-08-03',price:50},{time:'2026-08-04',price:49}],
    bars:[bar('2026-08-03',51),bar('2026-08-04',50),bar('2026-08-05',47.5)],
    condition:'cross_below',basis:'close',expectedValid:true,expectedFired:true,expectedPrevLine:49,expectedLine:48,
  },
  {
    name:'wick touch fires without close crossing',kind:'trigger',interval:'D',
    points:[{time:'2026-08-03',price:100},{time:'2026-08-04',price:100}],
    bars:[bar('2026-08-03',98,97,99),bar('2026-08-04',98.5,97.5,100.25)],
    condition:'touch',basis:'wick',expectedValid:true,expectedFired:true,expectedPrevLine:100,expectedLine:100,
  },
  {
    name:'wick cross above detects first high breach',kind:'trigger',interval:'D',
    points:[{time:'2026-08-03',price:100},{time:'2026-08-04',price:100}],
    bars:[bar('2026-08-03',98,97,99.5),bar('2026-08-04',99,98,100.5)],
    condition:'cross_above',basis:'wick',expectedValid:true,expectedFired:true,expectedPrevLine:100,expectedLine:100,
  },
  {
    name:'touch with close basis is rejected by contract',kind:'trigger',interval:'D',
    points:[{time:'2026-08-03',price:100},{time:'2026-08-04',price:100}],
    bars:[bar('2026-08-03',99),bar('2026-08-04',100)],
    condition:'touch',basis:'close',expectedValid:false,expectedFired:false,expectedPrevLine:100,expectedLine:100,
  },
  {
    name:'weekly close cross uses aggregated weekly closes',kind:'trigger',interval:'W',
    points:[{time:'2026-07-27',price:100},{time:'2026-08-03',price:102}],
    bars:[
      bar('2026-07-27',99),bar('2026-07-31',99.5),
      bar('2026-08-03',101),bar('2026-08-07',101.5),
      bar('2026-08-10',103),bar('2026-08-14',104.5),
    ],
    condition:'cross_above',basis:'close',expectedValid:true,expectedFired:true,expectedPrevLine:102,expectedLine:104,
  },
]
