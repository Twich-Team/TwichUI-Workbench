import {render,screen} from '@testing-library/react';
import {QueryClient,QueryClientProvider} from '@tanstack/react-query';
import {afterEach,expect,it,vi} from 'vitest';
import type {components} from '../src/api.generated';
import {TaskJourney} from '../src/TaskOverview';

afterEach(()=>vi.unstubAllGlobals());
it('a settled failed run and fresh offer lead to a new decision without implying execution',async()=>{
  const item={id:'request',status:'tasks_ready',original:{local_planning:'qwen-once'},
    tasks:[{id:'task',kind:'task',scope_review_required:false,proposal_id:'proposal'}],
    proposals:[{id:'proposal',content:{delivery:'work'}}],decisions:[]} as unknown as components['schemas']['IntakeView'];
  const execution={stage:'failed',current_approval:{id:'old-run'},run_budget:{current:true},runs:[]};
  const approval={available:true,receipt:null,offer:{repair_cycle:{continuation:{execution_ids:['old-run']}}}};
  const fetch=vi.fn(async(path:string)=>({ok:true,json:async()=>path.endsWith('/execution')?execution:approval}));
  vi.stubGlobal('fetch',fetch);
  const client=new QueryClient({defaultOptions:{queries:{retry:false}}});
  const view=render(<QueryClientProvider client={client}><TaskJourney item={item}/></QueryClientProvider>);
  await screen.findByText('Ready for a new run decision');
  expect(screen.getByText(/The previous run ended/)).toBeVisible();
  expect(screen.queryByText('Working in a separate copy')).not.toBeInTheDocument();
  expect(fetch.mock.calls.every(([path])=>path.endsWith('/execution')||path.endsWith('/run-approval'))).toBe(true);
  view.unmount();client.clear();
});
