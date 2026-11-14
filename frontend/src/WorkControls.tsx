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


export function TaskControls({identity}:{identity:string}){
  const client=useQueryClient();
  const query=useQuery({queryKey:['task-work-control',identity],queryFn:()=>request<Control>(`/api/intake/${identity}/work-control`),refetchInterval:5000});
  const [busy,setBusy]=useState(false),[message,setMessage]=useState(''),[error,setError]=useState('');
  async function change(next:boolean){
    if(!query.data)return;
    setBusy(true);setError('');
    try{
      if(next){
        const saved=await mutate<{message:string}>(`/api/intake/${identity}/work-next`,{});
        setMessage(saved.message);
      }else{
        const saved=await mutate<Control>(`/api/intake/${identity}/work-control`,{revision:query.data.revision,paused:!query.data.paused},'PUT');
        client.setQueryData(['task-work-control',identity],saved);setMessage(saved.message);
      }
      await client.invalidateQueries({queryKey:['task-work-control',identity]});
      await client.invalidateQueries({queryKey:['workspace']});
      await client.invalidateQueries({queryKey:['task-journey',identity]});
    }catch(e){setError(e instanceof Error?e.message:'The task setting could not be saved.');void query.refetch();}
    finally{setBusy(false);}
  }
  return <section className="panel" aria-label="Task controls"><h2>Guide this task</h2>
    <div className="task-control-buttons"><Button variant="secondary" disabled={busy||!query.data||query.isError} onClick={()=>void change(true)}>Work on this next</Button>
      <Button variant="secondary" disabled={busy||!query.data||query.isError} onClick={()=>void change(false)}>{query.data?.paused?'Resume this task':'Pause this task'}</Button></div>
    <p className="small-text">Priority applies when work is ready to start. Pausing keeps saved work and lets an active operation finish safely.</p>
    {(message||query.data?.paused)&&<p role="status">{message||query.data?.message}</p>}{error&&<p role="alert">{error}</p>}
  </section>;
}
