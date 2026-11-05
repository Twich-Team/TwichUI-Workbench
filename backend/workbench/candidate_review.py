"""Bounded derived review of the existing inert candidate; never reads repository paths."""
import difflib
import hashlib
import json
from pathlib import PurePosixPath
from typing import Literal

from pydantic import Field
from sqlalchemy import select

from workbench import admission, implementation as impl
from workbench.admission import GateError
from workbench.schemas import StrictModel
from workbench.visual_scope import digest

Category = Literal['Bug fix', 'Compatibility', 'Reliability', 'Performance', 'Usability / visual', 'Feature', 'Tests', 'Maintenance']
LIMIT = 24000
INPUT_LIMIT = 32768
CONTRACT = 'sample-pr-review-v1'


class FileComparison(StrictModel):
    path: str
    old_path: str | None = None
    kind: Literal['modified', 'added', 'deleted', 'renamed', 'binary', 'unavailable']
    additions: int | None = None
    deletions: int | None = None
    diff: str | None = None
    truncated: bool = False
    reason: str | None = None


class Review(impl.Holds):
    candidate_id: str
    task_id: str
    title: str
    purpose: str
    scope: list[str]
    category: Category = 'Feature'
    risk: str = 'Inert local sample; no addon changes'
    eligible: bool
    message: str
    fingerprint: str = Field(pattern=r'^[a-f0-9]{64}$')
    base_identity: str | None
    git_base_revision: None = None
    artifact_sha256: str | None
    files: list[FileComparison]
    verification: dict | None
    limitations: list[str]


def safe_path(value):
    return (isinstance(value, str) and 0 < len(value) <= 180 and
            not any(ord(ch) < 32 or ord(ch) == 127 for ch in value) and
            '\\' not in value and not PurePosixPath(value).is_absolute() and
            all(part not in {'', '.', '..'} for part in value.split('/')))


def compare(path, before, after, *, old_path=None, base_available=True, input_limit=INPUT_LIMIT):
    """Inputs are owned bytes. None means known absence; missing is explicit, never empty."""
    if type(input_limit) is not int or input_limit not in (INPUT_LIMIT, 65536):
        raise ValueError("Unsupported bounded comparison input allowance")
    if not safe_path(path) or (old_path is not None and not safe_path(old_path)):
        return dict(path='Unavailable path', kind='unavailable', reason='Unsafe display path was withheld.')
    common = dict(path=path, old_path=old_path)
    if not base_available or (before is None and after is None):
        return dict(**common, kind='unavailable', reason='Comparison inputs are unavailable; no diff was invented.')
    inputs = [data for data in (before, after) if data is not None]
    if any(not isinstance(data, bytes) or len(data) > input_limit for data in inputs):
        return dict(**common, kind='unavailable', reason=f'Comparison input exceeds the supported {input_limit // 1024} KiB limit.')
    try:
        if any(b'\0' in data for data in inputs):
            raise UnicodeError()
        old = before.decode('utf-8') if before is not None else ''
        new = after.decode('utf-8') if after is not None else ''
    except UnicodeError:
        return dict(**common, kind='binary', reason='Binary or non-UTF-8 content; text comparison unavailable.')
    kind = 'added' if before is None else 'deleted' if after is None else 'renamed' if old_path else 'modified'
    old_lines, new_lines = old.splitlines(keepends=True), new.splitlines(keepends=True)
    lines = list(difflib.unified_diff(old_lines, new_lines,
        fromfile='a/'+(old_path or path) if before is not None else '/dev/null',
        tofile='b/'+path if after is not None else '/dev/null'))
    additions = sum(line.startswith('+') for line in lines[2:])
    deletions = sum(line.startswith('-') for line in lines[2:])
    # Make missing final newlines legible without merging adjacent +/- lines.
    text = ''.join(line if line.endswith('\n') else line+'\n\\ No newline at end of file\n' for line in lines)
    return dict(**common, kind=kind, additions=additions, deletions=deletions,
                diff=text[:LIMIT], truncated=len(text) > LIMIT,
                reason='Diff truncated at 24,000 characters; preparation is held.' if len(text)>LIMIT else None)


def build(c, intake_id, task_id, candidate_id):
    task = admission.target(c, intake_id, task_id)
    candidate = impl.require(c, impl.candidates, candidate_id)
    attempt = impl.require(c, impl.attempts, candidate['attempt_id'])
    request = impl.require(c, impl.records, attempt['request_id'])
    if request['task_id'] != task_id or request['snapshot']['admission']['snapshot']['intake_id'] != intake_id:
        raise GateError('blocked', 'This sample belongs to another task.', 404)
    check = c.execute(select(impl.verifications).where(impl.verifications.c.candidate_id == candidate_id)).mappings().first()
    eligible, message = True, 'This checked sample can be used to prepare a sample PR. Real publication is disabled.'
    files, base_id = [], None
    try:
        impl.validate_candidate(c, intake_id, task_id, candidate_id)
    except GateError as error:
        eligible, message = False, error.message.replace('artifact binding', 'supporting evidence').replace('candidate binding', 'sample identity').replace('approval authority', 'approved plan')
    except impl.ERRORS:
        eligible, message = False, 'Sample inputs are unavailable. History is retained.'
    try:
        pinned = request['snapshot']
        fixture = pinned['fixture']
        if (digest(fixture) != pinned['fixture_sha256'] or digest(pinned) != request['binding'] or
                not impl.artifact_valid(candidate) or
                candidate['provenance'] != impl.provenance(request, attempt, candidate['artifact'])):
            raise ValueError('Integrity mismatch')
        artifact = json.loads(candidate['artifact'])
        if (artifact.get('format') != impl.CONTRACT or artifact.get('version') != fixture['version'] or
                not isinstance(artifact.get('cases'), list)):
            raise ValueError('Unsupported artifact')
        baseline = dict(format=impl.CONTRACT, version=fixture['version'],
                        cases=[dict(input=v['input'], output=v['input']) for v in fixture['cases']])
        base = (json.dumps(baseline, ensure_ascii=False, indent=2)+'\n').encode('utf-8')
        base_id = hashlib.sha256(base).hexdigest()
        files = [compare('sample/label-examples.json', base, candidate['artifact'])]
    except (*impl.ERRORS, UnicodeError, AttributeError):
        files = [compare('sample/label-examples.json', None, None, base_available=False)]
    if any(f['kind'] in {'unavailable','binary'} or f.get('truncated') for f in files):
        eligible, message = False, 'A complete readable sample comparison is required. No diff or publication is inferred.'
    if not check or not check_valid(check, intake_id, task_id, candidate):
        eligible = False
        # Preserve a more actionable approval/integrity hold if one already exists.
        if message.startswith('This checked sample'):
            message = 'Complete a successful current sample check before preparing a sample PR.'
    review = dict(candidate_id=candidate_id, task_id=task_id, title=task['title'], purpose=task['purpose'],
        scope=task['acceptance'], eligible=eligible, message=message, base_identity=base_id,
        artifact_sha256=candidate['provenance'].get('artifact_sha256') if impl.artifact_valid(candidate) else None,
        files=files, verification=impl.public(check) if check else None,
        limitations=['Synthetic input projection, not an addon code diff. No Git base revision is configured.',
          'Only the recorded deterministic label checks ran. No addon, provider, simulator or physical client ran.',
          'This approved scope is nonvisual; no design, implementation, simulator or physical-client visual evidence is claimed.',
          'Human review and real publication checks remain. No exhaustive secret detection is implemented.'])
    # Include raw evidence identity; a changed check/request/base cannot reuse the fingerprint.
    review['fingerprint'] = digest(dict(contract=CONTRACT, candidate=impl.candidate_identity(candidate),
        implementation_request=request, verification=dict(check) if check else None, base=base_id, files=files,
        task=dict(task)))
    return Review.model_validate(review).model_dump(), dict(check) if check else None


def check_valid(check, intake_id, task_id, candidate):
    expected_checks = [
        dict(label='Sample identity and content match the controlled fixture', passed=True),
        dict(label='Label examples remove surrounding spaces and preserve interior text', passed=True)]
    return (check['task_id'] == task_id and check['candidate_id'] == candidate['id'] and
        check['status'] == 'completed' and check['verification'] == 'passed' and
        bool(check['started_at'] and check['finished_at']) and check['checks'] == expected_checks and
        check['payload'] == dict(command_id=check['id'], mode='fixture', binding=candidate['id']) and
        check['snapshot'] == dict(intake_id=intake_id, candidate=impl.candidate_identity(candidate)))
