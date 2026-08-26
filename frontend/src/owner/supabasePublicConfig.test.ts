import test from 'node:test'
import assert from 'node:assert/strict'
import {isBrowserSafeSupabaseKey,OWNER_DATA_SCHEMA,requireBrowserSafeSupabaseKey} from './supabasePublicConfig.ts'

function jwt(role:string){
  const payload=Buffer.from(JSON.stringify({role})).toString('base64url')
  return`header.${payload}.signature`
}

test('public clients accept only publishable or legacy anon Supabase keys',()=>{
  assert.equal(OWNER_DATA_SCHEMA,'stockscout_unified_api')
  assert.equal(isBrowserSafeSupabaseKey('sb_publishable_abcdefghijklmnop'),true)
  assert.equal(isBrowserSafeSupabaseKey(jwt('anon')),true)
  assert.equal(isBrowserSafeSupabaseKey('sb_secret_abcdefghijklmnop'),false)
  assert.equal(isBrowserSafeSupabaseKey(jwt('service_role')),false)
  assert.throws(()=>requireBrowserSafeSupabaseKey('not-a-key'),/secret\/service-role/)
})
