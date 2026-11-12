import {useRef,useState} from 'react';
import {useQuery} from '@tanstack/react-query';
import {Image,ExternalLink,Plus,X} from 'lucide-react';
import {request,mutate} from './api';
import {Button} from './components/ui/button';
import type {components} from './api.generated';

type Input=components['schemas']['IntakeInput'];
type Attachment=components['schemas']['FeatureAttachmentView'];
type Reference=components['schemas']['FeatureReferenceEvidence'];
type Lookup=components['schemas']['FeatureReferenceLookupView'];
type ReferenceInput=components['schemas']['FeatureReferenceInput'];
const attachmentURL=(requestId:string,id:string)=>`/api/feature-attachments/${encodeURIComponent(requestId)}/${encodeURIComponent(id)}`;

function ReferenceCard({reference}:{reference:Reference}){
 return <article className="feature-addon-reference"><strong>{reference.name}</strong><p>{reference.status==='inspected'?'Repository metadata checked':reference.status==='needs_clarification'?'Choose the intended project':'Source not available'}</p>{reference.source_url&&<a href={reference.source_url} target="_blank" rel="noreferrer">Open reference project <ExternalLink size={15}/></a>}{reference.description&&<p>{reference.description}</p>}{reference.status==='inspected'&&<p className="small-text">Revision {reference.revision?.slice(0,8)} · License: {reference.license}</p>}<p className="small-text">{reference.game_version}</p>{reference.limitations.map((text,i)=><p className="small-text" key={i}>{text}</p>)}{reference.documentation&&<details><summary>Read saved documentation excerpt</summary><pre className="reference-document">{reference.documentation.excerpt}</pre><p className="small-text">{reference.documentation.excerpt_truncated?'Excerpt only. ':''}Read from the saved revision; untrusted reference text.</p></details>}</article>;
}

export function RequestReferences({requestId,files,references,notice}:{requestId:string;files:Attachment[];references:Reference[];notice?:string|null}){
 return <>{notice&&<p className="soft-callout" role="note">{notice}</p>}{files.map(file=><figure className="feature-file-reference" key={file.id}>{file.media_type==='image/png'&&<a href={attachmentURL(file.request_id||requestId,file.id)} target="_blank" rel="noreferrer"><img loading="lazy" src={attachmentURL(file.request_id||requestId,file.id)} alt={file.caption||file.filename}/></a>}<figcaption><a href={attachmentURL(file.request_id||requestId,file.id)} target="_blank" rel="noreferrer">{file.filename}</a> · {Math.ceil(file.size/1024)} KB{file.caption&&<p>{file.caption}</p>}<small>Owner reference · Not proof of implementation</small></figcaption></figure>)}{references.map((reference,i)=><ReferenceCard reference={reference} key={reference.source_url||reference.name+i}/>)}</>;
}

export function ProposalReferenceFiles({intake,proposal,disabled,act}:{
 intake:components['schemas']['IntakeView'];proposal:components['schemas']['ProposalView'];disabled:boolean;
 act:(suffix:string,body:unknown,method?:string)=>Promise<void>;
}){
 const claims=intake.original.feature_attachments||[];
 const files=useQuery({queryKey:['proposal-owner-files',intake.id,claims],queryFn:()=>Promise.all(claims.map(file=>request<Attachment>(attachmentURL(file.source_request_id||intake.id,file.id)+'/metadata'))),enabled:claims.length>0});
 const [error,setError]=useState('');const [saving,setSaving]=useState(false);
 async function useReference(file:Attachment){
  const key=`proposal-file:${proposal.id}:${file.id}`;setSaving(true);setError('');
  try{
   const previous=localStorage.getItem(key);let pending:components['schemas']['ArtifactReferenceInput']|null=null;
   try{pending=previous?JSON.parse(previous):null;}catch{/* A damaged local draft grants no authority. */}
   // A changed canonical binding fences the old copy. A fresh click can choose
   // this same original file for the newly opened review with a new identity.
   const command=pending?.binding===proposal.binding?pending:{artifact_id:crypto.randomUUID(),proposal_id:proposal.id,binding:proposal.binding,reference_id:file.id};
   localStorage.setItem(key,JSON.stringify(command));await act('reference-artifacts',command);localStorage.removeItem(key);
  }catch(reason){setError((reason as Error).message);}finally{setSaving(false);}
 }
 if(!claims.length)return null;
 return <section className="original-plan-references" aria-label="Original request files"><h3>Pictures and notes from your request</h3><p>Choose a saved reference for this plan without uploading it again. This attaches its original bytes and purpose to this revision; plan and run approval remain separate.</p>
 {files.isError&&<p role="alert">An original reference is unavailable. Its saved request remains unchanged.</p>}
 {files.data?.map(file=>{const used=proposal.artifacts.some(artifact=>artifact.sha256===file.sha256&&artifact.role===file.role);return <div key={file.id}>{used?<p>{file.filename} · Used in this plan.</p>:<><RequestReferences requestId={intake.id} files={[file]} references={[]}/><Button type="button" variant="secondary" disabled={disabled||saving} onClick={()=>void useReference(file)}>Use {file.filename} for this plan</Button></>}</div>;})}
 {error&&<p role="alert" className="form-error">{error} Retrying keeps the same saved attachment command.</p>}
 </section>;
}

export function FeatureContextFields({body,update,disabled,onBusy}:{body:Input;update:(change:(current:Input)=>Input)=>void;disabled:boolean;onBusy:(busy:boolean)=>void}){
 const fileField=useRef<HTMLInputElement>(null);
 const [file,setFile]=useState<File|null>(null);const [caption,setCaption]=useState('');
 const [role,setRole]=useState<'mockup'|'game_context'|'evidence'>('mockup');
 const [uploadId,setUploadId]=useState(()=>crypto.randomUUID());
 const [busy,setBusy]=useState(false);const [error,setError]=useState('');
 const [result,setResult]=useState<{input:string;data:Lookup}|null>(null);
 const claims=body.feature_attachments||[];const items=body.feature_references?.items||[];
 const signature=JSON.stringify(items);
 const files=useQuery({queryKey:['draft-feature-files',body.request_id,claims],queryFn:()=>Promise.all(claims.map(file=>request<Attachment>(attachmentURL(file.source_request_id||body.request_id,file.id)+'/metadata'))),enabled:claims.length>0});
 const saved=useQuery({queryKey:['draft-feature-reference-evidence',body.request_id,body.feature_references?.evidence_binding,signature],queryFn:()=>mutate<Lookup>('/api/feature-references/evidence',{request_id:body.feature_references?.source_request_id||body.request_id,references:items}),enabled:Boolean(body.feature_references?.evidence_binding)});
 const shown=result?.input===signature?result.data:saved.data;
 function editReference(index:number,change:Partial<ReferenceInput>){
  setResult(null);setError('');update(current=>({...current,feature_references:{items:(current.feature_references?.items||[]).map((item,i)=>i===index?{...item,...change}:item)}}));
 }
 async function upload(){
  if(!file)return;setBusy(true);onBusy(true);setError('');
  try{
   if(file.size>4*1024*1024)throw new Error('Choose a PNG or text file up to 4 MiB.');
   const encoded=await new Promise<string>((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(String(reader.result).split(',')[1]);reader.onerror=()=>reject(new Error('This file could not be read.'));reader.readAsDataURL(file);});
   const media=file.type||(/\.txt$/i.test(file.name)?'text/plain':'image/png');
   const attached=await mutate<Attachment>('/api/feature-attachments',{request_id:body.request_id,attachment_id:uploadId,filename:file.name,caption,role,media_type:media,data_base64:encoded},'POST',AbortSignal.timeout(30000));
   update(current=>({...current,feature_attachments:[...(current.feature_attachments||[]).filter(item=>item.id!==attached.id),{id:attached.id,source_request_id:attached.request_id,sha256:attached.sha256,metadata_binding:attached.metadata_binding}]}));
   setFile(null);if(fileField.current)fileField.current.value='';setCaption('');setUploadId(crypto.randomUUID());
  }catch(reason){setError((reason as Error).message);}finally{setBusy(false);onBusy(false);}
 }
 async function lookup(){
  setBusy(true);onBusy(true);setError('');
  try{
   const data=await mutate<Lookup>('/api/feature-references/lookup',{request_id:body.request_id,references:items},'POST',AbortSignal.timeout(125000));
   setResult({input:signature,data});update(current=>JSON.stringify(current.feature_references?.items)===signature?{...current,feature_references:{items,...(data.references.some(item=>item.status==='needs_clarification')?{}:{source_request_id:body.request_id,evidence_binding:data.binding})}}:current);
  }catch(reason){setError((reason as Error).message);}finally{setBusy(false);onBusy(false);}
 }
 return <details className="feature-context-fields"><summary>Optional pictures, notes and addon references</summary><p>Add design inspiration or useful context. Files and external source text are references, not instructions to execute or permission to copy code or artwork.</p>
 <fieldset disabled={disabled||busy}><legend>Pictures and supporting notes</legend><p className="small-text">PNG or UTF-8 text · Up to four files, 4 MiB each. Describe important picture details in the caption; the current planning model uses that text.</p>
 {files.isError&&<p role="alert">A saved file reference is unavailable. Keep the original file and review it before submitting.</p>}
 {claims.map(claim=>{const file=files.data?.find(item=>item.id===claim.id);return <div className="draft-reference-file" key={claim.id}><span>{file?.filename||'Saved file reference'}</span><Button type="button" variant="ghost" aria-label={'Remove '+(file?.filename||'saved file reference')} onClick={()=>update(current=>({...current,feature_attachments:(current.feature_attachments||[]).filter(item=>item.id!==claim.id)}))}><X size={16}/>Remove</Button></div>;})}
 {claims.length<4&&<><label>Reference file<input ref={fileField} type="file" accept="image/png,text/plain,.txt" onChange={e=>{const selected=e.target.files?.[0]||null;setFile(selected);setUploadId(crypto.randomUUID());if(selected&&(/\.txt$/i.test(selected.name)||selected.type==='text/plain'))setRole('evidence');}}/></label><label>What should Workbench notice? Optional<textarea rows={2} maxLength={800} value={caption} onChange={e=>{setCaption(e.target.value);setUploadId(crypto.randomUUID());}}/></label><label>Reference purpose<select value={role} onChange={e=>{setRole(e.target.value as typeof role);setUploadId(crypto.randomUUID());}}><option value="mockup">Proposed appearance or inspiration</option><option value="game_context">Current in-game appearance</option><option value="evidence">Supporting note or evidence</option></select></label><Button type="button" variant="secondary" disabled={!file} onClick={()=>void upload()}><Image size={16}/>Add file reference</Button></>}
 </fieldset><fieldset disabled={disabled||busy}><legend>Reference addons</legend><p className="small-text">Use an addon name, GitHub repository or CurseForge WoW project link. Names may need a project choice. Sources that cannot be read stay clearly unverified.</p>
 {items.map((item,index)=><div className="draft-addon-reference" key={index}><label>Addon name or reference link<input maxLength={300} value={item.name_or_url} onChange={e=>editReference(index,{name_or_url:e.target.value})}/></label><label>What should Workbench learn? Optional<textarea rows={2} maxLength={800} value={item.instruction} onChange={e=>editReference(index,{instruction:e.target.value})}/></label><Button type="button" variant="ghost" onClick={()=>{setResult(null);update(current=>{const rest=(current.feature_references?.items||[]).filter((_,i)=>i!==index);return {...current,feature_references:rest.length?{items:rest}:null};});}}><X size={16}/>Remove addon reference</Button></div>)}
 {items.length<4&&<Button type="button" variant="secondary" onClick={()=>update(current=>({...current,feature_references:{items:[...(current.feature_references?.items||[]),{name_or_url:'',instruction:''}]}}))}><Plus size={16}/>Add an addon reference</Button>}
 {items.length>0&&<Button type="button" disabled={items.some(item=>!item.name_or_url.trim())} onClick={()=>void lookup()}>Look up reference addons</Button>}
 {saved.isError&&<p role="alert">Saved source information is unavailable. Look up the references again before planning.</p>}
 {shown?.references.map((reference,index)=><div key={index}><ReferenceCard reference={reference}/>{reference.status==='needs_clarification'&&<div className="reference-project-choices"><strong>Which project did you mean?</strong>{reference.choices.map(choice=><Button type="button" key={choice.project_id} variant="secondary" onClick={()=>editReference(index,{name_or_url:choice.source_url})}>Use {choice.name}</Button>)}{!reference.choices.length&&<p>Paste the exact project link to identify this addon.</p>}</div>}</div>)}
 </fieldset>{busy&&<p role="status">Saving reference information…</p>}{error&&<p role="alert" className="form-error">{error}</p>}{items.length>0&&(!body.feature_references?.evidence_binding||shown?.references.some(r=>r.status==='needs_clarification'))&&<p className="small-text">Look up the references and choose any ambiguous projects before submitting this feature.</p>}
 </details>;
}
