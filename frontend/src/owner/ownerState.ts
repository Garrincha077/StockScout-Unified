export type JsonRecord=Record<string,unknown>

const TICKER_PATTERN=/^[A-Z0-9._-]{1,20}$/
export const LEGACY_OWNER_WATCHLIST_KEY='stockscout-eod-watchlist-v1'

function ownerAppRoot(pathname:string){
  const tickerIndex=pathname.toLowerCase().indexOf('/ticker/')
  if(tickerIndex>=0)return`${pathname.slice(0,tickerIndex)}/`
  if(pathname.endsWith('/'))return pathname
  const lastSegment=pathname.split('/').at(-1)??''
  return lastSegment.includes('.')?pathname.replace(/[^/]+$/,''):`${pathname}/`
}

export function ownerMagicLinkRedirect(location:{origin:string;pathname:string}){
  return new URL(ownerAppRoot(location.pathname),location.origin).toString()
}

export function clearOwnerLocalStorage(storage:{removeItem:(key:string)=>void}){
  try{storage.removeItem(LEGACY_OWNER_WATCHLIST_KEY)}catch{}
}

export function normalizeOwnerTicker(value:string):string
export function normalizeOwnerTicker(value:string,allowEmpty:false):string
export function normalizeOwnerTicker(value:string,allowEmpty:true):string|null
export function normalizeOwnerTicker(value:string,allowEmpty=false):string|null{
  const ticker=value.trim().toUpperCase()
  if(allowEmpty&&ticker==='')return null
  if(!TICKER_PATTERN.test(ticker))throw new Error('Ticker must use 1-20 letters, numbers, dots, dashes or underscores.')
  return ticker
}

export function parseOwnerJsonObject(value:string,label='JSON payload'):JsonRecord{
  let parsed:unknown
  try{parsed=JSON.parse(value)}catch{throw new Error(`${label} must be valid JSON.`)}
  if(parsed===null||Array.isArray(parsed)||typeof parsed!=='object')throw new Error(`${label} must be a JSON object.`)
  return parsed as JsonRecord
}

export function watchlistAfterSessionChange(current:string[],previousUserId:string|null,nextUserId:string|null){
  return nextUserId!==null&&previousUserId===nextUserId?current:[]
}

export function nextOwnerWatchlist(current:string[],ticker:string,userId:string|null){
  if(!userId)throw new Error('Owner sign-in is required to change the watchlist.')
  const normalized=normalizeOwnerTicker(ticker)
  return current.includes(normalized)
    ?current.filter(item=>item!==normalized)
    :[...current,normalized]
}
