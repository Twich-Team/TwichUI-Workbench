"""Ordered choices composed with the existing tool-free planners and journals."""
from uuid import UUID,uuid5

from workbench import ai_strategy as ai, codex_provider as codex
from workbench.claude_client import exchange
from workbench.planning_provider import PlanningUnavailable


class Waiting(PlanningUnavailable):
    def __init__(self,decision):
        self.decision=ai.Decision.model_validate(decision).model_dump()
        super().__init__(self.decision['reason'],'ai_wait',dict(generation_state='not_started'))


def context(selection,*,suitable_local=False,reviewed_local=False,waiting_since=None):
    return dict(strategy=selection['strategy'],suitable_local=suitable_local,reviewed_local=reviewed_local,
        waiting_since=waiting_since or {},manual_stage=selection.get('manual_stage'),requested_stage=selection.get('requested_stage'))


def choose(selection,stop=lambda:False,**options):
    result=exchange(dict(operation='smart-choose',**context(selection,**options)),stop,60)
    if result.get('state')=='held':
        raise Waiting(ai.Decision(state='waiting',reason=result['reason'],recheck_at=__import__('time').time()+60).model_dump())
    decision=ai.Decision.model_validate(result['decision'])
    if decision.state!='selected':
        raise Waiting(decision.model_dump())
    return decision


def encode_request(selection,decision,prompt,schema,request_id,request_seconds,**options):
    return dict(operation='smart-turn',**context(selection,**options),stage_id=decision.stage_id,
        request_id=request_id,prompt=prompt,schema=schema,request_seconds=request_seconds)


def remote_generate(selection,decision,prompt,schema,request_id,stop=lambda:False,*,request_seconds=120,**options):
    request=encode_request(selection,decision,prompt,schema,request_id,request_seconds,**options)
    result=exchange(request,stop,request_seconds+60)
    if result.get('state') in {'waiting','reselect'}:
        value=result['decision']
        if value['state']=='selected':
            value={**value,'state':'waiting','reason':'Your next authorized choice changed before dispatch. Ready to check again.',
                   'recheck_at':__import__('time').time()+1}
        raise Waiting(value)
    if result.get('state')!='completed':
        raise codex.Hold('protocol',started=result.get('state')!='held')
    native=result['result']
    if native.get('actual_model')!=decision.model or not isinstance(native.get('usage'),dict):
        raise codex.Hold('model_identity',started=True)
    class Output(dict):
        pass
    output=Output(native['output'])
    output.provider_receipt=native.get('provider_receipt')
    output.ai_admission=result['admission']
    return output,native['usage']


class Planner(codex.Planner):
    strategy_control=True
    usage_source='Native provider observations; exact attribution is recorded for each strategy request'

    def __init__(self,selection,stop):
        super().__init__(selection,stop)
        self.frozen=selection
        self.options={}
        self.engine=self.parent_attempt=None
        self.decision=None
        self.backend='smart'

    def bind_operation(self,engine,parent,*,suitable_local=False,reviewed_local=False,waiting_since=None):
        self.engine,self.parent_attempt=engine,parent
        self.options=dict(suitable_local=suitable_local,reviewed_local=reviewed_local,waiting_since=waiting_since or {})

    def generate(self,packet,schema,*,expected_model=None):
        if self.engine is None or self.parent_attempt is None or self.response_evidence is None:
            raise codex.Hold('disclosure')
        try:
            self.decision=choose(self.frozen,self.stopped,**self.options)
        except Waiting as error:
            self.diagnostics=dict(generation_state='not_started',ai_wait=error.decision)
            raise
        stage=next(s for s in ai.Strategy.model_validate(self.frozen['strategy']).stages if s.id==self.decision.stage_id)
        self.selection={**self.frozen,'model':stage.model,'effort':stage.effort,'provider':stage.provider}
        self.backend=stage.provider
        self.usage_source=('Native Claude Code reported response tokens including cache reads and creation' if stage.provider=='claude' else 'Codex app-server reported turn tokens' if stage.provider=='codex' else 'local llama.cpp response tokens')
        identity=str(uuid5(UUID(self.response_evidence.identifier),'strategy-provider-request'))
        self.record(identity,'running',stage)
        try:
            if self.backend=='local':
                from workbench.planning_provider import LocalProvider
                local=LocalProvider(self.stop_event,ends_at=self.ends_at)
                local.response_evidence=self.response_evidence
                try:
                    result=local.generate(packet,schema,expected_model=stage.model)
                finally:
                    self.diagnostics=local.diagnostics
            else:
                result=super().generate(packet,schema)
            self.diagnostics.update(ai_decision=self.decision.model_dump(),actual_provider=self.backend)
            self.record(identity,'completed',stage)
            return result
        except Waiting as error:
            self.diagnostics=dict(generation_state='not_started',ai_wait=error.decision)
            self.record(identity,'not_started',stage)
            raise
        except (PlanningUnavailable,ValueError,OSError):
            self.record(identity,'interrupted_unknown' if self.diagnostics.get('generation_state')=='uncertain' else 'failed',stage)
            raise

    def reported_usage(self,counted):
        return {**super().reported_usage(counted),'status':'complete'}

    def dispatch(self,model,effort,prompt,schema,stop,*,input_limit=codex.MAX_PACKET,request_seconds=120):
        if model!=self.decision.model or effort!=self.decision.effort:
            raise codex.Hold('model_identity')
        return remote_generate(self.frozen,self.decision,prompt,schema,
            self.response_evidence.identifier,stop,request_seconds=request_seconds,**self.options)

    def record(self,identity,outcome,stage):
        from sqlalchemy import select
        from workbench.db import model_attempts,write_transaction
        from workbench.service import now
        with write_transaction(self.engine) as c:
            parent=c.execute(select(model_attempts).where(model_attempts.c.id==self.parent_attempt)).mappings().one()
            if outcome=='running':
                if parent['finished_at']:
                    raise codex.Hold('canceled')
                c.execute(model_attempts.update().where(model_attempts.c.id==self.parent_attempt).values(source='strategy-control'))
                c.execute(model_attempts.insert().values(id=identity,job_id=parent['job_id'],task_id=parent['task_id'],
                    source='workbench',backend=stage.provider,purpose='strategy-request',requested_model=stage.model,
                    requested_effort=stage.effort,policy_revision=parent['policy_revision'],
                    routing_reason=self.decision.reason,started_at=now(),outcome='running'))
            else:
                facts=self.diagnostics
                usage=facts.get('usage')
                if outcome=='not_started':
                    usage=dict(status='not_started',requests_observed=0,source='Verified no native strategy request started',units='tokens',values=dict(prompt_tokens=0,completion_tokens=0,total_tokens=0))
                c.execute(model_attempts.update().where(model_attempts.c.id==identity).values(
                    outcome=outcome,finished_at=now(),actual_model=facts.get('actual_model'),actual_effort=facts.get('actual_effort'),usage=usage))
