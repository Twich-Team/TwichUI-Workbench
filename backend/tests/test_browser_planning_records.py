"""Browser prefilters preserve attribution; private terminal proof owns admission."""
import json
from pathlib import Path
import subprocess
from project_env import environment


def test_browser_handles_confirmed_interruption_projection_without_accepting_unknown_or_other_models():
    root=Path(__file__).resolve().parents[2]
    source="""
import assert from 'node:assert/strict';
import {planningRecordMatches} from './scripts/browser_acceptance.mjs';
const candidate={provider:'codex',model:'selected'};
const attempt={id:'old',purpose:'planning',backend:'codex',requested_model:'selected',actual_model:null,outcome:'incomplete_output'};
const reply={renew_operation:true,settled_attempts:['old']};
assert(planningRecordMatches(attempt,candidate,reply));
for(const change of [{outcome:'running'},{outcome:'interrupted_unknown'},{actual_model:'other'},
 {backend:'local'},{requested_model:'other'},{purpose:'implementation'},{id:'unverified'}])
 assert(!planningRecordMatches({...attempt,...change},candidate,reply));
assert(!planningRecordMatches(attempt,candidate,undefined));
assert(!planningRecordMatches({...attempt,outcome:'completed'},candidate,reply));
assert(planningRecordMatches({...attempt,outcome:'completed',actual_model:'selected'},candidate));
console.log(JSON.stringify({prefilter:true,execution_authorized:false}));
"""
    result=subprocess.run(['node','--input-type=module','-e',source],cwd=root,env=environment(),capture_output=True,text=True,timeout=10)
    assert result.returncode==0,result.stderr
    assert json.loads(result.stdout)==dict(prefilter=True,execution_authorized=False)


def test_preparation_ignores_settled_historical_planning_but_holds_current_or_uncertain_work():
    root=Path(__file__).resolve().parents[2]
    source="""
import assert from 'node:assert/strict';
import {preparationReady} from './scripts/browser_acceptance.mjs';
const old={id:'old',kind:'planning',revision:1,status:'failed',finished_at:'saved'};
const current={id:'current',kind:'behavioral_preparation',revision:2,status:'running'};
const item={decisions:[{id:'scope',status:'applied'}],tasks:[{id:'task'}],jobs:[old,current]};
assert(!preparationReady(item,{revision:2},{id:'scope'}));
const recovering=structuredClone(item);recovering.jobs[1].status='failed';recovering.jobs[1].automatic_recovery=true;
assert(!preparationReady(recovering,{revision:2},{id:'scope'}));
recovering.jobs[1].automatic_recovery=false;assert.throws(()=>preparationReady(recovering,{revision:2},{id:'scope'}));
const done=structuredClone(item);done.jobs[1].status='completed';
assert(preparationReady(done,{revision:2},{id:'scope'}));
for(const change of [{status:'failed'},{revision:3},{kind:'behavioral_preparation',revision:1}]) {
 const changed=structuredClone(done);Object.assign(changed.jobs[1],change);
 assert.throws(()=>preparationReady(changed,{revision:2},{id:'scope'}));
}
const unknown=structuredClone(done);unknown.jobs[0].status='running';
assert.throws(()=>preparationReady(unknown,{revision:2},{id:'scope'}));
console.log('PASS');
"""
    result=subprocess.run(['node','--input-type=module','-e',source],cwd=root,env=environment(),capture_output=True,text=True,timeout=10)
    assert result.returncode==0,result.stderr
    assert result.stdout.strip()=='PASS'


def test_applied_scope_continuation_does_not_reauthorize_expired_new_planning_or_stale_bindings():
    root=Path(__file__).resolve().parents[2]
    source="""
import assert from 'node:assert/strict';
import {scopeConsentValid} from './scripts/browser_acceptance.mjs';
const now=Date.now(),time=new Date(now-3600000).toISOString();
const plan={id:'p',binding:'exact',scope_revision:1};
const decision={status:'applied',proposal_id:'p',binding:'exact',scope_revision:1,payload:{action:'approve'}};
assert(!scopeConsentValid({decisions:[]},plan,time,now));
assert(scopeConsentValid({decisions:[decision]},plan,time,now));
for(const change of [{status:'pending'},{binding:'changed'},{scope_revision:2},{proposal_id:'other'},
 {payload:{action:'approve',run_budget:{}}}])
 assert(!scopeConsentValid({decisions:[{...decision,...change}]},plan,time,now));
assert(!scopeConsentValid({decisions:[decision]},plan,new Date(now+1000).toISOString(),now));
console.log('PASS');
"""
    result=subprocess.run(['node','--input-type=module','-e',source],cwd=root,env=environment(),capture_output=True,text=True,timeout=10)
    assert result.returncode==0,result.stderr
    assert result.stdout.strip()=='PASS'


def test_continuation_browser_keeps_known_failed_history_but_never_ignores_new_failure_or_uncertainty():
    root=Path(__file__).resolve().parents[2]
    source="""
import assert from 'node:assert/strict';
import {preparationReady,scopeConsentValid,checkedProgress} from './scripts/browser_acceptance.mjs';
const scope={id:'scope',status:'applied',proposal_id:'p',binding:'b',scope_revision:1,payload:{action:'approve'}};
const prior={id:'old-session',status:'applied',payload:{execution_continuation:{scope_approval_id:'scope'},run_budget:{publication:'hold'}}};
const item={decisions:[prior,scope],tasks:[{}],jobs:[{revision:1,status:'completed',kind:'planning'}]};
assert(preparationReady(item,{revision:1},scope));
assert(scopeConsentValid(item,{id:'p',binding:'b',scope_revision:1},new Date(Date.now()-3600000).toISOString(),Date.now()));
const old={id:'old',status:'failed',recovery_pending:false};
const waiting={stage:'failed',runs:[old],session:{id:'old-session',status:'failed'},publication_allowed:false};
assert(!checkedProgress(waiting,['old'],['old-session']));
assert.throws(()=>checkedProgress({...waiting,session:{id:'new-session',status:'failed'}},['old'],['old-session']));
assert.throws(()=>checkedProgress({...waiting,runs:[{...old,recovery_pending:true}]},['old'],['old-session']));
const done={...waiting,stage:'checked',session:{id:'new-session',status:'completed'},runs:[{id:'new',status:'checked',verification:'passed'},old]};
assert(checkedProgress(done,['old'],['old-session']));
assert.throws(()=>checkedProgress(done));
console.log('PASS');
"""
    result=subprocess.run(['node','--input-type=module','-e',source],cwd=root,env=environment(),capture_output=True,text=True,timeout=10)
    assert result.returncode==0,result.stderr
    assert result.stdout.strip()=='PASS'
