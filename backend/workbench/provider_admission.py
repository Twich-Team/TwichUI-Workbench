"""Serialized request admission and usage projection in the existing connector.

The planning/execution journals still own work and recovery. This owns only
allowance reservations and native observations; it does not schedule or retry.
"""
from contextlib import contextmanager
from hashlib import sha256
import fcntl
import json
import os
from pathlib import Path
import stat
import time
from uuid import UUID, uuid4

from workbench import ai_strategy as policy


def canonical(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False)


def digest(value):
    return sha256(canonical(value).encode()).hexdigest()


def read(path, default):
    if not path.exists():
        return default
    info=path.lstat()
    if (path.resolve()!=path or not stat.S_ISREG(info.st_mode) or info.st_uid!=os.getuid()
            or info.st_mode&0o077 or info.st_size>2<<20):
        raise ValueError('Private provider admission evidence changed.')
    return json.loads(path.read_bytes())


def write(path, value):
    raw=canonical(value).encode()
    if len(raw)>2<<20:
        raise ValueError('Provider admission projection exceeded its storage bound.')
    if path.exists() or path.is_symlink():
        read(path,None)
    temporary=path.with_name('.'+path.name+'.'+uuid4().hex)
    fd=os.open(temporary,os.O_CREAT|os.O_EXCL|os.O_WRONLY|os.O_NOFOLLOW,0o600)
    try:
        with os.fdopen(fd,'wb') as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary,path)
        directory=os.open(path.parent,os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        temporary.unlink(missing_ok=True)


@contextmanager
def exclusive(root, stop=lambda:False, seconds=10):
    root=Path(root)
    info=root.lstat()
    if root.resolve()!=root or not stat.S_ISDIR(info.st_mode) or info.st_uid!=os.getuid() or info.st_mode&0o077:
        raise ValueError('Private connector directory required.')
    fd=os.open(root/'admission.lock',os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
    try:
        info=os.fstat(fd)
        if info.st_uid!=os.getuid() or info.st_mode&0o077:
            raise ValueError('Provider admission lock changed.')
        until=time.monotonic()+seconds
        while True:
            if stop() or time.monotonic()>=until:
                raise TimeoutError('Another Workbench request is using the provider boundary.')
            try:
                fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
                break
            except BlockingIOError:
                time.sleep(.05)
        yield
    finally:
        os.close(fd)


def observed_delta(before, after, current):
    """Conservatively include outside consumption; reset never invents a delta."""
    result=dict(current)
    end={w.name:w for w in after.windows} if after else {}
    for window in before.windows:
        prior=result.get(window.name,{})
        previous=prior.get('consumed',0) if prior.get('reset')==window.resets_at else 0
        value=end.get(window.name)
        delta=(value.used_percent-window.used_percent if value is not None
            and value.resets_at==window.resets_at and value.used_percent is not None
            and window.used_percent is not None and value.used_percent>=window.used_percent else None)
        result[window.name]=dict(reset=window.resets_at,
            consumed=previous+delta if previous is not None and delta is not None else None)
    return result


def budget_key(strategy):
    # Models/effort and display-name edits cannot replenish a stage's reserve.
    return digest([dict(id=s.id,provider=s.provider,allocation=s.allocation,role=s.role) for s in strategy.stages])


def allowance_pool(state,strategy):
    # Cumulative role reserves survive card/model changes and frozen task variants.
    stages=state.get('stages',{})
    values={s.id:stages.get(s.provider+':'+s.role+':'+s.id,{}) for s in strategy.stages}
    values.update({'@'+key:value for key,value in state.get('roles',{}).items()})
    return values


def save_consumption(state,pool,stage,usage,role):
    pool[stage.id]=usage.model_dump(exclude={'stage_id'})
    state.setdefault('stages',{})[stage.provider+':'+stage.role+':'+stage.id]=pool[stage.id]
    state.setdefault('roles',{})[stage.provider+':'+stage.role]=role.model_dump(exclude={'stage_id'})


def run(root, request_id, strategy, *, observations, dispatch, stop=lambda:False,
        waiting_since=None, failures=None, suitable_local=False, reviewed_local=False, manual_stage=None,
        expected_stage=None, requested_stage=None, now=time.time):
    """Fresh read -> admission -> exactly one dispatch -> observation under one lock.

    observations() uses native supported reads. dispatch(stage) returns its real
    result and a final Observation, or raises with consumption left unresolved.
    This function never retries a dispatched request, even after process restart.
    """
    root=Path(root)
    if str(UUID(request_id))!=request_id:
        raise ValueError('An exact native request identity is required.')
    strategy=policy.Strategy.model_validate(strategy)
    key=budget_key(strategy)
    receipt=root/(request_id+'.json')
    with exclusive(root,stop):
        if receipt.exists():
            # Results and terminal reconciliation belong to the existing native
            # journal reader, not a second execution or a cached-output shortcut.
            return dict(state='held',reason='This native request already has a dispatch receipt. Reconcile its saved result before continuing.')
        state=read(root/'allowances.json',dict(version=1,strategies={},active=None))
        if state.get('version')!=1 or state.get('active'):
            return dict(state='held',reason='An earlier provider dispatch needs native settlement. No duplicate was sent.')
        pool=allowance_pool(state,strategy)
        fresh=[policy.Observation.model_validate(o) for o in observations()]
        decision=policy.evaluate(strategy,fresh,[dict(stage_id=k,**v) for k,v in pool.items()],
            now=now(),waiting_since=waiting_since,failures=failures,suitable_local=suitable_local,
            reviewed_local=reviewed_local,manual_stage=manual_stage,requested_stage=requested_stage)
        write(root/'latest.json',dict(observations=[o.model_dump() for o in fresh],decision=decision.model_dump()))
        if decision.state!='selected':
            return dict(state='waiting',decision=decision.model_dump())
        if expected_stage is not None and decision.stage_id!=expected_stage:
            return dict(state='reselect',decision=decision.model_dump())
        if stop():
            return dict(state='held',reason='The task stopped before provider dispatch.')
        stage=next(s for s in strategy.stages if s.id==decision.stage_id)
        before=next(o for o in fresh if o.provider==stage.provider)
        usage=policy.StageConsumption.model_validate(dict(stage_id=stage.id,**pool.get(stage.id,{})))
        role=policy.StageConsumption.model_validate(dict(stage_id='@'+stage.provider+':'+stage.role,**pool.get('@'+stage.provider+':'+stage.role,{})))
        usage.requests+=1
        role.requests+=1
        if decision.unknown_usage:
            usage.unknown_requests+=1
            role.unknown_requests+=1
        save_consumption(state,pool,stage,usage,role)
        state['strategies'][key]={k:v for k,v in pool.items() if not k.startswith('@')}
        state['active']=dict(request_id=request_id,strategy=key,stage=stage.id,provider=stage.provider)
        admission=dict(version=1,request_id=request_id,strategy_sha256=key,
            stage=stage.model_dump(),decision=decision.model_dump(),before=before.model_dump(),
            admitted_at=now(),state='dispatch_reserved')
        # Reserve durably before sending even one byte. Lost/late outcomes hold.
        write(receipt,admission)
        write(root/(request_id+'.admitted.json'),admission)
        write(root/'allowances.json',state)
        try:
            result,after=dispatch(stage)
            after=policy.Observation.model_validate(after) if after is not None else None
            if after is not None and after.provider!=stage.provider:
                raise ValueError('Native provider observation changed identity.')
            usage.windows=observed_delta(before,after,usage.windows)
            role.windows=observed_delta(before,after,role.windows)
            admission.update(state='completed',after=after.model_dump() if after else None,
                overrun=[dict(window=w.name,used_percent=w.used_percent,cutoff=decision.cutoff)
                         for w in after.windows if decision.cutoff is not None and w.used_percent is not None
                         and w.used_percent>decision.cutoff] if after else [],finished_at=now())
            save_consumption(state,pool,stage,usage,role)
            state['strategies'][key]={k:v for k,v in pool.items() if not k.startswith('@')}
            state['active']=None
            write(receipt,admission)
            write(root/'allowances.json',state)
            final=[after if after is not None and o.provider==after.provider else o for o in fresh]
            write(root/'latest.json',dict(observations=[o.model_dump() for o in final],decision=decision.model_dump(),overrun=admission['overrun']))
            return dict(state='completed',result=result,admission=admission)
        except BaseException as error:
            # Do not clear a reservation merely because a local caller failed.
            # Existing native settlement/recovery must establish the outcome.
            admission.update(state='unresolved',last_observed_at=now())
            write(receipt,admission)
            from workbench import claude_native, codex_provider
            not_started=(isinstance(error,claude_native.Held) and error.dispatched is False
                         or isinstance(error,codex_provider.Hold) and error.started is False)
            if not_started:
                usage.requests-=1
                role.requests-=1
                if decision.unknown_usage:
                    usage.unknown_requests-=1
                    role.unknown_requests-=1
                save_consumption(state,pool,stage,usage,role)
                state['strategies'][key]={k:v for k,v in pool.items() if not k.startswith('@')}
                state['active']=None
                admission.update(state='not_started')
                write(receipt,admission)
                write(root/'allowances.json',state)
            raise
