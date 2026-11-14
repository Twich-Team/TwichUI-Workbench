import {useState} from 'react';
import {useQuery,useQueryClient} from '@tanstack/react-query';
import {mutate,request} from './api';
import type {components} from './api.generated';
import {Button} from './components/ui/button';

type Control=components['schemas']['Control'];
export function RoutineBugs(){
  const client=useQueryClient();
  const query=useQuery({queryKey:['routine-bug-policy'],queryFn:()=>request<Control>('/api/routine-bug-policy'),refetchInterval:15000});
  const [busy,setBusy]=useState(false),[error,setError]=useState('');
  async function change(){
    if(!query.data)return;
    setBusy(true);setError('');
    try{
      const saved=await mutate<Control>('/api/routine-bug-policy',{revision:query.data.revision,enabled:!query.data.enabled},'PUT');
      client.setQueryData(['routine-bug-policy'],saved);
      await client.invalidateQueries({queryKey:['execution-policy']});
      await client.invalidateQueries({queryKey:['models']});
    }catch(e){setError(e instanceof Error?e.message:'The setting could not be confirmed.');void query.refetch();}
    finally{setBusy(false);}
  }
  return <section className="panel" aria-label="Routine bug repairs"><h2>Routine bug repairs</h2>
    <p>Automatically investigate and fix routine bugs</p>
    <p className="detail-copy">New covered reports use the saved AI choices to investigate, work in an isolated copy and check the result. Draft PRs require the separate publication permission. Existing reports keep their original permissions.</p>
    {query.data?<><p role="status">{query.data.enabled?'Automatic routine repairs are on.':'Automatic routine repairs are off.'}</p>
      <Button disabled={busy||query.isError} onClick={()=>void change()}>{busy?'Saving…':query.data.enabled?'Turn off automatic repairs':'Turn on automatic repairs'}</Button>
      <details><summary>Permission boundaries</summary><p>{query.data.message}</p><p>Only supported addon source, tests and tools can change. Other repositories, credentials, billing, merges, releases and game installation require your decision. Turning this back on covers new reports; revoked reports stay held.</p></details>
    </>:<p role="status">{query.isError?'Routine bug permission could not be loaded.':'Loading routine bug permission…'}</p>}
    {error&&<p role="alert">{error}</p>}
  </section>;
}
