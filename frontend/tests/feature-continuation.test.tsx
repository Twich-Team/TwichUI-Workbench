import {QueryClient,QueryClientProvider} from '@tanstack/react-query';
import {render,screen} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import {afterEach,expect,it,vi} from 'vitest';
import type {components} from '../src/api.generated';
import {FeatureRun} from '../src/FeatureRun';

type Feature=components['schemas']['FeatureView'];
const feature={id:'same-feature',intake:{archived_at:null},scope_correction:null,
 run:{id:'original-run',status:'held',phase:'held',message:'Original slot held.',can_stop:true,cancel_requested:false,expires_at:'2026-01-01T00:00:00Z'},
 run_recovery:{binding:'a'.repeat(64),hours:6,task_title:'The original step',message:'The coding request never started.'}} as unknown as Feature;
function form(value=feature){const client=new QueryClient({defaultOptions:{queries:{retry:false}}});const saved=vi.fn();return {saved,...render(<QueryClientProvider client={client}><FeatureRun feature={value} onSaved={saved}/></QueryClientProvider>)};}
afterEach(()=>{vi.unstubAllGlobals();localStorage.clear();});

it('requires bounded continuation consent and keeps the same command after a lost response',async()=>{
 const sent:string[]=[];
 vi.stubGlobal('fetch',vi.fn(async(path:string,init?:RequestInit)=>{
  if(path==='/api/session')return {ok:true,json:async()=>({csrf_token:'controlled-token'})};
  if(init?.method==='POST'){sent.push(String(init.body));throw new Error('Response lost');}
  return {ok:true,json:async()=>({})};
 }));
 const first=form();
 expect(screen.getByRole('button',{name:'Continue saved feature'})).toBeDisabled();
 expect(sent).toHaveLength(0);
 await userEvent.click(screen.getByLabelText('Continue the same feature within these limits.'));
 await userEvent.dblClick(screen.getByRole('button',{name:'Continue saved feature'}));
 await screen.findByRole('alert');expect(sent.length).toBeGreaterThan(0);expect(sent.every(value=>value===sent[0])).toBe(true);expect(first.saved).not.toHaveBeenCalled();const prior=sent.length;
 first.unmount();form();
 expect(screen.getByRole('button',{name:'Retry saved continuation'})).toBeDisabled();
 await userEvent.click(screen.getByLabelText('Continue the same feature within these limits.'));
 await userEvent.click(screen.getByRole('button',{name:'Retry saved continuation'}));
 await screen.findByRole('alert');expect(sent).toHaveLength(prior+1);expect(sent.every(value=>value===sent[0])).toBe(true);
 expect(JSON.parse(sent[0])).toMatchObject({binding:feature.run_recovery!.binding});
});

it('a changed native offer requires fresh review and consent',async()=>{
 localStorage.setItem('feature-unstarted-continuation:'+feature.id,JSON.stringify({command_id:crypto.randomUUID(),binding:'b'.repeat(64)}));
 vi.stubGlobal('fetch',vi.fn());form();
 expect(screen.getByRole('button',{name:'Retry saved continuation'})).toBeDisabled();
 expect(screen.getByLabelText('Continue the same feature within these limits.')).toBeDisabled();
 await userEvent.click(screen.getByRole('button',{name:'Review current continuation'}));
 expect(screen.getByLabelText('Continue the same feature within these limits.')).not.toBeChecked();
 expect(screen.getByRole('button',{name:'Continue saved feature'})).toBeDisabled();
 expect(fetch).not.toHaveBeenCalled();
});

it('shows a separate coding reservation without promising a new time window',async()=>{
 const sent:string[]=[];
 vi.stubGlobal('fetch',vi.fn(async(path:string,init?:RequestInit)=>{
  if(path==='/api/session')return {ok:true,json:async()=>({csrf_token:'controlled-token'})};
  if(init?.method==='POST')sent.push(String(init.body));
  return {ok:true,json:async()=>({})};
 }));
 const value={...feature,run_recovery:{...feature.run_recovery!,kind:'coding_attempt',hours:0,
  message:'Earlier coding attempts ended without a checked result.',
  limits_description:'One additional isolated coding attempt. Existing deadline stays in place. Earlier grants and unknown usage remain counted.'}};
 const rendered=form(value);
 await userEvent.click(screen.getByText('Continuation limits'));
 expect(screen.getByText(value.run_recovery.limits_description)).toBeVisible();
 expect(screen.queryByText(/A separate 0-hour/)).not.toBeInTheDocument();
 expect(screen.getByRole('button',{name:'Continue saved feature'})).toBeDisabled();
 expect(sent).toHaveLength(0);
 await userEvent.click(screen.getByLabelText('Continue the same feature within these limits.'));
 await userEvent.click(screen.getByRole('button',{name:'Continue saved feature'}));
 expect(sent).toHaveLength(1);
 expect(JSON.parse(sent[0])).toMatchObject({binding:value.run_recovery.binding});
 expect(rendered.saved).toHaveBeenCalledOnce();
});
