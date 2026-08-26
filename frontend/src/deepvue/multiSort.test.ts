import test from 'node:test'
import assert from 'node:assert/strict'
import {applyMultiSort} from './multiSort.ts'

test('balanced multi-sort never mutates source rows',()=>{
  const rows=[
    {ticker:'AAA',opportunityScore:90,rsRank:40,nested:{keep:true}},
    {ticker:'BBB',opportunityScore:70,rsRank:95,nested:{keep:true}},
    {ticker:'CCC',opportunityScore:80,rsRank:80,nested:{keep:true}},
  ]
  const before=structuredClone(rows)
  const sorted=applyMultiSort(rows,[{id:'opportunityScore',desc:true},{id:'rsRank',desc:true}])
  assert.equal(sorted.length,3)
  assert.deepEqual(rows,before)
  assert.equal(Object.prototype.hasOwnProperty.call(rows[0],'__mixScore'),false)
})
