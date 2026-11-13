"""Unmodified native Claude Code boundary. No OAuth-token or private HTTP client.

Called by the existing private connector, never from an HTTP handler. Native
authentication stays in the CLI. A terminal /usage read checks credits before
generation; stdout events are independently validated after generation.
"""
from datetime import datetime, timezone
from hashlib import sha256
import codecs
import fcntl
import json
import math
import os
from pathlib import Path
import pty
import re
import selectors
import signal
import stat
import struct
import subprocess
import termios
import time

import pyte

MODELS = {'claude-sonnet-5-5', 'claude-opus-5-5', 'claude-haiku-5-5'}
EFFORTS = {'low', 'medium', 'high', 'xhigh', 'max'}
MAX_WIRE = 2 << 20
BUILTINS = {'cc-plugin-agents-md','cc-plugin-privacy','cc-plugin-plugin-authoring'}


def builtin_plugins(values):
    return isinstance(values,list) and all(isinstance(v,dict) and set(v)=={'name','path','source'}
        and v['name'] in BUILTINS and v['path']=='builtin'
        and v['source']==v['name']+'@builtin' for v in values)


class Held(Exception):
    def __init__(self, reason, *, dispatched=False, terminal=False, usage=None, usage_complete=False,actual_model=None):
        self.reason, self.dispatched, self.terminal, self.usage = reason, dispatched, terminal, usage
        self.usage_complete=usage_complete
        self.actual_model=actual_model
        super().__init__('Claude '+reason.replace('_', ' ')+'.')


def validate_selection(model, effort):
    if model not in MODELS or effort is not None and effort not in EFFORTS:
        raise Held('unsupported_model_or_effort')


def private(path, *, directory=False):
    path = Path(path)
    s = path.lstat()
    if (path.resolve() != path or s.st_uid != os.getuid() or s.st_mode & 0o077
            or not (stat.S_ISDIR(s.st_mode) if directory else stat.S_ISREG(s.st_mode))):
        raise Held('private_configuration_unavailable')
    return path


def configuration(path):
    path = private(path)
    if path.stat().st_size > 4096:
        raise Held('private_configuration_unavailable')
    value = json.loads(path.read_bytes())
    if (set(value) != {'binary', 'binary_sha256', 'home', 'usage_credits', 'api_billing'}
            or value['usage_credits'] != 'off' or value['api_billing'] != 'denied'):
        raise Held('billing_permission')
    binary = Path(value['binary'])
    s = binary.lstat()
    if (binary.resolve() != binary or not stat.S_ISREG(s.st_mode) or s.st_uid != os.getuid()
            or s.st_mode & 0o022 or sha256(binary.read_bytes()).hexdigest() != value['binary_sha256']):
        raise Held('binary_changed')
    home = Path(value['home'])
    if home != Path.home() or home.resolve() != home:
        raise Held('authentication')
    # Validate filesystem ownership only. The application never reads credentials.
    private(home/'.claude/.credentials.json')
    private(home/'.claude.json')
    return value


def command(config, work, hosts, *, terminal=False, model=None, effort=None):
    """Empty filesystem/workspace, original native login mounted read-only.

    No owner config/settings/hooks/plugins/source mounted. Failed native refresh
    holds for native reauthentication; it cannot overwrite the owner's login.
    """
    home = Path(config['home'])
    if not terminal:
        validate_selection(model,effort)
    args = ['/usr/bin/bwrap', '--unshare-all', '--share-net', '--die-with-parent',
        '--cap-drop', 'ALL', '--clearenv', '--ro-bind', '/usr', '/usr',
        '--ro-bind', '/lib', '/lib', '--ro-bind', '/lib64', '/lib64',
        '--ro-bind', '/etc/ssl/certs', '/etc/ssl/certs',
        '--ro-bind', str(hosts), '/etc/hosts', '--proc', '/proc', '--dev', '/dev',
        '--tmpfs', '/tmp', '--tmpfs', '/native', '--tmpfs', '/native/.claude',
        '--ro-bind', str(home/'.claude/.credentials.json'), '/native/.claude/.credentials.json',
        '--ro-bind', str(home/'.claude.json'), '/native/.claude.json',
        '--ro-bind', config['binary'], '/claude', '--bind', str(work), '/work',
        '--chdir', '/work', '--setenv', 'HOME', '/native', '--setenv', 'PATH', '/usr/bin:/bin',
        '--setenv', 'LANG', 'C.UTF-8', '--setenv', 'TERM', 'xterm-256color']
    environment = {'DISABLE_AUTOUPDATER':'1', 'DISABLE_TELEMETRY':'1', 'DISABLE_ERROR_REPORTING':'1',
            'CLAUDE_CODE_DISABLE_BACKGROUND_TASKS':'1', 'CLAUDE_CODE_DISABLE_1M_CONTEXT':'1',
            'CLAUDE_CODE_DISABLE_ADVISOR_TOOL':'1', 'CLAUDE_CODE_MAX_RETRIES':'0',
            'CLAUDE_CODE_RETRY_WATCHDOG':'0'}
    # The native /usage command must be able to refresh account observations.
    if not terminal:
        environment['CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC']='1'
    for key, value in environment.items():
        args += ['--setenv', key, value]
    settings=dict(disableAllHooks=True,fastMode=False,autoContinueAtUsageLimit=False,forceLoginMethod='claudeai')
    if not terminal:
        settings.update(availableModels=[model],enforceAvailableModels=True)
    args += ['/claude', '--tools', '', '--strict-mcp-config', '--mcp-config', '{"mcpServers":{}}',
        '--setting-sources', '', '--settings', json.dumps(settings),
        '--no-chrome', '--permission-mode', 'dontAsk']
    if not terminal:
        args += ['-p', '--output-format', 'stream-json', '--verbose', '--max-turns', '1',
            '--disable-slash-commands', '--no-session-persistence', '--model', model]
        if effort is not None:
            args += ['--effort', effort]
    return args


def stop_child(child, first=signal.SIGTERM):
    if child.poll() is None:
        try:
            os.killpg(child.pid, first)
        except ProcessLookupError:
            child.wait(timeout=3)
            return
        try:
            child.wait(timeout=3)
        except subprocess.TimeoutExpired:
            os.killpg(child.pid, signal.SIGKILL)
            child.wait(timeout=3)


def usage_screen(text):
    """Read displayed native facts only; never treat missing credits as Off."""
    if re.search(r'Showing last.known|Refreshing|Loading|rate limited', text, re.I):
        return None
    if not re.search(r'Usage credits\s+Usage credits are off', text):
        return None
    windows = []
    for label, name in [('Current session', 'five_hour'), ('Current week (all models)', 'seven_day')]:
        match = re.search(re.escape(label)+r'\s+[^\n]*?(\d+)% used\s+Resets ([^\n]+)', text)
        if not match or not 0 <= int(match[1]) <= 100:
            return None
        windows.append(dict(name=name, used_percent=int(match[1]), reset_label=match[2].strip(),
                            resets_at=None, precision='native displayed percentage'))
    for family in ('opus','sonnet'):
        label=re.search(r'Current week \([^\n)]*'+family+r'[^\n)]*\)',text,re.I)
        if label:
            match=re.search(re.escape(label[0])+r'\s+[^\n]*?(\d+)% used\s+Resets ([^\n]+)',text)
            if not match or not 0<=int(match[1])<=100:
                return None
            windows.append(dict(name='seven_day_'+family,model_family=family,used_percent=int(match[1]),
                reset_label=match[2].strip(),resets_at=None,precision='native displayed percentage'))
    return dict(provider='claude', authentication='subscription', paid_usage='disabled',
        source='Native Claude Code /usage screen', observed_at=time.time(), windows=windows)


def preflight(argv, stop=lambda:False, *, seconds=20, observe=lambda *a, **k:None):
    """No model prompt: fixed native /usage, with hooks/tools disabled by argv."""
    screen = pyte.Screen(140, 70)
    stream = pyte.Stream(screen)
    decoder = codecs.getincrementaldecoder('utf-8')('replace')
    master, slave = pty.openpty()
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack('HHHH', 70, 140, 0, 0))
    child = None
    requested = trusted = False
    action_at = time.monotonic()+.5
    text = ''
    candidate = None
    last_output = time.monotonic()
    used = 0
    deadline = time.monotonic()+seconds
    try:
        child = subprocess.Popen(argv, stdin=slave, stdout=slave, stderr=slave,
                                 start_new_session=True, env={})
        os.close(slave)
        slave = None
        with selectors.DefaultSelector() as selector:
            selector.register(master, selectors.EVENT_READ)
            while child.poll() is None:
                if stop() or time.monotonic() >= deadline:
                    raise Held('canceled' if stop() else 'allowance_unavailable')
                if candidate is not None and time.monotonic()-last_output>=.75:
                    candidate['observed_at']=time.time()
                    observe('allowance-read',snapshot=candidate)
                    return candidate
                if time.monotonic()>=action_at:
                    if trusted == 'selecting':
                        os.write(master,b'\r')
                        trusted=True
                        action_at=time.monotonic()+.5
                    elif not trusted and 'Yes, I trust this folder' in text:
                        # Trust only this operation's empty /work directory.
                        os.write(master,b'\x1b[B')
                        trusted='selecting'
                        action_at=time.monotonic()+.25
                    elif not requested and ('Try ' in text or 'for shortcuts' in text
                            or 'Claude Pro' in text and re.search(r'^❯\s*$',text,re.M)):
                        os.write(master,b'/usage')
                        requested='typing'
                        action_at=time.monotonic()+.25
                    elif requested == 'typing':
                        os.write(master,b'\r')
                        requested=True
                if not selector.select(.1):
                    continue
                try:
                    chunk = os.read(master, 65536)
                except OSError:
                    break
                used += len(chunk)
                last_output=time.monotonic()
                if used > MAX_WIRE:
                    raise Held('protocol')
                stream.feed(decoder.decode(chunk))
                text = '\n'.join(screen.display)
                candidate=(usage_screen(text) if requested is True and 'Esc to cancel' in text else None)
                if re.search(r'Usage credits\s+(?:\d|Unlimited|\$)', text):
                    raise Held('billing_permission')
        raise Held('authentication_or_allowance_unavailable')
    finally:
        observe('preflight-screen', display=text)
        if child is not None:
            stop_child(child)
        os.close(master)
        if slave is not None:
            os.close(slave)


def native_usage(value):
    keys = ('input_tokens', 'cache_creation_input_tokens', 'cache_read_input_tokens', 'output_tokens')
    if not isinstance(value, dict) or any(type(value.get(k)) is not int or value[k]<0 for k in keys):
        return None
    prompt = sum(value[k] for k in keys[:-1])
    return dict(prompt_tokens=prompt, completion_tokens=value['output_tokens'],
        total_tokens=prompt+value['output_tokens'], cache_creation_tokens=value[keys[1]],
        cache_read_tokens=value[keys[2]])


def allowance_event(value):
    """Optional native structured readings; no token-derived percentage/reset."""
    result = []
    if not isinstance(value, dict):
        return result
    for name, window in value.get('unifiedWindows', {}).items():
        if name not in {'five_hour', 'seven_day', 'seven_day_opus', 'seven_day_sonnet'} or not isinstance(window, dict):
            continue
        percent, reset = window.get('utilization'), window.get('resetsAt')
        if type(percent) not in (float, int) or not math.isfinite(percent) or not 0 <= percent <= 1:
            percent = None
        if type(reset) is not int or reset <= 0:
            reset = None
        result.append(dict(name=name, used_percent=None if percent is None else round(percent*100, 6),
                           resets_at=reset,status=window.get('status','unknown'),
                           **(dict(model_family=name.rsplit('_',1)[1]) if name in {'seven_day_opus','seven_day_sonnet'} else {})))
    return result


class Response:
    """Native lifecycle, actual attribution and accounting; content parsed last."""
    def __init__(self, model):
        self.model, self.session, self.final = model, None, None
        self.initialized = self.response_started = False
        self.partial_usage, self.windows = None, []

    def event(self, event):
        if not isinstance(event, dict):
            raise Held('protocol', dispatched=True)
        session = event.get('session_id')
        if self.session and session and session != self.session:
            raise Held('identity_changed', dispatched=True)
        if event.get('type') == 'system' and event.get('subtype') == 'init':
            if (self.initialized or event.get('model') != self.model or event.get('tools') != []
                    or event.get('mcp_servers') != [] or event.get('apiKeySource') != 'none'
                    or not builtin_plugins(event.get('plugins',[])) or not session):
                raise Held('isolation_or_model_changed', dispatched=True)
            self.initialized, self.session = True, session
        elif event.get('type') == 'assistant':
            message = event.get('message', {})
            if message.get('model') != self.model:
                raise Held('identity_changed', dispatched=True)
            self.response_started = True
            self.partial_usage = native_usage(message.get('usage'))
            if any(c.get('type') == 'tool_use' for c in message.get('content', [])):
                raise Held('unexpected_tool', dispatched=True)
        elif event.get('type') == 'rate_limit_event':
            info = event.get('rate_limit_info', {})
            self.windows = allowance_event(info)
            if info.get('isUsingOverage') is True:
                raise Held('unexpected_billing', dispatched=True)
        elif event.get('type') == 'result':
            if self.final is not None or not self.initialized:
                raise Held('protocol', dispatched=True)
            self.final = event

    def result(self, exit_code):
        final = self.final
        if not final or exit_code != 0 or final.get('subtype') != 'success' or final.get('is_error') is not False:
            # The real native SIGINT result can claim aggregate zeros after tokens.
            # Retain the partial subtotal but never promote it to complete usage.
            raise Held('incomplete_response', dispatched=True, terminal=bool(final), usage=self.partial_usage,actual_model=self.model if self.initialized else None)
        models = final.get('modelUsage')
        usage = native_usage(final.get('usage'))
        if not isinstance(models, dict) or set(models) != {self.model} or usage is None:
            raise Held('attribution_or_usage_unavailable', dispatched=True, terminal=True, usage=self.partial_usage,actual_model=self.model if self.initialized else None)
        if not isinstance(final.get('result'), str):
            raise Held('invalid_content', dispatched=True, terminal=True, usage=usage,usage_complete=True,actual_model=self.model)
        try:
            content = json.loads(final['result'])
        except ValueError:
            raise Held('invalid_content', dispatched=True, terminal=True, usage=usage,usage_complete=True,actual_model=self.model) from None
        if not isinstance(content, dict):
            raise Held('invalid_content', dispatched=True, terminal=True, usage=usage,usage_complete=True,actual_model=self.model)
        return dict(output=content, usage=usage, actual_model=self.model, actual_effort=None,
            windows=self.windows, observed_at=datetime.now(timezone.utc).isoformat())


def turn(argv, prompt, model, stop=lambda:False, *, seconds=120, observe=lambda *a, **k:None):
    """One process, one native request, no replay or private provider endpoint."""
    response = Response(model)
    deadline = time.monotonic()+seconds
    raw = (prompt+'\n').encode()
    if len(raw)>393216 or stop():
        raise Held('canceled' if stop() else 'input_limit')
    child = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                             stderr=subprocess.DEVNULL, start_new_session=True, env={}, bufsize=0)
    sent, total, buffer = 0, 0, b''
    try:
        with selectors.DefaultSelector() as selector:
            os.set_blocking(child.stdin.fileno(), False)
            selector.register(child.stdin, selectors.EVENT_WRITE)
            selector.register(child.stdout, selectors.EVENT_READ)
            while selector.get_map():
                if stop() or time.monotonic()>=deadline:
                    stop_child(child, signal.SIGINT)
                    raise Held('canceled' if stop() else 'timeout', dispatched=sent>0,
                               usage=response.partial_usage,actual_model=response.model if response.initialized else None)
                for key, _ in selector.select(.1):
                    if key.fileobj is child.stdin:
                        sent += os.write(child.stdin.fileno(), raw[sent:])
                        if sent == len(raw):
                            selector.unregister(child.stdin)
                            child.stdin.close()
                            observe('dispatch', provider='claude', requested_model=model)
                    else:
                        chunk = os.read(child.stdout.fileno(), 65536)
                        if not chunk:
                            selector.unregister(child.stdout)
                            continue
                        total += len(chunk)
                        buffer += chunk
                        if total>MAX_WIRE:
                            raise Held('wire_limit', dispatched=True)
                        while b'\n' in buffer:
                            line, buffer = buffer.split(b'\n', 1)
                            try:
                                event = json.loads(line)
                            except ValueError:
                                raise Held('protocol', dispatched=True) from None
                            observe('native-event', event=event)
                            response.event(event)
        if buffer.strip():
            raise Held('incomplete_response', dispatched=True)
        return response.result(child.wait(timeout=max(.1, deadline-time.monotonic())))
    except (OSError, subprocess.SubprocessError):
        raise Held('transport', dispatched=sent>0, usage=response.partial_usage,actual_model=response.model if response.initialized else None) from None
    finally:
        stop_child(child)
        child.stdout.close()
        if not child.stdin.closed:
            child.stdin.close()
        observe('producer-closed', returncode=child.returncode)
