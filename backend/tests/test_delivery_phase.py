"""Local acceptance and processing sequence never replace provider lifecycle."""
import json
import sqlite3
from uuid import uuid4

import pytest
from workbench.delivery_phase import observe,renewable
from test_provider_receipts import receipts


@pytest.mark.parametrize('fault',[None,'active','missing_capabilities','unknown_usage','changed_model'])
def test_locally_rejected_content_can_continue_only_with_settled_read_only_receipts(tmp_path,fault):
    identity,_,_,usage=receipts(tmp_path)
    payload=dict(recovery_capabilities=dict(source_writes=False,execution_dispatch=False,external_action_tools=False))
    if fault=='missing_capabilities':
        payload={}
    if fault=='unknown_usage':
        usage=None
    with sqlite3.connect(':memory:') as c:
        c.execute('CREATE TABLE planning_jobs (id,revision,status,finished_at,payload,intake_id,kind)')
        c.execute('CREATE TABLE model_attempts (id,backend,requested_model,actual_model,outcome,finished_at,usage,job_id,purpose)')
        job=str(uuid4())
        c.execute('INSERT INTO planning_jobs VALUES (?,?,?,?,?,?,?)',
            (job,1,'running' if fault=='active' else 'failed',None if fault=='active' else 'finished',json.dumps(payload),'task','planning'))
        c.execute('INSERT INTO model_attempts VALUES (?,?,?,?,?,?,?,?,?)',
            (identity,'codex','model','other' if fault=='changed_model' else 'model','failed',
             None if fault=='active' else 'finished',json.dumps(usage),job,'planning'))
        if fault=='changed_model':
            with pytest.raises(ValueError):
                observe(c,'task',[tmp_path])
            return
        result=observe(c,'task',[tmp_path])
        assert renewable(result)==(fault is None)
        if fault is None:
            assert result['phases'][0]['database_outcome']=='failed'
            assert result['phases'][0]['provider_state']=='completed'
            assert result['phases'][0]['output_accepted'] is False
            assert result['known_usage']['total_tokens']==34
            assert result['next_sequence']==2
        if fault in ('active','unknown_usage'):
            assert result['unknown_usage'] is True
