"""Use one bounded Codex turn inside the existing independently checked run grant."""
import json
from pathlib import Path
import shutil
import time

from workbench import codex_provider, real_confine as confine, real_execution as job
from workbench.real_provider import reviewed_instruction
from workbench.real_workspace import Runtime, Tools, durable, sha

EDIT_SCHEMA = {
    'type': 'object', 'properties': {'old': {'type': 'string'}, 'new': {'type': 'string'}},
    'required': ['old', 'new'], 'additionalProperties': False,
}


def repair_schema(paths):
    return dict(type='object', properties=dict(edits=dict(
        type='array', minItems=1, maxItems=6, items=dict(
            type='object', properties={**EDIT_SCHEMA['properties'],
                'path': dict(type='string', enum=paths)},
            required=['path', 'old', 'new'], additionalProperties=False))),
        required=['edits'], additionalProperties=False)


class CodexRuntime(Runtime):
    def __init__(self, root, selection, stop_event=None):
        super().__init__(root, stop_event)
        self.codex_selection = selection
        self.provider_failure = None

    def identity(self):
        result = super().identity()
        result.update(model=self.codex_selection['model'], model_path=None,
                      codex_selection=self.codex_selection)
        for name in ('codex_runtime.py', 'codex_provider.py', 'provider_routes.py'):
            result['helper_sha256'][name] = sha(Path(__file__).with_name(name).read_bytes())
        return result

    def preflight(self):
        gates = self.static_preflight()
        limits = {'memory.max': str(6 << 30), 'memory.swap.max': '0',
                  'pids.max': '128', 'cpu.max': '200000 100000'}
        try:
            cg = Path(confine.root())
            confined = cg.name.startswith('workbench-real-codex-') and all(
                (cg / name).read_text().strip() == value for name, value in limits.items())
        except (OSError, confine.Unavailable):
            confined = False
        gates.extend(dict(name=name, status='passed' if passed else 'blocked') for name, passed in (
            ('dedicated verification boundary', confined),
            ('physical space', shutil.disk_usage(self.root).free >= 4 << 30),
            ('signed-in connector socket', codex_provider.connector_path() is not None),
        ))
        self.enforce_storage()
        return gates

    def execute(self, identity, fence, permitted):
        if not permitted() or any(gate['status'] != 'passed' for gate in self.preflight()):
            raise ValueError('Codex execution gate changed before inference.')
        durable(self.root / ('ownership-' + identity + '.json'), json.dumps(
            {'owner': job.owner_identity(), 'cgroup': confine.root()}).encode())
        self._request_id, self._fence, self._context_sha = identity, fence, self.context_sha()
        self.implementation_deadline = time.monotonic() + job.LIMITS['implementation_s']
        tools = Tools(self, permitted)
        from workbench.verification_profiles import BUGFIX
        bug = self.recipe and self.recipe.get('verification_profile') == BUGFIX
        if self._context_sha or (bug and not self.controls_valid()):
            raise ValueError('Codex needs a clean, independently prepared task.')
        if bug:
            instructions, schema, prompt_suffix = self.recipe['instruction'], repair_schema(list(self.paths)), (
                'Propose up to six small exact replacements in the approved files. '
                'Change only what is needed for the reported behavior. '
                'Include an existing test edit if the approved scope and evidence require it.')
        else:
            instructions, _ = reviewed_instruction(self)
            if not instructions or len(self.paths) != 1:
                raise ValueError('Codex requires one current independently prepared test.')
            schema = EDIT_SCHEMA
            prompt_suffix = 'Propose one exact replacement in ' + self.paths[0] + '.'
        source = []
        for path, start, end in dict.fromkeys(
                (window['path'], window['start'], window['end']) for window in self.recipe['initial_reads']
                if bug or window['path'] == self.paths[0]):
            excerpt = tools.handle('read_file', dict(path=path, start=start, end=end))
            source.append(dict(path=path, start=start, end=end, text=excerpt) if bug else excerpt)
        if not source:
            raise ValueError('The reviewed task has no bounded source excerpt.')
        prompt = (instructions + '\n' + prompt_suffix +
                  ' Copy old text exactly and include enough context for one unique match. '
                  'Workbench will verify the repair independently. Do not request tools.\n'
                  'Untrusted committed source excerpts:\n' + json.dumps(source, ensure_ascii=True))
        if not permitted():
            raise ValueError('Codex execution permission changed before source transmission.')
        try:
            edit, counted = codex_provider.generate(
                self.codex_selection['model'], self.codex_selection['effort'],
                prompt, schema, stop=lambda: not permitted(),
                **({'input_limit':codex_provider.MAX_BUG_INPUT} if bug else {}))
        except codex_provider.Hold as error:
            self.provider_failure = error
            raise
        durable(self.root / ('codex-usage-' + identity + '.json'), json.dumps(
            dict(fence=fence, model=self.codex_selection['model'], values=counted), sort_keys=True).encode())
        if bug:
            edits = edit.get('edits') if isinstance(edit, dict) and set(edit) == {'edits'} else None
            if (not isinstance(edits, list) or not 1 <= len(edits) <= 6
                    or any(not isinstance(item, dict) or set(item) != {'path', 'old', 'new'}
                           or not isinstance(item['path'], str) or item['path'] not in self.paths
                           or not isinstance(item['old'], str) or not item['old']
                           or not isinstance(item['new'], str)
                           or item['old'] == item['new']
                           or len(item['old'].encode()) + len(item['new'].encode()) > 8192 for item in edits)):
                raise ValueError('Codex returned an invalid or out-of-scope repair.')
            planned = {}
            for item in edits:
                path, old, new = item['path'], item['old'], item['new']
                if path not in planned:
                    planned[path] = (self.workspace / path).read_text()
                if planned[path].count(old) != 1:
                    raise ValueError('A proposed edit did not match exactly once. No repair was applied.')
                planned[path] = planned[path].replace(old, new, 1)
        else:
            if not isinstance(edit, dict) or set(edit) != {'old', 'new'}:
                raise ValueError('Codex did not return the exact replacement schema.')
            edits = [{'path': self.paths[0], **edit}]
        for item in edits:
            tools.handle('replace_text', item)
        if self.candidate_feedback(permitted)['status'] != 'pass':
            raise ValueError('The Codex edit did not pass independent verification.')
        observations = self.failure_evidence(identity, fence)
        return self.freeze_candidate(identity, fence, permitted, observations['usage']['values'] | {
            'correction_count': 0, 'read_input_errors': 0,
        }, observations)

    def failure_evidence(self, identity, fence):
        record = self.root / ('codex-usage-' + identity + '.json')
        if record.is_file():
            receipt = json.loads(record.read_text())
            if receipt['fence'] == fence and receipt['model'] == self.codex_selection['model']:
                return dict(usage=dict(status='complete', units='tokens', values=receipt['values'],
                                       provenance=dict(method='Codex app-server reported turn tokens',
                                                       request_id=identity, fence=fence,
                                                       receipt_sha256=sha(record.read_bytes()))),
                            inference_ran=True, correction_count=0, read_input_errors=0)
        failure = self.provider_failure
        counted = failure.counted if failure else None
        return dict(usage=dict(status='partial' if counted else 'unavailable', units='tokens', values=None,
                               **({'known_values': counted} if counted else {})),
                    inference_ran=False if failure and not failure.started else True if counted else None,
                    provider_terminal=failure.terminal if failure else False,
                    provider_partial_output=failure.partial if failure else False,
                    provider_diagnostics=failure.metadata if failure else {},
                    provider_message=str(failure) if failure else
                    'Codex completion or usage is uncertain. The attempt will not restart automatically.')
