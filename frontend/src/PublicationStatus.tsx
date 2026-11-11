import {useState} from 'react';
import {useQuery,useQueryClient} from '@tanstack/react-query';
import {mutate,request} from './api';
import {Button} from './components/ui/button';

type Delivery={status:string;message:string;published:boolean;source:string|null;url:string|null;
  repository:string;base_branch:string;current_eligible:boolean;current_message:string;
  preview:{title:string;body:string;category:string}};
export function PublicationStatus({identity,checked}:{identity:string;checked:boolean}){
  const query=useQuery({queryKey:['task-publication',identity],queryFn:async()=>{const value=await request<Delivery|null>(`/api/real-executions/${encodeURIComponent(identity)}/delivery`);if(value&&(!value.preview||typeof value.preview.body!=='string'))throw new Error('Invalid delivery evidence');return value;},refetchInterval:3000});
  if(!query.data){
    if(query.isError)return <p role="alert">Draft pull request status could not be loaded.</p>;
    if(query.isPending)return checked?<p>Checking draft pull request status…</p>:null;
    return checked?<section className="task-publication" aria-label="Draft PR delivery"><h3>Saved in Workbench</h3><p>A draft pull request has not been submitted.</p><p>Next: review the checked result and submit an eligible draft when ready.</p><DraftAction identity={identity}/></section>:null;
  }
  const data=query.data;
  return <section className="task-publication" aria-label="Draft PR delivery">
    <h3 aria-live="polite">{data.published?'Draft pull request submitted':data.source==='controlled'?'Controlled delivery result':data.status==='held'?'PR delivery needs attention':'Preparing draft PR delivery'}</h3>
    <p className="draft-title">{data.preview.title}</p>
    {!data.published&&<p role="status">{data.message}</p>}
    {query.isError&&<p role="alert">Refresh unavailable. Showing the last loaded delivery record.</p>}
    {data.published&&data.url&&/^https:\/\/github\.com\/Twich-Team\/TwichUI\/pull\/[1-9][0-9]{0,9}$/.test(data.url)&&
      <Button asChild><a href={data.url} target="_blank" rel="noreferrer">Review the draft PR on GitHub</a></Button>}
    {data.published&&<p>Next: review the draft on GitHub. You decide whether to merge.</p>}
    {!data.published&&data.source!=='controlled'&&<p>{data.status==='held'?'Next: open the task and review the delivery issue. The checked changes remain saved.':'Workbench is preparing delivery. You can leave this page; the request remains saved.'}</p>}
    {!data.current_eligible&&<p className="soft-callout">Historical delivery evidence. {data.current_message}</p>}
    {data.source==='controlled'&&<p>No real PR was created.</p>}
    <details><summary>PR summary and destination</summary>{data.published&&<p>{data.message} This saved record confirms draft submission only.</p>}<p>{data.repository} · {data.base_branch} · {data.preview.category}</p><h4>{data.preview.title}</h4><pre className="candidate-diff" tabIndex={0}>{data.preview.body}</pre></details>
  </section>;
}


function DraftAction({identity}:{identity:string}){
  const client=useQueryClient();
  const [busy,setBusy]=useState(false),[error,setError]=useState('');
  const query=useQuery({queryKey:['draft-review',identity],queryFn:()=>request<{
    available:boolean;binding?:string;preview?:{title:string;body:string};
  }>('/api/real-executions/'+encodeURIComponent(identity)+'/draft-review')});
  async function submit(){
    if(!query.data?.binding)return;
    setBusy(true);setError('');
    try{
      await mutate('/api/real-executions/'+encodeURIComponent(identity)+'/draft-delivery',{
        binding:query.data.binding,
      });
      await client.invalidateQueries({queryKey:['task-publication',identity]});
      await client.invalidateQueries({queryKey:['workspace']});
    }catch(e){setError(e instanceof Error?e.message:'The draft request could not be confirmed.');}
    finally{setBusy(false);}
  }
  if(!query.data?.available)return null;
  return <section aria-label="Submit checked draft">
    <Button disabled={busy||query.isError} onClick={()=>void submit()}>{busy?'Saving draft request…':'Submit draft pull request'}</Button>
    {error&&<p role="alert">{error}</p>}
    <details><summary>Review the proposed draft summary</summary><h4>{query.data.preview?.title}</h4><pre className="candidate-diff">{query.data.preview?.body}</pre></details>
  </section>;
}
