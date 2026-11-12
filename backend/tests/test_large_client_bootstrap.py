"""Discover existing long fixture startup without executing or replacing it."""
import pytest
from pydantic import ValidationError

from workbench.bug_investigation import client_bootstrap
from workbench import bug_program, bug_evidence


def long_setup():
    return ['local c = MakeClient("Controlled", {})', *['-- native game inputs']*405,
        'for _, file in ipairs({"module.lua"}) do',
        '  local chunk = assert(loadfile(ROOT .. file))',
        '  chunk()', 'end', 'error("Discovery must never execute this startup")', '',
        'assert(c.result == "existing behavior")']


def test_long_committed_startup_is_a_bounded_syntax_checked_prefix():
    lines = long_setup()
    setup = client_bootstrap(lines)
    assert setup == dict(client='c', through_line=411)
    for model in (bug_program.Bootstrap, bug_evidence.ClientBootstrap):
        fields = dict(client='c', through_line=411)
        if model is bug_program.Bootstrap:
            fields.update(kind='client-bootstrap', test_path='tests/test_large.lua')
        assert model.model_validate(fields).through_line == 411
        fields['through_line'] = 601
        with pytest.raises(ValidationError):
            model.model_validate(fields)


def test_old_short_choice_is_exact_even_when_a_later_load_exists():
    lines = ['local c = MakeClient("Controlled", {})', 'local f = loadfile(ROOT .. "one.lua")',
        'f()', '', *['-- later test body']*400, 'loadfile(ROOT .. "two.lua")()', '']
    assert client_bootstrap(lines) == dict(client='c', through_line=3)


@pytest.mark.parametrize('fault', ['syntax', 'bytes', 'beyond_line_bound'])
def test_long_startup_cannot_escape_parse_or_size_bound(fault):
    lines = long_setup()
    if fault == 'syntax':
        lines.insert(1, 'if true then')
    elif fault == 'bytes':
        lines[1:406] = ['--'+'x'*200]*405
    else:
        lines[1:1] = ['-- move beyond boundary']*200
    assert client_bootstrap(lines) is None


def test_selected_setup_above_legacy_16kb_limit_compiles_without_losing_oracle_bound():
    from test_bug_program import event_evidence
    evidence = event_evidence()
    # A real selected client bootstrap includes game-input stubs. Exercise the
    # downstream evidence/compiler boundary too, not only source discovery.
    evidence['bootstrap_source'] = ('-- inspected game-input setup\n' * 700) + evidence['bootstrap_source']
    assert len(evidence['bootstrap_source']) > 16032
    evidence['review_digest'] = bug_evidence.review_digest(evidence['plan'], evidence['bootstrap_source'])
    result = bug_evidence.compile_plan(evidence)
    assert len(result['oracle'].encode()) < 65536
    assert bug_evidence.lua(evidence['bootstrap_source']) in result['oracle']
    evidence['bootstrap_source'] = '--' + 'x' * 65664
    evidence['review_digest'] = bug_evidence.review_digest(evidence['plan'], evidence['bootstrap_source'])
    with pytest.raises(ValidationError):
        bug_evidence.compile_plan(evidence)
