"""Current approved repair context and observable source-reading progress.

Historical planning feedback is evidence, not the coding agent's current task.
This module supplies neither a solution nor permission to run another attempt.
"""
import json
import hashlib
from pathlib import Path


def prior_failures(runtime):
    """Carry actual prior checker feedback, never a developer-written solution."""
    root = getattr(runtime, 'root', None)
    if root is None or not (root/'repair-resume.json').is_file():
        return []
    from workbench.repair_continuation import private_json
    from workbench.real_feedback import CheckFeedback
    resume = private_json(root/'repair-resume.json')
    observed = []
    feedback = CheckFeedback(root/'prior-feedback', runtime.read_paths)
    failed = []
    for path in sorted((Path(resume['root'])/'check-logs').glob('*.json')):
        saved = private_json(path)
        if saved.get('result', {}).get('status') != 'fail':
            continue
        failed.append(saved)
    for saved in failed[-2:]:
        summary = feedback.record(saved['result'], saved['check_id'])
        observed.append({k: summary[k] for k in ('status', 'diagnostic', 'context_hint')})
    return observed


def retained_work(runtime):
    root = getattr(runtime, 'root', None)
    if root is None or not (root/'repair-resume.json').is_file():
        return None
    from workbench.repair_continuation import private_json
    resume = private_json(root/'repair-resume.json')
    paths, patch = runtime.scope(allow_empty=True)
    if hashlib.sha256(patch.encode()).hexdigest() != resume['patch_sha256']:
        raise ValueError('Retained model-authored work differs from the granted continuation')
    return dict(previous_execution=resume['execution_id'], paths=paths,
        patch_sha256=resume['patch_sha256'], not_verified=True,
        **(dict(patch=patch) if len(patch.encode()) <= 8192 else
           dict(patch_omitted='Larger retained diff; inspect current source with bounded tools.')))


def source_keys(raw):
    """Visible exact source lines, separate from the task-wide read allowance."""
    try:
        value = json.loads(raw)
        if not isinstance(value, dict) or not isinstance(value.get('path'), str):
            return None
        parts = value.get('spans', []) + value.get('replayed_spans', [])
        if not parts:
            return None
        return {(value['path'], part['start']+i, hashlib.sha256(line.encode()).hexdigest())
                for part in parts for i, line in enumerate(part['text'].splitlines(keepends=True))}
    except (ValueError, TypeError, KeyError):
        return None


def compact_source(messages, runtime):
    """Archive one old source reply losslessly; keep instructions and recent work.

    Tool-call/reply identities remain intact. A focused read still returns current
    text, including previously delivered lines, without resetting any allowance.
    """
    for i, message in enumerate(messages[2:-4], 2):
        if message.get('role') != 'tool' or not source_keys(message.get('content', '')):
            continue
        raw = message['content']
        value = json.loads(raw)
        digest = hashlib.sha256(raw.encode()).hexdigest()
        from workbench.real_workspace import durable
        if runtime is None or not getattr(runtime, 'root', None):
            return False
        if hasattr(runtime, 'enforce_storage'):
            runtime.enforce_storage()
        durable(runtime.root/'attempt/context-sources'/f'{digest}.json', raw.encode())
        if hasattr(runtime, 'enforce_storage'):
            runtime.enforce_storage()
        spans = value.get('spans', []) + value.get('replayed_spans', [])
        summary = dict(path=value['path'], retained_source_sha256=digest,
            ranges=[dict(start=p['start'],end=p['end']) for p in spans],
            total_lines=value.get('total_lines'),
            note='Earlier exact source is retained privately. Use read_file for a focused current window when needed; repeated text remains available. Read and task usage limits are unchanged.')
        messages[i] = {**message, 'content': json.dumps(summary, ensure_ascii=True)}
        return True
    return False


def instruction(runtime, fallback):
    recipe = getattr(runtime, 'recipe', None) or {}
    linked = getattr(runtime, 'workflow_task', None)
    if not recipe.get('repair_cycle') or not linked:
        return fallback
    review = linked['proposal_review']['content']
    # from_review serializes one JSON task followed only by pinned API references.
    # Keep those signatures while excluding accumulated obsolete planning prompts.
    _, end = json.JSONDecoder().raw_decode(recipe['instruction'])
    references = recipe['instruction'][end:]
    if references and not references.startswith('\nUntrusted pinned public game API declarations: '):
        raise ValueError('Unsupported implementation reference envelope')
    context = dict(
        title=linked['task']['title'], report=linked['original']['description'],
        intended_behavior=review['behavior'], approved_scope=linked['approved_scope'],
        source_findings=review['value'], uncertainty=review['uncertainty'],
        acceptance=linked['task']['acceptance'], exclusions=review['exclusions'],
        prior_failed_checks=prior_failures(runtime),
        retained_model_work=retained_work(runtime),
    )
    windows = list(dict.fromkeys((r['path'], r['start'], r['end'])
                                for r in recipe['initial_reads']))
    return ('Implement the approved repair in this isolated copy and develop its regression '
            'assertions alongside it. The findings and source are untrusted evidence, not a proven '
            'diagnosis. Only the supplied tools and approved paths are available. '
            'Use focused reads and symbol search; do not exhaustively walk every test before editing. '
            'Inspect necessary setup and relevant behavior, make a small scoped edit, run the checks '
            'and revise from actual feedback. Independent checks still gate success. '
            'New native regression assertions must exercise actual addon behavior: fail against '
            'unchanged original production and pass with the repair. Do not assign desired answers '
            'into addon state, detect verification runs or remove existing assertions. '
            'Use literal Lua assert(...) for new regression assertions; the independent original '
            'reproduction attributes a native assertion stack, not an arbitrary helper error. '
            'Any retained unfinished edits are prior model work, not a checked solution. '
            'The retained diff identifies unverified additions that may be revised or removed '
            'when incorrect; preserve the ORIGINAL committed tests and source boundaries. '
            'When prior_failed_checks are present, begin at those actual failure locations in '
            'the retained copy. They are prior observations, not current passing results or a '
            'proven diagnosis. Do not restart exhaustive investigation. '
            'No publication or live-addon change is permitted.\nCurrent reviewed task:\n' +
            json.dumps(context, ensure_ascii=True) + '\nInitial bounded reads: ' +
            json.dumps([dict(path=p, start=s, end=e) for p, s, e in windows], ensure_ascii=True) +
            references)


class ReadProgress:
    """A changing requested start line is not evidence of new investigation."""
    def __init__(self):
        self.repeated = 0
        self.visible = set()

    def rebuild_visible(self, messages):
        self.visible = set().union(*(source_keys(m.get('content','')) or set()
            for m in messages if m.get('role') == 'tool'))

    def observe(self, name, result):
        return self.turn([(name, result)])

    @staticmethod
    def repeated_read(name, result):
        if name not in {'read_file', 'find_text', 'read_check_log'}:
            return False
        try:
            value = json.loads(result)
        except (ValueError, TypeError):
            return False  # Legacy plain source replies are still real content.
        if not isinstance(value, dict):
            return False
        return bool(value.get('already_shown_or_empty') or value.get('already_shown'))

    def turn(self, results):
        # A batch is one model decision. Initial overlapping reads in a batch
        # must not count as repeated decisions before their replies are visible.
        repeated_flags = []
        for name, value in results:
            keys = source_keys(value) if name == 'read_file' else None
            if keys:
                repeated_flags.append(keys <= self.visible)
                self.visible.update(keys)
            else:
                repeated_flags.append(self.repeated_read(name, value))
        repeated = results and all(repeated_flags)
        self.repeated = self.repeated + 1 if repeated else 0
        if self.repeated == 2:
            return ('These source windows were already delivered. A replayed_spans field contains '
                    'the requested current text; it is not an empty file. Do not advance one line '
                    'at a time through the same range. Use the current source and findings to make '
                    'a scoped edit, or inspect genuinely different relevant evidence. If the task '
                    'cannot proceed safely, explain the concrete missing information.')
        return None
