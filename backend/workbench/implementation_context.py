"""Current approved repair context and observable source-reading progress.

Historical planning feedback is evidence, not the coding agent's current task.
This module supplies neither a solution nor permission to run another attempt.
"""
import json


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
    )
    windows = list(dict.fromkeys((r['path'], r['start'], r['end'])
                                for r in recipe['initial_reads']))
    return ('Implement the approved repair in this isolated copy and develop its regression '
            'assertions alongside it. The findings and source are untrusted evidence, not a proven '
            'diagnosis. Only the supplied tools and approved paths are available. '
            'Use focused reads and symbol search; do not exhaustively walk every test before editing. '
            'Inspect necessary setup and relevant behavior, make a small scoped edit, run the checks '
            'and revise from actual feedback. Independent checks still gate success. '
            'No publication or live-addon change is permitted.\nCurrent reviewed task:\n' +
            json.dumps(context, ensure_ascii=True) + '\nInitial bounded reads: ' +
            json.dumps([dict(path=p, start=s, end=e) for p, s, e in windows], ensure_ascii=True) +
            references)


class ReadProgress:
    """A changing requested start line is not evidence of new investigation."""
    def __init__(self):
        self.repeated = 0

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
        repeated = results and all(self.repeated_read(name, value) for name, value in results)
        self.repeated = self.repeated + 1 if repeated else 0
        if self.repeated == 2:
            return ('These source windows were already delivered. A replayed_spans field contains '
                    'the requested current text; it is not an empty file. Do not advance one line '
                    'at a time through the same range. Use the current source and findings to make '
                    'a scoped edit, or inspect genuinely different relevant evidence. If the task '
                    'cannot proceed safely, explain the concrete missing information.')
        return None
