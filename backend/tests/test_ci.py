"""CI must cover the whole suite and may never approve partial feedback."""
from pathlib import Path
import pytest
import xml.etree.ElementTree as ET
import json
import subprocess
import re
import ci
from ci import REQUIRED, acceptance, backend_files, partition


def test_feature_browser_gate_discovers_the_actual_controlled_journey():
    from project_env import environment
    root = Path(__file__).resolve().parents[2]
    workflow = (root / '.github/workflows/verify.yml').read_text()
    step = workflow.split('id: feature-connected-acceptance', 1)[1].split('- uses:', 1)[0]
    assert "WORKBENCH_E2E_EVERYDAY: '1'" in step
    assert "if: matrix.shard == '1/4'" in step
    pattern = re.search(r"run: npm run test-e2e -- --grep '([^']+)'", step)[1]
    result = subprocess.run(['node', str(root/'node_modules/@playwright/test/cli.js'), 'test',
        '--project=chromium', '--list', '--grep', pattern], cwd=root,
        env=environment(), text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=20)
    assert result.returncode == 0, result.stdout
    for title in (
        'Feature Workshop grounded intake automatically plans connected steps',
        'one browser feature reaches independent combined checks and one coherent local result',
        'feature files and source references stay with the original request through reload',
        'a held feature needs a separate read-only allowance before more source preparation',
    ):
        assert title in result.stdout
    assert 'Total: 4 tests in 1 file' in result.stdout


def test_one_matrix_source_covers_the_whole_partition():
    plans = ci.matrices()
    for name, total in [('backend', ci.BACKEND_SHARDS), ('browser', ci.BROWSER_SHARDS)]:
        entries = plans[name]['include']
        assert len(entries) == total
        assert {entry['shard'] for entry in entries} == {f'{part}/{total}' for part in range(1, total + 1)}
        assert len({entry['label'] for entry in entries}) == total


def test_backend_partition_covers_every_test_file_once():
    root = Path(__file__).resolve().parents[2]
    files = backend_files(root)
    assert 'backend/tests/test_ci.py' in files and len(files) > 100
    for total in (1, 2, 4):
        shards = [partition(files, f'{part}/{total}') for part in range(1, total + 1)]
        assert sorted(file for shard in shards for file in shard) == files
        assert len({file for shard in shards for file in shard}) == len(files)
        assert max(map(len, shards)) - min(map(len, shards)) <= 1


@pytest.mark.parametrize('shard', ['0/2', '3/2', '1/0', '1/99999', '1', '../2', '1/2 extra'])
def test_invalid_or_empty_partition_refused(shard):
    with pytest.raises(ValueError):
        partition(['first', 'second'], shard)


@pytest.mark.parametrize('result', ['failure', 'cancelled', 'skipped', 'neutral', None])
def test_partial_or_failed_groups_never_authorize_release(result):
    groups = {key: {'result': 'success'} for key in REQUIRED}
    groups['browser']['result'] = result
    with pytest.raises(ValueError, match='Every required'):
        acceptance('a' * 40, groups, ['test.py'])


def test_fast_feedback_or_missing_group_cannot_be_accepted():
    with pytest.raises(ValueError):
        acceptance('a' * 40, {'checks': {'result': 'success'}}, ['test.py'])
    groups = {key: {'result': 'success'} for key in REQUIRED}
    with pytest.raises(ValueError, match='Exact commit'):
        acceptance('branch-name', groups, ['test.py'])
    receipt = acceptance('a' * 40, groups, ['test.py'])
    assert receipt['full_release_acceptance'] and receipt['commit'] == 'a' * 40
    assert receipt['backend_shards'] == 2 and receipt['browser_shards'] == 4


@pytest.mark.parametrize('fails', [False, True])
def test_partition_runner_executes_selected_tests_and_preserves_failure(tmp_path, monkeypatch, fails):
    folder = tmp_path / 'backend/tests'
    folder.mkdir(parents=True)
    (folder / 'test_first.py').write_text(f'def test_selected():\n    assert {not fails}\n')
    (folder / 'test_second.py').write_text('def test_other_shard():\n    assert False\n')
    monkeypatch.setattr(ci, 'ROOT', tmp_path)
    monkeypatch.setattr(ci.sys, 'argv', ['ci.py', 'backend', '--shard', '1/2'])
    assert ci.main() == (1 if fails else 0)
    report = ET.parse(tmp_path / 'artifacts/ci/backend-1-of-2.xml')
    cases = report.findall('.//testcase')
    assert len(cases) == 1 and cases[0].attrib['name'] == 'test_selected'
    assert bool(cases[0].findall('failure')) == fails


def test_release_receipt_rejects_another_checkout(tmp_path, monkeypatch):
    subprocess.run(['git', 'init', '-q', str(tmp_path)], check=True)
    (tmp_path / 'README').write_text('Controlled CI checkout only.\n')
    subprocess.run(['git', 'add', 'README'], cwd=tmp_path, check=True)
    subprocess.run(['git', '-c', 'user.name=CI fixture', '-c', 'user.email=ci@example.invalid',
                    'commit', '-qm', 'Controlled checkout'], cwd=tmp_path, check=True)
    commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=tmp_path, text=True).strip()
    monkeypatch.setattr(ci, 'ROOT', tmp_path)
    monkeypatch.setattr(ci.sys, 'argv', ['ci.py', 'acceptance'])
    monkeypatch.setenv('GITHUB_SHA', 'a' * 40)
    monkeypatch.setenv('CI_RESULTS', json.dumps({key: {'result': 'success'} for key in REQUIRED}))
    target = tmp_path / 'artifacts/ci/full-release-acceptance.json'
    with pytest.raises(ValueError, match='checked-out'):
        ci.main()
    assert not target.exists()
    monkeypatch.setenv('GITHUB_SHA', commit)
    from release_bundle import build
    (tmp_path / 'frontend/dist').mkdir(parents=True)
    (tmp_path / 'frontend/dist/index.html').write_text('Checked fixture frontend')
    build(tmp_path, tmp_path / 'artifacts/ci/application.tar.gz')
    assert ci.main() == 0 and json.loads(target.read_text())['commit'] == commit
