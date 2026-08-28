import{useEffect,useState,type FormEvent}from'react'
import{useOwnerData}from'./OwnerDataProvider'
import{OPEN_OWNER_ACCESS_EVENT}from'./ownerAccessEvent'
import'./owner.css'

export default function OwnerAccess(){
  const{configured,loading,user,error,signInWithGoogle,sendMagicLink,signOut}=useOwnerData()
  const[open,setOpen]=useState(false),[cooldown,setCooldown]=useState(0),[email,setEmail]=useState('')
  useEffect(()=>{const show=()=>setOpen(true);window.addEventListener(OPEN_OWNER_ACCESS_EVENT,show);return()=>window.removeEventListener(OPEN_OWNER_ACCESS_EVENT,show)},[])
  useEffect(()=>{if(cooldown<=0)return;const timer=window.setInterval(()=>setCooldown(value=>Math.max(0,value-1)),1000);return()=>window.clearInterval(timer)},[cooldown>0])
  const submit=async(event:FormEvent)=>{event.preventDefault();if(await sendMagicLink(email))setCooldown(60)}
  if(user)return<div className="owner-access"><span title={user.email??'Owner'}>Owner · {user.email}</span><button onClick={signOut}>Sign out</button></div>
  return<div className="owner-access"><button className="owner-login" disabled={loading} onClick={()=>setOpen(value=>!value)}>{configured?'Owner sign in':'Owner setup required'}</button>{open&&(configured?<div className="owner-popover"><b>Owner access</b><p>Use the allowlisted Google account. Drawings and alerts stay private.</p><button type="button" className="owner-google-login" disabled={loading} onClick={()=>void signInWithGoogle()}>{loading?'Opening sign in…':'Continue with Google'}</button><details><summary>Magic-link fallback</summary><form onSubmit={submit}><p>Use only if Google sign-in is unavailable. Supabase limits its built-in email delivery.</p><label>Email<input type="email" autoComplete="email" required value={email} onChange={event=>setEmail(event.target.value)}/></label>{cooldown>0&&<small className="owner-reset-notice" role="status">Magic link sent. You can request another in {cooldown}s.</small>}<button type="submit" disabled={loading||cooldown>0}>{loading?'Sending…':cooldown>0?`Retry in ${cooldown}s`:'Send magic link'}</button></form></details>{error&&<small role="alert">{error}</small>}</div>:<div className="owner-popover owner-config-missing" role="status"><b>Owner features are not configured</b><p>This deployment can read public scans, but synced drawings and alerts need the dedicated owner-state project variables.</p><code>VITE_SUPABASE_URL<br/>VITE_SUPABASE_PUBLISHABLE_KEY<br/>UNIFIED_DELIVERY_ENDPOINT</code></div>)}</div>
}
