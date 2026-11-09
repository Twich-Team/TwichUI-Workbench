"""Mock return fields cannot invent game capabilities; no live provider/game."""
import subprocess

import pytest

from workbench.api_mock_contract import declarations,guard,validate

SOURCE = '''local Example={Namespace="C_Example",
Functions={
\t\t{
\t\t\tName = "MapNodes", Type = "Function",
\t\t\tReturns = {
\t\t\t\t{ Name = "nodes", Type = "table", InnerType = "MapNode", Nilable = false },
\t\t\t},
\t\t},
\t\t{
\t\t\tName = "MasterNodes", Type = "Function",
\t\t\tReturns = {
\t\t\t\t{ Name = "nodes", Type = "table", InnerType = "MasterNode", Nilable = false },
\t\t\t},
\t\t},
}, Tables={
\t\t{
\t\t\tName = "MapNode", Type = "Structure",
\t\t\tFields = {
\t\t\t\t{ Name = "nodeID", Type = "number" },
\t\t\t\t{ Name = "isUndiscovered", Type = "bool" },
\t\t\t},
\t\t},
\t\t{
\t\t\tName = "MasterNode", Type = "Structure",
\t\t\tFields = {
\t\t\t\t{ Name = "nodeID", Type = "number" },
\t\t\t\t{ Name = "state", Type = "number" },
\t\t\t},
\t\t},
}}
'''


def test_generated_declarations_distinguish_map_listing_from_master_observation():
    result = declarations(SOURCE)
    assert result == {'MapNodes': {'1': ['isUndiscovered', 'nodeID']},
                      'MasterNodes': {'1': ['nodeID', 'state']}}


@pytest.mark.parametrize('assignment', ['constructor', 'namespace', 'function'])
def test_actual_lua_client_mocks_cannot_supply_undeclared_positive_fields(tmp_path, assignment):
    api = '{MapNodes=function() return {{nodeID=7,isUndiscovered=false,state=1}} end, MasterNodes=function() return {{nodeID=7,state=1}} end}'
    (tmp_path/'harness.lua').write_text('function MakeClient() return setmetatable({answer=42'+(
        ',C_Example='+api if assignment=='constructor' else '')+'},{__index={inherited="kept"}}) end\n')
    setup = 'do end' if assignment=='constructor' else ('c.C_Example='+api if assignment=='namespace' else
        'c.C_Example={}; c.C_Example.MapNodes=function() return {{nodeID=7,state=1}} end; c.C_Example.MasterNodes=function() return {{nodeID=7,state=1}} end')
    test = ('dofile("harness.lua"); local c=MakeClient(); '+setup+'; '
        'local map=c.C_Example.MapNodes(); local master=c.C_Example.MasterNodes(); '
        'assert(map[1].nodeID==7 and map[1].state==nil,"undeclared map state must not create learned knowledge"); '
        'assert(master[1].nodeID==7 and master[1].state==1); '
        'assert(c.answer==42 and c.inherited=="kept"); c.extra="kept"; assert(c.extra=="kept")')
    (tmp_path/'focused.lua').write_text(test)
    (tmp_path/'oracle.lua').write_text(guard({'C_Example': declarations(SOURCE)})+'dofile("focused.lua")')
    unguarded=subprocess.run(['lua5.1','focused.lua'],cwd=tmp_path,capture_output=True,timeout=5)
    assert unguarded.returncode != 0
    checked=subprocess.run(['lua5.1','oracle.lua'],cwd=tmp_path,capture_output=True,timeout=5)
    assert checked.returncode == 0, checked.stderr.decode()


def test_guard_preserves_partial_failed_and_multiple_return_observations(tmp_path):
    (tmp_path/'harness.lua').write_text('function MakeClient() return {} end')
    (tmp_path/'focused.lua').write_text('''dofile("harness.lua");local c=MakeClient()
        c.C_Example={MapNodes=function()return {{nodeID=7},false},nil,"third" end}
        local a,b,d=c.C_Example.MapNodes();assert(a[1].nodeID==7 and a[1].state==nil and a[2]==false and b==nil and d=="third")
        c.C_Example.MapNodes=function()error("unavailable")end
        assert(not pcall(c.C_Example.MapNodes));c.C_Example=nil;assert(c.C_Example==nil)
    ''')
    (tmp_path/'oracle.lua').write_text(guard({'C_Example': declarations(SOURCE)})+'dofile("focused.lua")')
    result=subprocess.run(['lua5.1','oracle.lua'],cwd=tmp_path,capture_output=True,timeout=5)
    assert result.returncode==0,result.stderr.decode()


@pytest.mark.parametrize('value', [[], {'../bad':{}}, {'C_Okay':{'run':{'0':['state']}}}, {'C_Okay':{'run':{'1':['state;os.execute']}}}])
def test_contract_data_cannot_inject_lua_or_escape_bounds(value):
    with pytest.raises(ValueError):
        validate(value)


def test_public_reference_is_hash_checked_and_shared_with_source_context(tmp_path, monkeypatch):
    import hashlib
    import json
    from workbench import api_mock_contract,game_api_reference,regression_preparation

    (tmp_path/'reference_data').mkdir()
    cache=tmp_path/'.local/game-api-reference'
    cache.mkdir(parents=True)
    name='ExampleDocumentation.lua'
    (cache/name).write_text(SOURCE)
    (tmp_path/'reference_data/forever-api.json').write_text(json.dumps(dict(entries=[dict(
        namespace='C_Example',file=name,bytes=len(SOURCE.encode()),sha256=hashlib.sha256(SOURCE.encode()).hexdigest())],
        directory='public-api',commit='a'*40,origin='public fixture',client='controlled')))
    monkeypatch.setattr(api_mock_contract,'ROOT',tmp_path)
    monkeypatch.setattr(game_api_reference,'ROOT',tmp_path)
    monkeypatch.setattr(regression_preparation,'git',lambda *_:b'local nodes=C_Example.MapNodes()')
    contracts,references=api_mock_contract.source_support(tmp_path,'b'*40,['tests/test_x.lua'])
    assert contracts=={'C_Example':declarations(SOURCE)}
    assert references[0]['namespace']=='C_Example'
    assert 'MasterNodes' in references[0]['excerpts'][0]['text']
    (cache/name).write_text(SOURCE+'-- changed')
    with pytest.raises(ValueError,match='changed'):
        api_mock_contract.source_support(tmp_path,'b'*40,['tests/test_x.lua'])
