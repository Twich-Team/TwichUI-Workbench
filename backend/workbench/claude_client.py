"""Claude adapter over the existing private, peer-checked provider connector."""
import json
import socket
from uuid import UUID

from workbench import codex_provider as codex


class Held(codex.Hold):
    def __init__(self, reason, **kwargs):
        self.native_known_subtotal=kwargs.pop('known_subtotal',None)
        super().__init__(reason,**kwargs)
        self.args=(str(self).replace('Codex','Claude'),)


def encode_request(model,effort,prompt,schema,request_id,request_seconds=120):
    return dict(operation='claude-turn',request_id=request_id,model=model,effort=effort,
                prompt=prompt,schema=schema,request_seconds=request_seconds)


def exchange(request, stop=lambda:False, seconds=120):
    path=codex.connector_path()
    if path is None:
        raise Held('unavailable')
    raw=json.dumps(request,ensure_ascii=True,allow_nan=False).encode()+b'\n'
    if len(raw)>codex.MAX_BUG_INPUT or stop():
        raise Held('canceled' if stop() else 'protocol')
    import time
    with socket.socket(socket.AF_UNIX) as connection:
        connection.settimeout(2)
        try:
            connection.connect(str(path))
        except OSError:
            raise Held('unavailable') from None
        if stop():
            raise Held('canceled')
        sent=False
        try:
            connection.sendall(raw)
            sent=True
            reader=codex.Channel(connection.fileno(),connection.sendall,stop,time.monotonic()+seconds+35)
            try:
                result=reader.receive()
            finally:
                reader.close()
        except OSError:
            raise Held('protocol',started=sent) from None
    if not isinstance(result,dict):
        raise Held('protocol',started=sent)
    if 'error' in result:
        raise Held(result['error'],started=result.get('started') is not False,
            terminal=result.get('terminal') is True,partial=result.get('partial') is True,
            counted=result.get('counted'),provider_receipt=result.get('provider_receipt'),
            known_subtotal=result.get('known_subtotal'),actual_model=result.get('actual_model'),
            terminal_status=result.get('terminal_status'))
    return result


def generate(model,effort,prompt,schema,stop=lambda:False,*,request_id,
             input_limit=codex.MAX_PACKET,request_seconds=codex.SECONDS):
    if str(UUID(request_id))!=request_id:
        raise ValueError('A frozen Claude request identity is required.')
    if input_limit not in (codex.MAX_PACKET,codex.MAX_BUG_INPUT) or request_seconds not in (120,300):
        raise ValueError('Unsupported Claude operation bounds.')
    if len(prompt.encode())>input_limit:
        raise ValueError('The approved Claude prompt exceeds its input bound.')
    result=exchange(encode_request(model,effort,prompt,schema,request_id,request_seconds),stop,request_seconds)
    if result.get('actual_model')!=model or not isinstance(result.get('usage'),dict):
        raise Held('model_identity',started=True,terminal=True)
    class Output(dict):
        pass
    output=Output(result['output'])
    output.provider_receipt=result['provider_receipt']
    return output,result['usage']


class Planner(codex.Planner):
    backend='claude'
    usage_source='Native Claude Code reported response tokens including cache reads and creation'

    def encode_request(self,model,effort,prompt,schema,seconds):
        request=encode_request(model,effort,prompt,schema,self.response_evidence.identifier,seconds)
        return json.dumps(request,ensure_ascii=True,allow_nan=False).encode()

    def dispatch(self,*args,**kwargs):
        if self.response_evidence is None:
            raise Held('disclosure')
        return generate(*args,request_id=self.response_evidence.identifier,**kwargs)
