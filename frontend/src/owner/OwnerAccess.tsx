import{useEffect,useState,type FormEvent}from'react'
import{useOwnerData}from'./OwnerDataProvider'
import{OPEN_OWNER_ACCESS_EVENT}from'./ownerAccessEvent'
import'./owner.css'

export default function OwnerAccess(){
  const{configured,loading,user,error,sendMagicLink,signOut}=useOwnerData()
  const[open,setOpen]=useState(false),[sent,setSent]=useState(false),[email,setEmail]=useState('')
  useEffect(()=>{const show=()=>setOpen(true);window.addEventListener(OPEN_OWNER_ACCESS_EVENT,show);return()=>window.removeEventListener(OPEN_OWNER_ACCESS_EVENT,show)},[])
  const submit=async(event:FormEvent)=>{event.preventDefault();if(await sendMagicLink(email))setSent(true)}
  if(user)return<div className="owner-access"><span title={user.email??'Owner'}>Owner · {user.email}</span><button onClick={signOut}>Sign out</button></div>
  return<div className="owner-access"><button className="owner-login" disabled={loading} onClick={()=>setOpen(value=>!value)}>{configured?'Owner sign in':'Owner setup required'}</button>{open&&(configured?<form className="owner-popover" onSubmit={submit}><b>Owner access</b><p>Enter the allowlisted email. We will send a one-time magic link; no password is used.</p><label>Email<input type="email" autoComplete="email" required value={email} onChange={event=>setEmail(event.target.value)}/></label>{sent&&<small className="owner-reset-notice" role="status">Magic link sent. Open it on this device to sign in.</small>}{error&&<small role="alert">{error}</small>}<button type="submit" disabled={loading||sent}>{loading?'Sending…':sent?'Link sent':'Send magic link'}</button></form>:<div className="owner-popover owner-config-missing" role="status"><b>Owner features are not configured</b><p>This deployment can read public scans, but synced drawings and alerts need the dedicated owner-state project variables.</p><code>VITE_SUPABASE_URL<br/>VITE_SUPABASE_PUBLISHABLE_KEY<br/>UNIFIED_DELIVERY_ENDPOINT</code></div>)}</div>
}
