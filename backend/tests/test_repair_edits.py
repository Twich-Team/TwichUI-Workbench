"""Real Git workspace transactions; no provider or confinement claim."""
import json

import pytest

from workbench import repair_edits as edits
from workbench.real_workspace import sha,tree
from workbench import verification_profiles as profiles
from test_verification_profiles import setup,PRODUCTION,TEST,BUGGY,CORRECT


def fixture(tmp_path):
    runtime=setup(tmp_path,profiles.BUGFIX)
    runtime.paths=(PRODUCTION,TEST)
    return runtime


def test_whole_batch_over_limit_changes_neither_file_and_returns_useful_counts(tmp_path):
    runtime=fixture(tmp_path)
    before=tree(runtime.workspace)
    with pytest.raises(edits.RepairConstraint) as caught:
        edits.replace_batch(runtime,[dict(path=PRODUCTION,old=BUGGY,new=CORRECT),
            dict(path=TEST,old=(runtime.workspace/TEST).read_text(),
                 new=(runtime.workspace/TEST).read_text()+''.join('-- extra\n' for _ in range(151)))],lambda:True)
    assert caught.value.code=='change-budget'
    assert caught.value.counts['changed_lines']>150
    assert caught.value.counts['allowed_changed_lines']==150
    assert tree(runtime.workspace)==before
    assert not list((runtime.root/'edit-batches').glob('*'))


def test_whole_batch_matching_and_test_preservation_reject_before_first_write(tmp_path):
    runtime=fixture(tmp_path)
    before=tree(runtime.workspace)
    for old,new,code in [('missing','replacement','exact-match'),
                         ((runtime.workspace/TEST).read_text(),'-- removed old tests\n','test-preservation')]:
        with pytest.raises(edits.RepairConstraint) as caught:
            edits.replace_batch(runtime,[dict(path=PRODUCTION,old=BUGGY,new=CORRECT),
                dict(path=TEST,old=old,new=new)],lambda:True)
        assert caught.value.code==code
        assert tree(runtime.workspace)==before


def test_exception_after_multiple_writes_restores_whole_tree(tmp_path,monkeypatch):
    runtime=fixture(tmp_path)
    before=tree(runtime.workspace)
    def reject():
        raise RuntimeError('injected final scope failure')
    monkeypatch.setattr(runtime,'scope',reject)
    with pytest.raises(RuntimeError,match='injected'):
        edits.replace_batch(runtime,[dict(path=PRODUCTION,old=BUGGY,new=CORRECT),
            dict(path=TEST,old=(runtime.workspace/TEST).read_text(),
                new=(runtime.workspace/TEST).read_text()+'assert(true)\n')],lambda:True)
    assert tree(runtime.workspace)==before
    assert len(list((runtime.root/'edit-batches').glob('*/rolled-back.json')))==1


def test_interrupted_known_batch_is_restored_but_unexplained_change_holds(tmp_path):
    runtime=fixture(tmp_path)
    before=tree(runtime.workspace)
    folder=runtime.root/'edit-batches/interrupted'
    (folder/'before'/PRODUCTION).parent.mkdir(parents=True)
    (folder/'before'/PRODUCTION).write_text(BUGGY)
    (folder/'prepared.json').write_text(json.dumps(dict(base=runtime.base,tree=before,
        before={PRODUCTION:sha(BUGGY.encode())},after={PRODUCTION:sha(CORRECT.encode())})))
    (runtime.workspace/PRODUCTION).write_text(CORRECT+'-- unexpected\n')
    with pytest.raises(ValueError,match='Uncertain'):
        edits.reconcile(runtime,lambda:True)
    assert not (folder/'rolled-back.json').exists()
    (runtime.workspace/PRODUCTION).write_text(CORRECT)
    edits.reconcile(runtime,lambda:True)
    assert tree(runtime.workspace)==before
    assert json.loads((folder/'rolled-back.json').read_text())['restored']


def test_valid_batch_is_saved_and_original_source_is_never_edited(tmp_path):
    runtime=fixture(tmp_path)
    edits.replace_batch(runtime,[dict(path=PRODUCTION,old=BUGGY,new=CORRECT)],lambda:True)
    assert (runtime.workspace/PRODUCTION).read_text()==CORRECT
    assert (tmp_path/'source'/PRODUCTION).read_text()==BUGGY
    assert len(list((runtime.root/'edit-batches').glob('*/applied.json')))==1
