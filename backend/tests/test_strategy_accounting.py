"""A logical investigation may contain separately attributed native requests."""
from copy import deepcopy

from workbench.ai_strategy import Stage, Strategy
from workbench.strategy_accounting import combine, complete_requests


def sample():
    stages=[Stage(id='codex',provider='codex',model='gpt-test',effort='high',allocation=50),
            Stage(id='claude',provider='claude',model='claude-test',effort='low',allocation=75)]
    payload={'local_planning_receipt':{'codex_route':{'strategy':Strategy(stages=stages).model_dump()}}}
    def usage(stage,identity):
        values=dict(prompt_tokens=40,completion_tokens=10,total_tokens=50)
        record=dict(stage_id=stage.id,provider=stage.provider,model=stage.model,effort=stage.effort,
                    evidence_id=identity,values=values)
        return dict(status='complete',units='tokens',requests_observed=1,values=values,strategy_requests=[record])
    return payload,usage(stages[0],'first'),usage(stages[1],'second')


def test_resumed_mixed_provider_totals_keep_individual_frozen_attribution():
    payload,first,second=sample()
    payload['ai_resumed_phases']=[dict(attempt='retired',usage=first)]
    result=combine(second,payload)
    assert result['values']['total_tokens']==100 and result['requests_observed']==2
    assert complete_requests(result,payload)
    assert second['values']['total_tokens']==50
    for change in ('duplicate','model','effort','total'):
        invalid=deepcopy(result)
        record=invalid['strategy_requests'][1]
        if change=='duplicate':
            record['evidence_id']='first'
        elif change=='total':
            invalid['values']['total_tokens']=99
        else:
            record[change]='not-authorized'
        assert not complete_requests(invalid,payload)


def test_additional_manual_authority_is_distinct_from_original_frozen_route():
    payload,_,second=sample()
    payload['local_planning_receipt']['codex_route']['strategy']['stages'].pop()
    assert not complete_requests(second,payload)
    stage=Stage(id='claude',provider='claude',model='claude-test',effort='low',allocation=75)
    payload['manual_ai']={'route':{'strategy':Strategy(stages=[stage]).model_dump()}}
    assert complete_requests(second,payload)
