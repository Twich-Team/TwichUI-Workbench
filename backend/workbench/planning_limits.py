"""Local operation bounds follow the actual saved consent, including old history."""
import json

LEGACY_COMPLETION = 2600
PROGRAM_COMPLETION = 6144
PROGRAM_REASONING = 3072


def consent_cap(consent):
    version = consent.get('investigation_version')
    if version is not None and type(version) is not int:
        raise ValueError('Malformed local operation consent')
    if version in (7, 8):
        if (not all(type(consent.get(k)) is int for k in ('max_local_completion_tokens',
                'max_local_requests','planning_deadline_seconds','local_request_deadline_seconds'))
                or consent.get('max_local_completion_tokens') != PROGRAM_COMPLETION
                or consent.get('max_local_requests') != 8
                or consent.get('planning_deadline_seconds') != 1800
                or consent.get('local_request_deadline_seconds') != 300):
            raise ValueError('The saved local operation bounds changed')
        if version == 8 and (not all(type(consent.get(k)) is int for k in (
                'max_codex_requests','max_total_model_requests','codex_request_deadline_seconds'))
                or consent['max_codex_requests'] != 8 or consent['max_total_model_requests'] != 8
                or consent['codex_request_deadline_seconds'] != 120):
            raise ValueError('The saved shared investigation bounds changed')
        return PROGRAM_COMPLETION
    if version is None or version in (1, 2, 3, 4, 5, 6):
        return LEGACY_COMPLETION
    raise ValueError('Unknown local operation consent')


def final_bound(payload):
    cap = consent_cap(payload.get('local_planning_receipt', {}))
    actual = payload.get('diagnostics', {}).get('request_completion_limit',
        LEGACY_COMPLETION if cap == LEGACY_COMPLETION else None)
    if type(actual) is not int or actual not in (LEGACY_COMPLETION, PROGRAM_COMPLETION) or actual > cap:
        raise ValueError('The final request bound is unavailable')
    return actual


def model_cap(connection, identity):
    sql = '''SELECT j.payload FROM planning_jobs j JOIN model_attempts a ON a.job_id=j.id
             WHERE a.id=?'''
    cursor = (connection.exec_driver_sql(sql, (identity,)) if hasattr(connection, 'exec_driver_sql')
              else connection.execute(sql, (identity,)))
    row = cursor.fetchone()
    return consent_cap(json.loads(row[0]).get('local_planning_receipt', {})) if row else LEGACY_COMPLETION
