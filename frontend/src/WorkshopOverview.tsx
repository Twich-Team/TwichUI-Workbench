import {Link} from 'react-router-dom';
import type {components} from './api.generated';

type Summary=NonNullable<components['schemas']['WorkspacePage']['summary']>;
export type ResultView='all'|'checked'|'draft'|'local'|'findings'|'incomplete';
export const resultLabels:Record<ResultView,string>={all:'All results',checked:'Checked changes',draft:'Draft PR submitted',local:'Saved locally',findings:'Findings',incomplete:'Needs another look'};
const states:Record<string,string>={queued:'Queued',understanding:'Understanding',preparing:'Preparing',working:'Working',checking:'Checking',cleanup:'Finishing safely',needs_input:'Needs input',needs_approval:'Needs approval',decision_pending:'Decision recorded',held:'On hold',failed:'Could not finish',interrupted:'Interrupted',stopped:'Stopped',completed:'Ready to review',unavailable:'Saved for later',paused:'Paused'};
const kinds:Record<string,string>={bug:'Bug fixes',coverage:'Test coverage',feature:'Features',inspection:'Inspections',simulation:'Simulator',data:'Data updates',other:'Other work'};
export function CompactBars({rows,label}:{rows:{key:string;label:string;value:number;href?:string}[];label:string}){
 const max=Math.max(1,...rows.map(r=>r.value));
 return <ul className="compact-bars" aria-label={label}>{rows.map(row=><li key={row.key} className={'bar-row tone-'+row.key}>
  {row.href?<Link to={row.href} title={`${row.label}: ${row.value} saved requests`}><span>{row.label}</span>{' '}<strong>{row.value.toLocaleString()}</strong></Link>:<div><span>{row.label}</span>{' '}<strong>{row.value.toLocaleString()}</strong></div>}
  <span className="bar-track" aria-hidden="true"><span style={{width:`${row.value/max*100}%`}}/></span>
 </li>)}</ul>;
}
export function WorkshopOverview({summary,total}:{summary:Summary;total:number}){
 return <div className="workshop-overview"><p className="overview-total"><strong>{total.toLocaleString()}</strong><span>saved {total===1?'request':'requests'}<small>Each request counts once; attempts stay in its history.</small></span></p>
 {total?<><h3>Request states</h3><CompactBars label="Requests by current state" rows={Object.entries(summary.states).map(([key,value])=>({key,label:states[key]||key,value,href:'/backlog?state='+encodeURIComponent(key)}))}/>
 <h3>Types of work</h3><CompactBars label="Requests by type" rows={Object.entries(summary.kinds).map(([key,value])=>({key,label:kinds[key]||key,value,href:'/backlog?kind='+encodeURIComponent(key)}))}/></>:<p className="small-text">Your first request will start this overview. No work has been recorded yet.</p>}
 </div>;
}
export function ResultOverview({summary}:{summary:Summary['results']}){
 const items:[ResultView,string,string][]=[['checked','Checked changes','Saved patches and test changes whose applicable automated checks passed.'],['draft','Draft PRs submitted','Recorded submissions, not live GitHub merge status.'],['local','Changes saved locally','Checked patches and test changes without a recorded draft submission.']];
 return <section className="result-overview" aria-label="Result summary"><div className="result-summary-cards">{items.map(([key,label,hint])=><Link key={key} to={'/results?view='+key} title={hint}><strong>{summary[key].toLocaleString()}</strong>{' '}<span>{label}</span>{' '}<small>{key==='checked'?'Automated checks passed':key==='draft'?'Recorded submissions':'No draft submission recorded'}</small></Link>)}</div><p className="small-text">{summary.all} saved {summary.all===1?'outcome':'outcomes'}{summary.findings>0?` · ${summary.findings} with findings`:''}{summary.incomplete>0?` · ${summary.incomplete} needing another look`:''}. Categories can overlap. These totals do not track merging, installation or game testing.</p></section>;
}
