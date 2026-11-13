import {useState} from 'react';
import {useQuery} from '@tanstack/react-query';
import {request} from './api';
import type {components} from './api.generated';
import {Button} from './components/ui/button';
import {ModelChoiceFields} from './ModelChoiceFields';
type Choice=components['schemas']['TaskModelChoice'];
type Overview=components['schemas']['ModelOverview'];
const names={local:'Local Qwen',codex:'Codex',claude:'Claude'};
export function ManualAI({binding,blocked,start}:{binding:string;blocked:boolean;start:(body:unknown)=>Promise<void>}){
 const query=useQuery({queryKey:['models'],queryFn:()=>request<Overview>('/api/models')});
 const [choice,setChoice]=useState<Choice|null>(null),[override,setOverride]=useState(false),[command,setCommand]=useState(()=>crypto.randomUUID());
 const policy=query.data?.policy;
 const selected=choice||(policy?{provider:policy.preferred,model:policy.providers?.[policy.preferred]?.model||'',effort:policy.providers?.[policy.preferred]?.effort}:null);
 function change(next:Choice){setChoice(next);setCommand(crypto.randomUUID());}
 return <section className="panel" aria-label="Start now with AI"><h2>Start now with…</h2><p>Make an explicit choice for this waiting operation. Your earlier frozen choices and activity stay in its history.</p>
 {selected&&<><label>AI provider<select value={selected.provider} disabled={blocked||query.isError} onChange={e=>{const provider=e.target.value as Choice['provider'];change({provider,model:policy?.providers?.[provider]?.model||'',effort:policy?.providers?.[provider]?.effort});}}>{(['local','codex','claude'] as const).map(p=><option value={p} key={p} disabled={!policy?.providers?.[p]?.enabled}>{names[p]}{!policy?.providers?.[p]?.enabled?' · disabled':''}</option>)}</select></label>
 <ModelChoiceFields label="Start now" value={selected} report={query.data?.capabilities} onChange={change}/>
 <label className="check-label"><input type="checkbox" checked={override} disabled={blocked} onChange={e=>{setOverride(e.target.checked);setCommand(crypto.randomUUID());}}/>For this operation, allow starting above Workbench’s strategy allocation</label>
 <p className="small-text">{selected.provider==='local'?'Uses the existing local service and resource checks.':'Sends this task and its permitted source to '+(selected.provider==='claude'?'Anthropic through native Claude Code.':'OpenAI through signed-in Codex.')} Provider limits, sign-in, billing permissions and required safety checks still apply. Unknown prior requests must settle first. This cannot approve an addon change or publication.</p>
 <Button disabled={blocked||query.isError||!selected.model} onClick={()=>void start({command_id:command,binding,choice:selected,exceed_workbench_allocation:override}).catch(()=>{})}>Start now with {names[selected.provider]}</Button></>}
 {!selected&&<p role="status">{query.isError?'AI choices could not be loaded.':'Reading available model choices…'}</p>}</section>;
}
