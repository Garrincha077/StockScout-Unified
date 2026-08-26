import{useState,type FormEvent}from'react'
import{useOwnerData}from'./OwnerDataProvider'
import'./owner.css'

export default function OwnerAccess(){
  const{configured,loading,user,error,sendMagicLink,signOut}=useOwnerData()
  const[open,setOpen]=useState(false),[sent,setSent]=useState(false),[email,setEmail]=useState('')
  const submit=async(event:FormEvent)=>{event.preventDefault();if(await sendMagicLink(email))setSent(true)}
  if(user)return<div className="owner-access"><span title={user.email??'Owner'}>Owner · {user.email}</span><button onClick={signOut}>Sign out</button></div>
  return<div className="owner-access"><button className="owner-login" disabled={!configured||loading} onClick={()=>setOpen(value=>!value)}>{configured?'Owner sign in':'Local/public data'}</button>{open&&<form className="owner-popover" onSubmit={submit}><b>Owner access</b><p>Enter the allowlisted email. We will send a one-time magic link; no password is used.</p><label>Email<input type="email" autoComplete="email" required value={email} onChange={event=>setEmail(event.target.value)}/></label>{sent&&<small className="owner-reset-notice" role="status">Magic link sent. Open it on this device to sign in.</small>}{error&&<small role="alert">{error}</small>}<button type="submit" disabled={loading||sent}>{loading?'Sending…':sent?'Link sent':'Send magic link'}</button></form>}</div>
}
