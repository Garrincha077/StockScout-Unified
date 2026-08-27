import type{Session,SupabaseClient,User}from'@supabase/supabase-js'
import{createContext,useCallback,useContext,useEffect,useMemo,useRef,useState,type ReactNode}from'react'
import{useMode,type ModeId}from'../modes/ModeProvider'
import{clearOwnerLocalStorage,nextOwnerWatchlist,normalizeOwnerTicker,ownerMagicLinkRedirect,watchlistAfterSessionChange,type JsonRecord}from'./ownerState'
import{isBrowserSafeSupabaseKey,OWNER_DATA_SCHEMA}from'./supabasePublicConfig'
import type{OwnerAlertPayloadV1}from'./alerts'

type OwnerSupabaseClient=SupabaseClient<any,any,typeof OWNER_DATA_SCHEMA,any,any>
const DEFAULT_WATCHLIST='Default'
const TABLES={watchlists:'unified_watchlist_items',savedScreens:'unified_saved_screens',drawings:'unified_drawings',alerts:'unified_alerts',alertEvents:'unified_alert_events'}as const

export type OwnerSavedScreen={id:string;name:string;mode:ModeId;price_basis:string;definition:JsonRecord;created_at?:string;updated_at?:string}
export type OwnerDrawing={id:string;ticker:string;interval:string;mode:ModeId;price_basis:string;payload:JsonRecord;created_at?:string;updated_at?:string}
export type OwnerAlert={id:string;name:string;ticker:string|null;mode:ModeId;price_basis:string;payload:JsonRecord;enabled:boolean;created_at?:string;updated_at?:string}
export type OwnerAlertEvent={id:number;alert_id:string;run_id:string;mode:ModeId;price_basis:string;payload:JsonRecord;triggered_at:string}
export type OwnerSavedScreenInput={id?:string;name:string;definition:JsonRecord}
export type OwnerDrawingInput={id?:string;ticker:string;interval:string;payload:JsonRecord}
export type OwnerAlertInput={id?:string;name:string;ticker:string|null;payload:OwnerAlertPayloadV1|JsonRecord;enabled:boolean}

type OwnerContextValue={
  configured:boolean;loading:boolean;user:User|null;error:string;watchlist:string[]
  sendMagicLink:(email:string)=>Promise<boolean>;signOut:()=>Promise<void>;toggleWatch:(ticker:string)=>Promise<void>
  listSavedScreens:()=>Promise<OwnerSavedScreen[]>;saveSavedScreen:(screen:OwnerSavedScreenInput)=>Promise<void>;deleteSavedScreen:(id:string)=>Promise<void>
  listDrawings:(ticker:string)=>Promise<OwnerDrawing[]>;saveDrawing:(drawing:OwnerDrawingInput)=>Promise<void>;deleteDrawing:(id:string)=>Promise<void>
  listAlerts:()=>Promise<OwnerAlert[]>;listAlertEvents:()=>Promise<OwnerAlertEvent[]>;saveAlert:(alert:OwnerAlertInput)=>Promise<void>;deleteAlert:(id:string)=>Promise<void>
}

const OwnerContext=createContext<OwnerContextValue|null>(null)
function ownerConfig(){const url=import.meta.env.VITE_SUPABASE_URL?.trim(),key=import.meta.env.VITE_SUPABASE_PUBLISHABLE_KEY?.trim();return url&&key&&isBrowserSafeSupabaseKey(key)?{url,key}:null}
function clearOwnerCache(){if(typeof localStorage!=='undefined')clearOwnerLocalStorage(localStorage)}

export function OwnerDataProvider({children}:{children:ReactNode}){
  const{mode,definition}=useMode(),priceBasis=definition.priceBasis
  const[config]=useState(ownerConfig),[client,setClient]=useState<OwnerSupabaseClient|null>(null),[session,setSession]=useState<Session|null>(null)
  const[loading,setLoading]=useState(Boolean(config)),[error,setError]=useState(''),[watchlist,setWatchlist]=useState<string[]>([])
  const activeUserId=useRef<string|null>(null),user=session?.user??null
  const applySession=useCallback((next:Session|null)=>{const nextUserId=next?.user.id??null;setWatchlist(current=>watchlistAfterSessionChange(current,activeUserId.current,nextUserId));activeUserId.current=nextUserId;if(!nextUserId)clearOwnerCache();setSession(next)},[])

  useEffect(()=>{if(!config){clearOwnerCache();setLoading(false);return}let live=true;import('@supabase/supabase-js').then(({createClient})=>{if(live)setClient(createClient(config.url,config.key,{db:{schema:OWNER_DATA_SCHEMA},auth:{persistSession:true,autoRefreshToken:true,detectSessionInUrl:true}}))}).catch(next=>{if(live){setError(next instanceof Error?next.message:String(next));setLoading(false)}});return()=>{live=false}},[config])
  useEffect(()=>{if(!client){setLoading(false);return}let live=true;client.auth.getSession().then(({data,error:sessionError})=>{if(live){if(sessionError)setError(sessionError.message);applySession(data.session);setLoading(false)}});const{data:{subscription}}=client.auth.onAuthStateChange((_event,next)=>{if(live)applySession(next)});return()=>{live=false;subscription.unsubscribe()}},[client,applySession])
  useEffect(()=>{if(!client||!user){setWatchlist([]);return}let live=true;const ownerId=user.id;client.from(TABLES.watchlists).select('ticker').eq('user_id',ownerId).eq('name',DEFAULT_WATCHLIST).eq('mode',mode).eq('price_basis',priceBasis).order('ticker').then(({data,error:queryError})=>{if(!live||activeUserId.current!==ownerId)return;if(queryError){setError(queryError.message);return}setWatchlist((data??[]).map(row=>String(row.ticker).toUpperCase()))});return()=>{live=false}},[client,user?.id,mode,priceBasis])

  const sendMagicLink=useCallback(async(email:string)=>{if(!client){setError('Owner sync is not configured for this deployment.');return false}setLoading(true);setError('');const{error:signInError}=await client.auth.signInWithOtp({email:email.trim(),options:{shouldCreateUser:false,emailRedirectTo:ownerMagicLinkRedirect(window.location)}});setLoading(false);if(signInError){setError(signInError.message);return false}return true},[client])
  const signOut=useCallback(async()=>{applySession(null);setError('');if(client){const{error:signOutError}=await client.auth.signOut();if(signOutError)setError(signOutError.message)}},[client,applySession])
  const requireOwner=useCallback(()=>{if(!client||!user)throw new Error('Owner sign-in is required');return{client,user}},[client,user])
  const toggleWatch=useCallback(async(ticker:string)=>{const{client:ownerClient,user:owner}=requireOwner(),normalized=normalizeOwnerTicker(ticker),removing=watchlist.includes(normalized),next=nextOwnerWatchlist(watchlist,normalized,owner.id);setWatchlist(next);setError('');const result=await ownerClient.rpc('unified_set_watchlist_ticker',{p_name:DEFAULT_WATCHLIST,p_ticker:normalized,p_mode:mode,p_price_basis:priceBasis,p_present:!removing});if(result.error){if(activeUserId.current===owner.id)setWatchlist(watchlist);setError(result.error.message);throw result.error}},[requireOwner,watchlist,mode,priceBasis])

  const listSavedScreens=useCallback(async()=>{const{client:ownerClient,user:owner}=requireOwner();const{data,error:queryError}=await ownerClient.from(TABLES.savedScreens).select('id,name,mode,price_basis,definition,created_at,updated_at').eq('user_id',owner.id).eq('mode',mode).eq('price_basis',priceBasis).order('updated_at',{ascending:false});if(queryError)throw queryError;return(data??[])as OwnerSavedScreen[]},[requireOwner,mode,priceBasis])
  const saveSavedScreen=useCallback(async(screen:OwnerSavedScreenInput)=>{const{client:ownerClient,user:owner}=requireOwner(),name=screen.name.trim();if(!name||name.length>80)throw new Error('Screen name must use 1-80 characters.');const values={name,mode,price_basis:priceBasis,definition:screen.definition};const result=screen.id?await ownerClient.from(TABLES.savedScreens).update(values).eq('id',screen.id).eq('user_id',owner.id):await ownerClient.from(TABLES.savedScreens).insert({user_id:owner.id,...values});if(result.error)throw result.error},[requireOwner,mode,priceBasis])
  const deleteSavedScreen=useCallback(async(id:string)=>{const{client:ownerClient,user:owner}=requireOwner();const{error:deleteError}=await ownerClient.from(TABLES.savedScreens).delete().eq('id',id).eq('user_id',owner.id);if(deleteError)throw deleteError},[requireOwner])
  const listDrawings=useCallback(async(ticker:string)=>{const{client:ownerClient,user:owner}=requireOwner(),normalized=normalizeOwnerTicker(ticker);const{data,error:queryError}=await ownerClient.from(TABLES.drawings).select('id,ticker,interval,mode,price_basis,payload,created_at,updated_at').eq('user_id',owner.id).eq('ticker',normalized).eq('mode',mode).eq('price_basis',priceBasis).order('updated_at',{ascending:false});if(queryError)throw queryError;return(data??[])as OwnerDrawing[]},[requireOwner,mode,priceBasis])
  const saveDrawing=useCallback(async(drawing:OwnerDrawingInput)=>{const{client:ownerClient,user:owner}=requireOwner(),ticker=normalizeOwnerTicker(drawing.ticker),interval=drawing.interval.trim();if(!interval||interval.length>20)throw new Error('Drawing interval must use 1-20 characters.');const values={ticker,interval,mode,price_basis:priceBasis,payload:drawing.payload};const result=drawing.id?await ownerClient.from(TABLES.drawings).update(values).eq('id',drawing.id).eq('user_id',owner.id):await ownerClient.from(TABLES.drawings).insert({user_id:owner.id,...values});if(result.error)throw result.error},[requireOwner,mode,priceBasis])
  const deleteDrawing=useCallback(async(id:string)=>{const{client:ownerClient,user:owner}=requireOwner();const{error:deleteError}=await ownerClient.from(TABLES.drawings).delete().eq('id',id).eq('user_id',owner.id);if(deleteError)throw deleteError},[requireOwner])
  const listAlerts=useCallback(async()=>{const{client:ownerClient,user:owner}=requireOwner();const{data,error:queryError}=await ownerClient.from(TABLES.alerts).select('id,name,ticker,mode,price_basis,payload,enabled,created_at,updated_at').eq('user_id',owner.id).eq('mode',mode).eq('price_basis',priceBasis).order('updated_at',{ascending:false});if(queryError)throw queryError;return(data??[])as OwnerAlert[]},[requireOwner,mode,priceBasis])
  const listAlertEvents=useCallback(async()=>{const{client:ownerClient,user:owner}=requireOwner();const{data,error:queryError}=await ownerClient.from(TABLES.alertEvents).select('id,alert_id,run_id,mode,price_basis,payload,triggered_at').eq('user_id',owner.id).eq('mode',mode).eq('price_basis',priceBasis).order('triggered_at',{ascending:false}).limit(100);if(queryError)throw queryError;return(data??[])as OwnerAlertEvent[]},[requireOwner,mode,priceBasis])
  const saveAlert=useCallback(async(alert:OwnerAlertInput)=>{const{client:ownerClient,user:owner}=requireOwner(),name=alert.name.trim(),ticker=normalizeOwnerTicker(alert.ticker??'',true);if(!name||name.length>120)throw new Error('Alert name must use 1-120 characters.');const values={name,ticker,mode,price_basis:priceBasis,payload:alert.payload,enabled:alert.enabled};const result=alert.id?await ownerClient.from(TABLES.alerts).update(values).eq('id',alert.id).eq('user_id',owner.id):await ownerClient.from(TABLES.alerts).insert({user_id:owner.id,...values});if(result.error)throw result.error},[requireOwner,mode,priceBasis])
  const deleteAlert=useCallback(async(id:string)=>{const{client:ownerClient,user:owner}=requireOwner();const{error:deleteError}=await ownerClient.from(TABLES.alerts).delete().eq('id',id).eq('user_id',owner.id);if(deleteError)throw deleteError},[requireOwner])

  const value=useMemo<OwnerContextValue>(()=>({configured:Boolean(config),loading,user,error,watchlist,sendMagicLink,signOut,toggleWatch,listSavedScreens,saveSavedScreen,deleteSavedScreen,listDrawings,saveDrawing,deleteDrawing,listAlerts,listAlertEvents,saveAlert,deleteAlert}),[config,loading,user,error,watchlist,sendMagicLink,signOut,toggleWatch,listSavedScreens,saveSavedScreen,deleteSavedScreen,listDrawings,saveDrawing,deleteDrawing,listAlerts,listAlertEvents,saveAlert,deleteAlert])
  return<OwnerContext.Provider value={value}>{children}</OwnerContext.Provider>
}
export function useOwnerData(){const value=useContext(OwnerContext);if(!value)throw new Error('useOwnerData must be used within OwnerDataProvider');return value}
