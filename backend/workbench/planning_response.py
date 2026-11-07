"""Strict planning decoding and bounded private evidence, with no retry authority."""
from datetime import datetime, timezone
import hashlib
import json
import math
import os
import re
import stat
from uuid import UUID

MAX_EVIDENCE = 65536


class ResponseValidationError(ValueError):
    """Public fixed message, private exact field/constraint/observed structure."""
    def __init__(self, message, path, expected, observed):
        super().__init__(message)
        self.path, self.expected, self.observed = path, expected, observed


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def strict_json(raw):
    class Pairs(list):
        pass
    try:
        tree = json.loads(raw, object_pairs_hook=Pairs, parse_constant=lambda _: float('nan'))
    except RecursionError:
        raise ResponseValidationError('JSON nesting limit exceeded', [], 'depth at most 32', None) from None
    def convert(node, path=()):
        if len(path) > 32:
            raise ResponseValidationError('JSON nesting limit exceeded', path, 'depth at most 32', None)
        if isinstance(node, Pairs):
            result = {}
            for key, value in node:
                if key in result:
                    raise ResponseValidationError('Duplicate JSON field', (*path, key), 'unique object key', value)
                if any(0xD800 <= ord(char) <= 0xDFFF for char in key):
                    raise ResponseValidationError('Invalid JSON Unicode key', (*path, 'invalid-key'), 'Unicode scalar text', key)
                result[key] = convert(value, (*path, key))
            return result
        if isinstance(node, list):
            return [convert(value, (*path, index)) for index, value in enumerate(node)]
        if isinstance(node, float) and not math.isfinite(node):
            raise ResponseValidationError('Nonfinite JSON number', path, 'finite number', node)
        if isinstance(node, str) and any(0xD800 <= ord(char) <= 0xDFFF for char in node):
            raise ResponseValidationError('Invalid JSON Unicode text', path, 'Unicode scalar text', node)
        return node
    return convert(tree)


def payload(raw, *, bug=False):
    """Only remove an unambiguous outer formatting wrapper, never infer content."""
    if not isinstance(raw, str):
        raise ResponseValidationError('Expected one JSON text response', ['choices', 0, 'message', 'content'], 'JSON text string', raw)
    changes = []
    text = raw.strip()
    if text.startswith('\ufeff'):
        text = text[1:].strip()
        changes.append('leading-bom')
    match = re.fullmatch(r'```(?:json)?[ \t]*\r?\n(.*?)\r?\n```', text, re.DOTALL)
    if match:
        text = match[1]
        changes.append('single-json-fence')
    value = strict_json(text)
    if not isinstance(value, dict):
        raise ResponseValidationError('Expected one JSON object', [], 'JSON object', value)
    if bug:
        return normalize_bug(value, changes)
    return value, changes


def normalize_bug(value, changes=()):
    changes = list(changes)
    if isinstance(value, dict) and set(value) == {'finding', 'draft'}:
        draft = value['draft']
        if isinstance(draft, dict) and set(draft) in ({'plan', 'question', 'limitation'},
                                                    {'plan', 'question', 'limitation', 'inspection'}):
            value = dict(finding=value['finding'], **draft)
            changes.append('unwrap-outcome-draft')
    if isinstance(value, dict):
        # Empty *present* optional alternatives carry no meaning. Missing fields,
        # missing plan content, numeric strings and conflicting outcomes still fail.
        for field in ('question', 'limitation'):
            if field in value and isinstance(value[field], str) and not value[field].strip():
                value[field] = None
                changes.append('empty-' + field)
    return value, changes


def wire_schema(model, coverage=None, files=None):
    result = model.model_json_schema()
    if model.__name__ != 'BugPlanningResponse':
        return result
    # The provider grammar cannot execute Python validators. Express Step's
    # shape rules in its supported object/array/alternative schema subset too.
    original = result['$defs']['Step']
    alternatives = []
    for op in ('load', 'table', 'set', 'stub', 'capture', 'call'):
        variant = json.loads(json.dumps(original))
        props = variant['properties']
        props['op'] = {'const': op, 'type': 'string'}
        if op == 'load':
            props['source'] = props['source']['anyOf'][0]
            props['path'] = {'const': [], 'type': 'array'}
            variant['required'] = ['op', 'source']
        else:
            props['source'] = {'const': None, 'type': 'null'}
            if op != 'call':
                props['path']['minItems'] = 1
                variant['required'] = ['op', 'path']
        if op not in ('stub', 'capture', 'call'):
            props['args']['maxItems'] = 0
        if op not in ('stub', 'capture'):
            props['fields']['maxItems'] = 0
        if op != 'set':
            props['value'] = {'const': None, 'type': 'null'}
        if op not in ('capture', 'call'):
            props['method'] = {'const': False, 'type': 'boolean'}
        allowed = {'op'} | {
            'load': {'source'}, 'table': {'path'}, 'set': {'path', 'value'},
            'stub': {'path', 'args', 'fields'}, 'capture': {'path', 'args', 'fields', 'method'},
            'call': {'path', 'args', 'method'},
        }[op]
        variant['properties'] = {key: value for key, value in props.items() if key in allowed}
        if op == 'set':
            variant['required'] = ['op', 'path', 'value']
        if op in ('stub', 'capture'):
            for excluded in ('args', 'fields'):
                branch = json.loads(json.dumps(variant))
                branch['properties'].pop(excluded)
                alternatives.append(branch)
        else:
            alternatives.append(variant)
    result['$defs']['Step'] = {'anyOf': alternatives}
    props = result['properties']
    outcomes = []
    fields = ('plan', 'question', 'limitation', 'inspection')
    selected_fields = ('inspection',) if isinstance(coverage, dict) and coverage.get('require_inspection') else fields
    for selected in selected_fields:
        choice = {}
        for field in fields:
            choice[field] = (next(v for v in props[field]['anyOf'] if v.get('type') != 'null')
                             if field == selected else {'type': 'null', 'const': None})
        outcomes.append(dict(type='object', properties=choice,
                             required=list(fields), additionalProperties=False))
    result['$defs']['BugOutcome'] = {'anyOf': outcomes}
    if isinstance(coverage, dict) and type(coverage.get('minimum_observations')) is int and coverage['minimum_observations'] == 3:
        result['$defs']['Reproducer']['properties']['observations']['minItems'] = 3
    if isinstance(files, list):
        selected = [item['path'] for item in files if isinstance(item, dict) and isinstance(item.get('path'), str)]
        plan_reads = [path for path in selected if path.startswith(('!!!TwichUI/', 'TwichUI_Settings/', 'tests/test_'))]
        writable = [item['path'] for item in files if isinstance(item, dict) and isinstance(item.get('path'), str) and not item.get('support_only')]
        sources = [path for path in selected if path.startswith(('!!!TwichUI/', 'TwichUI_Settings/'))]
        tests = [path for path in selected if path.startswith('tests/test_')]
        if selected and writable and sources and tests:
            plan = result['$defs']['BugPlan']['properties']
            plan['read_paths']['items'] = {'type': 'string', 'enum': plan_reads}
            plan['writable_paths']['items'] = {'type': 'string', 'enum': writable}
            plan['focused_test'] = {'type': 'string', 'enum': tests}
            for variant in alternatives:
                if variant['properties']['op']['const'] == 'load':
                    variant['properties']['source'] = {'type': 'string', 'enum': sources}
    # A generated plan must explicitly select its executable fixture setup.
    # Optional bootstrap/implicit loads repeatedly produced unusable proposals.
    from workbench.bug_wire import setup_schema
    references = []
    for index, variant in enumerate(alternatives):
        name = f'FixtureStep{index}'
        result['$defs'][name] = variant
        references.append((variant['properties']['op']['const'], {'$ref': '#/$defs/'+name}))
    result['$defs']['Step'] = {'anyOf': [ref for _, ref in references]}
    reproducer = result['$defs']['Reproducer']
    reproducer['properties'].pop('fixture')
    reproducer['properties'].pop('bootstrap')
    reproducer['properties']['setup'] = setup_schema(files, references)
    reproducer['properties'] = {key: reproducer['properties'][key]
                                for key in ('description', 'setup', 'steps', 'observations')}
    reproducer['required'] = ['description', 'setup', 'steps', 'observations']
    # The installed decoder emits object properties in schema order. Select
    # setup before repeating its files in the read scope or choosing a test.
    plan = result['$defs']['BugPlan']
    plan['properties'] = {key: plan['properties'][key]
                          for key in ('scope', 'reproducer', 'read_paths', 'writable_paths', 'focused_test')}
    result['properties'] = {'finding': props['finding'], 'draft': {'$ref': '#/$defs/BugOutcome'}}
    result['required'] = ['finding', 'draft']
    def compact(node):
        if isinstance(node, dict):
            return {key: ({name: compact(child) for name, child in value.items()}
                          if key in ('properties', '$defs') else compact(value))
                    for key, value in node.items() if key not in ('title', 'default')}
        if isinstance(node, list):
            return [compact(value) for value in node]
        return node
    return compact(result)


class Evidence:
    """Structure-only private journal. No prompts, source text, tokens or keys."""
    def __init__(self, root, attempt, model, coverage=None, files=None):
        from workbench.trusted_preparation import owned

        self.schema = wire_schema(model, coverage, files)
        self.schema_hash = sha(json.dumps(self.schema, sort_keys=True).encode())
        self.keys = {'choices', 'message', 'content', 'model', 'usage', 'id', 'tool_calls',
                     'finish_reason', 'prompt_tokens', 'completion_tokens', 'total_tokens'}
        self.enums, self.patterns = set(), set()
        def collect(node):
            if isinstance(node, dict):
                self.keys.update(node.get('properties', {}))
                self.keys.update(node.get('$defs', {}))
                self.enums.update(v for v in node.get('enum', []) if isinstance(v, str))
                if isinstance(node.get('const'), str):
                    self.enums.add(node['const'])
                if isinstance(node.get('pattern'), str):
                    self.patterns.add(node['pattern'])
                for v in node.values():
                    collect(v)
            elif isinstance(node, list):
                for v in node:
                    collect(v)
        collect(self.schema)
        collect(model.model_json_schema())  # Names in the compiled data program are safe too.
        self.identifier = str(UUID(attempt))
        base = owned(root)
        for name in ('artifacts', 'runs'):
            base = base / name
            base.mkdir(mode=0o700, exist_ok=True)
            owned(base)
        self.directory = base / ('planning-evidence-' + self.identifier)
        self.directory.mkdir(mode=0o700)
        self.file = self.directory / 'events.jsonl'
        self.fd = os.open(self.file, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        self.identity = os.fstat(self.fd).st_ino
        self.count = 0
        try:
            self.record('schema', version=1, model=model.__name__, schema_sha256=self.schema_hash,
                        schema_bytes=len(json.dumps(self.schema).encode()),
                        schema=self.schema,
                        control='response_format.json_schema.schema; local validators remain authoritative')
        except BaseException:
            self.close()
            raise

    def close(self):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None

    def record(self, phase, **facts):
        info = os.fstat(self.fd)
        if (info.st_uid != os.getuid() or not stat.S_ISREG(info.st_mode)
                or info.st_ino != self.identity or info.st_mode & 0o077 or info.st_nlink != 1):
            raise OSError('Private planning evidence identity changed')
        raw = (json.dumps(dict(time=datetime.now(timezone.utc).isoformat(), sequence=self.count,
                               phase=phase, **facts), allow_nan=False) + '\n').encode()
        if self.count >= 16 or len(raw) > 16384 or info.st_size + len(raw) > MAX_EVIDENCE:
            raise OSError('Private planning evidence limit reached')
        offset = 0
        while offset < len(raw):
            offset += os.write(self.fd, raw[offset:])
        os.fsync(self.fd)
        self.count += 1

    def location(self, path):
        # Known schema fields are exact; untrusted keys use a stable identity.
        return [part if type(part) is int or part in self.keys else
                'redacted-key-sha256:' + sha(str(part).encode()) for part in path[:12]]

    def summary(self, value):
        result = {'type': type(value).__name__}
        if isinstance(value, (str, dict, list)):
            result['length'] = len(value)
        if isinstance(value, str):
            result['sha256'] = sha(value.encode('utf-8', 'surrogatepass'))
            result['excerpt'] = value if value in self.enums and len(value) <= 80 else '<redacted>'
        elif value is None or type(value) is bool:
            result['value'] = value
        return result

    def received(self, raw, *, complete=True):
        # Preserve JSON framing without retaining any quoted source or token text,
        # including an unterminated string in a malformed response.
        out, quoted, escaped = [], False, False
        for char in raw[:8192].decode('utf-8', 'replace'):
            if quoted:
                if escaped:
                    escaped = False
                elif char == '\\':
                    escaped = True
                elif char == '"':
                    out.append('"')
                    quoted = False
            elif char == '"':
                out.append('"<redacted>')
                quoted = True
            elif char in '{}[]:, \n\r\t':
                out.append(char)
            elif not out or out[-1] != '.':
                out.append('.')
            if sum(map(len, out)) > 512:
                break
        self.record('wire-received', bytes=len(raw), sha256=sha(raw), bounded_prefix=not complete,
                    format_excerpt=''.join(out)[:512], excerpt_contains_values=False)

    def envelope(self, result, diagnostics):
        identity = result.get('id')
        self.record('envelope', response_identity=(sha(identity.encode()) if isinstance(identity, str) else None),
                    response_id=identity if isinstance(identity, str) and re.fullmatch(r'chatcmpl-[A-Za-z0-9_-]{1,96}', identity) else None,
                    completion=diagnostics.get('finish_reason'),
                    generation_state=diagnostics.get('generation_state'), usage=diagnostics.get('usage'),
                    accounting_state=diagnostics.get('accounting_state'),
                    model_matches=diagnostics.get('model_matches'))

    def parsed(self, value, diagnostics, changes=()):
        structure = []
        structure_bytes = 0
        def visit(node, path=()):
            nonlocal structure_bytes
            if len(structure) >= 80 or len(path) > 8:
                return
            row = dict(path=self.location(path), **self.summary(node))
            if (type(node) is int and 0 <= node <= 2**31-1 and path
                    and path[-1] in {'start', 'end', 'after_step', 'through_line'}
                    and all(type(part) is int or part in self.keys for part in path)):
                row['value'] = node
            size = len(json.dumps(row).encode())
            if structure_bytes + size > 7000:
                return
            structure.append(row)
            structure_bytes += size
            if isinstance(node, dict):
                for key, item in list(node.items())[:16]:
                    visit(item, (*path, key))
            elif isinstance(node, list):
                for index, item in enumerate(node[:24]):
                    visit(item, (*path, index))
        visit(value)
        self.record('payload-decoded', shape=self.summary(value),
                    sha256=sha(json.dumps(value, sort_keys=True, allow_nan=False).encode()),
                    normalization=list(changes), generation_state=diagnostics.get('generation_state'),
                    usage=diagnostics.get('usage'), structure=structure,
                    outcome_shapes={key: self.summary(value[key]) if key in value else {'type': 'missing'}
                                    for key in ('plan', 'question', 'limitation', 'inspection')} if isinstance(value, dict) else {})

    def rejected(self, error, value=None, *, phase='validation'):
        errors = []
        if isinstance(error, ResponseValidationError):
            errors.append(dict(path=self.location(error.path), constraint='scope_or_binding',
                               expected=error.expected, observed=self.summary(error.observed)))
        if hasattr(error, 'errors'):
            for issue in error.errors(include_input=True, include_context=True, include_url=False)[:4]:
                context = {k: v for k, v in issue.get('ctx', {}).items()
                           if k in {'min_length', 'max_length', 'actual_length', 'gt', 'ge', 'lt', 'le',
                                    'multiple_of', 'field_type'} and type(v) in (int, float, bool)}
                extra = issue.get('ctx', {})
                if isinstance(extra.get('pattern'), str) and extra['pattern'] in self.patterns:
                    context['pattern'] = extra['pattern']
                literals = re.findall(r"'([^']*)'", extra['expected']) if isinstance(extra.get('expected'), str) else []
                if literals and all(value in self.enums for value in literals):
                    context['enum'] = literals
                expected = {'string_type': 'string', 'int_type': 'integer', 'bool_type': 'boolean',
                            'list_type': 'array', 'dict_type': 'object', 'model_type': 'object',
                            'missing': 'required field', 'extra_forbidden': 'field must be absent'}
                errors.append(dict(path=self.location(issue['loc']), constraint=issue['type'],
                                   expected=context or expected.get(issue['type'], 'declared schema or local semantic validator'),
                                   observed=({'type': 'missing'} if issue['type'] == 'missing' else self.summary(issue.get('input')))))
        facts = dict(error_type=type(error).__name__, errors=errors,
                     message_sha256=sha(str(error).encode()), value=self.summary(value))
        from workbench import bug_evidence, bug_investigation
        functions = [bug_evidence.Step.shape, bug_evidence.Reproducer.validate_reproducer,
                     bug_evidence.BugPlanningResponse.one_outcome, bug_investigation.resolve]
        rules = {v for function in functions for v in function.__code__.co_consts if isinstance(v, str)}
        message = str(error)
        for issue in (error.errors(include_input=False) if hasattr(error, 'errors') else []):
            candidate = str(issue.get('ctx', {}).get('error', ''))
            if candidate in rules:
                facts.setdefault('semantic_rules', []).append(candidate)
        if message in rules:
            facts['semantic_rules'] = [message]
        if isinstance(error, json.JSONDecodeError):
            facts.update(line=error.lineno, column=error.colno, position=error.pos)
        self.record(phase + '-rejected', **facts)


def create_evidence(attempt, schema, coverage=None, files=None):
    from workbench import everyday
    if not everyday.enabled():
        return None
    return Evidence(everyday.ROOT, attempt, schema, coverage, files)
