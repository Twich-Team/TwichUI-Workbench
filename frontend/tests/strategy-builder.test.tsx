import {render,screen,within} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import {QueryClient,QueryClientProvider} from '@tanstack/react-query';
import {afterEach,expect,it,vi} from 'vitest';
import {StrategyBuilder} from '../src/StrategyBuilder';
import {ManualAI} from '../src/ManualAI';
const provider=(model:string,effort:string|null)=>({enabled:true,model,effort,activated:false});
const policy={revision:8,preferred:'codex',substitution:'deny',fallback:['local'],fallback_on:['unavailable'],when_unavailable:'wait',pause_new_work:true,
 providers:{local:provider('pilot-qwen38-27b',null),codex:provider('gpt-6.1-sol','high'),claude:provider('claude-sonnet-5-5','high')},
 strategy:{name:'My game plan',ceiling:'request_start',unknown_usage:'wait',unknown_request_limit:0,recheck_seconds:300,stages:[
  {id:'first',provider:'codex',model:'gpt-6.1-sol',effort:'high',allocation:50,role:'primary',triggers:['allocation'],on_limit:'switch',wait_seconds:1800,local_suitability:'simple_only'},
  {id:'local',provider:'local',model:'pilot-qwen38-27b',effort:null,allocation:null,role:'primary',triggers:['allocation'],on_limit:'switch',wait_seconds:1800,local_suitability:'simple_only'},
  {id:'reserve',provider:'codex',model:'gpt-6.1-sol',effort:'high',allocation:25,role:'fallback',triggers:['allocation','unsuitable'],on_limit:'wait',wait_seconds:1800,local_suitability:'simple_only'}]}};
const capabilities={executors:Object.entries(policy.providers).map(([backend,p])=>({backend,models:[{model:p.model,efforts:p.effort?['low','high']:[]}],observed_at:new Date().toISOString(),source:'Controlled catalog',limitation:'No provider call'}))};
afterEach(()=>{vi.unstubAllGlobals();sessionStorage.clear();});
function fixture(){const writes:unknown[]=[];vi.stubGlobal('fetch',vi.fn(async(path:string,init?:RequestInit)=>{
 if(path==='/api/session')return {ok:true,json:async()=>({csrf_token:'controlled'})};
 if(init?.method==='PUT'){const body=JSON.parse(String(init.body));writes.push(body);return {ok:true,json:async()=>({...body,revision:9})};}
 return {ok:true,json:async()=>({policy,capabilities})};
}));const client=new QueryClient({defaultOptions:{queries:{retry:false}}});return {client,writes};}
it('reorders real strategy cards and preserves separate provider budgets and existing global controls',async()=>{
 const {client,writes}=fixture();render(<QueryClientProvider client={client}><StrategyBuilder/></QueryClientProvider>);const user=userEvent.setup();
 expect(await screen.findByText('Codex · 75%')).toBeVisible();
 expect(screen.getByText('50% primary + 25% fallback reserve')).toBeVisible();
 expect(screen.getByText('25% outside Workbench’s allocation')).toBeVisible();
 await user.click(screen.getByRole('button',{name:'Move card 2 later'}));
 await user.click(screen.getByRole('button',{name:'Save AI strategy'}));
 await screen.findByText(/Strategy saved for new task decisions/);
 expect(writes).toHaveLength(1);
 expect(writes[0]).toMatchObject({revision:8,pause_new_work:true,providers:policy.providers,strategy:{stages:[{id:'first',allocation:50},{id:'reserve',allocation:25},{id:'local',allocation:null}]}});
 client.clear();
});
it('prevents impossible combined allocations for one provider without treating Claude as part of Codex budget',async()=>{
 const {client,writes}=fixture();render(<QueryClientProvider client={client}><StrategyBuilder/></QueryClientProvider>);const user=userEvent.setup();
 await screen.findByText('Codex · 75%');
 const cards=screen.getByRole('list',{name:''});
 const numbers=within(cards).getAllByLabelText('Allocation percentage');
 await user.clear(numbers[1]);await user.type(numbers[1],'60');
 expect(screen.getByText('Codex allocations total 110%. Choose at most 100%.')).toBeVisible();
 expect(screen.getByRole('button',{name:'Save AI strategy'})).toBeDisabled();
 expect(writes).toEqual([]);client.clear();
});
it('manual Start now records actual model and effort with an explicit allocation override',async()=>{
 const {client}=fixture();const start=vi.fn(async()=>{});render(<QueryClientProvider client={client}><ManualAI binding="frozen" blocked={false} start={start}/></QueryClientProvider>);const user=userEvent.setup();
 await user.selectOptions(await screen.findByLabelText('AI provider'),'claude');
 await user.selectOptions(screen.getByLabelText('Start now reasoning effort'),'low');
 await user.click(screen.getByRole('checkbox'));
 await user.click(screen.getByRole('button',{name:'Start now with Claude'}));
 expect(start).toHaveBeenCalledWith(expect.objectContaining({binding:'frozen',choice:{provider:'claude',model:'claude-sonnet-5-5',effort:'low'},exceed_workbench_allocation:true}));client.clear();
});
