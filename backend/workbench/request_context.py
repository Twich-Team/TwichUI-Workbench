"""Fit the exact encoded provider request without changing task or approval data."""
import copy

from workbench.planning_transport import PlanningUnavailable
from workbench.source_memory import retain, requested_visibility


def fit(wire, packet, current, requested, encode, limit, stop=lambda:False, *, measure=None):
    result = copy.deepcopy(wire)
    before = len(encode(result))
    budget = result.get('source_memory', {}).get('retained_characters', 0)
    protected = [*requested]
    protected += [dict(w, coverage='requested committed lines') for w in current
                  if w.get('coverage') in {'complete committed test bootstrap',
                                          'committed enclosing declaration or registration',
                                          'committed public usage example'}]
    for item in packet['files']:
        protected += [dict(w, path=item['path'], coverage='requested committed lines')
                      for w in item.get('excerpts', [])
                      if w.get('coverage') in {'module prelude', 'partial module prelude',
                                              'committed test-support prelude'}]
    required_lines={}
    for window in protected:
        for offset,line in enumerate(window['text'].splitlines()):
            key=(window['path'],window['line']+offset)
            if key in required_lines and required_lines[key]!=line:
                raise ValueError('Required committed source lines disagree.')
            required_lines[key]=line
    minimum=sum(len(line)+1 for line in required_lines.values())
    for _ in range(16):
        if stop():
            raise PlanningUnavailable('Request preparation was stopped.', 'cancelled')
        size = len(encode(result))
        measured = measure(result) if size <= limit and measure is not None else None
        if size <= limit and (measured is None or measured[0] <= measured[1]):
            requested_visibility(result, protected)
            return result, dict(before_bytes=before, encoded_request_bytes=size,
                request_byte_limit=limit, source_characters_retained=result.get('source_memory',{}).get('retained_characters',0),
                requested_source_preserved=True,
                **(dict(prompt_tokens=measured[0],prompt_token_limit=measured[1]) if measured is not None else {}))
        if budget <= 0:
            break
        # Bytes and model tokens are different constraints. Recount the exact
        # envelope after trimming optional history; never trim required reads.
        excess = max(size-limit, (measured[0]-measured[1])*4 if measured is not None else 0)
        smaller=max(minimum,budget-max(256, excess))
        if smaller>=budget:
            break
        budget=smaller
        retain(result, packet, [*current, *protected], budget, requested=protected,
            focus_paths=set(packet.get('inspection_focus',[])) or {w['path'] for w in requested})
        result.setdefault('inspection_result', {})['visible_requested_windows'] = requested_visibility(result, requested)
    raise PlanningUnavailable('The complete request cannot fit while preserving required source.', 'context_budget')
