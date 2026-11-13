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
        assert renewable(result)==(fault in (None,'unknown_usage'))
        if fault in (None,'unknown_usage'):
            assert result['phases'][0]['database_outcome']=='failed'
            assert result['phases'][0]['provider_state']=='completed'
            assert result['phases'][0]['output_accepted'] is False
            assert result['known_usage']['total_tokens']==34
            assert result['next_sequence']==2
        if fault in ('active','unknown_usage'):
            assert result['unknown_usage'] is True


@pytest.mark.parametrize('fault',[None,'missing_terminal','unknown_usage','missing_capabilities'])
def test_first_operator_renewal_uses_terminal_proof_before_any_v4_job_exists(tmp_path,fault):
    from acceptance_policy import delivery_chain
    identity,_,_,usage=receipts(tmp_path)
    stage=tmp_path/'stage'
    (stage/'artifacts').mkdir(parents=True)
    (stage/'ops').mkdir()
    db=stage/'artifacts/history.sqlite3'
    caps=dict(source_writes=False,execution_dispatch=False,external_action_tools=False)
    from test_planning_limits import current
    payload=dict(recovery_capabilities={} if fault=='missing_capabilities' else caps,
                 local_planning_receipt=current())
    if fault=='unknown_usage':
        usage=None
    if fault=='missing_terminal':
        next((tmp_path/'artifacts/codex-evidence').glob('*.jsonl')).unlink()
    with sqlite3.connect(db) as c:
        c.execute('CREATE TABLE planning_jobs (id,revision,status,finished_at,payload,intake_id,kind,attempts)')
        c.execute('CREATE TABLE model_attempts (id,backend,requested_model,actual_model,outcome,finished_at,usage,job_id,purpose)')
        job=str(uuid4())
        c.execute('INSERT INTO planning_jobs VALUES (?,?,?,?,?,?,?,?)',(job,1,'failed','finished',json.dumps(payload),'task','planning',1))
        c.execute('INSERT INTO model_attempts VALUES (?,?,?,?,?,?,?,?,?)',(identity,'codex','model','model','failed','finished',json.dumps(usage),job,'planning'))
        c.commit()
        policy=stage/'ops/everyday.json'
        policy.write_text(json.dumps(dict(database=str(db),database_identity=[db.stat().st_dev,db.stat().st_ino],evidence_roots=[str(tmp_path)])))
        policy.chmod(0o600)
        before=c.total_changes
        # Historical default retains its original meaning; fresh intent is explicit.
        with pytest.raises(ValueError):
            delivery_chain(c,'task',1)
        if fault in ('missing_terminal','missing_capabilities'):
            with pytest.raises((ValueError,OSError)):
                delivery_chain(c,'task',1,renewal=True)
        else:
            result=delivery_chain(c,'task',1,renewal=True)
            assert result[0]['outcome']=='failed'
            assert result[0]['usage'] is None if fault=='unknown_usage' else result[0]['usage']['values']['total_tokens']==34
        assert c.total_changes==before


def test_known_interruption_can_renew_without_reusing_or_relabeling_partial_output(tmp_path):
    from test_provider_receipts import interrupted_receipts
    identity,_,_,usage,diag,caps=interrupted_receipts(tmp_path)
    payload=dict(recovery_capabilities=caps,diagnostics=diag)
    with sqlite3.connect(':memory:') as c:
        c.execute('CREATE TABLE planning_jobs (id,revision,status,finished_at,payload,intake_id,kind)')
        c.execute('CREATE TABLE model_attempts (id,backend,requested_model,actual_model,outcome,finished_at,usage,job_id,purpose)')
        c.execute('INSERT INTO planning_jobs VALUES (?,?,?,?,?,?,?)',('job',1,'failed','finished',json.dumps(payload),'task','planning'))
        c.execute('INSERT INTO model_attempts VALUES (?,?,?,?,?,?,?,?,?)',
                  (identity,'codex','selected',None,'incomplete_output','finished',json.dumps(usage),'job','planning'))
        before=c.total_changes
        result=observe(c,'task',[tmp_path])
        assert renewable(result) and not result['unknown_usage']
        phase=result['phases'][0]
        assert phase['provider_state']=='interrupted' and phase['database_outcome']=='incomplete_output'
        assert phase['actual_model'] is None and not phase['output_accepted']
        assert result['known_usage']==usage['values'] and c.total_changes==before
