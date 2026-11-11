import {render,screen} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import {QueryClient,QueryClientProvider} from '@tanstack/react-query';
import {afterEach,expect,it,vi} from 'vitest';
import {WorkControls} from '../src/WorkControls';

afterEach(()=>vi.unstubAllGlobals());

it('saves the explicit desired pause state and shows the saved resume action',async()=>{
  let state={revision:0,paused:false,message:'New work can start when its permissions and resources are ready.'};
  const writes:unknown[]=[];
  vi.stubGlobal('fetch',vi.fn(async(path:string,init?:RequestInit)=>{
    if(path==='/api/session')return {ok:true,json:async()=>({csrf_token:'controlled'})};
    if(init?.method==='PUT'){
      const body=JSON.parse(String(init.body));
      writes.push(body);
      state={revision:state.revision+1,paused:body.paused,message:body.paused?
        'New work is paused. Work already running can finish safely.':'New work can start when its permissions and resources are ready.'};
    }
    return {ok:true,json:async()=>state};
  }));
  const client=new QueryClient({defaultOptions:{queries:{retry:false}}});
  render(<QueryClientProvider client={client}><WorkControls/></QueryClientProvider>);
  await userEvent.click(await screen.findByRole('button',{name:'Pause new work'}));
  await screen.findByRole('button',{name:'Resume work'});
  expect(screen.getByRole('status')).toHaveTextContent('Work already running can finish safely.');
  expect(writes).toEqual([{revision:0,paused:true}]);
  client.clear();
});
