import {useQuery} from '@tanstack/react-query';
import {request} from './api';
import type {components} from './api.generated';

export function SimulatorComparison({identity}:{identity:string}){
 const query=useQuery({queryKey:['simulator-comparison',identity],queryFn:()=>request<components['schemas']['ComparisonView']>('/api/real-executions/'+identity+'/simulator')});
 if(query.isError)return <section className="result-simulator"><h3>Simulator comparison</h3><p>Simulator evidence is unavailable. The saved result is still available above.</p></section>;
 if(!query.data||query.data.status==='not_recorded')return null;
 const view=query.data;const evidence=view.evidence;
 return <section className="result-simulator" aria-label="Simulator comparison"><h3>Simulator comparison</h3><p>{view.message}</p>
 {view.status==='captured'&&<div className="simulator-captures">{(['before','after'] as const).map(which=><figure key={which}><a href={'/api/real-executions/'+identity+'/simulator/'+which} target="_blank" rel="noreferrer"><img src={'/api/real-executions/'+identity+'/simulator/'+which} alt={which==='before'?'Original addon in the simulator':'Combined feature in the simulator'} loading="lazy"/></a><figcaption>{which==='before'?'Before · Original addon':'After · Combined feature'}</figcaption></figure>)}</div>}
 {evidence&&<><ul>{evidence.limitations.map((text,i)=><li key={i}>{text}</li>)}</ul><details><summary>Capture conditions and source</summary><dl><dt>Simulator</dt><dd>{evidence.conditions.simulator}</dd><dt>Blizzard UI</dt><dd>{evidence.conditions.ui_version}</dd><dt>Viewport and scale</dt><dd>{evidence.conditions.viewport.join(' × ')} · {evidence.conditions.ui_scale}</dd><dt>Scenario</dt><dd>{evidence.conditions.scenario}</dd><dt>Game state</dt><dd>{evidence.conditions.state}</dd><dt>Settings</dt><dd>{evidence.conditions.settings}</dd><dt>Capture timing</dt><dd>{evidence.conditions.capture_seconds} seconds after opening</dd><dt>Captured</dt><dd>{evidence.captured_at}</dd>{evidence.before&&<><dt>Original revision</dt><dd>{evidence.before.source_revision}</dd></>}{evidence.after&&<><dt>Combined revision</dt><dd>{evidence.after.source_revision}</dd></>}</dl><p>No network or game installation access. Simulator captures do not establish game-client acceptance.</p></details></>}
 </section>;
}
