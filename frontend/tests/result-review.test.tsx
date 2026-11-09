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
