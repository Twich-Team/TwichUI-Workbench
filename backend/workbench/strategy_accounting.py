"""Controller totals stay separate from individually attributed native requests."""
from workbench.ai_strategy import Strategy


def complete_requests(usage,payload):
    records=usage.get('strategy_requests') if isinstance(usage,dict) else None
    if not isinstance(records,list) or len(records)!=usage.get('requests_observed',1) or not records:
        return False
    routes=[payload.get('local_planning_receipt',{}).get(key,{}) for key in ('codex_route','local_route')]
    routes += [p.get('route',{}) for p in [*payload.get('manual_ai_history',[]),payload.get('manual_ai') or {}]]
    allowed=[s for route in routes if route.get('strategy') for s in Strategy.model_validate(route['strategy']).stages]
    totals=dict(prompt_tokens=0,completion_tokens=0,total_tokens=0)
    seen=set()
    for record in records:
        if (record.get('evidence_id') in seen or not record.get('evidence_id')
                or not any((s.id,s.provider,s.model,s.effort)==(record.get('stage_id'),record.get('provider'),record.get('model'),record.get('effort')) for s in allowed)):
            return False
        seen.add(record['evidence_id'])
        values=record.get('values',{})
        if any(type(values.get(k)) is not int or values[k]<0 for k in totals) or values['prompt_tokens']+values['completion_tokens']>values['total_tokens']:
            return False
        for key in totals:
            totals[key]+=values[key]
    return all(usage.get('values',{}).get(k)==v for k,v in totals.items())


def combine(usage,payload):
    """Retired settled prefixes are counted once by the final logical operation."""
    if not isinstance(usage,dict) or usage.get('status')!='complete':
        return usage
    previous=[p['usage'] for p in payload.get('ai_resumed_phases',[]) if p['usage'].get('requests_observed')]
    if not previous:
        return usage
    parts=[*previous,usage]
    result={**usage,'requests_observed':sum(p.get('requests_observed',1) for p in parts),
        'values':{key:sum(p['values'][key] for p in parts) for key in ('prompt_tokens','completion_tokens','total_tokens')},
        'strategy_requests':[r for p in parts for r in p.get('strategy_requests',[])]}
    for key in ('max_request_total_tokens','max_request_completion_tokens'):
        if all(type(p.get(key)) is int for p in parts):
            result[key]=max(p[key] for p in parts)
    return result
