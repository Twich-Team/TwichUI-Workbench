import type {components} from './api.generated';
type Report=components['schemas']['CapabilityReport'];
type Choice=components['schemas']['TaskModelChoice'];

export function ModelChoiceFields({label,value,report,onChange}:{label:string;value:Choice;report?:Report;onChange:(value:Choice)=>void}){
 const entry=report?.executors.find(e=>e.backend===value.provider);
 const age=entry?.observed_at?Date.now()-Date.parse(entry.observed_at):NaN;
 const fresh=age>=0&&age<=86400000;
 const models=fresh?entry?.models||[]:[];
 const model=models.find(m=>m.model===value.model);
 return <fieldset className="provider-config"><legend>{label}</legend>
  <label>{label} model<select aria-label={`${label} model`} value={value.model} onChange={e=>onChange({...value,model:e.target.value,effort:null})}>
   {!models.some(m=>m.model===value.model)&&<option value={value.model}>{value.model?`${value.model} · support unverified`:'Choose a supported model'}</option>}
   {models.map(m=><option key={m.model} value={m.model}>{m.model}</option>)}
  </select></label>
  {Boolean(model?.efforts?.length||value.effort)&&<label>{label} reasoning effort<select aria-label={`${label} reasoning effort`} value={value.effort||''} onChange={e=>onChange({...value,effort:e.target.value||null})}>
   <option value="">Provider default</option>
   {value.effort&&!model?.efforts?.includes(value.effort)&&<option value={value.effort} disabled>{value.effort} · unsupported</option>}
   {model?.efforts?.map(e=><option key={e} value={e}>{e.charAt(0).toUpperCase()+e.slice(1)}</option>)}
  </select></label>}
  {!model&&<p className="small-text">No current catalog confirms this model. Refresh supported discovery before starting new work.</p>}
  {model&&!model.efforts?.length&&<p className="small-text">No selectable reasoning effort is supported.</p>}
  {entry&&<details><summary>Availability and restrictions</summary><p>{entry.limitation}</p><p>{entry.source}</p><p>Model support does not establish account access or included allowance. Workbench checks access before use.</p></details>}
 </fieldset>;
}
