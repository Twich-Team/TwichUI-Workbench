"""Bounded, non-executable fixture facts; never retain prose or scalar strings."""
import json
import re

from workbench.grounded_scenarios import LITERALS


def fixture_snapshot(value, files, summary):
    plan = value.get('plan') if isinstance(value, dict) else None
    fixture = plan.get('reproducer') if isinstance(plan, dict) else None
    if not isinstance(fixture, dict):
        return None
    checkpoint_facts = None
    if 'checkpoints' in fixture:
        points = fixture['checkpoints']
        checkpoint_facts = dict(wire_format='call-checkpoints',
            checkpoint_shape=summary(points), legacy_conflict='steps' in fixture or 'observations' in fixture,
            checkpoint_calls=[])
        fixture = dict(fixture, steps=[], observations=[])
        if isinstance(points, list):
            for index, point in enumerate(points[:4]):
                if not isinstance(point, dict):
                    checkpoint_facts['checkpoint_calls'].append(dict(index=index,shape=summary(point)))
                    continue
                before = point.get('before_call')
                if isinstance(before, list):
                    fixture['steps'].extend(before[:12])
                if 'call' in point:
                    fixture['steps'].append(point['call'])
                observed = point.get('observe')
                checkpoint_facts['checkpoint_calls'].append(dict(index=index,
                    call_step=len(fixture['steps']), before_call_shape=summary(before),
                    call_present='call' in point, observation_shape=summary(observed)))
                fixture['observations'].append(dict(observed, after_step=len(fixture['steps']))
                    if isinstance(observed, dict) else observed)
    paths, identifiers = set(), set()
    for item in files or []:
        if not isinstance(item, dict):
            continue
        if isinstance(item.get('path'), str):
            paths.add(item['path'])
        for excerpt in item.get('excerpts', []):
            text = excerpt.get('text', '') if isinstance(excerpt, dict) else ''
            if isinstance(text, str):
                masked = LITERALS.sub('', text)
                identifiers.update(re.findall(r'\b[A-Za-z_]\w{0,39}\b', masked))
        setup = item.get('client_bootstrap')
        if isinstance(setup, dict) and isinstance(setup.get('client'), str):
            identifiers.add(setup['client'])

    def path(value):
        if not isinstance(value, list):
            return summary(value)
        return [p if type(p) is int and 1 <= p <= 512 or isinstance(p, str) and p in identifiers
                else summary(p) for p in value[:8]]

    def source(value):
        return value if isinstance(value, str) and value in paths else summary(value)

    def scalar(value):
        row = summary(value)
        if type(value) in (int, float) and -2**31 <= value <= 2**31:
            row['value'] = value
        return row

    result = dict(executable=False, approved=False, scalar_strings_redacted=True,
                  steps=[], observations=[], truncated=False)
    if checkpoint_facts:
        result.update(checkpoint_facts)
    setup = fixture.get('setup')
    if isinstance(setup, dict):
        result['setup'] = dict(kind=setup.get('kind') if isinstance(setup.get('kind'), str) and setup.get('kind') in {
            'module', 'client-loads', 'client-bootstrap'} else summary(setup.get('kind')))
        for field in ('source', 'test_path'):
            if field in setup:
                result['setup'][field] = source(setup[field])
        if isinstance(setup.get('sources'), list):
            result['setup']['sources'] = [source(p) for p in setup['sources'][:6]]
        result['setup']['before_load_count'] = len(setup.get('before_load', [])) if isinstance(setup.get('before_load', []), list) else None
    for field, limit in (('steps', 24), ('observations', 4)):
        entries = fixture.get(field)
        if not isinstance(entries, list):
            result[field+'_shape'] = summary(entries)
            continue
        result[field+'_count'] = len(entries)
        for index, item in enumerate(entries[:limit]):
            row = dict(index=index)
            if not isinstance(item, dict):
                row['shape'] = summary(item)
            elif field == 'steps':
                op = item.get('op')
                row['op'] = op if isinstance(op, str) and op in {'load', 'table', 'set', 'stub', 'capture', 'call'} else summary(op)
                row['path'] = path(item.get('path', []))
                if 'source' in item:
                    row['source'] = source(item['source'])
                if 'value' in item:
                    row['value'] = scalar(item['value'])
                if isinstance(item.get('args'), list):
                    row['args'] = [dict(ref=path(v['ref'])) if isinstance(v, dict) and set(v)=={'ref'}
                                   else scalar(v) for v in item['args'][:8]]
                row['fields_count'] = len(item.get('fields', [])) if isinstance(item.get('fields', []), list) else None
                row['method'] = item.get('method') if type(item.get('method')) is bool else False
            else:
                after = item.get('after_step')
                row['after_step'] = after if type(after) is int and 0 <= after <= 2**31 else summary(after)
                kind = item.get('source')
                row['source'] = kind if isinstance(kind, str) and kind in {'return', 'field', 'captured'} else summary(kind)
                row.update(path=path(item.get('path')), expected=scalar(item.get('expected')),
                           incorrect=scalar(item.get('incorrect')),
                           expected_present='expected' in item, incorrect_present='incorrect' in item)
            # Preserve observations even if a hostile/large step list exhausts
            # its share. Both halves and the final event remain bounded.
            if len(json.dumps(result[field]+[row]).encode()) > 2400:
                result['truncated'] = True
                break
            result[field].append(row)
    if len(json.dumps(result).encode()) > 6000:
        return dict(executable=False, approved=False, truncated=True,
                    shape=summary(fixture))
    return result
