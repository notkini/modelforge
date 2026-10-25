import {cleanup,fireEvent,render,screen,waitFor,within} from '@testing-library/react';
import {afterEach,describe,expect,it,vi} from 'vitest';
import {Model,VersionSummary} from './types';

vi.mock('./Viewer',()=>({default:()=> <div data-testid="viewer-stub"/>}));
vi.mock('./apiClient',()=>({api:vi.fn()}));
import App from './App';
import {api} from './apiClient';

const mockApi=vi.mocked(api);

const spec={shape_type:'cylinder',dimensions_mm:{width:20,depth:20,height:50},lobes:[],resolution:48};
const validation={watertight:true,winding_consistent:true,vertices:100,faces:200,dimensions_mm:[20,20,50],printability:'ok'};
const baseModel=(modelId:string,version:number):Model=>({
 model_id:modelId,
 version,
 parent_version_id:version===1?null:'model-v'+(version-1),
 spec:{...spec},
 validation:{...validation},
 generation_method:'parametric_cad',
 vertices:[[0,0,0],[1,0,0],[0,1,0]],
 faces:[[0,1,2]],
 message:'here'
});
const detailedMessages=()=>[
 {id:'m-user',role:'user',text:'create a cylinder'},
 {id:'m-assist-1',role:'assistant',text:'here',metadata:{interpretation_provider:'local-deterministic'},model:baseModel('model-v1',1)},
 {id:'m-assist-2',role:'assistant',text:'taller',metadata:{interpretation_provider:'local-deterministic'},model:baseModel('model-v2',2)}
];
const historyItem=(version:number):VersionSummary=>({
 model_id:'model-v'+version,
 version,
 parent_version_id:version===1?null:'model-v'+(version-1),
 parent_version:version===1?null:version-1,
 created_at:'2026-10-25T10:30:00+00:00',
 shape_type:'cylinder',
 generation_method:'parametric_cad',
 provider:'local-deterministic',
 summary:'A 20 mm diameter cylinder.',
 script_available:false,
 artifact_available:true,
 step_available:true,
 watertight:true,
 dimensions_mm:[20,20,50],
 vertices:100,
 faces:200
});
const historyPage=()=>({items:[historyItem(3),historyItem(2),historyItem(1)],total:3});
const user={id:'user-1',email:'tester@example.com',name:'Tester',picture:null};

function bootBackend(){
 mockApi.mockImplementation(async(url:string)=>{
  const target=String(url);
  if(target.startsWith('/api/conversations?'))return {items:[{id:'conv-1',title:'Demo',archived:false,updated_at:'2026-10-25T00:00:00'}]};
  if(target==='/api/conversations/conv-1')return {messages:detailedMessages()};
  if(target==='/api/conversations/conv-1/models')return historyPage();
  if(target==='/api/conversations/conv-1/messages')return {};
  if(target==='/api/models/model-v1/refine')return {...baseModel('model-v3',3),parent_version_id:'model-v1',message:'Branched from v1.'};
  throw new Error('unexpected api call: '+target)
 });
}

const refineCalls=()=>mockApi.mock.calls.filter(call=>String(call[0]).endsWith('/refine'));
const modelsHistoryCalls=()=>mockApi.mock.calls.filter(call=>String(call[0])==='/api/conversations/conv-1/models');

afterEach(()=>{cleanup();vi.clearAllMocks()});

describe('App version history integration',()=>{
 it('refines the selected historical version, refreshes history, and selects the new version',async()=>{
  bootBackend();
  render(<App user={user} onLogout={()=>{}}/>);
  const cards=await screen.findAllByText('cylinder model');
  expect(cards).toHaveLength(2);
  expect(modelsHistoryCalls()).toHaveLength(0);
  expect((refineCalls())).toHaveLength(0);

  fireEvent.click(cards[0]);
  await screen.findByText('MODEL PREVIEW');
  await waitFor(()=>expect(screen.getAllByTestId('version-row')).toHaveLength(3));
  const rowsBefore=screen.getAllByTestId('version-row');
  const selectedRow=rowsBefore.find(row=>within(row).queryByText('Viewing'))!;
  expect(selectedRow.querySelector('.version-chip')?.textContent).toBe('v1');
  expect(screen.getByText(/an earlier version than the latest/)).toBeTruthy();

  fireEvent.change(screen.getByRole('textbox'),{target:{value:'make it taller'}});
  const modelsHistoryCallsBefore=modelsHistoryCalls().length;
  fireEvent.click(screen.getByTitle('Generate'));
  await screen.findByText('Branched from v1.');

  expect(refineCalls()).toHaveLength(1);
  expect(refineCalls()[0][0]).toBe('/api/models/model-v1/refine');
  expect(modelsHistoryCalls().length).toBeGreaterThan(modelsHistoryCallsBefore);
  const versions=screen.getAllByTestId('version-row').map(row=>row.querySelector('.version-chip')?.textContent);
  expect(versions).toEqual(['v3','v2','v1']);
  const currentRow=screen.getAllByTestId('version-row').find(row=>within(row).queryByText('Viewing'))!;
  expect(currentRow.querySelector('.version-chip')?.textContent).toBe('v3');
  expect(within(currentRow).queryByText(/an earlier version/)).toBeNull();
  expect(screen.queryByText(/an earlier version than the latest/)).toBeNull()
 })
});
