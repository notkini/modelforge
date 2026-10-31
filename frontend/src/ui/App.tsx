import {useCallback,useEffect,useMemo,useRef,useState} from 'react';
import {Archive,ChevronDown, ChevronLeft, ChevronRight, CircleHelp, Download, FileImage, Layers3, Menu, Moon, Pencil, Plus, Send, Settings2, Sun, Square, Trash2, X} from './icons';
import {Model,Message,Conversation,InterpreterChoice,VersionSummary,SettingsProvider} from './types';
import {api} from './apiClient';
import VersionHistory from './VersionHistory';
import {createVersionLoader,createsBranch,pickRefineParent} from './versions';
import {AuthUser} from './Auth';
import Viewer from './Viewer';
import RenameConversationDialog from './RenameConversationDialog';
import DeleteConversationDialog from './DeleteConversationDialog';
import SettingsPanel from './SettingsPanel';
import Toast from './Toast';

const examples=['Create a cylinder with a 20 mm diameter and 50 mm height.','Create a miniature table, 40 mm tall, with a 40 x 30 mm top and four legs.','Create a phone stand, 80 mm wide and 100 mm tall, with a sloped support.'];
const titleFromPrompt=(text:string)=>{const t=text.trim().slice(0,48).replace(/[.!?,]+$/,'');return t||'New creation'};
const CONVERSATION_TITLE_MAX=240;

export default function App({user,onLogout}:{user:AuthUser;onLogout:()=>void}){
  const [convos,setConvos]=useState<Conversation[]>([]);
  const [active,setActive]=useState('');
  const [prompt,setPrompt]=useState('');
  const [loading,setLoading]=useState(false);
  const [error,setError]=useState('');
  const [collapsed,setCollapsed]=useState(false);
  const [dark,setDark]=useState(true);
  const [attached,setAttached]=useState('');
  const [advanced,setAdvanced]=useState(false);
  const [dimensions,setDimensions]=useState([100,60,30]);
  const [selected,setSelected]=useState<Model|null>(null);
  const [wire,setWire]=useState(false);
  const [grid,setGrid]=useState(true);
  const [axes,setAxes]=useState(false);
  const [ortho,setOrtho]=useState(false);
  const [cameraReset,setCameraReset]=useState(0);
  const [inspector,setInspector]=useState(true);
  const [accountOpen,setAccountOpen]=useState(false);
  const [showArchived,setShowArchived]=useState(false);
  const [historyTick,setHistoryTick]=useState(0);
  const [versionLoading,setVersionLoading]=useState('');
  const [visibleComponents]=useState<Record<string,boolean>>({});
  const [clearance,setClearance]=useState(0);
  const [settingsOpen,setSettingsOpen]=useState(false);
  const [userSettings,setUserSettings]=useState<{provider:SettingsProvider;configured:boolean;connection_status:string|null}|null>(null);

  const [interpreter,setInterpreter]=useState<InterpreterChoice>(()=>{
    const stored=localStorage.getItem('modelforge.interpreter');
    return stored==='gemini'||stored==='local'?stored:'auto';
  });

  // Optimistic message / cancellation state
  const abortControllers=useRef<Map<string,AbortController>>(new Map());
  const [generatingConvId,setGeneratingConvId]=useState<string|null>(null);
  const [cancellationRequested,setCancellationRequested]=useState(false);
  const [currentRequestId,setCurrentRequestId]=useState<string|null>(null);
  const [toasts,setToasts]=useState<Array<{id:string;message:string;type:'error'|'success'}>>([]);
  const [renameOpen,setRenameOpen]=useState(false);
  const [renameTarget,setRenameTarget]=useState<{id:string;title:string}|null>(null);
  const [deleteOpen,setDeleteOpen]=useState(false);
  const [deleteTarget,setDeleteTarget]=useState<string|null>(null);

  useEffect(()=>{
    let live=true;
    (async()=>{
      try{
        const page=await api<{items:any[]}>(`/api/conversations?limit=50${showArchived?'&include_archived=true':''}`);
        if(!live)return;
        const list=page.items.filter(c=>showArchived?c.archived:!c.archived).map(c=>({id:c.id,title:c.title,messages:[],updated:Date.parse(c.updated_at),archived:c.archived}));
        setConvos(list);
        if(list.length){
          setActive(list[0].id);
          const detail=await api<{messages:any[]}>('/api/conversations/'+list[0].id);
          if(live)setConvos(xs=>xs.map(c=>c.id===list[0].id?{...c,messages:detail.messages.map(m=>({id:m.id,role:m.role,text:m.text,metadata:m.metadata,model:m.model?{...m.model,provider:(m.metadata?.interpretation_provider||m.metadata?.provider) as string,interpretation_provider:(m.metadata?.interpretation_provider||m.metadata?.provider) as string,fallback_reason:m.metadata?.fallback_reason as string}:undefined}))}:c));
        }
      }catch(e){if(live)setError((e as Error).message)}
    })();
    return()=>{live=false};
  },[showArchived]);

  useEffect(()=>{
    let live=true;
    (async()=>{
      try{
        const s=await api<{provider:string;configured:boolean;connection_status:string|null}>('/api/settings');
        if(!live)return;
        setUserSettings({provider:s.provider as SettingsProvider,configured:s.configured,connection_status:s.connection_status});
      }catch{
        // Settings fetch failure is non-fatal; the panel surfaces errors when opened.
      }
    })();
    return()=>{live=false};
  },[]);

  const current=convos.find(c=>c.id===active);
  const latest=useMemo(()=>current?.messages.slice().reverse().find(m=>m.model)?.model||null,[current]);
  const isGenerating=loading&&generatingConvId===active;

  const isScriptModel=(m:Model|null|undefined)=>!!m&&m.generation_method==='cadquery_script'&&!!(m.spec as Record<string,unknown>).creation_script;
  const modelDims=(m:Model|null|undefined):number[]|null=>{
    if(!m)return null;
    const raw=(m.spec as Record<string,unknown>).dimensions_mm as {width:number;depth:number;height:number}|undefined;
    if(raw&&[raw.width,raw.depth,raw.height].every(v=>Number.isFinite(v)))return[raw.width,raw.depth,raw.height];
    const v=m.validation?.dimensions_mm;
    if(v&&v.length>=3&&v.slice(0,3).every(x=>Number.isFinite(x)))return[v[0],v[1],v[2]];
    return null;
  };
  const applyModelDims=(m:Model|null|undefined)=>{
    if(!m)return;
    const v=modelDims(m);
    if(v)setDimensions(v);
  };

  const versionLoaderRef=useRef<ReturnType<typeof createVersionLoader>|null>(null);
  if(!versionLoaderRef.current)versionLoaderRef.current=createVersionLoader(modelId=>api<Model>(`/api/models/${modelId}`),{onStart:modelId=>setVersionLoading(modelId),onLoaded:model=>{setSelected(model);applyModelDims(model)},onFailed:message=>setError(message),onSettled:()=>setVersionLoading('')});
  const versionLoader=versionLoaderRef.current!;

  const interpreterLabel=latest?.provider==='gemini'?'Gemini':latest?.provider==='local-deterministic'?'Local deterministic parser':latest?'Parameter inspector':'Ready';
  const geometryLabel=latest?.generation_method==='gaussian_implicit_mesh'?'local Gaussian mesh':latest?.generation_method==='cadquery_script'?'Gemini CadQuery script':latest?.generation_method==='parametric_cad'?'local parametric CAD':'local geometry';

  const addToast=(message:string,type:'error'|'success'='error')=>{
    const id=Math.random().toString(36).slice(2);
    setToasts(xs=>[...xs,{id,message,type}]);
    setTimeout(()=>setToasts(xs=>xs.filter(t=>t.id!==id)),5000);
  };

  const openConversation=useCallback(async(id:string)=>{
    // Cancel any in-flight request for a different conversation before switching
    const ctrl=abortControllers.current.get(id);
    if(ctrl)ctrl.abort();
    setActive(id);
    setSelected(null);
    setGeneratingConvId(null);
    try{
      const detail=await api<{messages:any[]}>('/api/conversations/'+id);
      const messages:Message[]=detail.messages.map(m=>({id:m.id,role:m.role,text:m.text,metadata:m.metadata,model:m.model?{...m.model,provider:(m.metadata?.interpretation_provider||m.metadata?.provider) as string,interpretation_provider:(m.metadata?.interpretation_provider||m.metadata?.provider) as string,fallback_reason:m.metadata?.fallback_reason as string}:undefined}));
      setConvos(xs=>xs.map(c=>c.id===id?{...c,messages}:c));
    }catch(e){setError((e as Error).message)}
  },[]);

  const start=useCallback(async()=>{
    setError('');
    try{
      const c=await api<any>('/api/conversations',{title:'New creation'});
      const next={id:c.id,title:c.title,messages:[],updated:Date.now()};
      setConvos(x=>[next,...x]);
      setActive(next.id);
      setSelected(null);
    }catch(e){setError((e as Error).message)}
  },[]);

  const generate=useCallback(async(text=prompt)=>{
    if(!text.trim()||loading)return;
    setError('');
    const convId=active||'';
    if(!convId){
      try{
        const c=await api<any>('/api/conversations',{title:titleFromPrompt(text)});
        setActive(c.id);
        setConvos(xs=>[{id:c.id,title:c.title,messages:[],updated:Date.now()},...xs]);
      }catch(e){setError((e as Error).message);return;}
    }
    const id=active;
    if(!id)return;

    // Abort any previous request for this conversation
    const prevCtrl=abortControllers.current.get(id);
    if(prevCtrl)prevCtrl.abort();
    const ctrl=new AbortController();
    abortControllers.current.set(id,ctrl);

    // Optimistic user message
    const optimisticUserId=`opt-${Math.random().toString(36).slice(2)}`;
    const optimisticUser:Message={id:optimisticUserId,role:'user',text,optimisticId:optimisticUserId,status:'pending'};

    // Optimistic assistant (generating) message
    const optimisticAssistantId=`opt-assist-${Math.random().toString(36).slice(2)}`;
    const optimisticAssistant:Message={id:optimisticAssistantId,role:'assistant',text:'Generating…',optimisticId:optimisticAssistantId,status:'pending'};

    setLoading(true);
    setGeneratingConvId(id);
    setCancellationRequested(false);
    setCurrentRequestId(null);

    const base=convos.find(c=>c.id===id)?.messages||[];

    try{
      // Persist user message
      await api(`/api/conversations/${id}/messages`,{role:'user',text},'POST',ctrl.signal);

      // Add optimistic messages to UI
      const withUser=[...base,optimisticUser];
      const withAssistant=[...withUser,optimisticAssistant];
      setConvos(xs=>xs.map(c=>c.id===id?{...c,messages:withAssistant,updated:Date.now()}:c));
      setPrompt('');

      // Generate model
      const model=await api<Model>('/api/models/generate',{prompt:text,conversation_id:id,interpreter,...(clearance>0?{clearance_mm:clearance}:{})},'POST',ctrl.signal);

      // If conversation switched, ignore late response
      if(active!==id)return;

      // Store request_id for potential cancellation
      if(model.request_id)setCurrentRequestId(model.request_id);

      // Replace optimistic messages with final
      const finalMsg:Message={role:'assistant',text:model.message||'Model generated.',model,status:'complete'};
      setConvos(xs=>xs.map(c=>c.id===id?{...c,messages:[...base,optimisticUser,finalMsg],updated:Date.now()}:c));
      applyModelDims(model);
      setSelected(model);
      setHistoryTick(t=>t+1);
    }catch(e){
      // If conversation switched, ignore
      if(active!==id)return;
      // AbortError means user clicked Stop — preserve user message, mark assistant as cancelled
      if(e instanceof DOMException&&e.name==='AbortError'){
        setConvos(xs=>xs.map(c=>c.id===id?{...c,messages:[...base,optimisticUser,{id:optimisticAssistantId,role:'assistant',text:'Stopped.',status:'cancelled'}],updated:Date.now()}:c));
        setError('');
        setCancellationRequested(false);
        setCurrentRequestId(null);
        return;
      }
      setError((e as Error).message);
      setConvos(xs=>xs.map(c=>c.id===id?{...c,messages:[...base,optimisticUser,{id:optimisticAssistantId,role:'assistant',text:(e as Error).message||'Generation failed.',status:'failed'}],updated:Date.now()}:c));
    }finally{
      setLoading(false);
      setGeneratingConvId(null);
      abortControllers.current.delete(id);
    }
  },[prompt,active,loading,interpreter,clearance,convos,currentRequestId]);

  const refine=useCallback(async(text:string)=>{
    const parent=pickRefineParent(selected,latest);
    if(!parent||loading)return;
    const id=active;
    if(!id)return;

    // Abort any previous request for this conversation
    const prevCtrl=abortControllers.current.get(id);
    if(prevCtrl)prevCtrl.abort();
    const ctrl=new AbortController();
    abortControllers.current.set(id,ctrl);

    setLoading(true);
    setError('');
    setGeneratingConvId(id);
    setCancellationRequested(false);
    setCurrentRequestId(null);

    const optimisticUserId=`opt-${Math.random().toString(36).slice(2)}`;
    const optimisticUser:Message={id:optimisticUserId,role:'user',text,optimisticId:optimisticUserId,status:'pending'};
    const optimisticAssistantId=`opt-assist-${Math.random().toString(36).slice(2)}`;
    const optimisticAssistant:Message={id:optimisticAssistantId,role:'assistant',text:'Refining…',optimisticId:optimisticAssistantId,status:'pending'};

    const base=current?.messages||[];

    try{
      const withUser=[...base,optimisticUser];
      const withAssistant=[...withUser,optimisticAssistant];
      setConvos(xs=>xs.map(c=>c.id===id?{...c,messages:withAssistant,updated:Date.now()}:c));

      await api(`/api/conversations/${id}/messages`,{role:'user',text},'POST',ctrl.signal);
      const result=await api<Model>(`/api/models/${parent.model_id}/refine`,{instruction:text,interpreter},'POST',ctrl.signal);

      if(active!==id)return;

      // Store request_id for potential cancellation
      if(result.request_id)setCurrentRequestId(result.request_id);

      const assistant:Message={role:'assistant',text:result.message||'Model updated.',model:result,status:'complete'};
      setConvos(xs=>xs.map(c=>c.id===id?{...c,messages:[...base,optimisticUser,assistant],updated:Date.now()}:c));
      setSelected(result);
      applyModelDims(result);
      setPrompt('');
      setHistoryTick(t=>t+1);
    }catch(e){
      if(active!==id)return;
      if(e instanceof DOMException&&e.name==='AbortError'){
        setConvos(xs=>xs.map(c=>c.id===id?{...c,messages:[...base,optimisticUser,{id:optimisticAssistantId,role:'assistant',text:'Stopped.',status:'cancelled'}],updated:Date.now()}:c));
        setError('');
        setCancellationRequested(false);
        setCurrentRequestId(null);
        return;
      }
      setError((e as Error).message);
      setConvos(xs=>xs.map(c=>c.id===id?{...c,messages:[...base,optimisticUser,{id:optimisticAssistantId,role:'assistant',text:(e as Error).message||'Refinement failed.',status:'failed'}],updated:Date.now()}:c));
    }finally{
      setLoading(false);
      setGeneratingConvId(null);
      abortControllers.current.delete(id);
    }
  },[selected,latest,loading,active,interpreter,convos,current?.messages,currentRequestId]);

  const submit=()=>latest?refine(prompt):generate(prompt);

  const stopGeneration=useCallback(async()=>{
    const id=active;
    if(!id)return;
    const ctrl=abortControllers.current.get(id);
    if(ctrl){
      ctrl.abort();
      abortControllers.current.delete(id);
    }
    setCancellationRequested(true);
    const reqId=currentRequestId;
    // Send cancellation to backend (separate fetch so it can reach the server
    // even after the generation fetch is aborted).
    if(reqId){
      try{
        await api(`/api/models/cancel`,{request_id:reqId},'POST');
      }catch{
        // Cancellation network failure: UI still shows stopped state.
      }
    }
    setGeneratingConvId(null);
    setCancellationRequested(false);
    setCurrentRequestId(null);
  },[active,currentRequestId]);

  const selectModel=useCallback(async(model:Model)=>{
    setSelected(model);
    if(model.vertices&&model.faces)return;
    await versionLoader.load(model.model_id);
  },[versionLoader]);

  const exportModel=useCallback(async(format:'stl'|'glb'|'step',componentId?:string|null)=>{
    if(!selected)return;
    try{
      const url=new URL(`/api/models/${selected.model_id}/export`,window.location.origin);
      url.searchParams.set('format',format);
      if(componentId)url.searchParams.set('component',componentId);
      const r=await fetch(url.toString(),{credentials:'include'});
      if(!r.ok){
        const j=await r.json().catch(()=>null);
        throw new Error(j?.detail?.message||'Export failed.');
      }
      const a=document.createElement('a');
      a.href=URL.createObjectURL(await r.blob());
      const ext = format === 'step' ? 'step' : format;
      a.download=`modelforge${componentId?'-'+componentId:''}.${ext}`;
      a.click();
      URL.revokeObjectURL(a.href);
    }catch(e){setError((e as Error).message);}
  },[selected]);

  const editDim=useCallback(async(i:number,value:number)=>{
    const m=selected;
    if(!m||!Number.isFinite(value)||value<=0||!active)return;
    const arr=[...dimensions];
    arr[i]=value;
    setDimensions(arr);
    const spec={...m.spec,dimensions_mm:{width:arr[0],depth:arr[1],height:arr[2]}};
    setLoading(true);
    try{
      const next=await api<Model>('/api/models/generate',{spec,conversation_id:active,parent_model_id:m.model_id,...(clearance>0?{clearance_mm:clearance}:{})});
      const base=current?.messages||[];
      const assistant:Message={role:'assistant',text:'Updated model dimensions from the inspector. Previous versions remain in this conversation.',model:next,status:'complete'};
      setConvos(xs=>xs.map(c=>c.id===active?{...c,messages:[...base,assistant],updated:Date.now()}:c));
      setSelected(next);
      setHistoryTick(t=>t+1);
    }catch(e){setError((e as Error).message);}finally{setLoading(false);}
  },[selected,active,dimensions,clearance,current?.messages]);

  const editParam=useCallback(async(field:string,value:unknown)=>{
    const m=selected;
    if(!m||!active)return;
    const spec={...m.spec,[field]:value};
    setLoading(true);
    try{
      const next=await api<Model>('/api/models/generate',{spec,conversation_id:active,parent_model_id:m.model_id,...(clearance>0?{clearance_mm:clearance}:{})});
      const base=current?.messages||[];
      const assistant:Message={role:'assistant',text:'Updated '+field.replace(/_/g,' ')+' in the parameter inspector. Previous model versions are preserved.',model:next,status:'complete'};
      setConvos(xs=>xs.map(c=>c.id===active?{...c,messages:[...base,assistant],updated:Date.now()}:c));
      setSelected(next);
      setHistoryTick(t=>t+1);
    }catch(e){setError((e as Error).message);}finally{setLoading(false);}
  },[selected,active,clearance,current?.messages]);

  const remove=useCallback(async(id:string)=>{
    setDeleteTarget(id);
    setDeleteOpen(true);
  },[]);

  const confirmDelete=useCallback(async()=>{
    const id=deleteTarget;
    if(!id)return;
    setDeleteOpen(false);
    setDeleteTarget(null);
    try{
      await api(`/api/conversations/${id}`,undefined,'DELETE');
      setConvos(xs=>xs.filter(c=>c.id!==id));
      if(active===id){setActive('');setSelected(null);}
    }catch(e){
      setError((e as Error).message);
      addToast((e as Error).message,'error');
    }
  },[deleteTarget,active]);

  const rename=useCallback(async(id:string,title:string)=>{
    try{
      const c=await api<any>(`/api/conversations/${id}`,{title},'PATCH');
      setConvos(xs=>xs.map(x=>x.id===id?{...x,title:c.title}:x));
    }catch(e){
      setError((e as Error).message);
      addToast((e as Error).message,'error');
    }
  },[]);

  const openRename=useCallback((id:string,title:string)=>{
    setRenameTarget({id,title});
    setRenameOpen(true);
  },[]);

  const confirmRename=useCallback(async()=>{
    if(!renameTarget)return;
    const title=renameTarget.title.trim();
    if(!title){
      setError('Conversation title cannot be empty.');
      return;
    }
    if(title.length>CONVERSATION_TITLE_MAX){
      setError(`Title too long (max ${CONVERSATION_TITLE_MAX} characters).`);
      return;
    }
    setRenameOpen(false);
    const {id}=renameTarget;
    setRenameTarget(null);
    try{
      await rename(id,title);
    }catch(e){
      setError((e as Error).message);
    }
  },[renameTarget,rename]);

  const archive=useCallback(async(id:string,archived:boolean)=>{
    try{
      await api(`/api/conversations/${id}`,{archived},'PATCH');
      setConvos(xs=>xs.filter(c=>c.id!==id));
      if(active===id){setActive('');setSelected(null);}
    }catch(e){setError((e as Error).message);}
  },[]);

  const onFile=(file?:File)=>{if(file)setAttached(file.name)};

  return (
    <div className={`app ${dark?'theme-dark':'theme-light'}`}>
      <aside className={`sidebar ${collapsed?'is-collapsed':''}`}>
        <div className="brand">
          <div className="brand-mark"><Layers3 size={19}/></div>
          {!collapsed&&(
            <>
              <span>ModelForge</span>
              <button className="icon-button side-collapse" title="Collapse sidebar" onClick={()=>setCollapsed(true)}>
                <ChevronLeft size={17}/>
              </button>
            </>
          )}
        </div>
        {!collapsed?(
          <>
            <button className="new-chat" onClick={start}>
              <Plus size={17}/> New chat <span> K</span>
            </button>
            <div className="side-label">{showArchived?'ARCHIVED PROJECTS':'WORKSPACE'}</div>
            <button className="tool-button" onClick={()=>setShowArchived(!showArchived)}>
              {showArchived?'Back to projects':'View archived projects'}
            </button>
            <div className="conversation-list">
              {convos.map(c=>(
                <div className={`conversation ${active===c.id?'active':''}`} key={c.id}>
                  <button onClick={()=>openConversation(c.id)}>
                    <span className="conv-dot"/>{c.title}
                  </button>
                  {!c.archived&&(
                    <button className="rename-chat" title="Rename conversation" onClick={()=>openRename(c.id,c.title)}>
                      <Pencil size={13}/>
                    </button>
                  )}
                  <button className="archive-chat" title={c.archived?'Restore conversation':'Archive conversation'} onClick={()=>archive(c.id,!c.archived)}>
                    <Archive size={13}/>
                  </button>
                  <button className="delete-chat" title="Delete conversation" onClick={()=>remove(c.id)}>
                    <Trash2 size={14}/>
                  </button>
                </div>
              ))}
            </div>
            <div className="sidebar-bottom">
              <div className="side-label">YOUR WORKSPACE</div>
              <button className="profile" title="Account settings" onClick={()=>setAccountOpen(!accountOpen)}>
                <div className="avatar">{user.picture?<img src={user.picture} alt=""/>:user.name.slice(0,1).toUpperCase()}</div>
                <div><strong>{user.name}</strong><small>{user.email}</small></div>
                <Settings2 size={16}/>
              </button>
              {accountOpen&&(
                <div className="account-menu">
                  <span>{user.email}</span>
                  <button onClick={()=>setDark(!dark)}>{dark?'Switch to light theme':'Switch to dark theme'}</button>
                  <button onClick={()=>{setAccountOpen(false);setSettingsOpen(true);}}>Settings</button>
                  <button onClick={onLogout}>Sign out</button>
                </div>
              )}
            </div>
          </>
        ):(
          <button className="icon-button expand-side" title="Expand sidebar" onClick={()=>setCollapsed(false)}>
            <ChevronRight size={18}/>
          </button>
        )}
      </aside>

      <main className="main">
        <header className="topbar">
          <div className="mobile-brand">
            <button className="icon-button" onClick={()=>setCollapsed(!collapsed)}><Menu size={19}/></button>
            <div className="brand-mark"><Layers3 size={17}/></div>
            <b>ModelForge</b>
          </div>
          <div className="breadcrumbs">
            <span>Workspace</span><ChevronRight size={14}/><strong>{current?.title||'New creation'}</strong>
          </div>
          <div className="top-actions">
            <span className="local-badge"><span/> Interpreter: {interpreterLabel} · Geometry: {geometryLabel}</span>
            {userSettings&&(
              <span className="local-badge"><span style={{background:userSettings.configured?'#8db99a':'#d3a26b'}}/> {userSettings.provider} {userSettings.configured?'· key stored':'· no key'}</span>
            )}
            <button className="icon-button" title="Toggle theme" onClick={()=>setDark(!dark)}>{dark?<Sun size={17}/>:<Moon size={17}/>}</button>
            <button className="icon-button" title="Help"><CircleHelp size={17}/></button>
          </div>
        </header>

        <div className={`workarea ${selected?'has-preview':''}`}>
          <section className="chat-panel">
            <div className="chat-scroll" key={active||'welcome'}>
              {!current?.messages.length?(
                <div className="welcome">
                  <div className="welcome-icon"><Layers3 size={18}/></div>
                  <p className="eyebrow">A WORKSPACE FOR MAKING</p>
                  <h1>What would you like<br/>to <em>create?</em></h1>
                  <p className="welcome-sub">Describe a form, set your dimensions, and start shaping an idea into something real.</p>
                  <div className="examples">
                    {examples.map((e,i)=>(
                      <button key={e} onClick={()=>generate(e)}>
                        <span className="example-icon">{i===0?'':i===1?'':''}</span>{e}<ChevronRight size={15}/>
                      </button>
                    ))}
                  </div>
                  <div className="engine-note">
                    <span className="pulse"/> Geometry is generated locally <span></span> Gemini writes and runs CadQuery scripts when configured; otherwise local parsing is used
                  </div>
                </div>
              ):(
                <div className="messages">
                  {current.messages.map((m,i)=>(<article className={`message ${m.role} ${m.status||''}`} key={m.optimisticId||i}>
                    <div className={`message-avatar ${m.role}`}>{m.role==='assistant'?<Layers3 size={16}/>:<span>Y</span>}</div>
                    <div className="message-content">
                      <div className="message-name">{m.role==='assistant'?'ModelForge':'You'}</div>
                      <p>{m.text}</p>
                      {m.status==='pending'&&<div className="message-status"><span className="spinner"/> {m.role==='assistant'?'Generating…':'Sending…'}</div>}
                      {m.status==='failed'&&<div className="message-status message-failed">{m.text}</div>}
                      {m.status==='cancelled'&&<div className="message-status message-cancelled">{m.text||'Stopped.'}</div>}
                      {m.model&&(
                        <button className="result-card" onClick={()=>m.model&&selectModel(m.model)}>
                          <div className="result-thumb">{m.model.vertices&&m.model.faces?<Viewer model={m.model as Model} compact/>:<div className="imported-thumb"><Layers3 size={21}/></div>}</div>
                          <div className="result-info">
                            <strong>{String(m.model.validation.object_identity||m.model.spec.shape_type.replace('_',' '))} model</strong>
                            <span>{m.model.validation.dimensions_mm.map(x=>`${x} mm`).join('  ')}</span>
                            <small><span className={m.model.validation.watertight?'good-dot':'warn-dot'}/>{m.model.validation.watertight?'Watertight mesh':'Mesh needs inspection'} <b></b> {m.model.validation.vertices.toLocaleString()} vertices</small>
                          </div>
                          <ChevronRight size={17}/>
                        </button>
                      )}
                    </div>
                  </article>))}
                  {isGenerating&&(
                    <div className={cancellationRequested?'stopping-indicator':'loading-line'}>
                      <span className="spinner"/> {cancellationRequested?'Stopping…':'Generating model…'}
                    </div>
                  )}
                </div>
              )}
            </div>
          </section>

        <div className="composer-wrap">
          <div className="composer">
            {attached&&(
              <div className="attachment">
                <FileImage size={14}/>{attached}<button onClick={()=>setAttached('')}><X size={13}/></button>
                <small>Image input preview only  image-to-3D is not available yet</small>
              </div>
            )}
            {createsBranch(selected,latest)&&(
              <div className="branch-banner">Refining <strong>v{selected!.version}</strong> — an earlier version than the latest. The refined model will be saved as a new version branching from it.</div>
            )}
            <textarea
              value={prompt}
              onChange={e=>setPrompt(e.target.value)}
              onKeyDown={e=>{
                if(e.key==='Enter'&&!e.shiftKey){
                  e.preventDefault();
                  submit();
                }
              }}
              placeholder={latest?'Describe a refinement':'Describe the model you have in mind'}
              rows={1}
            />
            <div className="composer-tools">
              <div className="composer-left">
                <label className="icon-button attach-button" title="Attach reference image">
                  <FileImage size={17}/>
                  <input type="file" accept="image/*" onChange={e=>onFile(e.target.files?.[0])}/>
                </label>
                <button className={`tool-button ${advanced?'selected':''}`} onClick={()=>setAdvanced(!advanced)}>
                  <Settings2 size={15}/> Dimensions <ChevronDown size={13}/>
                </button>
                <select className="interpreter-select" value={interpreter} disabled={loading} title="Prompt interpreter: Gemini or the local deterministic parser. The selected interpreter must succeed — the other is never used as a fallback." onChange={e=>{
                  const value=e.target.value as InterpreterChoice;
                  setInterpreter(value);
                  localStorage.setItem('modelforge.interpreter',value);
                }}>
                  <option value="auto">Auto interpreter</option>
                  <option value="gemini">Gemini</option>
                  <option value="local">Local parser</option>
                </select>
                {advanced&&(
                  <div className="dimensions-popover">
                    {['Width','Depth','Height'].map((x,i)=>(
                      <label key={x}>
                        {x} <span>mm</span>
                        <input type="number" min="1" max="500" value={dimensions[i]} onChange={e=>setDimensions(d=>d.map((v,j)=>i===j?Number(e.target.value):v))}/>
                      </label>
                    ))}
                    <button onClick={()=>generate(`${dimensions[0]} x ${dimensions[1]} x ${dimensions[2]} mm box`)}><label>Clearance mm <input type="number" min="0" step="0.1" value={clearance} onChange={e=>setClearance(Number(e.target.value))}/></label>Apply dimensions</button>
                  </div>
                )}
              </div>
              {isGenerating?(
                <button className="send-button send-button-stop" onClick={stopGeneration} title="Stop generation">
                  <Square size={16}/>
                </button>
              ):(
                <button className="send-button" onClick={submit} disabled={loading||!prompt.trim()} title="Generate">
                  <Send size={16}/>
                </button>
              )}
            </div>
          </div>
          <div className="composer-caption">Enter to send <span></span> Shift + Enter for a new line <span></span> millimetres</div>
          {error&&(
            <div className="error-banner">
              <span>{error}</span>
              <button onClick={()=>setError('')}><X size={14}/></button>
            </div>
          )}
        </div>

        {toasts.length>0&&(
          <div className="toast-container">
            {toasts.map(t=>(<Toast key={t.id} message={t.message} type={t.type} onDismiss={()=>setToasts(xs=>xs.filter(x=>x.id!==t.id))}/>))}
          </div>
        )}

        {selected&&(
          <section className="preview-panel">
            <div className="preview-head">
              <div>
                <div className="preview-kicker">MODEL PREVIEW</div>
                <h2>{String(selected.validation.object_identity||selected.spec.shape_type.replace('_',' '))} <span></span> {selected.model_id.slice(0,6)}</h2>
              </div>
              <button className="icon-button close-preview" onClick={()=>setSelected(null)} title="Close preview"><X size={17}/></button>
            </div>
            <VersionHistory conversationId={active} selectedId={selected.model_id} loadingId={versionLoading} refreshKey={historyTick} onSelect={(item:VersionSummary)=>{versionLoader.load(item.model_id)}}/>
            <div className="viewport">
              <Viewer key={cameraReset} model={selected} wire={wire} grid={grid} axes={axes} ortho={ortho} visibleComponents={visibleComponents}/>
              <div className="viewport-controls">
                <button className="view-pill" onClick={()=>setOrtho(!ortho)}>{ortho?'Orthographic':'Perspective'} <ChevronDown size={13}/></button>
                <button title="Reset camera" onClick={()=>setCameraReset(cameraReset+1)}>Reset</button>
                <div>
                  <button title="Toggle grid" className={grid?'on':''} onClick={()=>setGrid(!grid)}>Grid</button>
                  <button title="Toggle axes" className="axis-toggle" onClick={()=>setAxes(!axes)}>Axes</button>
                  <button title="Toggle wireframe" className={wire?'on':''} onClick={()=>setWire(!wire)}>Wire</button>
                </div>
              </div>
              <div className="view-hint">Drag to orbit <span></span> Scroll to zoom <span></span> Right drag to pan</div>
            </div>
            <div className="specs-bar">
              <div className="dimension-strip">
                {[['W',selected.validation.dimensions_mm[0]],['D',selected.validation.dimensions_mm[1]],['H',selected.validation.dimensions_mm[2]]].map(([l,v])=>(
                  <div key={l}><span>{l}</span><strong>{Number(v).toFixed(1)} <small>mm</small></strong></div>
                ))}
              </div>
              <div className="validation-card">
                <div className={`validation-badge ${selected.validation.watertight?'valid':''}`}>{selected.validation.watertight?'Watertight':'Not watertight'}</div>
                <div>
                  <strong>{selected.validation.watertight?'Mesh validated':'Mesh requires review'}</strong>
                  <span>{selected.validation.watertight?'Winding checked — printable':'Not guaranteed printable'}</span>
                </div>
                <button className="icon-button" onClick={()=>setInspector(!inspector)}><ChevronDown size={16}/></button>
              </div>
            </div>
            {inspector&&(
              <div className="inspector">
                <div className="inspector-title">PARAMETERS <span>{selected.generation_method}</span></div>
                {isScriptModel(selected)?(
                  <div className="script-code">
                    <pre><code>{String((selected.spec as Record<string,unknown>).creation_script||'')}</code></pre>
                    <span>Executed in the restricted CadQuery sandbox; geometry validated locally{Array.isArray((selected.spec as Record<string,unknown>).plan_dimensions_mm)?` against ${(selected.spec as Record<string,unknown>).plan_dimensions_mm as number[]|unknown[]}`:''}.</span>
                  </div>
                ):(
                  <>
                    <div className="inspector-fields">
                      {[['Width',0],['Depth',1],['Height',2]].map(([name,i])=>(
                        <label key={name as string}>
                          {name}
                          <div>
                            <input type="number" value={dimensions[Number(i)]} min="1" max="500" onChange={e=>{const a=[...dimensions];a[Number(i)]=Number(e.target.value);setDimensions(a)}} onBlur={()=>editDim(Number(i),dimensions[Number(i)])}/>
                            <span>mm</span>
                          </div>
                        </label>
                      ))}
                    </div>
                    {([...(selected.spec.shape_type==='enclosure'?['wall_thickness','floor_thickness','corner_radius']:[]),...(selected.spec.shape_type==='rounded_box'?['corner_radius']:[]),...(['spacer','bushing','adapter','mounting_plate','bracket'].includes(selected.spec.shape_type)?['hole_diameter']:[]),...(['bracket','mounting_plate'].includes(selected.spec.shape_type)?['thickness']:[]),...(selected.spec.shape_type!=='organic'?['fillet_radius','chamfer_distance']:[])] as string[]).map(field=>(<label className="feature-field" key={field}>
                      {field.replace(/_/g,' ')}
                      <div>
                        <input type="number" min="0.5" step="0.5" defaultValue={(selected.spec[field] as number|null|undefined)??''} placeholder="Not set" onChange={()=>{}} onBlur={e=>{const n=e.currentTarget.value.trim()===''?null:Number(e.currentTarget.value);if(n===null||Number.isFinite(n))editParam(field,n)}}/>
                        <span>mm</span>
                      </div>
                    </label>))}
                    {['mounting_plate','bracket'].includes(selected.spec.shape_type)&&(
                      <button className="feature-add" onClick={()=>{
                        const w=selected.spec.dimensions_mm.width,d=selected.spec.dimensions_mm.depth,hole=Number(selected.spec.hole_diameter)||5,ox=w/2-hole/2-2,oy=d/2-hole/2-2;
                        if(ox<=hole/2||oy<=hole/2){setError('Part too small for four corner holes at this diameter.');return}
                        editParam('holes',[{position_mm:[-ox,-oy,0],diameter:hole,axis:'z'},{position_mm:[ox,-oy,0],diameter:hole,axis:'z'},{position_mm:[-ox,oy,0],diameter:hole,axis:'z'},{position_mm:[ox,oy,0],diameter:hole,axis:'z'}]);
                      }}>Create four corner mounting holes</button>
                    )}
                    {selected.spec.shape_type==='enclosure'&&(
                      <div className="feature-toggles">
                        <label><input type="checkbox" checked={selected.spec.enclosure_open!==false} onChange={e=>editParam('enclosure_open',e.target.checked)}/> Open top</label>
                        <label><input type="checkbox" checked={!!selected.spec.lid} onChange={e=>editParam('lid',e.target.checked)}/> Separate lid</label>
                      </div>
                    )}
                  </>
                )}
                <div className="mesh-stats">
                  <span>Vertices <b>{selected.validation.vertices.toLocaleString()}</b></span>
                  <span>Faces <b>{selected.validation.faces.toLocaleString()}</b></span>
                </div>
              </div>
            )}
            <div className="export-actions">
              <button onClick={()=>exportModel('stl')}>
                <Download size={15}/> Export STL
              </button>
              <button onClick={()=>exportModel('glb')}>
                <Download size={15}/> Export GLB
              </button>
              <button onClick={()=>exportModel('step')}>
                <Download size={15}/> Export STEP
              </button>
            </div>
            {selected.validation.component_pairs&&selected.validation.component_pairs.length>0&&(
              <div className="component-pairs-panel">
                <div className="component-pairs-header"><strong>Component Clearance / Interference</strong></div>
                {selected.validation.component_pairs.map(pair=>{
                  const st=pair.status;
                  const sc=st==='fail'?'fail-pair':st==='warning'?'warn-pair':st==='not_checked'?'unchecked-pair':'pass-pair';
                  return (
                    <div key={`${pair.component_a_id}-${pair.component_b_id}`} className={`component-pair-item ${sc}`}>
                      <span className="pair-names">{pair.component_a_name} <span>vs</span> {pair.component_b_name}</span>
                      <span className="pair-status">{st}</span>
                      {pair.min_distance_mm!=null&&<span className="pair-distance">{pair.min_distance_mm.toFixed(3)} mm</span>}
                      {pair.requested_clearance_mm!=null&&<span className="pair-clearance">requested {pair.requested_clearance_mm.toFixed(3)} mm</span>}
                      {pair.interference&&<span className="pair-interference">interference</span>}
                      {pair.reason&&<span className="pair-reason">{pair.reason}</span>}
                    </div>
                  );
                })}
              </div>
            )}
            {selected.validation.change_summary&&(
              <div className="change-summary-panel">
                <div className="change-summary-header"><strong>Change Summary</strong></div>
                {selected.validation.change_summary.verified===false&&(
                  <span className="change-summary-notice">{selected.validation.change_summary.note||'Change summary could not be verified.'}</span>
                )}
                {selected.validation.change_summary.components_added?.length&&(
                  <div className="change-summary-row"><span className="change-summary-label">Added:</span> {selected.validation.change_summary.components_added.join(', ')}</div>
                )}
                {selected.validation.change_summary.components_removed?.length&&(
                  <div className="change-summary-row"><span className="change-summary-label">Removed:</span> {selected.validation.change_summary.components_removed.join(', ')}</div>
                )}
                {selected.validation.change_summary.components_modified?.length&&(
                  <div className="change-summary-row"><span className="change-summary-label">Modified:</span> {selected.validation.change_summary.components_modified.join(', ')}</div>
                )}
                {selected.validation.change_summary.dimensions_changed&&(
                  <div className="change-summary-row">
                    <span className="change-summary-label">Dimensions:</span>
                    {(selected.validation.change_summary?.dimensions_changed?.old||[]).map((x,i)=>`${x}→${(selected.validation.change_summary?.dimensions_changed?.new||[])[i]}`).join(', ')} mm
                  </div>
                )}
                {selected.validation.change_summary.validation_changed&&Object.keys(selected.validation.change_summary.validation_changed).length>0&&(
                  <div className="change-summary-row">
                    <span className="change-summary-label">Validation changed:</span>
                    {Object.entries(selected.validation.change_summary?.validation_changed||{}).map(([k,v])=>`${k}: ${String(v.old)}→${String(v.new)}`).join(', ')}
                  </div>
                )}
                {selected.validation.change_summary.warnings?.length&&(
                  <div className="change-summary-warnings">
                    {selected.validation.change_summary.warnings.map((w,i)=>(<div key={i} className="design-warning">{w}</div>))}
                  </div>
                )}
              </div>
            )}
            {selected.validation.warnings?.map((warning,i)=>(<div className="design-warning" key={i}>{warning}</div>))}
            <div className="preview-foot">Units: millimetres <span></span> Geometry generated locally</div>
          </section>
        )}

        <RenameConversationDialog
          open={renameOpen}
          conversation={renameTarget?{id:renameTarget.id,title:renameTarget.title,messages:[],updated:0}:{id:'',title:'',messages:[],updated:0}}
          onClose={()=>setRenameOpen(false)}
          onRename={confirmRename}
        />
        <DeleteConversationDialog
          open={deleteOpen}
          conversation={{id:deleteTarget||'',title:'',messages:[],updated:0}}
          onClose={()=>setDeleteOpen(false)}
          onDelete={confirmDelete}
        />
        <SettingsPanel
          open={settingsOpen}
          onClose={()=>setSettingsOpen(false)}
          onSaved={()=>{
            setUserSettings(s=>s?{...s,configured:true}:null);
          }}
        />
      </div>
      </main>
    </div>
  );
}
