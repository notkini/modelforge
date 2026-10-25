import {useEffect,useState} from 'react';
import {api} from './apiClient';
import {VersionSummary} from './types';
import {historyItemLabel} from './versions';

function formatTime(iso:string){
 const date=new Date(iso);
 if(Number.isNaN(date.getTime()))return iso;
 return date.toLocaleString(undefined,{month:'short',day:'numeric',hour:'2-digit',minute:'2-digit'})
}
type HistoryState={items:VersionSummary[];loading:boolean;error:string};
export default function VersionHistory({conversationId,selectedId,loadingId,refreshKey,onSelect}:{conversationId:string;selectedId:string|undefined;loadingId:string|undefined;refreshKey:number;onSelect:(item:VersionSummary)=>void}){
 const [state,setState]=useState<HistoryState>({items:[],loading:true,error:''});
 const [retryTick,setRetryTick]=useState(0);
 useEffect(()=>{
  let cancelled=false;
  setState({items:[],loading:true,error:''});
  (async()=>{
   try{
    const page=await api<{items:VersionSummary[];total:number}>(`/api/conversations/${conversationId}/models`);
    if(cancelled)return;
    setState({items:page.items||[],loading:false,error:''})
   }catch(e){
    if(cancelled)return;
    setState({items:[],loading:false,error:(e as Error).message||'History failed to load.'})
   }
  })();
  return()=>{cancelled=true}
 },[conversationId,refreshKey,retryTick]);
 const newestVersion=state.items[0]?.version??null;
 return <section className="history-section" data-testid="version-history">
  <div className="history-head"><span className="preview-kicker">VERSION HISTORY</span><small>{state.items.length} saved</small></div>
  {state.loading&&<div className="history-status" data-testid="history-status"><span className="spinner"/>Loading versions…</div>}
  {!state.loading&&state.error&&<div className="history-status" data-testid="history-status">History failed to load. <button className="history-retry" data-testid="history-retry" onClick={()=>setRetryTick(t=>t+1)}>Retry</button></div>}
  {!state.loading&&!state.error&&state.items.length===0&&<div className="history-status" data-testid="history-status">No saved versions yet. Generate or refine a model to start this conversation's history.</div>}
  <div className="history-rows">
   {state.items.map(item=><button key={item.model_id} data-testid="version-row" className={`history-row ${item.model_id===selectedId?'selected':''}`} onClick={()=>onSelect(item)}>
    <span className="version-chip">v{item.version}</span>
    <span className="history-main">
     <b>{historyItemLabel(item)}</b>
     <small>{formatTime(item.created_at)}{item.parent_version?` · branched from v${item.parent_version}`:''}</small>
     {item.summary&&<small className="history-summary" title={item.summary}>{item.summary}</small>}
     {!item.artifact_available&&<small className="history-missing">Artifact missing — metadata only</small>}
    </span>
    <span className="history-flags">
     {loadingId===item.model_id&&<span className="pill loading">Loading…</span>}
     {item.model_id===selectedId&&<span className="pill viewing">Viewing</span>}
     {item.model_id!==selectedId&&item.version===newestVersion&&<span className="pill latest">Latest</span>}
    </span>
   </button>)}
  </div>
 </section>
}
