import {useQuery} from '@tanstack/react-query';
import {request} from './api';
import {Button} from './components/ui/button';

type Delivery={status:string;message:string;published:boolean;source:string|null;url:string|null;
  repository:string;base_branch:string;current_eligible:boolean;current_message:string;
  preview:{title:string;body:string;category:string}};
export function PublicationStatus({identity,checked}:{identity:string;checked:boolean}){
  const query=useQuery({queryKey:['task-publication',identity],queryFn:async()=>{const value=await request<Delivery|null>(`/api/real-executions/${encodeURIComponent(identity)}/delivery`);if(value&&(!value.preview||typeof value.preview.body!=='string'))throw new Error('Invalid delivery evidence');return value;},refetchInterval:3000});
  if(!query.data){
    if(query.isError)return <p role="alert">Draft pull request status could not be loaded.</p>;
    if(query.isPending)return checked?<p>Checking draft pull request status…</p>:null;
    return checked?<p>Saved in Workbench. A draft pull request has not been submitted.</p>:null;
  }
  const data=query.data;
  return <section className="task-publication" aria-label="Draft PR delivery">
    <h3>{data.published?'Draft pull request submitted':data.source==='controlled'?'Controlled delivery result':data.status==='held'?'PR delivery needs attention':'Preparing draft PR delivery'}</h3>
    <p role="status">{data.message}</p>
    {query.isError&&<p role="alert">Refresh unavailable. Showing the last loaded delivery record.</p>}
    {data.published&&data.url&&/^https:\/\/github\.com\/Twich-Team\/TwichUI\/pull\/[1-9][0-9]{0,9}$/.test(data.url)&&
      <Button asChild><a href={data.url} target="_blank" rel="noreferrer">Review the draft PR on GitHub</a></Button>}
    {!data.current_eligible&&<p className="soft-callout">Historical delivery evidence. {data.current_message}</p>}
    {data.source==='controlled'&&<p>No real PR was created.</p>}
    <details><summary>PR summary and destination</summary><p>{data.repository} · {data.base_branch} · {data.preview.category}</p><h4>{data.preview.title}</h4><pre className="candidate-diff" tabIndex={0}>{data.preview.body}</pre></details>
  </section>;
}
