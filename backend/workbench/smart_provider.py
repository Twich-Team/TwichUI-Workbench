"""Ordered choices composed with the existing tool-free planners and journals."""
import json
from uuid import UUID,uuid5

from workbench import ai_strategy as ai, codex_provider as codex
from workbench.claude_client import exchange
from workbench.planning_provider import PlanningUnavailable


class Waiting(PlanningUnavailable):
    def __init__(self,decision):
        self.decision=ai.Decision.model_validate(decision).model_dump()
        super().__init__(self.decision['reason'],'ai_wait',dict(generation_state='not_started'))


def context(selection,*,suitable_local=False,reviewed_local=False,waiting_since=None,failures=None):
    return dict(failures=failures or {},strategy=selection['strategy'],suitable_local=suitable_local,reviewed_local=reviewed_local,
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
        self.request_history=[]

    def bind_operation(self,engine,parent,*,suitable_local=False,reviewed_local=False,waiting_since=None,failures=None,settled_failures=None):
        self.engine,self.parent_attempt=engine,parent
        self.options=dict(suitable_local=suitable_local,reviewed_local=reviewed_local,waiting_since=waiting_since or {},failures=dict(failures or {}))
        self.settled_failures=list(settled_failures or [])

    def generate(self,packet,schema,*,expected_model=None):
        from workbench.planning_provider import LocalBeforeGeneration
        try:
            for _ in self.frozen['strategy']['stages']:
                try:
                    return self._generate_once(packet,schema,expected_model=expected_model)
                except LocalBeforeGeneration:
                    self.options.setdefault('failures',{})[self.decision.stage_id]='unavailable'
                except codex.Hold as error:
                    if error.started or error.reason not in {'unavailable','authentication','capacity'}:
                        raise
                    self.options.setdefault('failures',{})[self.decision.stage_id]={'capacity':'provider_limit'}.get(error.reason,error.reason)
            raise Waiting(ai.Decision(state='waiting',reason='All configured AI options are currently unavailable.',
                recheck_at=__import__('time').time()+300).model_dump())
        finally:
            self.diagnostics.update(ai_failures=dict(self.options.get('failures',{})),ai_settled_failures=list(getattr(self,'settled_failures',[])))

    def observe_settled_failure(self,reason):
        """Change model only after the shared receipt owner proves completion."""
        if reason not in {'invalid-response','no-progress'} or not self.request_history or not self.response_evidence:
            return False
        from workbench.provider_receipts import request_state
        from workbench.everyday import configuration
        last=self.request_history[-1]
        facts=self.diagnostics
        usage=dict(status='complete',units='tokens',values=last['values'],requests_observed=1)
        try:
            settled=request_state(self.response_evidence.identifier,last['provider'],last['model'],last['model'],
                'schema_validation',usage,facts,dict(source_writes=False,execution_dispatch=False,external_action_tools=False),
                configuration(self.engine).evidence_roots,local_status='failed',local_finished=True,finished=True)
        except (ValueError,OSError,KeyError,TypeError):
            return False
        if settled['provider_state']!='completed':
            return False
        observation=dict(stage_id=last['stage_id'],provider=last['provider'],model=last['model'],reason=reason,
            evidence_id=self.response_evidence.identifier,provider_receipt=last.get('native_receipt'),settled=True)
        if observation not in self.settled_failures:
            self.settled_failures.append(observation)
        self.response_evidence.record('strategy-settled-failure',**observation)
        count=sum(f['stage_id']==last['stage_id'] for f in self.settled_failures)
        if count<2:
            return False
        self.options.setdefault('failures',{})[last['stage_id']]='unsuitable'
        self.diagnostics.update(ai_failures=dict(self.options['failures']),ai_settled_failures=list(self.settled_failures))
        return True

    def _generate_once(self,packet,schema,*,expected_model=None):
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
        identity=str(uuid5(UUID(self.response_evidence.identifier),'strategy-provider-request-'+stage.id))
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
            self.request_history.append(dict(provider=self.backend,stage_id=stage.id,model=result[1],effort=stage.effort,
                evidence_id=self.response_evidence.identifier,native_receipt=self.diagnostics.get('provider_receipt'),values=self.diagnostics['usage']['values']))
            self.diagnostics.update(ai_decision=self.decision.model_dump(),actual_provider=self.backend,strategy_requests=list(self.request_history))
            if self.diagnostics.get('usage'):
                self.diagnostics['usage']={**self.diagnostics['usage'],'status':'complete','requests_observed':1,
                    'strategy_requests':[self.request_history[-1]]}
            self.record(identity,'completed',stage)
            return result
        except Waiting as error:
            self.diagnostics=dict(generation_state='not_started',ai_wait=error.decision)
            self.record(identity,'not_started',stage)
            raise
        except (PlanningUnavailable,ValueError,OSError) as error:
            from workbench.planning_provider import LocalBeforeGeneration
            facts=self.diagnostics
            if facts.get('generation_state')=='finished' and facts.get('accounting_state')=='complete' and isinstance(facts.get('usage',{}).get('values'),dict):
                self.request_history.append(dict(provider=self.backend,stage_id=stage.id,model=stage.model,effort=stage.effort,
                    evidence_id=self.response_evidence.identifier,native_receipt=facts.get('provider_receipt'),values=facts['usage']['values']))
            self.record(identity,'not_started' if isinstance(error,LocalBeforeGeneration) or isinstance(error,codex.Hold) and not error.started else 'interrupted_unknown' if facts.get('generation_state')=='uncertain' else 'failed',stage)
            raise

    def reported_usage(self,counted):
        return {**super().reported_usage(counted),'status':'complete'}

    def encode_request(self,model,effort,prompt,schema,seconds):
        request=encode_request(self.frozen,self.decision,prompt,schema,self.response_evidence.identifier,seconds,**self.options)
        return json.dumps(request,ensure_ascii=True,allow_nan=False).encode()

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
                if isinstance(usage,dict) and facts.get('generation_state') in {'uncertain','partial'}:
                    usage={**usage,'status':'partial','known_values':usage.get('values'),'values':None}
                if usage is None and isinstance(facts.get('native_known_subtotal'),dict):
                    usage=dict(status='partial',source='Native Claude observed subtotal; final usage unavailable',units='tokens',values=None,known_values=facts['native_known_subtotal'],observed_at=now())
                if outcome=='not_started':
                    usage=dict(status='not_started',requests_observed=0,source='Verified no native strategy request started',units='tokens',values=dict(prompt_tokens=0,completion_tokens=0,total_tokens=0))
                c.execute(model_attempts.update().where(model_attempts.c.id==identity).values(
                    outcome=outcome,finished_at=now(),actual_model=facts.get('actual_model'),actual_effort=facts.get('actual_effort'),usage=usage))
