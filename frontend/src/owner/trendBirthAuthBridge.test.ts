import test from 'node:test'
import assert from 'node:assert/strict'
import {isFreshTrendBirthBridge,trendBirthAuthForwardUrl,forwardTrendBirthAuthIfPending,TREND_BIRTH_AUTH_BRIDGE_KEY,TREND_BIRTH_APP_URL} from './trendBirthAuthBridge.ts'

test('Trend Birth auth bridge accepts only fresh pending markers and Supabase auth fragments',()=>{
  const now=1_800_000_000_000
  const marker=String(now-60_000)
  assert.equal(isFreshTrendBirthBridge(marker,now),true)
  assert.equal(isFreshTrendBirthBridge(String(now-3*60*60*1000),now),false)
  assert.equal(
    trendBirthAuthForwardUrl('#access_token=abc&refresh_token=def&type=magiclink',marker,now),
    TREND_BIRTH_APP_URL+'?tb_auth=complete#access_token=abc&refresh_token=def&type=magiclink',
  )
  assert.equal(trendBirthAuthForwardUrl('#unrelated=1',marker,now),null)
})

test('Trend Birth auth bridge consumes its marker before forwarding',()=>{
  const now=1_800_000_000_000
  const values=new Map([[TREND_BIRTH_AUTH_BRIDGE_KEY,String(now-1_000)]])
  const removed:string[]=[]
  let replaced=''
  const storage={getItem:(key:string)=>values.get(key)??null,removeItem:(key:string)=>{removed.push(key);values.delete(key)}}
  const location={hash:'#access_token=abc&refresh_token=def',replace:(url:string)=>{replaced=url}}
  assert.equal(forwardTrendBirthAuthIfPending(location,storage,now),true)
  assert.equal(replaced,TREND_BIRTH_APP_URL+'?tb_auth=complete#access_token=abc&refresh_token=def')
  assert.deepEqual(removed,[TREND_BIRTH_AUTH_BRIDGE_KEY])
})

test('normal Unified auth is untouched when no Trend Birth bridge is pending',()=>{
  let replaced=''
  const storage={getItem:()=>null,removeItem:()=>{throw new Error('must not remove')}}
  const location={hash:'#access_token=abc&refresh_token=def',replace:(url:string)=>{replaced=url}}
  assert.equal(forwardTrendBirthAuthIfPending(location,storage,Date.now()),false)
  assert.equal(replaced,'')
})
