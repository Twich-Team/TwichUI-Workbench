import {render,screen,within} from '@testing-library/react';
import {QueryClient,QueryClientProvider} from '@tanstack/react-query';
import {MemoryRouter,Route,Routes} from 'react-router-dom';
import {afterEach,expect,it,vi} from 'vitest';
import {RealExecutionReview} from '../src/RealExecutionReview';

const native={id:'result',title:'Historical repair',status:'checked',message:'Original checks passed.',base:'base',current_grant:false,
  grant_id:'grant',expires_at:'2026-11-09T00:00:00Z',publication_allowed:false,source:'real',behavior_reproduced:true,
  result:{verification:'passed',candidate_id:'candidate',checks:[],files:[]}};
afterEach(()=>vi.unstubAllGlobals());

it.each([null,'rejected','unavailable'] as const)('shows %s later review without promoting it or losing native evidence',async state=>{
  const calls=vi.fn(async(path:string,init?:RequestInit)=>{
    expect(!init?.method||init.method==='GET').toBe(true);
    return {ok:true,json:async()=>path.endsWith('/delivery')?null:{...native,later_review:state?{state,reason:'Later independent evidence requires attention.'}:null}};
  });
  vi.stubGlobal('fetch',calls);
  const client=new QueryClient({defaultOptions:{queries:{retry:false}}});
  render(<QueryClientProvider client={client}><MemoryRouter initialEntries={['/executions/result']}><Routes><Route path="/executions/:identity" element={<RealExecutionReview/>}/></Routes></MemoryRouter></QueryClientProvider>);
  await screen.findByRole('heading',{name:state==='rejected'?'Later review rejected this patch':state==='unavailable'?'Later review needs attention':'Result checked — automated checks passed'});
  expect(screen.getByText('Recorded verification passed')).toBeInTheDocument();
  if(state){
    expect(screen.queryByRole('heading',{name:'Result checked — automated checks passed'})).not.toBeInTheDocument();
    expect(screen.queryByText('The reviewed check reproduced the bug in the original code and passed with this patch.')).not.toBeInTheDocument();
    expect(screen.getByText(/original check results and usage are retained/)).toBeVisible();
  }else{
    expect(screen.getByText('The reviewed check reproduced the bug in the original code and passed with this patch.')).toBeVisible();
  }
  client.clear();
});

it('puts a confirmed draft link before technical evidence and removes the local-only message',async()=>{
  const url='https://github.com/Twich-Team/TwichUI/pull/123';
  vi.stubGlobal('fetch',vi.fn(async(path:string)=>({ok:true,json:async()=>path.endsWith('/delivery')?{
    status:'published',message:'Draft PR created and confirmed.',published:true,source:'github',url,
    repository:'Twich-Team/TwichUI',base_branch:'main',current_eligible:true,current_message:'',
    preview:{title:'Historical repair',body:'Reviewable summary',category:'Bug fix'},
  }:native})));
  const client=new QueryClient({defaultOptions:{queries:{retry:false}}});
  render(<QueryClientProvider client={client}><MemoryRouter initialEntries={['/executions/result']}><Routes><Route path="/executions/:identity" element={<RealExecutionReview/>}/></Routes></MemoryRouter></QueryClientProvider>);
  const link=await screen.findByRole('link',{name:'Review the draft PR on GitHub'});
  expect(link).toHaveAttribute('href',url);
  expect(screen.getByRole('heading',{name:'Result checked — automated checks passed'})).toBeVisible();
  expect(screen.getByText(/did not install the change or test it in World of Warcraft/)).toBeVisible();
  expect(screen.queryByText(/has not been submitted/)).not.toBeInTheDocument();
  expect(link.compareDocumentPosition(screen.getByText('Technical results and run details'))&Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  client.clear();
});

it('submits an exact checked draft through the saved review binding',async()=>{
  const writes:unknown[]=[];
  let submitted=false;
  vi.stubGlobal('fetch',vi.fn(async(path:string,init?:RequestInit)=>{
    if(path==='/api/session')return {ok:true,json:async()=>({csrf_token:'controlled'})};
    if(init?.method==='POST'){
      writes.push(JSON.parse(String(init.body)));submitted=true;
      return {ok:true,json:async()=>({status:'pending'})};
    }
    return {ok:true,json:async()=>path.endsWith('/draft-review')?{
      available:true,binding:'a'.repeat(64),preview:{title:'Historical repair',body:'Checked changes only.'},
    }:path.endsWith('/delivery')?(submitted?{
      status:'pending',message:'Waiting for authorized delivery.',published:false,source:null,url:null,
      repository:'Twich-Team/TwichUI',base_branch:'main',current_eligible:true,current_message:'',
      preview:{title:'Historical repair',body:'Checked changes only.',category:'Bug fix'},
    }:null):native};
  }));
  const client=new QueryClient({defaultOptions:{queries:{retry:false}}});
  render(<QueryClientProvider client={client}><MemoryRouter initialEntries={['/executions/result']}><Routes><Route path="/executions/:identity" element={<RealExecutionReview/>}/></Routes></MemoryRouter></QueryClientProvider>);
  const {default:userEvent}=await import('@testing-library/user-event');
  await userEvent.click(await screen.findByRole('button',{name:'Submit draft pull request'}));
  await screen.findByRole('heading',{name:'Preparing draft PR delivery'});
  expect(writes).toEqual([{binding:'a'.repeat(64)}]);
  client.clear();
});


it.each([
  {files:['tests/test_map.lua'],status:'checked',verification:'passed',heading:'Test coverage added — automated checks passed'},
  {files:['addon/map.lua','tests/test_map.lua'],status:'checked',verification:'passed',heading:'Fix prepared — automated checks passed'},
  {files:['addon/map.lua'],status:'failed',verification:'failed',heading:'Work could not finish'},
])('keeps outcome language bound to checked changes: $heading',async({files,status,verification,heading})=>{
  vi.stubGlobal('fetch',vi.fn(async(path:string)=>({ok:true,json:async()=>path.endsWith('/delivery')?null:{
    ...native,status,result:{...native.result,verification,files:files.map(path=>({path,kind:'modified',diff:'@@ -1 +1 @@\n-old\n+new',truncated:false}))},
  }})));
  const client=new QueryClient({defaultOptions:{queries:{retry:false}}});
  render(<QueryClientProvider client={client}><MemoryRouter initialEntries={['/executions/result']}><Routes><Route path="/executions/:identity" element={<RealExecutionReview/>}/></Routes></MemoryRouter></QueryClientProvider>);
  await screen.findByRole('heading',{name:heading});
  expect(screen.queryByRole('button',{name:/Test in game/})).not.toBeInTheDocument();
  expect(screen.getByText('In-game testing').nextElementSibling).toHaveTextContent('Not verified');
  const changes=screen.getByText(/See the changes in code/).closest('details')!;
  expect(changes.open).toBe(false);
  expect(within(changes).getAllByRole('heading',{hidden:true})).toHaveLength(files.length);
  if(status==='failed')expect(screen.queryByText('READY TO REVIEW')).not.toBeInTheDocument();
  client.clear();
});
