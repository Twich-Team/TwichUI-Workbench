"""Native Claude operations inside the existing provider connector peer boundary."""
import json
from pathlib import Path
import select
import tempfile
import time
from uuid import UUID

from workbench import claude_native as native, provider_admission as admission

REASONS=dict(authentication='authentication',authentication_or_allowance_unavailable='authentication',
    allowance_unavailable='accounting',billing_permission='credits',unexpected_billing='credits',
    unsupported_model_or_effort='model_identity',binary_changed='model_identity',
    canceled='canceled',timeout='timeout',input_limit='protocol',invalid_content='protocol',
    incomplete_response='protocol',attribution_or_usage_unavailable='accounting')


def execute(request,runtime,config_path,resolve,stop,*,before_dispatch=lambda snapshot:None):
    started=False
    receipt=None
    facts=[]
    config=None
    try:
        native.validate_selection(request['model'],request['effort'])
        config=native.configuration(config_path)
        root=Path(config_path).parent.parent/'artifacts/claude-evidence'
        root.mkdir(mode=0o700,exist_ok=True)
        receipt=root/(request['request_id']+'.json')
        # A stable evidence phase cannot dispatch twice after response loss/restart.
        with admission.exclusive(root,stop,seconds=45):
            if receipt.exists():
                raise native.Held('prior_request_needs_settlement',dispatched=True)
            deadline=time.monotonic()+request['request_seconds']
            mapping=''.join(resolve(stop,deadline,host) for host in ('api.anthropic.com','claude.ai'))
            def observe(phase,**values):
                nonlocal started
                if phase=='dispatch':
                    started=True
                    facts.append(dict(phase=phase,requested_model=request['model']))
                elif phase=='producer-closed':
                    facts.append(dict(phase=phase,returncode=values['returncode']))
                elif phase=='native-event':
                    event=values['event']
                    if event.get('type')=='result':
                        facts.append(dict(phase='native-result',subtype=event.get('subtype'),
                            is_error=event.get('is_error'),usage=native.native_usage(event.get('usage'))))
            with tempfile.TemporaryDirectory(prefix='claude-native-',dir=runtime) as temporary:
                directory=Path(temporary)
                work=directory/'work'
                work.mkdir(mode=0o700)
                hosts=directory/'hosts'
                hosts.write_text(mapping)
                snapshot=native.preflight(native.command(config,work,hosts,terminal=True),stop)
                before_dispatch(snapshot)
                if any(w['used_percent']>=100 for w in snapshot['windows']):
                    raise native.Held('provider_limit')
                admission.write(receipt,dict(state='dispatch_reserved',request_id=request['request_id'],
                    request_sha256=admission.digest(request),snapshot=snapshot,facts=[]))
                # Schema is task data, not a native tool grant. The existing local
                # semantic validators still validate this returned object.
                prompt=request['prompt']+'\nReturn only JSON matching this schema:\n'+json.dumps(request['schema'])
                result=native.turn(native.command(config,work,hosts,model=request['model'],effort=request['effort']),
                    prompt,request['model'],stop,seconds=request['request_seconds'],observe=observe)
                result['provider_receipt']=request['request_id']
                admission.write(receipt,dict(state='completed',request_id=request['request_id'],
                    request_sha256=admission.digest(request),snapshot=snapshot,result=result,facts=facts,
                    native_binary_sha256=config['binary_sha256'],
                    output_accepted=False,execution_authorized=False))
    except native.Held as error:
        completed=error.terminal and error.usage_complete
        if receipt and receipt.exists() and started:
            admission.write(receipt,dict(state='completed_rejected' if completed else 'unresolved',request_id=request.get('request_id'),
                reason=error.reason,facts=facts,known_subtotal=error.usage,
                usage=error.usage if completed else None,actual_model=request.get('model') if completed else None,
                aggregate_usage='complete' if completed else 'unavailable',
                native_binary_sha256=config['binary_sha256'],output_accepted=False,execution_authorized=False))
        raise
    return result


def handle(connection, request, runtime, config_path, resolve):
    """Caller has already passed the connector's fixed peer/cgroup checks."""
    try:
        if (set(request)!={'operation','request_id','model','effort','prompt','schema','request_seconds'}
                or request['operation']!='claude-turn' or str(UUID(request['request_id']))!=request['request_id']
                or not isinstance(request['prompt'],str) or len(request['prompt'].encode())>393216
                or not isinstance(request['schema'],dict) or type(request['request_seconds']) is not int
                or request['request_seconds'] not in (120,300)):
            raise native.Held('protocol')
        result=execute(request,runtime,config_path,resolve,
            lambda:bool(select.select([connection],[],[],0)[0]))
    except native.Held as error:
        completed=error.terminal and error.usage_complete
        result=dict(error='capacity' if error.reason=='provider_limit' else REASONS.get(error.reason,'protocol'),
            started=error.dispatched,terminal=completed,partial=error.dispatched and not completed,
            counted=error.usage if completed else None,known_subtotal=error.usage if not completed else None,
            **(dict(terminal_status='completed') if completed else {}))
    except (ValueError,OSError,KeyError,TypeError,TimeoutError):
        result=dict(error='unavailable',started=True,terminal=False)
    try:
        connection.sendall(json.dumps(result,ensure_ascii=True,allow_nan=False).encode()+b'\n')
    except OSError:
        pass
