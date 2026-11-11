import {render,screen} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import {MemoryRouter} from 'react-router-dom';
import {QueryClient,QueryClientProvider} from '@tanstack/react-query';
import {afterEach,expect,it,vi} from 'vitest';
import {SimpleModels} from '../src/SimpleModels';
import {savedTaskRoute} from '../src/modelPreferences';

const policy={revision:4,preferred:'local' as const,substitution:'deny' as const,test_fallback:'off' as const,
 fallback:['codex' as const],fallback_on:['unavailable' as const],when_unavailable:'wait' as const,pause_new_work:true,
 providers:{local:{enabled:true,model:'pilot-qwen38-27b',effort:null,activated:false as const},codex:{enabled:true,model:'included-codex',effort:'high',activated:false as const},claude:{enabled:false,model:'',effort:null,activated:false as const}}};
afterEach(()=>{vi.unstubAllGlobals();sessionStorage.clear();});
it('saves primary and allowed fallback through the existing policy without unpausing work',async()=>{
 const writes:typeof policy[]=[];
 vi.stubGlobal('fetch',vi.fn(async(path:string,init?:RequestInit)=>{
  if(path==='/api/session')return {ok:true,json:async()=>({csrf_token:'controlled'})};
  if(init?.method==='PUT'){
   const body=JSON.parse(String(init.body));writes.push(body);
   return {ok:true,json:async()=>({...body,revision:5})};
  }
  return {ok:true,json:async()=>path==='/api/local-work'?{ready:true,codex_available:true}:{policy}};
 }));
 const client=new QueryClient({defaultOptions:{queries:{retry:false}}});
 render(<QueryClientProvider client={client}><MemoryRouter><SimpleModels/></MemoryRouter></QueryClientProvider>);
 const user=userEvent.setup();
 await user.selectOptions(await screen.findByLabelText('Preferred provider'),'codex');
 await user.selectOptions(screen.getByLabelText('Fallback for new task decisions'),'codex-to-qwen');
 await user.click(screen.getByRole('button',{name:'Save task model preferences'}));
 await screen.findByText('Model preferences saved for new task decisions. Existing work keeps its approved provider.');
 expect(writes).toHaveLength(1);
 expect(writes[0]).toMatchObject({revision:4,preferred:'codex',substitution:'configured_fallback_only',test_fallback:'codex-to-qwen',pause_new_work:true,providers:policy.providers});
 expect(savedTaskRoute(writes[0])).toBe('codex-with-local-fallback');
 expect(screen.getByText(/No live subscription balance is shown/)).toBeVisible();
 client.clear();
});
it('never offers a saved switch when its direction, condition or secondary model is ineligible',()=>{
 expect(savedTaskRoute(policy)).toBe('qwen-once');
 const enabled={...policy,substitution:'configured_fallback_only' as const,test_fallback:'both' as const};
 expect(savedTaskRoute(enabled)).toBe('qwen-with-codex-fallback');
 expect(savedTaskRoute({...enabled,fallback_on:['capacity']})).toBe('qwen-once');
 expect(savedTaskRoute({...enabled,test_fallback:'codex-to-qwen'})).toBe('qwen-once');
 expect(savedTaskRoute({...enabled,providers:{...policy.providers,codex:{...policy.providers.codex,enabled:false}}})).toBe('qwen-once');
 expect(savedTaskRoute({...enabled,preferred:'claude'})).toBeNull();
});
it('keeps unsaved preferences on a revision conflict and requires a fresh policy before another save',async()=>{
 vi.stubGlobal('fetch',vi.fn(async(path:string,init?:RequestInit)=>{
  if(path==='/api/session')return {ok:true,json:async()=>({csrf_token:'controlled'})};
  if(init?.method==='PUT')return {ok:false,status:409,json:async()=>({detail:'Model preferences changed. Reload before saving.'})};
  return {ok:true,json:async()=>path==='/api/local-work'?{ready:true,codex_available:true}:{policy}};
 }));
 const client=new QueryClient({defaultOptions:{queries:{retry:false}}});
 render(<QueryClientProvider client={client}><MemoryRouter><SimpleModels/></MemoryRouter></QueryClientProvider>);
 const user=userEvent.setup();
 await user.selectOptions(await screen.findByLabelText('Preferred provider'),'codex');
 await user.click(screen.getByRole('button',{name:'Save task model preferences'}));
 await screen.findByText('Model preferences changed. Reload before saving.');
 expect(screen.getByLabelText('Preferred provider')).toHaveValue('codex');
 expect(screen.getByRole('button',{name:'Save task model preferences'})).toBeDisabled();
 expect(JSON.parse(sessionStorage.getItem('model-policy-draft')!).preferred).toBe('codex');
 await user.click(screen.getByRole('button',{name:'Discard model edits and reload'}));
 await screen.findByText('Saved model preferences reloaded.');
 expect(screen.getByLabelText('Preferred provider')).toHaveValue('local');
 client.clear();
});
it('holds a restored draft until the complete saved policy loads',async()=>{
 sessionStorage.setItem('model-policy-draft',JSON.stringify({...policy,preferred:'codex',fallback:['local']}));
 let finish!:(value:unknown)=>void;
 const pending=new Promise(resolve=>{finish=resolve;});
 const writes:unknown[]=[];
 vi.stubGlobal('fetch',vi.fn(async(path:string,init?:RequestInit)=>{
  if(init?.method)writes.push(init.method);
  return {ok:true,json:async()=>path==='/api/local-work'?{ready:true}:pending};
 }));
 const client=new QueryClient({defaultOptions:{queries:{retry:false}}});
 render(<QueryClientProvider client={client}><MemoryRouter><SimpleModels/></MemoryRouter></QueryClientProvider>);
 expect(await screen.findByRole('button',{name:'Save task model preferences'})).toBeDisabled();
 expect(screen.getByLabelText('Preferred provider')).toHaveValue('codex');
 finish({policy,capabilities:{executors:[]},observations:[]});
 await vi.waitFor(()=>expect(screen.getByRole('button',{name:'Save task model preferences'})).toBeEnabled());
 expect(writes).toEqual([]);
 client.clear();
});
