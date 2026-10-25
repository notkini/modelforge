import {cleanup,fireEvent,render,screen,waitFor,within} from '@testing-library/react';
import {afterEach,describe,expect,it,vi} from 'vitest';
import {VersionSummary} from './types';

vi.mock('./apiClient',()=>({api:vi.fn()}));
import {api} from './apiClient';
import VersionHistory from './VersionHistory';

const mockApi=vi.mocked(api);

const item=(version:number,options:{modelId?:string;artifact?:boolean}={}):VersionSummary=>({
 model_id:options.modelId||`model-v${version}`,
 version,
 parent_version_id:version>1?`model-v${version-1}`:null,
 parent_version:version>1?version-1:null,
 created_at:'2026-10-25T10:30:00+00:00',
 shape_type:'cylinder',
 generation_method:'parametric_cad',
 provider:'gemini',
 summary:'A 20 mm diameter cylinder.',
 script_available:false,
 artifact_available:options.artifact===undefined?true:options.artifact,
 step_available:true,
 watertight:true,
 dimensions_mm:[20,20,50],
 vertices:200,
 faces:400
});

const listProps=(overrides:Partial<{conversationId:string;selectedId:string|undefined;loadingId:string|undefined;refreshKey:number;onSelect:(item:VersionSummary)=>void}>={})=>({
 conversationId:'conv-1',
 selectedId:undefined as string|undefined,
 loadingId:undefined as string|undefined,
 refreshKey:0,
 onSelect:vi.fn(),
 ...overrides
});

const chipTexts=()=>{
 return screen.getAllByTestId('version-row').map(row=>row.querySelector('.version-chip')?.textContent)
};

afterEach(()=>{cleanup();vi.clearAllMocks()});

describe('VersionHistory',()=>{
 it('renders versions newest first as returned by the backend',async()=>{
  mockApi.mockResolvedValue({items:[item(3),item(2),item(1)],total:3});
  render(<VersionHistory {...listProps()}/>);
  await waitFor(()=>expect(chipTexts()).toEqual(['v3','v2','v1']));
  expect(mockApi).toHaveBeenCalledWith('/api/conversations/conv-1/models')
 });

 it('distinguishes the selected version from the newest version',async()=>{
  mockApi.mockResolvedValue({items:[item(3),item(2),item(1)],total:3});
  render(<VersionHistory {...listProps({selectedId:'model-v1'})}/>);
  await waitFor(()=>expect(chipTexts()).toEqual(['v3','v2','v1']));
  const rows=screen.getAllByTestId('version-row');
  const selectedRow=rows.find(row=>within(row).queryByText('Viewing'))!;
  expect(selectedRow.querySelector('.version-chip')?.textContent).toBe('v1');
  const latestRow=rows.find(row=>within(row).queryByText('Latest'))!;
  expect(latestRow.querySelector('.version-chip')?.textContent).toBe('v3');
  expect(within(selectedRow).queryByText('Latest')).toBeNull()
 });

 it('omits the Latest badge when the selected version is the newest',async()=>{
  mockApi.mockResolvedValue({items:[item(3),item(2),item(1)],total:3});
  render(<VersionHistory {...listProps({selectedId:'model-v3'})}/>);
  await waitFor(()=>expect(chipTexts()).toEqual(['v3','v2','v1']));
  expect(screen.queryByText('Latest')).toBeNull();
  const rows=screen.getAllByTestId('version-row');
  expect(within(rows[0]).queryByText('Viewing')).not.toBeNull()
 });

 it('shows the loading state before the history call resolves',()=>{
  mockApi.mockImplementation(()=>new Promise(()=>{}));
  render(<VersionHistory {...listProps()}/>);
  expect(screen.getByText('Loading versions…')).toBeTruthy();
  expect(screen.queryAllByTestId('version-row')).toHaveLength(0)
 });

 it('shows a helpful empty state when the conversation has no versions',async()=>{
  mockApi.mockResolvedValue({items:[],total:0});
  render(<VersionHistory {...listProps()}/>);
  expect(await screen.findByText(/No saved versions yet/)).toBeTruthy()
 });

 it('surfaces load errors, offers retry, and recovers on retry',async()=>{
  mockApi.mockRejectedValueOnce(new Error('backend unreachable'));
  render(<VersionHistory {...listProps()}/>);
  expect(await screen.findByText(/History failed to load/)).toBeTruthy();
  expect(screen.queryByTestId('version-row')).toBeNull();
  mockApi.mockResolvedValueOnce({items:[item(2),item(1)],total:2});
  fireEvent.click(screen.getByTestId('history-retry'));
  await waitFor(()=>expect(chipTexts()).toEqual(['v2','v1']));
  expect(mockApi).toHaveBeenCalledTimes(2)
 });

 it('displays provider, summary, parent version, and artifact-missing status',async()=>{
  mockApi.mockResolvedValue({items:[item(3),item(2,{artifact:false}),item(1)],total:3});
  render(<VersionHistory {...listProps()}/>);
  await waitFor(()=>expect(chipTexts()).toEqual(['v3','v2','v1']));
  const rows=screen.getAllByTestId('version-row');
  expect(within(rows[0]).getByText(/Gemini/)).toBeTruthy();
  expect(within(rows[0]).getByText(/branched from v2/)).toBeTruthy();
  expect(within(rows[0]).getByText(/A 20 mm diameter cylinder/)).toBeTruthy();
  expect(within(rows[1]).getByText(/Artifact missing — metadata only/)).toBeTruthy()
 });

 it('shows a loading pill on the version being fetched',async()=>{
  mockApi.mockResolvedValue({items:[item(3),item(2),item(1)],total:3});
  render(<VersionHistory {...listProps({loadingId:'model-v2'})}/>);
  await waitFor(()=>expect(chipTexts()).toEqual(['v3','v2','v1']));
  const rows=screen.getAllByTestId('version-row');
  expect(within(rows[1]).getByText('Loading…')).toBeTruthy()
 });

 it('invokes onSelect with the clicked version',async()=>{
  mockApi.mockResolvedValue({items:[item(3),item(2),item(1)],total:3});
  const onSelect=vi.fn();
  render(<VersionHistory {...listProps({onSelect})}/>);
  await waitFor(()=>expect(chipTexts()).toEqual(['v3','v2','v1']));
  fireEvent.click(screen.getAllByTestId('version-row')[2]);
  expect(onSelect).toHaveBeenCalledWith(expect.objectContaining({model_id:'model-v1'}))
 });

 it('never renders history from a previous conversation after switching',async()=>{
  let releaseConvA!:(value:{items:VersionSummary[];total:number})=>void;
  mockApi.mockImplementation((url:string)=>{
   const conversationId=String(url).split('/')[3];
   if(conversationId==='conv-a')return new Promise(resolve=>{releaseConvA=resolve});
   return Promise.resolve({items:[item(9,{modelId:'model-conv-b'})],total:1})
  });
  const {rerender}=render(<VersionHistory {...listProps({conversationId:'conv-a',selectedId:undefined,refreshKey:0})}/>);
  rerender(<VersionHistory {...listProps({conversationId:'conv-b',selectedId:undefined,refreshKey:0})}/>);
  await waitFor(()=>expect(chipTexts()).toEqual(['v9']));
  releaseConvA({items:[item(1,{modelId:'model-conv-a'})],total:1});
  await waitFor(()=>{expect(mockApi.mock.calls.length).toBeGreaterThanOrEqual(2)});
  expect(chipTexts()).toEqual(['v9']);
  expect(screen.queryByText('v1')).toBeNull()
 })
});
