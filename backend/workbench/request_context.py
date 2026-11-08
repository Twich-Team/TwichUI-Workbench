"""Fit the exact encoded provider request without changing task or approval data."""
import copy

from workbench.planning_transport import PlanningUnavailable
from workbench.source_memory import retain, requested_visibility


def fit(wire, packet, current, requested, encode, limit, stop=lambda:False):
    result = copy.deepcopy(wire)
    before = len(encode(result))
    budget = result.get('source_memory', {}).get('retained_characters', 0)
    protected = [*requested]
    protected += [dict(w, coverage='requested committed lines') for w in current
                  if w.get('coverage') in {'complete committed test bootstrap',
                                          'committed enclosing declaration or registration'}]
    for item in packet['files']:
        protected += [dict(w, path=item['path'], coverage='requested committed lines')
                      for w in item.get('excerpts', [])
                      if w.get('coverage') in {'module prelude', 'partial module prelude',
                                              'committed test-support prelude'}]
    for _ in range(16):
        if stop():
            raise PlanningUnavailable('Request preparation was stopped.', 'cancelled')
        size = len(encode(result))
        if size <= limit:
            requested_visibility(result, protected)
            return result, dict(before_bytes=before, encoded_request_bytes=size,
                request_byte_limit=limit, source_characters_retained=result.get('source_memory',{}).get('retained_characters',0),
                requested_source_preserved=True)
        if budget <= 0:
            break
        budget = max(0, budget-max(256, size-limit))
        retain(result, packet, current, budget, requested=requested)
        result.setdefault('inspection_result', {})['visible_requested_windows'] = requested_visibility(result, requested)
    raise PlanningUnavailable('The complete request cannot fit while preserving required source.', 'context_budget')
