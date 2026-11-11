import type {ReactNode} from 'react';
import type {components} from '../src/api.generated';
import {render,screen,fireEvent} from '@testing-library/react';
import {QueryClient,QueryClientProvider} from '@tanstack/react-query';
import {MemoryRouter} from 'react-router-dom';
import {afterEach,expect,it,vi} from 'vitest';
import {Dashboard,WorkspaceBacklog,WorkspaceResults} from '../src/Workspace';

const task:components['schemas']['WorkspaceTask']={id:'request',title:'Remember the selected route',kind:'bug',state:'decision_pending',stage:'Decision recorded',message:'Your choice is saved and waiting to apply.',active:false,needs_you:false,priority_manual:false,created_at:'2026-11-09T00:00:00Z',updated_at:'2026-11-09T00:00:00Z',result_id:null,result_kind:null,provider:null};
const base:components['schemas']['WorkspacePage']={items:[task],total:1,all_total:1,page:1,page_size:12,counts:{all:1,active:0,queued:0,needs_you:0,completed:0},observed_at:'2026-11-09T00:00:00Z',mode:'everyday',highlights:{active:[],needs_you:[],completed:[]}};
const clients:QueryClient[]=[];
afterEach(()=>{clients.forEach(c=>c.clear());vi.unstubAllGlobals();});
function open(component:ReactNode,data=base){
 const calls=vi.fn(async(_url:string)=>({ok:true,json:async()=>data}));vi.stubGlobal('fetch',calls);
 const client=new QueryClient({defaultOptions:{queries:{retry:false}}});clients.push(client);
 render(<QueryClientProvider client={client}><MemoryRouter>{component}</MemoryRouter></QueryClientProvider>);return calls;
}
it('idle dashboard has no false activity or unanswered decision and uses GET only',async()=>{
 const calls=open(<Dashboard/>);await screen.findByText('No work running');
 expect(screen.getByText('No questions or approvals are waiting for you.')).toBeVisible();
 expect(screen.queryByText('Decision recorded')).not.toBeInTheDocument();
 expect(screen.getByRole('link',{name:/Create your next task/})).toHaveAttribute('href','/intake/new');
 expect(calls.mock.calls[0][0]).toContain('/api/workspace');
});
it('recorded decisions remain distinct in backlog and filtering uses the actual API',async()=>{
 const calls=open(<WorkspaceBacklog/>);await screen.findByRole('link',{name:/Decision recorded/});
 expect(screen.getByText(task.message)).toBeVisible();
 fireEvent.change(screen.getByLabelText('Type of work'),{target:{value:'bug'}});
 fireEvent.change(screen.getByRole('searchbox',{name:'Find a task'}),{target:{value:'route'}});
 await vi.waitFor(()=>expect(calls.mock.calls.some(([url])=>url.includes('search=route')&&url.includes('kind=bug'))).toBe(true));
});
it('results explain local patch boundaries and link a real result identifier',async()=>{
 const data:typeof base={...base,items:[{...task,state:'completed',stage:'Ready to review',result_id:'saved-result',result_kind:'patch'}]};
 open(<WorkspaceResults/>,data);await screen.findByText('Local patch · Not installed in game');
 expect(screen.getByRole('link',{name:/Remember the selected route/})).toHaveAttribute('href','/executions/saved-result');
 expect(screen.getByText(/see whether Workbench submitted a draft pull request/)).toBeVisible();
});
it('a failed background refresh retains loaded records and explicitly marks the display stale',async()=>{
 const calls=open(<Dashboard/>);await screen.findByText('No work running');
 calls.mockImplementation(async()=>{throw Error('Controlled connection loss');});
 await clients.at(-1)!.invalidateQueries({queryKey:['workspace']});
 await screen.findByText('Refresh unavailable. These are the last loaded records. Decisions need a fresh view.');
 expect(screen.getByText('Status unavailable')).toBeVisible();
 expect(screen.getByRole('heading',{name:'Last known work'})).toBeVisible();
 expect(screen.getByText('Ready when you are')).toBeVisible();
});

it('queued model work stays visible without being called idle or active addon work',async()=>{
 const queued={...task,state:'queued' as const,stage:'Waiting for local Qwen',message:'A verified maintenance cycle is yielding the model.'};
 const data:typeof base={...base,items:[queued],counts:{all:1,active:0,queued:1,needs_you:0},
  local_model:{state:'yielding',message:queued.message,automatic:true,intake_ids:[queued.id],maintenance_active:true,reservation_owner_alive:true,reservation_locked:false,observed_at:null},
  highlights:{...base.highlights,waiting:[queued]}};
 open(<Dashboard/>,data);
 await screen.findByText('Work waiting');
 expect(screen.queryByText('Ready when you are')).not.toBeInTheDocument();
 expect(screen.queryByText('No work running')).not.toBeInTheDocument();
 expect(screen.getByRole('region',{name:'Local model status'})).toHaveTextContent('Recovery is automatic');
 expect(screen.getByRole('link',{name:/Remember the selected route/})).toBeVisible();
 expect(document.querySelector('.task-state .lucide-activity')).toBeNull();
});

it('maintenance is described separately while addon work counts remain zero',async()=>{
 const data:typeof base={...base,local_model:{state:'maintenance',message:'A verified maintenance cycle holds the model.',automatic:true,intake_ids:[],maintenance_active:true,reservation_owner_alive:false,reservation_locked:false,observed_at:null}};
 open(<Dashboard/>,data);await screen.findByText('Model used by maintenance');
 expect(screen.getByRole('region',{name:'Local model status'})).toHaveTextContent('verified maintenance');
 expect(screen.getByRole('link',{name:/0 Working/})).toBeVisible();
});

it('an unfinished outcome never appears among recently completed tasks',async()=>{
 const stopped={...task,state:'failed' as const,stage:'Could not finish',message:'The independent checks failed.'};
 open(<Dashboard/>,{...base,items:[stopped],highlights:{...base.highlights,outcomes:[stopped]}});
 await screen.findByText('No work running');
 expect(screen.getByText('Checked results will appear here when work finishes.')).toBeVisible();
 expect(screen.getByRole('heading',{name:'Recently completed'}).closest('section')).not.toHaveTextContent(stopped.title);
 expect(screen.getByRole('link',{name:new RegExp(stopped.title)})).toBeVisible();
 expect(screen.getByRole('heading',{name:'Work to revisit'})).toBeVisible();
});
