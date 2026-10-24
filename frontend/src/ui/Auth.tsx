import {FormEvent,useEffect,useRef,useState} from 'react';
import {Layers3, ShieldCheck} from './icons';
import {api} from './apiClient';
import './auth.css';

export type AuthUser={id:string;email:string;name:string;picture:string|null};
export type AuthConfig={google_client_id:string|null;csrf_token:string;nonce:string};
declare global {
 interface Window {
  google?: { accounts: { id: { initialize:(options:any)=>void; renderButton:(el:HTMLElement,options:any)=>void; disableAutoSelect:()=>void } } };
 }
}

export default function SignIn({config,onSignedIn}:{config:AuthConfig;onSignedIn:(user:AuthUser,csrf:string)=>void}){
 const button=useRef<HTMLDivElement>(null);
 const [mode,setMode]=useState<'login'|'register'>('login');
 const [error,setError]=useState('');
 const [busy,setBusy]=useState(false);
 const [name,setName]=useState('');
 const [email,setEmail]=useState('');
 const [password,setPassword]=useState('');
 const [confirmPassword,setConfirmPassword]=useState('');
 useEffect(()=>{
  if(!config.google_client_id||!button.current)return;
  const render=()=>{
   if(!window.google||!button.current)return;
   window.google.accounts.id.initialize({client_id:config.google_client_id,nonce:config.nonce,auto_select:false,callback:async(result:{credential:string})=>{
    setBusy(true);setError('');
    try{const response=await api<{user:AuthUser;csrf_token:string}>('/api/auth/google',{credential:result.credential});onSignedIn(response.user,response.csrf_token)}
    catch(e){setError((e as Error).message);setBusy(false)}
   }});
   window.google.accounts.id.renderButton(button.current,{type:'standard',theme:'outline',size:'large',shape:'rect',text:'signin_with',logo_alignment:'left',width:320});
  };
  const existing=document.getElementById('google-identity');
  if(window.google)render(); else existing?.addEventListener('load',render,{once:true});
  return()=>existing?.removeEventListener('load',render);
 },[config.google_client_id,config.nonce,onSignedIn]);

 const submit=async(event:FormEvent)=>{
  event.preventDefault();setError('');
  if(mode==='register'&&password!==confirmPassword){setError('Passwords do not match.');return}
  setBusy(true);
  try{
   const path=mode==='register'?'/api/auth/register':'/api/auth/login';
   const payload=mode==='register'?{name,email,password}:{email,password};
   const response=await api<{user:AuthUser;csrf_token:string}>(path,payload);
   onSignedIn(response.user,response.csrf_token);
  }catch(e){setError((e as Error).message);setBusy(false)}
 };

 return <main className="signin-page"><section className="signin-art"><div className="signin-brand"><span className="signin-mark"><Layers3 size={20}/></span><b>ModelForge</b></div><div className="signin-copy"><span className="signin-overline">IDEAS, BUILT TO SCALE</span><h1>Bring your<br/>ideas into <em>3D.</em></h1><p>Shape a thought into a model. Explore the geometry, refine the details, and make it yours.</p><div className="signin-orbit"><div className="orbit-ring ring-a"/><div className="orbit-ring ring-b"/><div className="orbit-core"><Layers3 size={34}/></div><div className="orbit-node node-a"/><div className="orbit-node node-b"/><div className="orbit-node node-c"/></div></div><div className="signin-footer"><ShieldCheck size={14}/> Private workspace · Your projects stay yours</div></section>
  <section className="signin-form-side"><div className="signin-form"><div className="signin-mobile-brand"><span className="signin-mark"><Layers3 size={18}/></span>ModelForge</div><div className="signin-icon"><Layers3 size={20}/></div><p className="signin-kicker">YOUR CREATION WORKSPACE</p><h2>{mode==='register'?'Create your account':'Welcome to ModelForge'}</h2><p className="signin-description">Save your conversations, models, and project history in your private workspace.</p>
   <div className="auth-tabs" role="tablist" aria-label="Account access"><button type="button" role="tab" aria-selected={mode==='login'} className={mode==='login'?'active':''} onClick={()=>{setMode('login');setError('')}}>Sign in</button><button type="button" role="tab" aria-selected={mode==='register'} className={mode==='register'?'active':''} onClick={()=>{setMode('register');setError('')}}>Create account</button></div>
   <form className="auth-form" onSubmit={submit}>{mode==='register'&&<label>Your name<input autoComplete="name" required maxLength={120} value={name} onChange={e=>setName(e.target.value)} placeholder="Name"/></label>}<label>Email<input type="email" autoComplete="email" required maxLength={320} value={email} onChange={e=>setEmail(e.target.value)} placeholder="you@example.com"/></label><label>Password<input type="password" autoComplete={mode==='register'?'new-password':'current-password'} required minLength={mode==='register'?12:1} maxLength={128} value={password} onChange={e=>setPassword(e.target.value)} placeholder={mode==='register'?'At least 12 characters':'Your password'}/>{mode==='register'&&<small>Use at least 12 characters.</small>}</label>{mode==='register'&&<label>Confirm password<input type="password" autoComplete="new-password" required minLength={12} maxLength={128} value={confirmPassword} onChange={e=>setConfirmPassword(e.target.value)} placeholder="Enter your password again"/></label>}<button className="auth-submit" type="submit" disabled={busy}>{busy?<><span className="signin-spinner"/> {mode==='register'?'Creating account…':'Signing in…'}</>:mode==='register'?'Create account':'Sign in'}</button></form>
   {error&&<div className="signin-error" role="alert">{error}</div>}
   <div className="auth-divider"><span/>or continue with<span/></div>
   {config.google_client_id?<div className={`google-button ${busy?'disabled':''}`} ref={button}/>:<div className="auth-provider-unavailable">Google sign-in isn’t configured here. You can use email and password.</div>}
   <div className="signin-security"><ShieldCheck size={14}/> Protected account sessions</div><div className="signin-legal">Passwords are stored as salted scrypt hashes. Authentication tokens stay in secure, HTTP-only cookies and are never saved in browser storage.</div>
  </div></section></main>
}
