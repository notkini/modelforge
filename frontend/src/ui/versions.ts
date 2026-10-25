import {Model,VersionSummary} from './types';

export function providerLabel(provider:string|null):string{
 if(!provider)return '';
 return provider==='gemini'?'Gemini':provider==='local-deterministic'?'Local parser':provider.replace(/_/g,' ')
}
export function historyItemLabel(item:VersionSummary):string{
 const provider=providerLabel(item.provider)?' · '+providerLabel(item.provider):'';
 return item.shape_type.replace(/_/g,' ')+provider
}
// Refinement always branches from the version the user has open in the preview;
// when nothing is selected the newest version keeps the previous behaviour.
export function pickRefineParent(selected:Model|null,latest:Model|null):Model|null{
 return selected||latest
}
export function createsBranch(selected:Model|null,latest:Model|null):boolean{
 return !!selected&&!!latest&&selected.model_id!==latest.model_id&&selected.version!==undefined
}
export type VersionLoadHandlers={
 onStart:(modelId:string)=>void;
 onLoaded:(model:Model)=>void;
 onFailed:(message:string)=>void;
 onSettled:()=>void
};
// Guards against stale model loads: once a newer request starts, an older
// in-flight response is discarded instead of replacing the newer selection.
export function createVersionLoader(fetchModel:(modelId:string)=>Promise<Model>,handlers:VersionLoadHandlers){
 let seq=0;
 return{load:async(modelId:string)=>{
  const request=++seq;handlers.onStart(modelId);
  try{
   const model=await fetchModel(modelId);
   if(request!==seq)return;
   handlers.onLoaded(model)
  }catch(e){
   if(request!==seq)return;
   handlers.onFailed((e as Error).message||'Request failed.')
  }finally{
   if(request===seq)handlers.onSettled()
  }}}
}
