"""Generic committed dependencies are visible as context, never writable sources."""
import copy

import pytest

from workbench.source_support_context import imports
from workbench import bug_investigation
from workbench.trusted_preparation import configuration
from test_bug_workflow import enable_bugs, report, CLIENT_TEST
from test_everyday import installed  # noqa: F401
from test_verification_profiles import git


def test_literal_imports_ignore_comments_strings_dynamic_paths_and_traversal():
    text = '''-- dofile(TESTS .. "private.lua")
local quoted = "dofile(TESTS .. 'private.lua')"
local continued = "start\\
dofile(TESTS .. 'private.lua')"
--[[
dofile(TESTS .. "private.lua")
]]
dofile(TESTS .. "shared.lua")
dofile(TESTS .. 'fixtures/example.lua')
dofile(TESTS .. '../outside.lua')
dofile(TESTS .. '.private.lua')
dofile(TESTS .. wanted)
'''
    assert imports(text) == ['tests/shared.lua','tests/fixtures/example.lua']


@pytest.mark.usefixtures('installed')
def test_selected_tests_discover_their_own_helpers_without_registration(store,monkeypatch):
    root=enable_bugs(monkeypatch)
    helper='tests/shared_notification.lua'
    (root/helper).write_text('SHARED_NOTIFICATION = { source = "existing fixture" }\n')
    test=root/CLIENT_TEST
    test.write_text('dofile(TESTS .. "shared_notification.lua")\n'+test.read_text())
    git(root,'add','.')
    git(root,'-c','user.name=Fixture','-c','user.email=fixture@invalid','commit','-qm','Unregistered dependency')
    before=git(root,'status','--porcelain')
    packet=bug_investigation.context(report(True),'',configuration(store))
    found=next(f for f in packet['files'] if f['path']==helper)
    assert found['support_only'] and found['support_dependency']['read_only']
    assert CLIENT_TEST in found['support_dependency']['imported_by']
    assert found['excerpts'][0]['text']==(root/helper).read_text().strip()
    assert git(root,'status','--porcelain')==before
    from workbench.bug_program import schema
    original=copy.deepcopy(packet)
    wire=schema({'minimum_observations':3},packet['files'])
    assert helper not in wire['$defs']['Plan']['properties']['other_reads']['items']['enum']
    assert helper not in wire['$defs']['Plan']['properties']['writable_paths']['items']['enum']
    assert packet==original
