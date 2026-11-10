"""Controlled execution-path regressions; no real provider or addon answer."""
import json
from types import SimpleNamespace

import pytest

from workbench.implementation_context import instruction, ReadProgress
from workbench.real_provider import implement, BudgetError
from workbench.real_workspace import Tools
from workbench.real_execution import PATHS
from test_real_execution import Transport, answer


def test_current_approved_brief_excludes_obsolete_planning_instructions():
    references = '\nUntrusted pinned public game API declarations: [{"function":"ControlledAPI"}]'
    linked = dict(task=dict(title='A supported bug', acceptance=['Check the reported behavior']),
        original=dict(description='Original plain-language report'), approved_scope=['One bounded repair'],
        proposal_review=dict(content=dict(behavior='Expected behavior', value='Source-grounded hypothesis',
            uncertainty=['Runtime cause unproven'], exclusions=['No publication'])))
    runtime = SimpleNamespace(recipe=dict(repair_cycle='scoped-repair-v1',
        instruction=json.dumps(dict(feedback='OLD: no addon work approved; investigate again'))+references,
        initial_reads=[dict(path=PATHS[0],start=1,end=20)]*2), workflow_task=linked)
    value = instruction(runtime, 'legacy fallback')
    assert 'OLD:' not in value and 'investigate again' not in value
    assert 'Original plain-language report' in value and 'Source-grounded hypothesis' in value
    assert 'Runtime cause unproven' in value and 'One bounded repair' in value
    assert value.endswith(references) and value.count('"start": 1') == 1
    assert 'develop its regression assertions alongside it' in value
    runtime.recipe['instruction'] = '{}\nUnexpected instructions'
    with pytest.raises(ValueError, match='reference envelope'):
        instruction(runtime, 'fallback')


def test_read_progress_recognizes_empty_reads_even_when_start_line_changes():
    progress = ReadProgress()
    value = json.dumps(dict(already_shown_or_empty=True, replayed_spans=[dict(text='current source')]))
    assert progress.observe('read_file', value) is None
    assert 'not an empty file' in progress.observe('read_file', value)
    assert progress.repeated == 2
    progress.observe('read_file', json.dumps(dict(spans=[dict(text='new')],already_shown_or_empty=False)))
    assert progress.repeated == 0


def test_overlapping_initial_reads_in_one_batch_are_one_useful_model_decision():
    progress = ReadProgress()
    new = json.dumps(dict(spans=[dict(text='new')],already_shown_or_empty=False))
    repeated = json.dumps(dict(already_shown_or_empty=True))
    assert progress.turn([('read_file',new),('read_file',new),
                          ('read_file',repeated),('read_file',repeated)]) is None
    assert progress.repeated == 0
    assert progress.turn([('read_file',repeated)]*8) is None
    assert progress.repeated == 1
    progress.observe('find_text', json.dumps(dict(already_shown=True)))
    progress.observe('replace_text', 'Edit saved in isolated workspace.')
    assert progress.repeated == 0


def response(tool, args, number):
    value=answer(20,10)
    value['choices'][0]['finish_reason']='tool_calls'
    value['choices'][0]['message']['tool_calls']=[dict(id='controlled-'+str(number),
        function=dict(name=tool,arguments=json.dumps(args)))]
    return value


def test_real_tool_responses_support_reread_then_edit_in_same_counted_attempt(tmp_path):
    path=tmp_path/PATHS[0]
    path.parent.mkdir(parents=True)
    path.write_text('local controlled = 1\n')
    runtime=SimpleNamespace(workspace=tmp_path,read_paths=[PATHS[0]],paths=[PATHS[0]],
        recipe=dict(source_context=[{}],read_budget_policy='distinct-lines-v1',repair_cycle='scoped-repair-v1'),
        tool_commands=lambda:{'focused':['controlled-no-shell']})
    tools=Tools(runtime,lambda:True)
    # Keep actual source reads; substitute only fixture editing/check handling.
    real_handle=tools.handle
    def handle(name,args):
        if name=='replace_text':
            assert args['old'] in path.read_text()
            path.write_text(path.read_text().replace(args['old'],args['new']))
            return 'Edit saved in isolated workspace.'
        return real_handle(name,args)
    tools.handle=handle
    Transport.responses=[response('read_file',dict(path=PATHS[0],start=1,end=1),0),
        response('read_file',dict(path=PATHS[0],start=1,end=1),1),
        response('read_file',dict(path=PATHS[0],start=1,end=1),2),
        response('replace_text',dict(path=PATHS[0],old='= 1',new='= 2'),3), answer(20,10)]
    Transport.bodies=[]
    value=implement(tools,'Controlled task', '', 'controlled',lambda:True,transport=Transport)
    assert '= 2' in path.read_text() and len(value['log'])==5
    messages=Transport.bodies[3]['messages']
    assert 'not an empty file' in messages[-1]['content']
    replay=json.loads(next(m['content'] for m in reversed(messages) if m['role']=='tool'))
    assert replay['replayed_spans'][0]['text']=='local controlled = 1\n'
    assert tools.read==len('local controlled = 1\n')
    assert all(body['tools'] == Transport.bodies[0]['tools'] for body in Transport.bodies)
    assert 'read_file' in {t['function']['name'] for t in Transport.bodies[-1]['tools']}


def test_unchanged_read_loop_stops_with_complete_counted_receipts_not_twenty_calls(tmp_path):
    p=tmp_path/PATHS[0]
    p.parent.mkdir(parents=True)
    p.write_text('local controlled = 1\n')
    runtime=SimpleNamespace(workspace=tmp_path,read_paths=[PATHS[0]],paths=[PATHS[0]],
        recipe=dict(source_context=[{}],read_budget_policy='distinct-lines-v1',repair_cycle='scoped-repair-v1'),
        tool_commands=lambda:{'focused':['controlled-no-shell']})
    Transport.responses=[response('read_file',dict(path=PATHS[0],start=1,end=1),i) for i in range(20)]
    Transport.bodies=[]
    turns=[]
    with pytest.raises(BudgetError, match='Repeated source reads made no progress'):
        implement(Tools(runtime,lambda:True),'Controlled task','','controlled',lambda:True,
                  transport=Transport,on_turn=turns.append)
    assert len(Transport.bodies)==len(turns)==5
    assert all(t['response_completed'] and t['usage_validated'] for t in turns)
    assert p.read_text()=='local controlled = 1\n'


def test_scoped_no_progress_forces_the_installed_string_tool_choice_without_weakening_dispatch(tmp_path):
    path=tmp_path/PATHS[0]
    path.parent.mkdir(parents=True)
    path.write_text('local controlled = 1\n')
    runtime=SimpleNamespace(workspace=tmp_path,read_paths=[PATHS[0]],paths=[PATHS[0]],
        recipe=dict(source_context=[{}],verification_profile='lua-offline-bugfix-v1',repair_cycle='scoped-repair-v1'),
        tool_commands=lambda:{'focused':['controlled-no-shell']})
    class ControlledTools:
        def __init__(self):self.runtime=runtime
        def handle(self,name,args):
            if name=='run_check':
                return json.dumps(dict(status='fail',diagnostic='Controlled rejection'))
            assert name=='replace_text'
            path.write_text(path.read_text().replace(args['old'],args['new']))
            return 'Edit saved in isolated workspace.'
    Transport.responses=[response('run_check',dict(name='candidate'),0),
        response('run_check',dict(name='candidate'),1),
        response('replace_text',dict(path=PATHS[0],old='= 1',new='= 2'),2),answer()]
    Transport.bodies=[]
    implement(ControlledTools(),'Controlled scope','','controlled',lambda:True,transport=Transport)
    assert Transport.bodies[0]['tools']==Transport.bodies[1]['tools']==Transport.bodies[3]['tools']
    assert Transport.bodies[2]['tool_choice']=='required'
    assert [t['function']['name'] for t in Transport.bodies[2]['tools']]==['replace_text']
    assert '= 2' in path.read_text()
