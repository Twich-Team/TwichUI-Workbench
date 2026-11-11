import {useState} from 'react';
import {useQuery,useQueryClient} from '@tanstack/react-query';
import {mutate,request} from './api';
import type {components} from './api.generated';
import {Button} from './components/ui/button';

type Control=components['schemas']['WorkControl'];
export function WorkControls(){
  const client=useQueryClient();
  const query=useQuery({queryKey:['work-controls'],queryFn:()=>request<Control>('/api/work-controls'),refetchInterval:5000});
  const [busy,setBusy]=useState(false),[error,setError]=useState('');
  async function change(){
    if(!query.data)return;
    const current=query.data;
    setBusy(true);setError('');
    try{
      const saved=await mutate<Control>('/api/work-controls',{
        revision:current.revision,paused:!current.paused,
      },'PUT');
      client.setQueryData(['work-controls'],saved);
      await client.invalidateQueries({queryKey:['workspace']});
    }catch(e){
      setError(e instanceof Error?e.message:'The work setting could not be confirmed.');
      void query.refetch();
    }finally{setBusy(false);}
  }
  return <section className="panel" aria-label="Work controls">
    <h2>Work controls</h2>
    {query.data?<><p role="status">{query.data.message}</p>
      <Button disabled={busy||query.isError} onClick={()=>void change()}>
        {busy?'Saving…':query.data.paused?'Resume work':'Pause new work'}
      </Button>
      <p className="small-text">Pending requests and approvals stay saved. You can stop an active run from its task page when that control is available.</p>
    </>:<p role="status">{query.isError?'Work status could not be loaded.':'Loading work status…'}</p>}
    {query.isError&&<p role="alert">Refresh unavailable. Reload the current work status before changing it. <Button variant="secondary" onClick={()=>void query.refetch()}>Reload work status</Button></p>}
    {error&&<p role="alert">{error}</p>}
  </section>;
}
