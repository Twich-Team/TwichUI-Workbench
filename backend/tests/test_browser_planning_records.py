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
