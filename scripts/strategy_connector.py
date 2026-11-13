"""Smart choices at the existing native connector boundary, never a work queue."""
import json
from pathlib import Path
import select
import tempfile
import time
from uuid import UUID

from workbench import ai_strategy as ai, codex_provider as codex, claude_native as claude
from workbench import provider_admission as admission


class Wait(codex.Hold):
    def __init__(self, decision):
        self.decision=decision
        super().__init__('capacity',started=False)


def codex_observation(result):
    limits,bucket=codex.quota_bucket(result)
    if bucket['reason']:
        return ai.Observation(provider='codex')
    checks=codex.allowance_checks(result,cutoff=101)
    billing=[c for c in checks if c['check'] in {'credits','unlimited','credit_balance'}]
    disabled=bool(billing) and not any(c['reason'] for c in billing)
    ordinary=next(c for c in checks if c['check']=='ordinary_usage')
    windows=[]
    for name in ('primary','secondary'):
        window=limits.get(name)
        if window is None and name=='secondary' and not ordinary['reason']:
            continue  # Native ordinary-usage permission documents this absent window.
        window=window if isinstance(window,dict) else {}
        used=window.get('usedPercent')
        reset=window.get('resetsAt')
        windows.append(ai.Window(name=name,
            used_percent=used if type(used) is int and 0<=used<=100 else None,
            resets_at=reset if type(reset) is int and reset>0 else None))
    return ai.Observation(provider='codex',observed_at=time.time(),
        state='available' if not ordinary['reason'] else 'provider_limit' if ordinary['state']=='contrary' else 'unknown',
        paid_usage='disabled' if disabled else 'unknown',windows=windows,
        source='Native Codex account/rateLimits/read; account-wide subscription usage')


def claude_observation(snapshot):
    return ai.Observation(provider='claude',state='available',observed_at=snapshot['observed_at'],
        paid_usage=snapshot['paid_usage'],source=snapshot['source'],
        windows=[{k:v for k,v in w.items() if k!='precision'} for w in snapshot['windows']])


class Boundary:
    def __init__(self, connection, binary, credential, runtime, evidence_root, config_path, resolve):
        self.connection,self.binary,self.credential=connection,binary,credential
        self.runtime,self.evidence_root,self.config_path=runtime,Path(evidence_root),config_path
        self.resolve=resolve
        self.root=self.evidence_root.parent/'ai-allowances'
        self.root.mkdir(mode=0o700,exist_ok=True)

    def stop(self):
        return bool(select.select([self.connection],[],[],0)[0])

    def hosts(self,directory,provider,seconds=30):
        hosts=Path(directory)/'hosts'
        deadline=time.monotonic()+seconds
        names=(codex.BACKEND_HOST,) if provider=='codex' else ('api.anthropic.com','claude.ai')
        hosts.write_text(''.join(self.resolve(self.stop,deadline,n) for n in names),encoding='ascii')
        return hosts

    def observations(self,strategy):
        result=[ai.Observation(provider='local',state='available',observed_at=time.time(),
            paid_usage='disabled',source='Local resource eligibility is checked by the existing worker before dispatch')]
        for provider in dict.fromkeys(s.provider for s in strategy.stages if s.provider!='local'):
            try:
                with tempfile.TemporaryDirectory(prefix='ai-observe-',dir=self.runtime) as temporary:
                    hosts=self.hosts(temporary,provider)
                    if provider=='codex':
                        with codex.app_server(codex.command(self.binary,self.credential,hosts=hosts),
                                              self.stop,time.monotonic()+25) as rpc:
                            codex.initialize(rpc)
                            codex.require_checks(codex.account_checks(rpc.call('account/read',{'refreshToken':False})),'account/read')
                            observation=codex_observation(rpc.call('account/rateLimits/read',{}))
                    else:
                        config=claude.configuration(self.config_path)
                        work=Path(temporary)/'work'
                        work.mkdir(mode=0o700)
                        observation=claude_observation(claude.preflight(claude.command(config,work,hosts,terminal=True),self.stop))
                result.append(observation)
            except (codex.Hold,claude.Held,OSError,ValueError,TimeoutError) as error:
                reason=getattr(error,'reason','unavailable')
                state='authentication' if 'auth' in reason else 'provider_limit' if reason in {'capacity','provider_limit'} else 'unavailable'
                result.append(ai.Observation(provider=provider,state=state,observed_at=time.time(),
                    source='Native access check unavailable; no generation dispatched'))
        self.fresh=result
        return result

    def choose(self,request,strategy):
        with admission.exclusive(self.root,self.stop,seconds=45):
            state=admission.read(self.root/'allowances.json',dict(version=1,strategies={},active=None))
            if state.get('active'):
                return dict(state='held',reason='An earlier provider request needs settlement. No duplicate will be sent.')
            pool=admission.allowance_pool(state,strategy)
            fresh=self.observations(strategy)
            decision=ai.evaluate(strategy,fresh,[dict(stage_id=k,**v) for k,v in pool.items()],
                now=time.time(),**selection_options(request))
            admission.write(self.root/'latest.json',dict(observations=[o.model_dump() for o in fresh],decision=decision.model_dump()))
            return dict(state='choice',decision=decision.model_dump())

    def turn(self,request,strategy):
        def dispatch(stage):
            if stage.provider=='local':
                raise codex.Hold('protocol')  # Local service remains inside the worker's existing resource locks.
            if stage.provider=='claude':
                # The existing Claude adapter rechecks native /usage and credits immediately before print.
                from smart_connector import execute
                native_request={k:request[k] for k in ('request_id','prompt','schema','request_seconds')}
                native_request.update(operation='claude-turn',model=stage.model,effort=stage.effort)
                result=execute(native_request,self.runtime,self.config_path,self.resolve,self.stop,
                    before_dispatch=lambda snapshot:self.confirm(request,strategy,stage,claude_observation(snapshot)))
                return result,ai.Observation(provider='claude',state='available',paid_usage='disabled',
                    observed_at=time.time(),source='Native Claude Code rate_limit_event',
                    windows=[{k:v for k,v in w.items() if k in ai.Window.model_fields} for w in result.get('windows',[])])
            from workbench.codex_journal import Journal
            raw=codex.encode_request(stage.model,stage.effort,request['prompt'],request['schema'],request['request_seconds'])
            journal=Journal(self.evidence_root,raw)
            try:
                with tempfile.TemporaryDirectory(prefix='ai-codex-',dir=self.runtime) as temporary:
                    hosts=self.hosts(temporary,'codex')
                    with codex.app_server(codex.command(self.binary,self.credential,hosts=hosts,sessions=journal.sessions()),
                            self.stop,time.monotonic()+request['request_seconds']) as rpc:
                        rpc.evidence=journal.record
                        rpc.persistent_sessions=True
                        result=codex.turn(rpc,stage.model,stage.effort,request['prompt'],request['schema'],
                            on_admit=lambda snapshot:self.confirm(request,strategy,stage,codex_observation(snapshot)))
                        try:
                            after=codex_observation(rpc.call('account/rateLimits/read',{}))
                        except (codex.Hold,OSError,ValueError):
                            after=None
                result['provider_receipt']=journal.identifier
                result['actual_model']=stage.model
                result['actual_effort']=None
                return result,after
            finally:
                journal.record('connection-closed',status='local-producer-closed')
                journal.close()
        value=admission.run(self.root,request['request_id'],strategy,
            observations=lambda:self.observations(strategy),dispatch=dispatch,stop=self.stop,
            expected_stage=request['stage_id'],**selection_options(request))
        return value

    def confirm(self,request,strategy,stage,observation):
        # Called under the same serialized reservation, directly before native dispatch.
        admission_state=admission.read(self.root/'allowances.json',{})
        pool=admission.allowance_pool(admission_state,strategy)
        # The current request's conservative reservation is already counted; remove
        # it for its own eligibility recheck, never for a competing request.
        current={k:dict(v) for k,v in pool.items()}
        current[stage.id]['requests']-=1
        own=admission.read(self.root/(request['request_id']+'.json'),{})
        role='@'+stage.provider+':'+stage.role
        current[role]['requests']-=1
        if own['decision']['unknown_usage']:
            current[stage.id]['unknown_requests']-=1
            current[role]['unknown_requests']-=1
        fresh=[observation if o.provider==observation.provider else o for o in self.fresh]
        decision=ai.evaluate(strategy,fresh,[dict(stage_id=k,**v) for k,v in current.items()],
            now=time.time(),**selection_options(request))
        if decision.state!='selected' or decision.stage_id!=stage.id:
            raise Wait(decision.model_dump())


def selection_options(request):
    return {k:request[k] for k in ('suitable_local','reviewed_local','waiting_since','manual_stage','requested_stage')}


def handle(connection,request,binary,credential,runtime,evidence_root,config_path,resolve):
    result=None
    try:
        common={'operation','strategy','suitable_local','reviewed_local','waiting_since','manual_stage','requested_stage'}
        turn={'request_id','stage_id','prompt','schema','request_seconds'}
        if request.get('operation') not in {'smart-choose','smart-turn'} or set(request)!=(common|(turn if request['operation']=='smart-turn' else set())):
            raise ValueError('Unsupported strategy operation')
        strategy=ai.Strategy.model_validate(request['strategy'])
        if any(type(request[k]) is not bool for k in ('suitable_local','reviewed_local')):
            raise ValueError('Task suitability must be explicit')
        if (not isinstance(request['waiting_since'],dict) or set(request['waiting_since'])-{s.id for s in strategy.stages}
                or any(type(v) not in (int,float) or not 0<v<=time.time() for v in request['waiting_since'].values())
                or any(request[k] is not None and request[k] not in {s.id for s in strategy.stages} for k in ('manual_stage','requested_stage'))):
            raise ValueError('Unknown strategy stage')
        if request['operation']=='smart-turn':
            if (str(UUID(request['request_id']))!=request['request_id'] or request['stage_id'] not in {s.id for s in strategy.stages}
                    or not isinstance(request['prompt'],str) or len(request['prompt'].encode())>393216
                    or not isinstance(request['schema'],dict) or type(request['request_seconds']) is not int
                    or request['request_seconds'] not in (120,300)):
                raise ValueError('Unsupported bounded generation')
        boundary=Boundary(connection,binary,credential,runtime,evidence_root,config_path,resolve)
        result=boundary.choose(request,strategy) if request['operation']=='smart-choose' else boundary.turn(request,strategy)
    except Wait as error:
        result=dict(state='waiting',decision=error.decision)
    except claude.Held as error:
        result=dict(error='authentication' if 'auth' in error.reason else 'protocol',started=error.dispatched,
            terminal=error.terminal and error.usage_complete,counted=error.usage if error.usage_complete else None,
            known_subtotal=error.usage if not error.usage_complete else None)
    except codex.Hold as error:
        result=dict(error=error.reason,started=error.started,terminal=error.terminal,counted=error.counted,
                    partial=error.partial,**error.metadata)
    except (ValueError,TypeError,KeyError,OSError,TimeoutError):
        result=dict(error='unavailable',started=request.get('operation')=='smart-turn')
    try:
        connection.sendall(json.dumps(result,ensure_ascii=True,allow_nan=False).encode()+b'\n')
    except OSError:
        pass
