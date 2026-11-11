import {render,screen} from '@testing-library/react';
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
  await screen.findByRole('heading',{name:state==='rejected'?'Later review rejected this patch':state==='unavailable'?'Later review needs attention':'Changes checked'});
  expect(screen.getByText('Recorded verification passed')).toBeInTheDocument();
  if(state){
    expect(screen.queryByRole('heading',{name:'Changes checked'})).not.toBeInTheDocument();
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
  expect(screen.getByText(/prepared a fix and the automated checks passed/)).toBeVisible();
  expect(screen.getByText(/did not install the change or test it in World of Warcraft/)).toBeVisible();
  expect(screen.queryByText(/has not been submitted/)).not.toBeInTheDocument();
  expect(link.compareDocumentPosition(screen.getByText('Technical results and run details'))&Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  client.clear();
});
