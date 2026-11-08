"""Measure the actual nested JSON envelope; never trade away mandatory context."""
import copy
import json

import pytest

from workbench.codex_provider import encode_request, SECONDS, BUG_SECONDS
from workbench.planning_transport import PlanningUnavailable
from workbench.request_context import fit
from workbench.source_memory import retain, requested_visibility


def context():
    path = 'tests/ordinary.lua'
    def window(line, text, coverage):
        return dict(path=path, line=line, through_line=line + len(text.splitlines())-1,
                    text=text, coverage=coverage)
    prelude = window(1, 'local module = {}', 'module prelude')
    requested = window(100, 'return module.run()', 'requested committed lines')
    declaration = window(99, 'function module.run()', 'committed enclosing declaration or registration')
    bootstrap = window(20, 'local client = createClient()', 'complete committed test bootstrap')
    older = window(400, '\n'.join('local s%d = "\\\\ quoted é"' % i for i in range(300)), 'prior read')
    packet = dict(base='committed', plan_review='source-grounded-lua-v1',
                  files=[dict(path=path, excerpts=[prelude])], inspection_windows=[older])
    current = [requested, declaration, bootstrap]
    wire = dict(files=copy.deepcopy(packet['files']), report='unchanged owner report',
                coverage=dict(write_paths=[path]), self_review=dict(decision='unapproved'))
    retain(wire, packet, current, 128000, requested=[requested])
    return packet, current, requested, wire


def encoder(wire):
    return encode_request('controlled', None, 'Instructions\n'+json.dumps(wire, ensure_ascii=True),
                          dict(description='large schema '+('"\\' * 400)), BUG_SECONDS)


def test_full_encoded_envelope_fits_preserving_requested_declarations_and_authority():
    packet, current, requested, wire = context()
    original = copy.deepcopy((packet, current, wire))
    limit = len(encoder(wire)) - 5000
    assert wire['source_memory']['retained_characters'] < limit < len(encoder(wire))
    packed, facts = fit(wire, packet, current, [requested], encoder, limit)
    assert len(encoder(packed)) <= limit
    assert facts['before_bytes'] > facts['encoded_request_bytes']
    assert facts['encoded_request_bytes'] == len(encoder(packed))
    assert facts['requested_source_preserved']
    assert (packet, current, wire) == original
    for key in ('report', 'coverage', 'self_review'):
        assert packed[key] == wire[key]
    protected = [dict(w, coverage='requested committed lines') for w in
                 [*current, dict(packet['files'][0]['excerpts'][0],path=packet['files'][0]['path'])]]
    assert all(f['provided'] for f in requested_visibility(packed, protected))
    assert packed['source_memory']['omitted_characters'] > 0


def test_already_fitting_request_is_unchanged():
    packet, current, requested, wire = context()
    packed, facts = fit(wire, packet, current, [requested], encoder, len(encoder(wire)))
    assert packed == wire and packed is not wire
    assert facts['before_bytes'] == facts['encoded_request_bytes']


@pytest.mark.parametrize('cancelled', [False, True])
def test_impossible_or_cancelled_context_never_discards_mandatory_source(cancelled):
    packet, current, requested, wire = context()
    with pytest.raises(PlanningUnavailable) as held:
        fit(wire, packet, current, [requested], encoder, 1, lambda: cancelled)
    assert held.value.reason == ('cancelled' if cancelled else 'context_budget')


@pytest.mark.parametrize('seconds', [SECONDS, BUG_SECONDS])
def test_shared_encoder_preserves_existing_protocol_bytes(seconds):
    prompt = 'untrusted "quotes" \\ and é\n'
    schema = dict(type='object', description='unchanged')
    legacy = dict(model='actual-selection', effort=None, prompt=prompt, schema=schema)
    if seconds != SECONDS:
        legacy['request_seconds'] = seconds
    assert encode_request('actual-selection', None, prompt, schema, seconds) == json.dumps(
        legacy, ensure_ascii=True, allow_nan=False).encode()
