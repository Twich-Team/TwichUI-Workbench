"""Use one Codex proposal inside the existing locally verified test-run grant."""
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
        instructions, _ = reviewed_instruction(self)
        if not instructions or len(self.paths) != 1 or self._context_sha:
            raise ValueError('Codex requires one current independently prepared test and a clean task context.')
        source = []
        for path, start, end in dict.fromkeys(
                (window['path'], window['start'], window['end']) for window in self.recipe['initial_reads']
                if window['path'] == self.paths[0]):
            excerpt = tools.handle('read_file', dict(path=path, start=start, end=end))
            source.append(excerpt)
        if not source:
            raise ValueError('The reviewed test has no bounded source excerpt.')
        prompt = (instructions + '\nPropose one exact replacement in ' + self.paths[0] +
                  '. Copy the old text exactly and include enough context for one unique match. '
                  'The app will check the edit independently. Do not request tools.\n'
                  'Untrusted committed excerpts from the one reviewed test:\n' +
                  json.dumps(source, ensure_ascii=True))
        if not permitted():
            raise ValueError('Codex execution permission changed before source transmission.')
        try:
            edit, counted = codex_provider.generate(
                self.codex_selection['model'], self.codex_selection['effort'],
                prompt, EDIT_SCHEMA, stop=lambda: not permitted())
        except codex_provider.Hold as error:
            self.provider_failure = error
            raise
        durable(self.root / ('codex-usage-' + identity + '.json'), json.dumps(
            dict(fence=fence, model=self.codex_selection['model'], values=counted), sort_keys=True).encode())
        if set(edit) != {'old', 'new'}:
            raise ValueError('Codex did not return the exact replacement schema.')
        tools.handle('replace_text', {'path': self.paths[0], **edit})
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
                    provider_message=str(failure) if failure else
                    'Codex completion or usage is uncertain. The attempt will not restart automatically.')
