export const TREND_BIRTH_AUTH_BRIDGE_KEY='stockscout:trend-birth-auth-bridge'
export const TREND_BIRTH_APP_URL='https://stockscout-trend-birth-review-lab.vercel.app/'
const BRIDGE_MAX_AGE_MS=2*60*60*1000

type StorageLike={getItem:(key:string)=>string|null;removeItem:(key:string)=>void}
type LocationLike={hash:string;replace:(url:string)=>void}

export function isFreshTrendBirthBridge(value:string|null,now=Date.now()){
  if(!value)return false
  const created=Number(value)
  return Number.isFinite(created)&&created>0&&now-created>=0&&now-created<=BRIDGE_MAX_AGE_MS
}

export function trendBirthAuthForwardUrl(hash:string,marker:string|null,now=Date.now()){
  if(!isFreshTrendBirthBridge(marker,now))return null
  const fragment=String(hash||'')
  if(!/(?:^#|&)(?:access_token|error|error_code)=/.test(fragment))return null
  return TREND_BIRTH_APP_URL+'?tb_auth=complete'+fragment
}

export function forwardTrendBirthAuthIfPending(
  locationLike:LocationLike=window.location,
  storage:StorageLike=window.localStorage,
  now=Date.now(),
){
  const marker=storage.getItem(TREND_BIRTH_AUTH_BRIDGE_KEY)
  const target=trendBirthAuthForwardUrl(locationLike.hash,marker,now)
  if(!target){
    if(marker&&!isFreshTrendBirthBridge(marker,now))storage.removeItem(TREND_BIRTH_AUTH_BRIDGE_KEY)
    return false
  }
  storage.removeItem(TREND_BIRTH_AUTH_BRIDGE_KEY)
  locationLike.replace(target)
  return true
}
