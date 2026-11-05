"""One read contract and content-free diagnostics for the bounded real-task tools."""
import json

from workbench.real_execution import PATHS

READ_LINES = 250
MAX_LINE = 2**31-1
READ_ARGUMENT_BYTES = 2048
TOOL_ARGUMENT_BYTES = 131072  # Existing edit_file may contain up to64 KiB escaped source.
TOOL_NAMES = ('read_file', 'edit_file', 'replace_text', 'run_check', 'read_check_log', 'find_text')
CHECK_NAMES = ('syntax-data','syntax-recorder','focused','candidate')
READ_DESCRIPTION = (
    'Read an allowed source file using 1-based inclusive start/end lines. '
    'start defaults to1; omitted end is min(start+249,2147483647), up to250 lines. '
    'Require integer lines1..2147483647, end>=start, end-start<250; at most2048 UTF8 argument bytes. End clips at EOF; '
    'start beyond EOF returns empty text. Preserve whitespace. '
    'At most45000 source characters per attempt; repeated reads count again. '
    'Tool replies over8 KiB are explicitly truncated; request fewer lines then. '
    'At most two safe read-input errors receive structured feedback in the same attempt; a third stops. '
)
READ_INSTRUCTION = (
    'read_file uses 1-based inclusive lines, at most250 per call. '
    'Omit end to read up to250 lines starting at start (default1). '
    'EOF may return fewer lines or empty text. Read smaller windows if a reply is truncated. '
    'At most two safe read-input errors may be corrected using ordinary counted turns; third terminates. '
    'Security, exhausted budgets and infrastructure failures are terminal.'
)
READ_PARAMETERS = dict(type='object', additionalProperties=False, required=['path'], properties={
    'path': dict(type='string', enum=list(PATHS)),
    'start': dict(type='integer', minimum=1, maximum=MAX_LINE, default=1,
                  description='First line, 1-based inclusive; default1.'),
    'end': dict(type='integer', minimum=1, maximum=MAX_LINE,
                description='Last line, inclusive. Omit for min(start+249,2147483647). '
                            'Must be >=start and <=start+249. May exceed EOF.')})


class ToolInputError(ValueError):
    """No side effect; feedback only under the explicit bounded read policy."""
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)

    def as_result(self, correction_number=None, tool=None):
        if self.code == 'check_log_input' or tool == 'read_check_log':
            return dict(ok=False,error=dict(code=self.code,message=str(self),correctable=True,continuation_enabled=correction_number is not None,correction_number=correction_number,contract=dict(tool='read_check_log',indexing='1-based inclusive',start_default=1,end_default='start+39',max_lines=80,column_default=0,eof='clips',scope='only check_id returned in this attempt')))
        if self.code == 'search_input' or tool == 'find_text':
            return dict(ok=False,error=dict(code=self.code,message=str(self),correctable=True,continuation_enabled=correction_number is not None,correction_number=correction_number,contract=dict(tool='find_text',matching='literal text',max_query_utf8_bytes=160,max_matches=12,scope='exact allowed source paths only')))
        if self.code == 'check_name':
            return dict(ok=False,error=dict(code=self.code,message=str(self),correctable=True,
                continuation_enabled=correction_number is not None,correction_number=correction_number,
                contract=dict(tool='run_check',allowed_names=list(getattr(self,'allowed_checks',CHECK_NAMES)),
                              arbitrary_commands=False)))
        if self.code == 'replace_match':
            return dict(ok=False,error=dict(code=self.code,message=str(self),correctable=True,
                continuation_enabled=correction_number is not None,correction_number=correction_number,
                contract=dict(tool='replace_text',matching='old must occur exactly once',
                              max_old_plus_new_utf8_bytes=8192,
                              correction='Read current allowed content and supply an exact short old string')))
        return dict(ok=False, error=dict(code=self.code, message=str(self),
                    correctable=True, continuation_enabled=correction_number is not None,
                    correction_number=correction_number,
                    contract=dict(indexing='1-based-inclusive', min_line=1, max_line=MAX_LINE,
                                  max_lines=READ_LINES, start_default=1, end_must_be_at_least_start=True,
                                  eof='clips; empty beyond EOF', end_default='min(start+249,2147483647)')))


class ToolDenied(ValueError):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


class ToolBudgetExceeded(ValueError):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


def read_range(args, paths=PATHS):
    if not isinstance(args, dict):
        raise ToolInputError('argument_object', 'Read arguments must be a JSON object')
    if 'path' not in args:
        raise ToolInputError('missing_path', 'read_file requires path from its enum')
    if args['path'] not in paths:
        raise ToolDenied('path_denied', 'Path outside exact allowlist')
    if set(args)-{'path', 'start', 'end'}:
        raise ToolInputError('unknown_fields', 'read_file accepts only path, start and end')
    start = args.get('start', 1)
    if type(start) is not int or not 1 <= start <= MAX_LINE:
        raise ToolInputError('start_line', 'start must be an integer from1 to2147483647')
    end = args.get('end', min(MAX_LINE, start+READ_LINES-1))
    if type(end) is not int or not 1 <= end <= MAX_LINE:
        raise ToolInputError('end_line', 'end must be an integer from1 to2147483647')
    if end < start:
        raise ToolInputError('reversed_range', 'end must be >=start (inclusive lines)')
    if end-start >= READ_LINES:
        raise ToolInputError('read_window', 'Read at most 250 lines per tool call')
    return start, end


def parse_arguments(name, raw):
    if name not in TOOL_NAMES:
        raise ToolDenied('tool_denied', 'Unknown bounded tool')
    if not isinstance(raw, str):
        raise ToolInputError('argument_json', 'Tool arguments must be JSON text')
    cap = READ_ARGUMENT_BYTES if name in ('read_file','read_check_log','find_text') else TOOL_ARGUMENT_BYTES
    if len(raw) > cap or len(raw.encode('utf-8', errors='replace')) > cap:
        raise ToolBudgetExceeded('argument_limit', 'Tool argument byte limit exceeded')
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ToolInputError('duplicate_field', 'Duplicate JSON fields are invalid')
            result[key] = value
        return result
    def invalid_constant(_):
        raise ToolInputError('argument_json', 'Nonstandard JSON number refused')
    try:
        args = json.loads(raw, object_pairs_hook=pairs, parse_constant=invalid_constant)
    except (ValueError, RecursionError) as error:
        if isinstance(error, ToolInputError):
            raise
        raise ToolInputError('argument_json', 'Malformed JSON arguments') from None
    if not isinstance(args, dict):
        raise ToolInputError('argument_object', 'Tool arguments must be a JSON object')
    return args


def diagnostic(name, raw, args, turn, index, outcome, error=None, paths=PATHS, checks=CHECK_NAMES):
    """No raw paths, IDs, keys, strings, code, hashes or exception text retained.

    Only fixed vocabulary and bounded integers cross into the evidence record.
    A rejected path may contain a secret; even that value is deliberately omitted.
    """
    event = dict(turn=turn, call_index=index, tool=name if name in TOOL_NAMES else 'unsupported',
                 outcome=outcome, continuation_enabled=False,
                 argument_bytes=min(len(raw[:TOOL_ARGUMENT_BYTES+1].encode('utf-8', errors='replace')), TOOL_ARGUMENT_BYTES+1)
                 if isinstance(raw, str) else None)
    if isinstance(args, dict):
        if name == 'run_check':
            event['check'] = args.get('name') if args.get('name') in checks else 'unsupported'
        event['field_count'] = len(args)
        event['path'] = list(paths).index(args['path']) if args.get('path') in paths else (
            'missing' if 'path' not in args else 'denied')
        for field in ('start', 'end'):
            if field not in args:
                event[field] = dict(kind='omitted')
            else:
                value = args[field]
                kind = ('integer' if type(value) is int else 'boolean' if type(value) is bool
                        else 'null' if value is None else 'string' if isinstance(value, str)
                        else 'number' if isinstance(value, float) else 'container')
                event[field] = dict(kind=kind)
                if type(value) is int:
                    event[field]['value'] = value if abs(value) <= MAX_LINE else 'outside_integer_bound'
    if error:
        event['error_class'] = ('input' if isinstance(error, ToolInputError) else
                                'security' if isinstance(error, ToolDenied) else
                                'budget' if isinstance(error, ToolBudgetExceeded) else 'execution')
        event['error_code'] = error.code if isinstance(error, (ToolInputError, ToolDenied, ToolBudgetExceeded)) else 'tool_execution_failed'
    return event


def invoke(tools, call, turn, index, on_tool=None, allowed_names=None):
    """Same parser/validator ingress for native and scripted responses; fail closed."""
    name = raw = args = None
    error = None
    try:
        function = call.get('function') if isinstance(call, dict) else None
        if not isinstance(function, dict):
            raise ToolInputError('call_envelope', 'Malformed tool function envelope')
        name, raw = function.get('name'), function.get('arguments')
        if allowed_names is not None and name not in allowed_names:
            raise ToolDenied('tool_phase_denied','Tool is unavailable in the current bounded work phase')
        args = parse_arguments(name, raw)
        return tools.handle(name, args)
    except Exception as caught:
        error = caught
        raise
    finally:
        if on_tool:
            runtime=getattr(tools,'runtime',None)
            paths=getattr(runtime,'read_paths',PATHS)
            checks=(*runtime.tool_commands(),'candidate') if hasattr(runtime,'tool_commands') else CHECK_NAMES
            on_tool(diagnostic(name, raw, args, turn, index, 'failed' if error else 'completed', error, paths, checks))
