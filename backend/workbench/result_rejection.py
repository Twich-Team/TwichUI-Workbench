"""Read-only later rejection of a historically checked candidate.

Native execution outcomes, usage and grants remain unchanged. Only independently
bound private failure evidence can establish rejection; it never grants a replay.
"""
import json
import os
import stat
from pathlib import Path
from hashlib import sha256
from uuid import UUID

from workbench.visual_scope import digest


def valid_identity(execution):
    try:
        return isinstance(execution, str) and str(UUID(execution)) == execution
    except (ValueError, TypeError, AttributeError):
        return False


def validated_rejection(connection, execution):
    """A checked test run is not a successful acceptance after a proven rejection.

    Only private operator review plus an exact, confined new-assertion failure
    against that frozen candidate permits fresh work. This grants no reuse of
    output, task bindings, provider switching, or execution permission.
    """
    from workbench.api_mock_contract import source_support
    from workbench.repair_cycle import oracle, new_assertion_failure
    from workbench.regression_preparation import git
    from workbench.trusted_preparation import owned

    if not valid_identity(execution):
        return None
    try:
        run = connection.execute('SELECT result,grant_id,status FROM real_execution_requests WHERE id=?',
                                 (execution,)).fetchone()
        result = json.loads(run[0])
        grant = connection.execute('SELECT snapshot,revoked_at FROM real_execution_grants WHERE id=?',
                                   (run[1],)).fetchone()
        snapshot = json.loads(grant[0])
        linked = snapshot['workflow_task']
        plan = linked['proposal_review']['task_contract']['bug_intent']['plan']
        focused = plan['focused_test']
        db = Path(connection.execute('PRAGMA database_list').fetchone()[2])
        if not db.is_absolute():
            return None
        folder = owned(db.parent/'acceptance/rejected-results')

        def read(suffix, limit):
            path = owned(folder/(execution+suffix))
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
            with os.fdopen(fd, 'rb') as file:
                info = os.fstat(file.fileno())
                if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                        or info.st_mode & 0o077 or info.st_nlink != 1 or info.st_size > limit):
                    raise ValueError('Private bounded rejection evidence required')
                raw = file.read(limit + 1)
                if len(raw) > limit:
                    raise ValueError('Rejection evidence grew beyond its bound')
                return raw

        review = json.loads(read('.json', 8192))
        check_raw = read('.check.json', 65536)
        test = read('.test.lua', 65536)
        check = json.loads(check_raw)
        source, base = Path(snapshot['contract']['source']), snapshot['contract']['base']
        api, _ = source_support(source, base, plan['read_paths'])
        expected_oracle = oracle(focused, api).encode()
        valid = bool(run[2] == 'checked' and grant[1] and result['verification'] == 'passed'
            and review['version'] == 1 and review['approved'] is False
            and review['execution'] == execution and review['candidate'] == result['candidate_id']
            and sha256(json.dumps(result['manifest'],sort_keys=True).encode()).hexdigest() == result['candidate_id']
            and review['criterion'] in {c['id'] for c in plan['criteria']}
            and isinstance(review['reason'], str) and 20 <= len(review['reason']) <= 800
            and review['candidate_tree_sha256'] == digest(result['manifest']['tree'])
            and review['oracle_sha256'] == sha256(expected_oracle).hexdigest()
            and read('.oracle.lua', 32768) == expected_oracle
            and review['check_sha256'] == sha256(check_raw).hexdigest()
            and sha256(test).hexdigest() == result['manifest']['tree'][focused]
            and not connection.execute('SELECT 1 FROM real_publications WHERE execution_id=?',(execution,)).fetchone()
            and new_assertion_failure(check, focused, git(source,'show',base+':'+focused).decode(),test.decode()))
        return review if valid else None
    except (OSError, ValueError, KeyError, TypeError, IndexError):
        return None

def later_review(connection, execution):
    """Project only bounded review metadata; missing or bad evidence grants nothing."""
    if not valid_identity(execution):
        return None
    db = Path(connection.execute('PRAGMA database_list').fetchone()[2])
    if not db.is_absolute():
        return None
    path = db.parent/'acceptance/rejected-results'/(execution+'.json')
    if not os.path.lexists(path):
        return None
    review = validated_rejection(connection, execution)
    if review:
        return dict(state='rejected', reason=review['reason'])
    return dict(state='unavailable', reason='A later review was recorded, but its evidence could not be validated. Do not rely on this patch until the review is resolved.')
