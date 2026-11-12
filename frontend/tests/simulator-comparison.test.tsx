import {render,screen} from '@testing-library/react';
import {QueryClient,QueryClientProvider} from '@tanstack/react-query';
import {afterEach,expect,it,vi} from 'vitest';
import {SimulatorComparison} from '../src/SimulatorComparison';

afterEach(()=>vi.unstubAllGlobals());

it.each(['captured','unsupported','unavailable','not_recorded'])('presents %s simulator evidence without a game or completion claim',async status=>{
 const image={source_revision:'1'.repeat(40)};
 const evidence={conditions:{simulator:'Controlled simulator',ui_version:'Controlled UI',viewport:[1440,1000],ui_scale:1,scenario:'Notification appearance',state:'Synthetic solo',settings:'Clean defaults',capture_seconds:2},captured_at:'2026-11-12T00:00:00Z',before:image,after:image,limitations:['Structural evidence only; fonts and game behavior remain unverified.']};
 const calls=vi.fn(async(_path:string,init?:RequestInit)=>{expect(init?.method||'GET').toBe('GET');return {ok:true,json:async()=>({status,message:status==='not_recorded'?'No simulator comparison is recorded.':'Controlled simulator outcome',evidence:['captured','unsupported'].includes(status)?evidence:null})};});
 vi.stubGlobal('fetch',calls);
 const client=new QueryClient({defaultOptions:{queries:{retry:false}}});
 const rendered=render(<QueryClientProvider client={client}><SimulatorComparison identity="controlled-result"/></QueryClientProvider>);
 if(status==='not_recorded'){
  await vi.waitFor(()=>expect(calls).toHaveBeenCalled());
  expect(rendered.container).toBeEmptyDOMElement();
 }else{
  await screen.findByRole('region',{name:'Simulator comparison'});
  if(status==='captured'){
   expect(screen.getByRole('img',{name:'Original addon in the simulator'})).toHaveAttribute('src','/api/real-executions/controlled-result/simulator/before');
   expect(screen.getByRole('img',{name:'Combined feature in the simulator'})).toHaveAttribute('src','/api/real-executions/controlled-result/simulator/after');
  }else expect(screen.queryByRole('img')).not.toBeInTheDocument();
  expect(screen.queryByText(/feature completed/i)).not.toBeInTheDocument();
 }
 client.clear();
});
