import {useCallback,useEffect,useState} from 'react';
import Workspace from './App';
import SignIn,{AuthConfig,AuthUser} from './Auth';
import {api,setCsrfToken} from './apiClient';

const LOCAL_HISTORY='modelforge.conversations.v1';
const IMPORT_OWNER='modelforge.history-owner.v1';
const IMPORT_DONE='modelforge.history-imported.v1';
type Boot={user:AuthUser|null;config:AuthConfig|null;error:string;loading:boolean};

async function migrateLocal(user:AuthUser){
 const owner=localStorage.getItem(IMPORT_OWNER);
 if(owner&&owner!==user.id)return;
 if(localStorage.getItem(IMPORT_DONE)===user.id)return;
 let history:any[]=[];
 try{history=JSON.parse(localStorage.getItem(LOCAL_HISTORY)||'[]')}catch{return}
 if(!history.length)return;
 if(owner===user.id)return;
 const conversations=history.slice(0,100).map(c=>({id:String(c.id),title:String(c.title||'Imported conversation').slice(0,240),messages:(Array.isArray(c.messages)?c.messages:[]).slice(-200).map((m:any)=>({role:['user','assistant','system'].includes(m.role)?m.role:'assistant',text:String(m.text||'').slice(0,8000),metadata:m.model?{local_model_id:String(m.model.model_id||'').slice(0,100),shape_type:m.model.spec?.shape_type,dimensions_mm:m.model.spec?.dimensions_mm,generation_method:m.model.generation_method,validation:m.model.validation}:undefined}))}));
 // Bind this legacy history to the first authenticated account before the request, so a failed
 // import cannot later be retried into a different user's account.
 localStorage.setItem(IMPORT_OWNER,user.id);
 await api('/api/conversations/import-local',{conversations});
 // Keep the original key as a recovery copy after the server confirms success.
 localStorage.setItem(IMPORT_DONE,user.id);
}

export default function Root(){
 const [boot,setBoot]=useState<Boot>({user:null,config:null,error:'',loading:true});
 const loadConfig=useCallback(async()=>{
  const config=await api<AuthConfig>('/api/auth/config');setCsrfToken(config.csrf_token);return config;
 },[]);
 const activate=useCallback(async(user:AuthUser,csrf:string)=>{
  setCsrfToken(csrf);
  try{await migrateLocal(user)}catch(error){console.error('Local history import failed; the browser copy was kept.',error)}
  setBoot(x=>({...x,user,loading:false,error:''}));
 },[]);
 useEffect(()=>{
  let alive=true;
  (async()=>{
   try{
    let user:AuthUser|null=null;
    try{user=(await api<{user:AuthUser}>('/api/auth/me')).user}catch(e){if(!(e as Error).message.includes('Sign in'))throw e}
    const config=await loadConfig();
    if(!alive)return;
    if(user){await migrateLocal(user);if(!alive)return}
    setBoot({user,config,error:'',loading:false});
   }catch(e){if(alive)setBoot({user:null,config:null,error:(e as Error).message,loading:false})}
  })();
  return()=>{alive=false};
 },[loadConfig]);
 const signOut=useCallback(async()=>{
  try{await api('/api/auth/logout',{},'POST')}catch{/* Expired sessions are cleared locally as well. */}
  try{const config=await loadConfig();setBoot({user:null,config,error:'',loading:false})}catch(e){setBoot({user:null,config:null,error:(e as Error).message,loading:false})}
 },[loadConfig]);
 if(boot.loading)return <div className="auth-loading"><span className="signin-spinner"/> Loading your workspace…</div>;
 if(boot.user)return <Workspace user={boot.user} onLogout={signOut}/>;
 if(!boot.config)return <div className="auth-loading auth-load-error">{boot.error}<button onClick={()=>location.reload()}>Retry</button></div>;
 return <SignIn config={boot.config} onSignedIn={activate}/>;
}
