const PUBLISHABLE_KEY=/^sb_publishable_[A-Za-z0-9._-]{16,}$/
export const OWNER_DATA_SCHEMA='stockscout_unified_api'

function jwtRole(value:string){
  const payload=value.split('.')[1]
  if(!payload)return null
  try{
    const normalized=payload.replaceAll('-','+').replaceAll('_','/')
    const padded=normalized+'='.repeat((4-normalized.length%4)%4)
    const claims=JSON.parse(globalThis.atob(padded))
    return claims&&typeof claims==='object'&&typeof claims.role==='string'?claims.role:null
  }catch{return null}
}

export function isBrowserSafeSupabaseKey(value:string){
  const key=value.trim()
  if(!key||key.startsWith('sb_secret_'))return false
  if(PUBLISHABLE_KEY.test(key))return true
  return jwtRole(key)==='anon'
}

export function requireBrowserSafeSupabaseKey(value:string){
  if(!isBrowserSafeSupabaseKey(value)){
    throw new Error('Supabase browser key must be publishable or a legacy anon JWT; secret/service-role keys are forbidden.')
  }
  return value.trim()
}

export function googleAuthAvailability(value:unknown):boolean|null{
  if(!value||typeof value!=='object'||Array.isArray(value))return null
  const external=(value as Record<string,unknown>).external
  if(!external||typeof external!=='object'||Array.isArray(external))return null
  const google=(external as Record<string,unknown>).google
  return typeof google==='boolean'?google:null
}
