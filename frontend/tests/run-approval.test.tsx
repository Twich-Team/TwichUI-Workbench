import {render,screen,waitFor} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import {QueryClient,QueryClientProvider} from '@tanstack/react-query';
import {afterEach,expect,it,vi} from 'vitest';
import {RunApproval} from '../src/RunApproval';

const budget={offer_id:'23cd68be-0364-4545-ad24-24971d5dbb70',offer_fingerprint:'a'.repeat(64),publication:'draft-pr',activate_once:true};
const available={available:true,message:'Runtime configured; current gates still required.',binding:'b'.repeat(64),scope_approval_id:'e0216d41-14ef-4a97-9567-39d742109a88',scope:{content:{scope:['Add exact regression coverage.'],acceptance:['Reject case faults.'],exclusions:['No production changes.']}},offer:{id:budget.offer_id,fingerprint:budget.offer_fingerprint,title:'Reviewed regression',repository:'Twich-Team/TwichUI',base_branch:'main',base:'c'.repeat(40),paths:['tests/test_normalize.lua'],checks:[{argv:['bash','tests/run.sh'],cwd:'.'}],limits:{turns:20},budget}};
function form(){const client=new QueryClient({defaultOptions:{queries:{retry:false}}});const view=render(<QueryClientProvider client={client}><RunApproval intakeId="request" taskId="task"/></QueryClientProvider>);return {client,view};}
afterEach(()=>vi.unstubAllGlobals());

it('requires express scope/budget/destination consent and replays exact payload after response loss',async()=>{
  const sent:string[]=[];
  vi.stubGlobal('fetch',vi.fn(async(path:string,init?:RequestInit)=>{
    if(path==='/api/session')return {ok:true,json:async()=>({csrf_token:'token'})};
    if(init?.method==='POST'){sent.push(String(init.body));throw new Error('Response lost');}
    return {ok:true,json:async()=>available};
  }));
  const {view}=form();
  const button=await screen.findByRole('button',{name:'Approve scope, budget and one draft-PR run'});
  expect(button).toBeDisabled();expect(sent).toHaveLength(0);
  await userEvent.click(screen.getByRole('checkbox'));
  expect(button).toBeEnabled();expect(sent).toHaveLength(0);
  await userEvent.click(button);await screen.findByRole('alert');
  view.unmount();form();
  await userEvent.click(await screen.findByRole('button',{name:'Retry same task approval'}));
  await screen.findByRole('alert');expect(sent).toHaveLength(2);expect(sent[0]).toEqual(sent[1]);
  expect(JSON.parse(sent[0])).toMatchObject({binding:available.binding,scope_approval_id:available.scope_approval_id,run_budget:budget});
});

it('freezes reviewed evidence and requires refreshed review if the offer changes',async()=>{
  vi.stubGlobal('fetch',vi.fn(async()=>({ok:true,json:async()=>available})));
  const {client}=form();await screen.findByRole('checkbox');await userEvent.click(screen.getByRole('checkbox'));
  client.setQueryData(['task-run-approval','request','task'],{...available,binding:'d'.repeat(64),offer:{...available.offer,fingerprint:'e'.repeat(64)}});
  await screen.findByRole('alert');expect(screen.queryByRole('button',{name:'Approve scope, budget and one draft-PR run'})).not.toBeInTheDocument();
  await userEvent.click(screen.getByRole('button',{name:'Review current offer'}));
  expect(screen.getByRole('checkbox')).not.toBeChecked();
  expect(screen.getByRole('button',{name:'Approve scope, budget and one draft-PR run'})).toBeDisabled();
});

it('explains missing certification without an approval control',async()=>{
  vi.stubGlobal('fetch',vi.fn(async()=>({ok:true,json:async()=>({available:false,offer:null,message:'Missing independent certification.'})})));
  form();await screen.findByText('Missing independent certification.');
  expect(screen.queryByRole('checkbox')).not.toBeInTheDocument();expect(screen.queryByRole('button')).not.toBeInTheDocument();
});

it('requires refreshed consent when runtime availability changes without a scope change',async()=>{
  vi.stubGlobal('fetch',vi.fn(async()=>({ok:true,json:async()=>available})));
  const {client}=form();await screen.findByRole('checkbox');await userEvent.click(screen.getByRole('checkbox'));
  client.setQueryData(['task-run-approval','request','task'],{...available,message:'Execution unavailable.',offer:{...available.offer,budget:{...budget,activate_once:false}}});
  await screen.findByRole('alert');expect(screen.queryByRole('button',{name:'Approve scope, budget and one draft-PR run'})).not.toBeInTheDocument();
  await userEvent.click(screen.getByRole('button',{name:'Review current offer'}));
  expect(screen.getByRole('button',{name:'Approve scope and budget · Execution unavailable'})).toBeDisabled();
  expect(screen.getByRole('checkbox')).not.toBeChecked();
});

it('displays an applied receipt as history while explaining current certification holds',async()=>{
  vi.stubGlobal('fetch',vi.fn(async()=>({ok:true,json:async()=>({available:false,offer:null,message:'Certification changed; execution held.',receipt:{id:'saved',status:'applied',message:'Approval applied.',payload:{run_budget:budget}}})})));
  form();await screen.findByRole('heading',{name:'Task budget applied'});
  await waitFor(()=>expect(screen.getByText('Certification changed; execution held.')).toBeVisible());
  expect(screen.queryByRole('checkbox')).not.toBeInTheDocument();
});
