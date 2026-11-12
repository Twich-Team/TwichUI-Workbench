"""Controlled execution-path regressions; no real provider or addon answer."""
import json
from types import SimpleNamespace

import pytest

from workbench.implementation_context import instruction, ReadProgress, source_keys
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


def test_continuation_remembers_that_a_passing_original_test_failed_to_discriminate(tmp_path):
    from workbench.implementation_context import prior_failures
    prior=tmp_path/'prior'
    working=prior/'working-checks/00'
    working.mkdir(parents=True)
    (working/'outcome.json').write_text(json.dumps(dict(status='fail',
        failed_check_name='original-with-new-tests',original_test_differential={'status':'fail'})))
    raw=working/'original-with-new-tests.json'
    raw.write_text(json.dumps(dict(status='pass',rc=0,out='CONTROLLED original passed')))
    logs=prior/'check-logs'
    logs.mkdir()
    for i, (status, command, cwd, message) in enumerate([
        ('fail', ['lua5.1', 'test_fixture.lua'], 'workspace/tests', 'Old focused setup failure'),
        ('fail', ['lua5.1', 'test_fixture.lua'], 'different/tests', 'Separate workspace failure'),
        ('pass', ['lua5.1', 'test_fixture.lua'], 'workspace/tests', 'Focused check passes now'),
    ]):
        identity=f'fixed-{i:03}'
        (logs/f'{identity}.json').write_text(json.dumps(dict(check_id=identity, result=dict(
            status=status, rc=int(status!='pass'), command=command, working_directory=cwd, out=message))))
    current=tmp_path/'current'
    current.mkdir()
    (current/'repair-resume.json').write_text(json.dumps(dict(root=str(prior))))
    seen=prior_failures(SimpleNamespace(root=current,read_paths=['tests/test_fixture.lua']))
    assert len(seen)==2 and seen[0]['status']=='fail'
    assert 'also passed against unchanged original' in seen[0]['diagnostic']
    assert seen[1]['diagnostic']=='Separate workspace failure'
    assert json.loads((logs/'fixed-000.json').read_text())['result']['status']=='fail'
    assert json.loads(raw.read_text())['status']=='pass'


def test_comparison_carries_across_a_stopped_attempt_only_for_the_same_patch(tmp_path):
    from workbench.implementation_context import prior_failures
    tested, stopped, fresh = [tmp_path/name for name in ('tested', 'stopped', 'fresh')]
    working = tested/'working-checks/00'
    working.mkdir(parents=True)
    patch = 'a'*64
    (working/'manifest.json').write_text(json.dumps(dict(patch_sha256=patch)))
    (working/'outcome.json').write_text(json.dumps(dict(status='fail', failed_check_name='original-with-new-tests',
        original_test_differential={'status':'fail'})))
    (working/'original-with-new-tests.json').write_text(json.dumps(dict(status='pass', rc=0)))
    for dest, source in [(stopped, tested), (fresh, stopped)]:
        dest.mkdir()
        (dest/'repair-resume.json').write_text(json.dumps(dict(root=str(source), patch_sha256=patch)))
    runtime = SimpleNamespace(root=fresh, read_paths=['tests/test_fixture.lua'])
    assert 'also passed against unchanged original' in prior_failures(runtime)[0]['diagnostic']
    (fresh/'repair-resume.json').write_text(json.dumps(dict(root=str(stopped), patch_sha256='b'*64)))
    assert prior_failures(runtime) == []


def test_retained_candidate_reports_a_settled_full_suite_timeout_without_claiming_success(tmp_path):
    from hashlib import sha256
    from workbench.implementation_context import prior_failures
    old, fresh = tmp_path/'old', tmp_path/'fresh'
    folder = old/'working-checks/00'
    folder.mkdir(parents=True)
    patch = 'a'*64
    (folder/'manifest.json').write_text(json.dumps(dict(patch_sha256=patch)))
    for i in range(1, 5):
        (folder/f'check-{i:02}.json').write_text(json.dumps(dict(status='pass', rc=0)))
    suite = folder/'check-05.json'
    result = dict(command=['bash', 'tests/run.sh'], status='timeout', rc=None,
                  usage=dict(cgroup_removed=True, leaked_after_kill=False))
    suite.write_text(json.dumps(result))
    raw = folder/'original-with-new-tests.json'
    raw.write_text(json.dumps(dict(status='fail', rc=1), sort_keys=True))
    (folder/'original-test-differential.json').write_text(json.dumps(dict(
        status='pass', original_production_unchanged=True, result_sha256=sha256(raw.read_bytes()).hexdigest())))
    fresh.mkdir()
    (fresh/'repair-resume.json').write_text(json.dumps(dict(root=str(old), patch_sha256=patch)))
    runtime = SimpleNamespace(root=fresh, read_paths=['tests/test_fixture.lua'])
    observed = prior_failures(runtime)
    assert observed[0]['status'] == 'incomplete'
    assert 'Run the candidate check on these unchanged edits first' in observed[0]['diagnostic']
    assert 'no verified result was saved' in observed[0]['diagnostic']
    result['usage']['cgroup_removed'] = False
    suite.write_text(json.dumps(result))
    assert prior_failures(runtime) == []


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


def test_different_empty_searches_require_a_changed_navigation_strategy():
    progress=ReadProgress(lifetime=True)
    assert progress.observe('find_text',json.dumps(dict(matches=[],already_shown=False))) is None
    hint=progress.observe('find_text',json.dumps(dict(matches=[],already_shown=False)))
    assert progress.repeated==2 and 'short symbol' in hint
    assert 'JSON-escaped' in hint
    progress.observe('find_text',json.dumps(dict(matches=[dict(line=21,preview='local item')],already_shown=False)))
    assert progress.repeated==0
    # A mixed batch with actual new evidence still makes progress.
    progress.turn([('find_text',json.dumps(dict(matches=[]))),
                   ('find_text',json.dumps(dict(matches=[dict(line=22)])))])
    assert progress.repeated==0


def response(tool, args, number):
    value=answer(20,10)
    value['choices'][0]['finish_reason']='tool_calls'
    value['choices'][0]['message']['tool_calls']=[dict(id='controlled-'+str(number),
        function=dict(name=tool,arguments=json.dumps(args)))]
    return value


def test_current_source_merges_overlap_preserves_exact_text_and_invalidates_edits(tmp_path):
    from workbench.implementation_context import CurrentSource
    runtime=SimpleNamespace(root=tmp_path,enforce_storage=lambda:None)
    messages=[dict(role='system',content='Preserved policy'),dict(role='user',content='Preserved scope')]
    view=CurrentSource(runtime,messages)
    def read(start,text):
        return view.read(json.dumps(dict(path='source.lua',total_lines=5,
            spans=[dict(start=start,end=start+len(text.splitlines())-1,text=text)],next_start=None)))
    first=json.loads(read(1,'local value = 1\n  -- exact whitespace\n'))
    read(2,'  -- exact whitespace\nreturn value\n')
    assert first['current_source_view'] and first['delivered_ranges']==[dict(start=1,end=2)]
    assert len(messages)==3 and messages[0]['content']=='Preserved policy' and messages[1]['content']=='Preserved scope'
    data=json.loads(messages[2]['content'].rsplit('CURRENT_SOURCE_MANIFEST ',1)[1])
    assert [(r['start'],r['end']) for r in data[0]['ranges']]==[(1,3)]
    assert '```lua\nlocal value = 1\n  -- exact whitespace\nreturn value\n```' in messages[2]['content']
    assert 'local value = 1\\n' not in messages[2]['content']
    assert len(list((tmp_path/'attempt/context-sources').glob('*.json')))==2
    view.edited('source.lua')
    assert json.loads(messages[2]['content'].rsplit('CURRENT_SOURCE_MANIFEST ',1)[1])==[]


def test_current_source_compacts_only_old_complete_pure_read_exchanges(tmp_path):
    from workbench.implementation_context import CurrentSource
    runtime=SimpleNamespace(root=tmp_path,enforce_storage=lambda:None)
    messages=[dict(role='system',content='Policy'),dict(role='user',content='Scope')]
    view=CurrentSource(runtime,messages)
    view.read(json.dumps(dict(path='s',spans=[dict(start=1,end=1,text='source\n')])))
    read=dict(role='assistant',tool_calls=[dict(id='read-1',function=dict(name='read_file',arguments='{}'))])
    messages.extend([read,dict(role='tool',tool_call_id='read-1',content='source is in current view')])
    edit=dict(role='assistant',tool_calls=[dict(id='edit-1',function=dict(name='replace_text',arguments='{}'))])
    messages.extend([edit,dict(role='tool',tool_call_id='edit-1',content='Edit saved')])
    messages.extend([dict(role='user',content='Recent checker failure')]*4)
    view.completed_input()
    assert view.compact_history()
    assert read not in messages and edit in messages and messages[-1]['content']=='Recent checker failure'
    assert not view.compact_history()


def test_full_log_and_search_reads_archive_without_losing_current_source_or_check_results(tmp_path):
    from workbench.implementation_context import CurrentSource
    runtime=SimpleNamespace(root=tmp_path,enforce_storage=lambda:None)
    messages=[dict(role='system',content='Policy'),dict(role='user',content='Scope')]
    view=CurrentSource(runtime,messages)
    view.read(json.dumps(dict(path='s',spans=[dict(start=1,end=1,text='exact source\n')])))
    exchanges=[]
    for name in ('read_check_log','find_text','run_check'):
        exchange=[dict(role='assistant',tool_calls=[dict(id=name,function=dict(name=name,arguments='{}'))]),
            dict(role='tool',tool_call_id=name,content='Exact actual observation: '+name)]
        exchanges.append(exchange)
        messages.extend(exchange)
    messages.extend([dict(role='user',content='Recent actual check summary')]*4)
    view.completed_input()
    assert view.compact_history() and view.compact_history() and not view.compact_history()
    assert all(part in messages for part in exchanges[-1])
    assert 'exact source' in messages[2]['content'] and messages[0]['content']=='Policy'
    archived=[json.loads(p.read_text())['read_exchange'] for p in (tmp_path/'attempt/context-sources').glob('*.json')
        if 'read_exchange' in json.loads(p.read_text())]
    assert all(exchange in archived for exchange in exchanges[:2])


def current_fixture(tmp_path):
    from workbench.implementation_context import CurrentSource
    workspace=tmp_path/'workspace'
    workspace.mkdir()
    path=workspace/'source.lua'
    path.write_text(''.join(f'local current_{i} = {i}\n' for i in range(1,301)))
    runtime=SimpleNamespace(root=tmp_path,workspace=workspace,paths=['source.lua'],read_paths=['source.lua'],
        enforce_storage=lambda:None,source_read_limit=45000,
        recipe=dict(source_context=[{}],read_budget_policy='distinct-lines-v1',
            initial_reads=[dict(path='source.lua',start=200,end=210)]))
    messages=[dict(role='system',content='Policy'),dict(role='user',content='Scope')]
    return path,Tools(runtime,lambda:True),CurrentSource(runtime,messages)


@pytest.mark.parametrize('name',['tests/test_large.lua','production.lua'])
def test_large_test_context_focus_preserves_setup_changes_recent_requested_code_and_exact_cached_rereads(tmp_path,name):
    from workbench.implementation_context import CurrentSource
    workspace=tmp_path/'workspace'
    workspace.mkdir()
    path=workspace/name
    path.parent.mkdir(parents=True,exist_ok=True)
    original=''.join(f'local value_{i} = {i}\n' for i in range(1,701))
    path.write_text(original)
    runtime=SimpleNamespace(root=tmp_path,workspace=workspace,paths=[name],read_paths=[name],
        source_read_limit=45000,enforce_storage=lambda:None,
        recipe=dict(source_context=[{}],read_budget_policy='distinct-lines-v1'))
    tools=Tools(runtime,lambda:True)
    messages=[dict(role='system',content='POLICY'),dict(role='user',content='APPROVED scope')]
    view=CurrentSource(runtime,messages)
    view.prefill(tools)
    def shown():return view.message['content']
    assert len(view.files[name]['lines'])==700 and tools.read==len(original)
    assert 'local value_1 = 1\n' in shown() and 'local value_700 = 700\n' in shown()
    assert ('local value_350 = 350\n' in shown()) is (name=='production.lua')
    used=tools.read
    for start,end in [(350,370),(500,510),(200,205)]:
        raw=tools.handle('read_file',dict(path=name,start=start,end=end))
        view.read(raw)
        assert f'local value_{start} = {start}\n' in shown()
    assert tools.read==used and len(view.files[name]['lines'])==700
    if name.startswith('tests/'):
        assert 'local value_350 = 350\n' not in shown()  # Old focus is cached, not accumulated in every prompt.
        assert 'local value_500 = 500\n' in shown() and 'local value_200 = 200\n' in shown()
    # A model edit in the middle must stay visible after future reads, even when
    # it is far from setup/tail. Original tests are never removed from the cache.
    path.write_text(original.replace('local value_360 = 360\n','local value_360 = 361\n'))
    keys=view.edited(name,tools)
    assert 'local value_360 = 361\n' in shown()
    assert any(key[1]==360 for key in keys)
    view.read(tools.handle('read_file',dict(path=name,start=420,end=425)))
    view.read(tools.handle('read_file',dict(path=name,start=460,end=465)))
    assert 'local value_360 = 361\n' in shown()
    assert len(view.files[name]['lines'])==700


def test_small_working_set_is_read_through_real_tools_and_refreshed_after_line_insertion(tmp_path):
    path,tools,view=current_fixture(tmp_path)
    view.prefill(tools)
    assert view.files['source.lua']['lines'][300]=='local current_300 = 300\n'
    original_cost=tools.read
    assert original_cost==len(path.read_text())
    view.hydrate(tools,['source.lua'])
    assert tools.read==original_cost
    path.write_text('-- model-authored inserted line\n'+path.read_text())
    keys=view.edited('source.lua',tools)
    assert view.files['source.lua']['lines'][301]=='local current_300 = 300\n'
    assert view.files['source.lua']['lines'][1]=='-- model-authored inserted line\n'
    assert len(keys)==301 and keys==view.visible_keys()
    assert tools.read==original_cost+len(path.read_text())
    tools.permitted=lambda:False
    with pytest.raises(ValueError,match='Current grant'):
        view.edited('source.lua',tools)
    assert 'source.lua' not in view.files  # Never retain stale positions on revocation.


def test_optional_prefill_falls_back_to_planned_windows_without_spending_reads_again(tmp_path):
    path,tools,view=current_fixture(tmp_path)
    view.prefill(tools)
    cost=tools.read
    assert view.narrow_initial() and not view.narrow_initial()
    assert set(view.files['source.lua']['lines'])==set(range(1,81))|set(range(200,211))
    known=view.visible_keys()
    raw=tools.handle('read_file',dict(path='source.lua',start=299,end=300))
    assert tools.read==cost
    assert not source_keys(raw)<=known
    view.read(raw)
    assert view.files['source.lua']['lines'][300]=='local current_300 = 300\n'
    path.write_text('x'*45001)
    view.initial_prefill=False
    view.prefill(tools)
    assert not view.initial_prefill  # Large working sets keep normal scoped navigation.


def test_old_edit_exchange_archives_only_after_the_complete_current_file_is_supplied(tmp_path):
    path,tools,view=current_fixture(tmp_path)
    view.prefill(tools)
    messages=view.messages
    edit=dict(role='assistant',tool_calls=[dict(id='edit',function=dict(name='replace_text',
        arguments=json.dumps(dict(path='source.lua',old='old',new='new'))))])
    reply=dict(role='tool',tool_call_id='edit',content='Edit saved')
    messages.extend([edit,reply]+[dict(role='user',content='Latest independent failure')]*4)
    view.completed_input()
    view.edited('source.lua')
    assert not view.compact_history() and edit in messages
    view.hydrate(tools,['source.lua'])
    assert view.compact_history() and edit not in messages and reply not in messages
    assert 'local current_300' in messages[2]['content']
    assert any(json.loads(p.read_text()).get('edit_exchange')==[edit,reply]
        for p in (tmp_path/'attempt/context-sources').glob('*.json'))


def test_latest_log_can_archive_after_receipt_in_a_completed_turn_but_fresh_reply_cannot(tmp_path):
    path,tools,view=current_fixture(tmp_path)
    view.prefill(tools)
    call=dict(role='assistant',tool_calls=[dict(id='log',function=dict(name='read_check_log',arguments='{}'))])
    reply=dict(role='tool',tool_call_id='log',content='Actual full log')
    view.messages.extend([call,reply])
    assert not view.compact_history()
    view.completed_input()
    new=dict(role='assistant',tool_calls=[dict(id='log',function=dict(name='read_file',arguments='{}'))])
    view.messages.extend([new,dict(role='tool',tool_call_id='log',content='Fresh source')])
    assert view.compact_history() and call not in view.messages and new in view.messages
    assert not view.compact_history()


def test_one_line_edit_refreshes_unchanged_prefix_and_preserves_prompt_prefix(tmp_path):
    path,tools,view=current_fixture(tmp_path)
    view.prefill(tools)
    before=view.message['content']
    cost=tools.read
    path.write_text(path.read_text().replace('current_299 = 299','current_299 = 900'))
    view.edited('source.lua',tools)
    assert len(view.files['source.lua']['lines'])==300
    assert view.files['source.lua']['lines'][1]=='local current_1 = 1\n'
    assert view.files['source.lua']['lines'][299]=='local current_299 = 900\n'
    assert tools.read==cost+len('local current_299 = 900\n')
    assert before.split('local current_299',1)[0]==view.message['content'].split('local current_299',1)[0]


def test_one_check_projection_replaces_old_details_and_keeps_full_private_observations(tmp_path):
    from workbench.implementation_context import WorkingCheck
    path,tools,view=current_fixture(tmp_path)
    view.prefill(tools)
    check=WorkingCheck(view)
    check.set(dict(status='fail',diagnostic='old failed check '+'x'*5000),'focused')
    message=check.message
    check.edited()
    assert json.loads(message['content'].split('\n',1)[1])['code_changed_since_check']
    check.set(dict(status='fail',diagnostic='new actual observation',check_id='fixed-001'),'focused')
    assert check.message is message and 'old failed check' not in message['content']
    assert len(message['content'])<1800 and 'new actual observation' in message['content']
    records=[json.loads(p.read_text()) for p in (tmp_path/'attempt/context-sources').glob('*.json')]
    assert any(r.get('result',{}).get('diagnostic')=='old failed check '+'x'*5000 for r in records)


def test_reloading_source_into_context_does_not_turn_repetition_into_investigation_progress():
    progress=ReadProgress(lifetime=True)
    value=json.dumps(dict(path='source.lua',spans=[dict(start=1,end=1,text='local value=1\n')]))
    progress.observe('read_file',value)
    for _ in range(3):
        progress.rebuild_visible([])
        progress.observe('read_file',value)
    assert progress.repeated==3
    progress.edited('source.lua')
    progress.observe('read_file',value)
    assert progress.repeated==0


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


@pytest.mark.parametrize('test_source_shown',[True,False])
def test_feature_empty_searches_require_an_edit_only_after_both_writable_files_are_shown(tmp_path,test_source_shown):
    source,test='source.lua','tests/test_scope.lua'
    for name in [source,test]:
        path=tmp_path/name
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text('local controlled = 1\n')
    runtime=SimpleNamespace(workspace=tmp_path,read_paths=[source,test],paths=[source,test],
        recipe=dict(source_context=[{}],verification_profile='lua-feature-step-v1',repair_cycle='scoped-feature-v1',
            approved_feature_scope=dict(scope=dict(writable_paths=[source,test]))),
        tool_commands=lambda:{'focused':['controlled-no-shell']})
    class ControlledTools:
        def __init__(self):self.runtime=runtime
        def handle(self,name,args):
            if name=='read_file':
                return json.dumps(dict(path=args['path'],spans=[dict(start=1,end=1,text=(tmp_path/args['path']).read_text())]))
            if name=='find_text':
                return json.dumps(dict(path=args['path'],matches=[]))
            assert name=='replace_text' and args['path']==source
            path=tmp_path/source
            path.write_text(path.read_text().replace(args['old'],args['new']))
            return 'Edit saved in isolated workspace.'
    calls=[response('read_file',dict(path=source),0)]
    if test_source_shown:
        calls.append(response('read_file',dict(path=test),1))
    calls += [response('find_text',dict(path=test,query='absent-harness'),2),
        response('find_text',dict(path=test,query='absent-harness'),3),
        response('replace_text',dict(path=source,old='= 1',new='= 2'),4),answer()]
    Transport.responses=calls
    Transport.bodies=[]
    implement(ControlledTools(),'Controlled current feature','','controlled',lambda:True,transport=Transport)
    edit=Transport.bodies[-2]
    if test_source_shown:
        assert edit['tool_choice']=='required'
        assert [t['function']['name']for t in edit['tools']]==['replace_text']
    else:
        assert 'find_text' in [t['function']['name']for t in edit['tools']]
    assert '= 2' in (tmp_path/source).read_text()


def test_current_feature_brief_preserves_compiled_checks_and_prioritizes_focused_test_setup():
    linked=dict(task=dict(title='Current implementation step',acceptance=['Current behavior']),
        original=dict(description='Whole parent feature'),approved_scope=['Current scope'],
        proposal_review=dict(content=dict(behavior='Parent behavior',value='Actual source findings',
            uncertainty=['Existing interface is proposed work'],exclusions=['No settings'])))
    reads=[dict(path='source.lua',start=1,end=120),dict(path='source.lua',start=121,end=240),
        dict(path='source.lua',start=241,end=360),dict(path='source.lua',start=361,end=480),
        dict(path='tests/test_scope.lua',start=1,end=120)]
    scope=dict(writable_paths=['source.lua','tests/test_scope.lua'],checks=[dict(observation='CONTROLLED compiled step observation')])
    runtime=SimpleNamespace(recipe=dict(repair_cycle='scoped-feature-v1',instruction='{}',
        initial_reads=reads[:4],source_context=reads,approved_feature_scope=dict(scope=scope)),workflow_task=linked)
    value=instruction(runtime,'legacy fallback')
    assert 'CONTROLLED compiled step observation' in value
    assert 'Implement only the current approved feature step' in value
    initial=json.JSONDecoder().raw_decode(value.split('Initial bounded reads: ',1)[1])[0]
    assert initial[0]['path']=='source.lua' and initial[1]['path']=='tests/test_scope.lua'
    task=json.JSONDecoder().raw_decode(value.split('Current reviewed task:\n',1)[1])[0]
    assert task['report']=='Current implementation step'
    assert task['intended_behavior']['acceptance']==['Current behavior']
    assert task['feature_background_only']==dict(original_request='Whole parent feature')
    assert 'Actual source findings' not in value and 'Parent behavior' not in value
    assert value.count('CONTROLLED compiled step observation')==1


def test_bounded_feature_checks_changed_work_before_consuming_all_twenty_turns(tmp_path):
    source=tmp_path/'source.lua'
    source.write_text('local controlled = 1\n')
    runtime=SimpleNamespace(root=tmp_path,workspace=tmp_path,read_paths=['source.lua'],paths=['source.lua'],
        recipe=dict(source_context=[{}],verification_profile='lua-feature-step-v1',repair_cycle='scoped-feature-v1'),
        tool_commands=lambda:{'focused':['controlled-no-shell']})
    checks=[]
    class ControlledTools:
        def __init__(self):self.runtime=runtime
        def handle(self,name,args):
            if name=='replace_text':
                source.write_text(source.read_text().replace(args['old'],args['new']))
                return 'Controlled edit saved.'
            assert name=='run_check'
            checks.append((args['name'],source.read_text()))
            return json.dumps(dict(status='fail' if len(checks)==1 else 'pass',
                diagnostic='Controlled new-test setup failure' if len(checks)==1 else 'Controlled pass'))
    Transport.responses=[response('replace_text',dict(path='source.lua',old=f'= {i}',new=f'= {i+1}'),i-1)
        for i in range(1,5)]
    Transport.bodies=[]
    result=implement(ControlledTools(),'Controlled feature','','controlled',lambda:True,transport=Transport)
    assert [name for name,_ in checks]==['focused','focused','candidate']
    assert '= 3' in checks[0][1] and '= 5' in checks[-1][1]
    assert len(Transport.bodies)==4
    assert any('Controlled new-test setup failure' in m.get('content','') for m in Transport.bodies[2]['messages'])
    assert result['completion_reason']=='independent-checks-passed-after-progress-observation'
    records=sorted((tmp_path/'attempt').glob('progress-check-*.json'))
    assert len(records)==3 and json.loads(records[0].read_text())['result']['status']=='fail'


def test_bounded_feature_never_finishes_on_focused_pass_when_candidate_fails(tmp_path,monkeypatch):
    from workbench import real_provider
    source=tmp_path/'source.lua'
    source.write_text('local controlled = 1\n')
    runtime=SimpleNamespace(root=tmp_path,workspace=tmp_path,read_paths=['source.lua'],paths=['source.lua'],
        recipe=dict(source_context=[{}],verification_profile='lua-feature-step-v1',repair_cycle='scoped-feature-v1'),
        tool_commands=lambda:{'focused':['controlled-no-shell']})
    monkeypatch.setitem(real_provider.LIMITS,'turns',4)
    checks=[]
    class ControlledTools:
        def __init__(self):self.runtime=runtime
        def handle(self,name,args):
            if name=='replace_text':
                source.write_text(source.read_text().replace(args['old'],args['new']))
                return 'Controlled edit saved.'
            assert name=='run_check'
            checks.append(args['name'])
            return json.dumps(dict(status='pass' if args['name']=='focused' else 'fail',
                diagnostic='Controlled independent candidate rejection'))
    Transport.responses=[response('replace_text',dict(path='source.lua',old=f'= {i}',new=f'= {i+1}'),i-1)
        for i in range(1,5)]
    Transport.bodies=[]
    with pytest.raises(BudgetError,match='turn limit consumed'):
        implement(ControlledTools(),'Controlled feature','','controlled',lambda:True,transport=Transport)
    assert len(Transport.bodies)==4 and checks==['focused','candidate','focused','candidate']


def test_large_feature_prefill_uses_bounded_real_setup_and_tail_with_normal_read_accounting(tmp_path):
    path,tools,view=current_fixture(tmp_path)
    view.runtime.recipe['repair_cycle']='scoped-feature-v1'
    view.runtime.source_read_limit=path.stat().st_size-1
    view.prefill(tools)
    assert set(view.files['source.lua']['lines'])==set(range(1,121))|set(range(181,301))
    assert 'current_1 = 1' in view.message['content'] and 'current_300 = 300' in view.message['content']
    assert tools.read < view.runtime.source_read_limit
    before=tools.read
    view.read(tools.handle('read_file',dict(path='source.lua',start=1,end=120)))
    assert tools.read==before
