import {useEffect,useState} from 'react';
import {useQuery,useQueryClient} from '@tanstack/react-query';
import {Archive,MoreHorizontal,RotateCcw,Trash2} from 'lucide-react';
import type {components} from './api.generated';
import {ApiError,mutate,request} from './api';
import {Button} from './components/ui/button';
import {Dialog,DialogContent,DialogDescription,DialogTitle} from './components/ui/dialog';

type Context=components['schemas']['CleanupContext'];
type Command=components['schemas']['CleanupCommand'];
type Result=components['schemas']['CleanupResult'];
export type CleanupChoice={ids:string[];action:Command['action'];expected?:Record<string,number>};
const pendingKey='workbench-pending-cleanup';
function savedCommand():Command|null{try{return JSON.parse(sessionStorage.getItem(pendingKey)||'null') as Command|null;}catch{return null;}}
function forgetPending(){try{sessionStorage.removeItem(pendingKey);}catch{/* A saved receipt can be recovered again if storage is unavailable. */}}
export function TaskActions({id,title,archived,choose}:{id:string;title:string;archived?:boolean;choose:(choice:CleanupChoice)=>void}){
 return <details className="task-actions-menu" onKeyDown={e=>{if(e.key==='Escape'){e.currentTarget.removeAttribute('open');e.currentTarget.querySelector('summary')?.focus();}}}><summary aria-label={`Actions for ${title}`} title="Task actions"><MoreHorizontal size={21}/></summary><div className="task-actions-popover">
 <Button variant="ghost" onClick={e=>{e.currentTarget.closest('details')?.removeAttribute('open');choose({ids:[id],action:archived?'restore':'archive'});}}>{archived?<RotateCcw size={17}/>:<Archive size={17}/>} {archived?'Restore':'Archive'}</Button>
 <Button variant="ghost" onClick={e=>{e.currentTarget.closest('details')?.removeAttribute('open');choose({ids:[id],action:'delete'});}}><Trash2 size={17}/> Delete…</Button>
 </div></details>;
}

/** One controller outside the rows survives refetches, pagination and removed rows. */
export function CleanupReview({choice,setChoice,onDone}:{choice:CleanupChoice|null;setChoice:(value:CleanupChoice|null)=>void;onDone?:()=>void}){
 const client=useQueryClient();const [pending,setPending]=useState<Command|null>(savedCommand);
 const [busy,setBusy]=useState(false),[error,setError]=useState(''),[result,setResult]=useState<Result|null>(null),[confirmed,setConfirmed]=useState(false);
 const ids=choice?.ids||[];
 const review=useQuery({queryKey:['cleanup-review',ids],queryFn:()=>Promise.all(ids.map(id=>request<Context>(`/api/intake/${id}/cleanup`))),enabled:!!choice&&!pending,retry:false,staleTime:0});
 useEffect(()=>{setError('');setConfirmed(false);},[choice]);
 const action=pending?.action||choice?.action;
 const contexts=review.data||[];
 const allowed=contexts.length===ids.length&&contexts.length>0&&contexts.every(c=>c[`can_${action!}`])&&!review.isError;
 async function send(command:Command){
  setBusy(true);setError('');setPending(command);try{sessionStorage.setItem(pendingKey,JSON.stringify(command));}catch{/* In-memory replay remains available. */}
  try{
   const saved=await mutate<Result>('/api/task-cleanup',command);setResult(saved);setPending(null);forgetPending();setChoice(null);onDone?.();
   await Promise.all([client.invalidateQueries({queryKey:['workspace']}),client.invalidateQueries({queryKey:['intake']}),client.invalidateQueries({queryKey:['intake-detail']}),client.invalidateQueries({queryKey:['cleanup-review']})]);
  }catch(e){setError((e as Error).message);if(e instanceof ApiError&&e.status>0&&e.status<500){setPending(null);setConfirmed(false);forgetPending();void review.refetch();}}
  finally{setBusy(false);}
 }
 function apply(){
  if(pending){void send(pending);return;}
  if(!choice||!allowed)return;
  if(choice.expected&&contexts.some(c=>c.revision!==choice.expected![c.intake_id])){setError('This task changed after it was archived. Open its current actions to restore it.');return;}
  void send({command_id:crypto.randomUUID(),action:choice.action,targets:contexts.map(c=>({intake_id:c.intake_id,binding:c.binding})),confirm_delete:choice.action==='delete'&&confirmed});
 }
 return <>
 {result&&<div className="cleanup-notice" role="status"><span>{result.intake_ids.length>1?`${result.intake_ids.length} tasks ${result.action==='archive'?'archived':'restored'}. Their history is preserved.`:result.message}</span>{result.action==='archive'&&<Button variant="secondary" onClick={()=>{setChoice({ids:result.intake_ids,action:'restore',expected:result.revisions});setResult(null);}}>Undo archive</Button>}<Button variant="ghost" aria-label="Dismiss cleanup message" onClick={()=>setResult(null)}>Dismiss</Button></div>}
 <Dialog open={!!choice||!!pending} onOpenChange={open=>{if(!open&&!busy&&!pending)setChoice(null);}}><DialogContent onCloseAutoFocus={event=>{const heading=document.querySelector<HTMLElement>('[data-cleanup-focus]');if(heading){event.preventDefault();heading.focus();}}}>
 <DialogTitle>{action==='delete'?'Delete this task?':action==='restore'?'Restore to your Backlog?':'Archive saved work?'}</DialogTitle>
 <DialogDescription>{action==='delete'?'Only an archived idea with no work or history can be permanently removed.':action==='restore'?'Bring this request back to ordinary task views. This does not grant new work permission.':'Move this request out of ordinary task views. Its original request, attempts, checks, results and PR links remain available in Archived.'}</DialogDescription>
 {pending?<p className="soft-callout">A cleanup request may already be saved. Retry to recover the same receipt safely; it will not apply twice.</p>:review.isPending?<p role="status">Checking saved work and its relationships…</p>:review.isError?<p role="alert">{(review.error as Error).message}</p>:<ul className="cleanup-review-list">{contexts.map(c=><li key={c.intake_id}><strong>{c.title}</strong>{c.member_count>1&&<p>{c.member_count} linked attempts stay together.</p>}{(action==='delete'?c.delete_reasons:action==='archive'?c.archive_reasons:[]).map(reason=><p key={reason}>{reason}</p>)}{action==='delete'&&c.can_delete&&<><p>{c.deletion_explanation}</p><details><summary>What is kept</summary><p>{c.retention_explanation}</p></details></>}</li>)}</ul>}
 {action==='delete'&&allowed&&!pending&&<label className="cleanup-confirm"><input type="checkbox" checked={confirmed} onChange={e=>setConfirmed(e.target.checked)}/> Permanently remove this unused request. I understand this cannot be undone.</label>}
 {error&&<p role="alert" className="form-error">{error}</p>}
 <div className="cleanup-dialog-actions"><Button disabled={busy||(!pending&&(!allowed||(action==='delete'&&!confirmed)))} onClick={apply}>{busy?'Saving…':pending?'Recover saved action':action==='delete'?'Delete permanently':action==='restore'?'Restore task':ids.length>1?`Archive ${ids.length} tasks`:'Archive task'}</Button>
 {action==='delete'&&!allowed&&contexts.length===1&&contexts[0].can_archive&&!pending&&<Button variant="secondary" onClick={()=>setChoice({ids,action:'archive'})}>Archive instead</Button>}
 {action==='archive'&&!allowed&&contexts.some(c=>c.can_archive)&&!pending&&<Button variant="secondary" onClick={()=>setChoice({ids:contexts.filter(c=>c.can_archive).map(c=>c.intake_id),action:'archive'})}>Review eligible tasks only</Button>}
 {!pending&&<Button variant="secondary" disabled={busy} onClick={()=>setChoice(null)}>Cancel</Button>}{!pending&&review.isError&&<Button variant="secondary" onClick={()=>void review.refetch()}>Try again</Button>}</div>
 </DialogContent></Dialog>
 </>;
}
