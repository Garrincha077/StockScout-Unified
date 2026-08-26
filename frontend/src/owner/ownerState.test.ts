import test from 'node:test'
import assert from 'node:assert/strict'
import {clearOwnerLocalStorage,LEGACY_OWNER_WATCHLIST_KEY,nextOwnerWatchlist,normalizeOwnerTicker,ownerMagicLinkRedirect,parseOwnerJsonObject,watchlistAfterSessionChange} from './ownerState.ts'

test('sign-out and owner changes clear owner watchlist state',()=>{
  assert.deepEqual(watchlistAfterSessionChange(['AAA'],'owner-1',null),[])
  assert.deepEqual(watchlistAfterSessionChange(['AAA'],'owner-1','owner-2'),[])
  assert.deepEqual(watchlistAfterSessionChange(['AAA'],'owner-1','owner-1'),['AAA'])
})

test('anonymous toggles cannot create state later inherited by an owner',()=>{
  assert.throws(()=>nextOwnerWatchlist([], 'aaa', null),/sign-in is required/i)
  assert.deepEqual(nextOwnerWatchlist([], 'aaa', 'owner-1'),['AAA'])
  assert.deepEqual(nextOwnerWatchlist(['AAA'], 'aaa', 'owner-1'),[])
})

test('private legacy watchlist cache is removed on sign-out',()=>{
  const removed:string[]=[]
  clearOwnerLocalStorage({removeItem:key=>removed.push(key)})
  assert.deepEqual(removed,[LEGACY_OWNER_WATCHLIST_KEY])
})

test('owner form helpers validate tickers and object JSON',()=>{
  assert.equal(normalizeOwnerTicker(' brk.b '),'BRK.B')
  assert.equal(normalizeOwnerTicker(' ',true),null)
  assert.deepEqual(parseOwnerJsonObject('{"logic":"all"}','Screen definition'),{logic:'all'})
  assert.throws(()=>parseOwnerJsonObject('[]'),/JSON object/)
  assert.throws(()=>normalizeOwnerTicker('BAD TICKER'),/Ticker must use/)
})

test('magic links redirect to the app root from GitHub Pages ticker links',()=>{
  assert.equal(
    ownerMagicLinkRedirect({origin:'https://garrincha077.github.io',pathname:'/StockScout-Unified/ticker/NVDA'}),
    'https://garrincha077.github.io/StockScout-Unified/',
  )
  assert.equal(
    ownerMagicLinkRedirect({origin:'http://127.0.0.1:4173',pathname:'/'}),
    'http://127.0.0.1:4173/',
  )
})
