"""Compile an explicitly selected fixture setup, never infer missing plan data."""
import copy


def setup_schema(files, steps):
    sources = [f['path'] for f in files or [] if isinstance(f, dict)
               and isinstance(f.get('path'), str) and f['path'].startswith(('!!!TwichUI/', 'TwichUI_Settings/'))]
    source = dict(type='string', **({'enum': sources} if sources else {}))
    modules = [f['path'] for f in files or [] if isinstance(f, dict) and f.get('path') in sources
               and f.get('top_level_returns_value', True) is True]

    def variant(properties):
        return dict(type='object', properties=properties, required=list(properties), additionalProperties=False)

    generic = [variant(dict(kind=dict(type='string', const='client-loads'),
                    sources=dict(type='array', items=source, minItems=1, maxItems=6),
                    before_load=dict(type='array', minItems=0, maxItems=4, items=dict(anyOf=[
                        ref for op, ref in steps if op in {'table', 'set', 'stub', 'capture'}]))))]
    if modules or not files:
        generic.append(variant(dict(kind=dict(type='string', const='module'),
            source=dict(type='string', **({'enum': modules} if modules else {})))))
    variants = []
    for item in files or []:
        setup = item.get('client_bootstrap') if isinstance(item, dict) else None
        if setup:
            variants.append(variant(dict(kind=dict(type='string', const='client-bootstrap'),
                test_path=dict(type='string', const=item['path']),
                client=dict(type='string', const=setup['client']),
                through_line=dict(type='integer', const=setup['through_line']))))
    return dict(anyOf=[*variants, *generic])


def compile_setup(raw, files):
    """Serialize only the provider's explicit choice into the existing program.

    Setup runs before the listed actions; observation indexes name actions.
    Required reads are the union of explicit file selections and other_reads.
    Expected results, behavior, write scope and permission are never filled.
    Legacy complete data programs remain valid; a missing setup stays missing.
    """
    from workbench.bug_evidence import Observation, Step
    from workbench.planning_response import ResponseValidationError
    from pydantic import ValidationError

    if not isinstance(raw, dict) or not isinstance(raw.get('plan'), dict):
        return raw, None
    original = raw['plan'].get('reproducer')
    if not isinstance(original, dict) or 'setup' not in original:
        if 'other_reads' in raw['plan']:
            raise ResponseValidationError('Additional reads require explicit fixture setup.',
                ['plan', 'other_reads'], 'an explicit setup with derived read scope', None)
        return raw, None

    def reject(field, expected, value):
        raise ResponseValidationError('The explicitly selected fixture setup is invalid.',
            ['plan', 'reproducer', 'setup', field], expected, value)

    def validate(model, value, location):
        try:
            return model.model_validate(value)
        except ValidationError as error:
            issues = error.errors(include_input=True, include_url=False)
            for issue in issues:
                issue['loc'] = (*location, *issue['loc'])
            raise ValidationError.from_exception_data(model.__name__, issues) from None

    if 'fixture' in original or 'bootstrap' in original:
        reject('kind', 'one explicit setup without conflicting legacy setup fields', None)
    if 'read_paths' in raw['plan'] or 'other_reads' not in raw['plan']:
        raise ResponseValidationError('Use explicit file selections and additional reads.',
            ['plan', 'other_reads'], 'other_reads without a conflicting legacy read_paths field', None)
    setup = original['setup']
    if not isinstance(setup, dict):
        reject('kind', 'a declared committed fixture setup', setup)
    selected = {f['path']: f for f in files or [] if isinstance(f, dict) and isinstance(f.get('path'), str)}
    kind = setup.get('kind')
    if not isinstance(kind, str):
        reject('kind', 'one declared setup kind', None)
    prefix, bootstrap = [], None
    if kind == 'client-bootstrap':
        if set(setup) != {'kind', 'test_path', 'client', 'through_line'}:
            reject('kind', 'exact committed bootstrap selection fields', None)
        if not isinstance(setup.get('test_path'), str):
            reject('test_path', 'one supplied committed test path', None)
        item = selected.get(setup.get('test_path'))
        wanted = dict(client=setup['client'], through_line=setup['through_line'])
        if (not item or not item.get('client_bootstrap') or wanted != item['client_bootstrap']
                or type(setup['through_line']) is not int):
            reject('test_path', 'one supplied exact committed client bootstrap', setup.get('test_path'))
        fixture = 'client'
        bootstrap = dict(test_path=setup['test_path'], **wanted)
    elif kind in {'module', 'client-loads'}:
        allowed = {'kind', 'source'} if kind == 'module' else {'kind', 'sources', 'before_load'}
        if set(setup) != allowed:
            reject('kind', 'exact explicit source-loading fields', None)
        paths = [setup['source']] if kind == 'module' else setup['sources']
        if (not isinstance(paths, list) or not 1 <= len(paths) <= 6
                or any(not isinstance(p, str) for p in paths) or len(set(paths)) != len(paths)
                or any(p not in selected or not p.startswith(('!!!TwichUI/', 'TwichUI_Settings/')) for p in paths)):
            reject('sources', 'one to six distinct supplied addon sources', paths)
        if kind == 'module' and selected[paths[0]].get('top_level_returns_value', True) is not True:
            reject('source', 'a source with a compiled top-level value return; callable type remains to be checked', paths[0])
        fixture = 'module' if kind == 'module' else 'client'
        before = [] if kind == 'module' else setup['before_load']
        if not isinstance(before, list) or len(before) > 4:
            reject('before_load', 'at most four explicit fixture preparation steps', None)
        for index, entry in enumerate(before):
            step = validate(Step, entry, ('plan', 'reproducer', 'setup', 'before_load', index))
            if step.op not in {'table', 'set', 'stub', 'capture'}:
                reject('before_load', 'table, set, stub or capture before loading', step.op)
            prefix.append(step.model_dump(mode='json'))
        prefix.extend(dict(op='load', source=p) for p in paths)
    else:
        reject('kind', 'module, client-loads or client-bootstrap', kind)
    actions, observations = original.get('steps'), original.get('observations')
    if not isinstance(actions, list) or not isinstance(observations, list):
        reject('steps', 'explicit action and observation arrays', None)
    checked = [validate(Observation, o, ('plan', 'reproducer', 'observations', i)).model_dump(mode='json')
               for i, o in enumerate(observations)]
    for observation in checked:
        observation['after_step'] += len(prefix)
    result = copy.deepcopy(raw)
    plan = result['plan']
    extra = plan.pop('other_reads')
    writes, focused = plan.get('writable_paths'), plan.get('focused_test')
    if (not isinstance(extra, list) or len(extra) > 12 or not isinstance(writes, list)
            or not isinstance(focused, str) or any(not isinstance(p, str) for p in [*extra, *writes])):
        raise ResponseValidationError('Invalid explicitly selected read scope.',
            ['plan', 'other_reads'], 'bounded inspected paths and explicit write/test selections', None)
    reads = [s.get('source') for s in [*prefix, *actions] if isinstance(s, dict) and s.get('op') == 'load']
    reads += ([bootstrap['test_path']] if bootstrap else []) + writes + [focused] + extra
    if any(not isinstance(p, str) or p not in selected for p in reads):
        raise ResponseValidationError('An explicitly selected file was not inspected.',
            ['plan', 'other_reads'], 'only inspected committed paths in every file selection', None)
    plan['read_paths'] = list(dict.fromkeys(reads))
    target = result['plan']['reproducer']
    target.pop('setup')
    target.update(fixture=fixture, bootstrap=bootstrap, steps=[*prefix, *actions], observations=checked)
    return result, dict(kind=kind, explicit_setup=True, prepended_steps=len(prefix),
                        derived_read_count=len(plan['read_paths']),
                        approved=False, executed=False)
