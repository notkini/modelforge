import {describe,expect,it,vi} from 'vitest';
import {createVersionLoader,createsBranch,pickRefineParent} from './versions';
import {Model} from './types';

const model=(modelId:string,version:number):Model=>({
 model_id:modelId,
 version,
 spec:{shape_type:'cylinder',dimensions_mm:{width:20,depth:20,height:50},lobes:[],resolution:48},
 validation:{watertight:true,winding_consistent:true,vertices:200,faces:400,dimensions_mm:[20,20,50],printability:'ok'},
 generation_method:'parametric_cad'
});

describe('pickRefineParent',()=>{
 it('uses the selected historical version as the refinement parent',()=>{
  const selected=model('m1',1),latest=model('m2',2);
  expect(pickRefineParent(selected,latest)).toBe(selected)
 });
 it('falls back to the newest version when nothing is selected',()=>{
  const latest=model('m2',2);
  expect(pickRefineParent(null,latest)).toBe(latest);
  expect(pickRefineParent(null,null)).toBeNull()
 })
});

describe('createsBranch',()=>{
 it('flags refining an older, different version',()=>{
  expect(createsBranch(model('m1',1),model('m2',2))).toBe(true)
 });
 it('does not flag refining the newest version itself',()=>{
  expect(createsBranch(model('m2',2),model('m2',2))).toBe(false)
 });
 it('does not flag without a selection or when versions are unknown',()=>{
  expect(createsBranch(null,model('m2',2))).toBe(false);
  const unversioned={...model('m1',1),version:undefined} as unknown as Model;
  expect(createsBranch(unversioned,model('m2',2))).toBe(false)
 })
});

describe('createVersionLoader',()=>{
 it('reports start, the loaded model, and settles on success',async()=>{
  const handlers={onStart:vi.fn(),onLoaded:vi.fn(),onFailed:vi.fn(),onSettled:vi.fn()};
  const loader=createVersionLoader(()=>Promise.resolve(model('m1',1)),handlers);
  await loader.load('m1');
  expect(handlers.onStart).toHaveBeenCalledWith('m1');
  expect(handlers.onLoaded).toHaveBeenCalledTimes(1);
  expect(handlers.onLoaded).toHaveBeenCalledWith(expect.objectContaining({model_id:'m1'}));
  expect(handlers.onFailed).not.toHaveBeenCalled();
  expect(handlers.onSettled).toHaveBeenCalledTimes(1)
 });
 it('discards a stale in-flight response that resolves after a newer selection',async()=>{
  const resolvers:Array<(value:Model)=>void>=[];
  const handlers={onStart:vi.fn(),onLoaded:vi.fn(),onFailed:vi.fn(),onSettled:vi.fn()};
  const loader=createVersionLoader(()=>new Promise<Model>(resolve=>{resolvers.push(resolve)}),handlers);
  const first=loader.load('m1');
  const second=loader.load('m2');
  resolvers[1](model('m2',2));
  await second;
  resolvers[0](model('m1',1));
  await first;
  expect(handlers.onLoaded).toHaveBeenCalledTimes(1);
  expect(handlers.onLoaded).toHaveBeenCalledWith(expect.objectContaining({model_id:'m2'}))
 });
 it('ignores a stale failure and reports fresh failures only',async()=>{
  const settlements:{resolve?:(m:Model)=>void;reject?:(e:Error)=>void}[]=[];
  const handlers={onStart:vi.fn(),onLoaded:vi.fn(),onFailed:vi.fn(),onSettled:vi.fn()};
  const loader=createVersionLoader(()=>new Promise<Model>((resolve,reject)=>{settlements.push({resolve,reject})}),handlers);
  const first=loader.load('m1');
  const second=loader.load('m2');
  settlements[1].resolve!(model('m2',2));
  await second;
  settlements[0].reject!(new Error('Request failed (410).'));
  await first;
  expect(handlers.onFailed).not.toHaveBeenCalled();
  settlements.splice(0,1);
  const third=loader.load('m3');
  settlements[1].reject!(new Error('Request failed (404).'));
  await third;
  expect(handlers.onFailed).toHaveBeenCalledTimes(1);
  expect(handlers.onFailed).toHaveBeenCalledWith('Request failed (404).');
  expect(handlers.onSettled).toHaveBeenCalledTimes(2)
 })
});
