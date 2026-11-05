"""Small synchronized probe witness; no I/O, generation, authority or replay."""
from copy import deepcopy
import threading
import time

from workbench.real_primary import PrimaryHeld


class ProbeWitness:
    """Output proves tokens existed; a fresh slot snapshot proves a live decoder.

    No required coincidence at the first output. All admission decisions are frozen
    before DELETE; subsequent observations can explain cleanup, never change them.
    """
    def __init__(self, epoch, cap, clock=time.monotonic):
        self.epoch, self.cap, self.clock = deepcopy(epoch), cap, clock
        self.condition = threading.Condition()
        self.output_at = None
        self.latest = None
        self.identity = None
        self.fault = None
        self.terminal = False
        self.decision = None
        self.trace = []

    def _note(self, kind, **fields):
        self.trace = (self.trace+[dict(kind=kind, at=self.clock(), **fields)])[-32:]
        self.condition.notify_all()

    def output(self):
        with self.condition:
            if self.output_at is None:
                self.output_at = self.clock()
                self._note('generated_output')

    def finished(self):
        with self.condition:
            self.terminal = True
            self._note('normal_terminal')

    def observe(self, epoch, slot, task, processing, decoded, started_at):
        with self.condition:
            row = dict(epoch=deepcopy(epoch), slot=slot, task=task,
                       is_processing=processing, observed_decoded=decoded,
                       query_started_at=started_at, observed_at=self.clock())
            if epoch != self.epoch:
                self.fault = 'epoch_changed'
            elif type(slot) is not int or type(task) is not int or slot < 0 or task < 0:
                self.fault = 'invalid_identity'
            elif self.identity is not None and self.identity != (slot, task):
                self.fault = 'task_changed'
            elif type(processing) is not bool or type(decoded) is not int or decoded < 0:
                self.fault = 'invalid_decoder_snapshot'
            elif processing is True:
                self.identity = (slot, task)
            self.latest = row
            self._note('slot', **row)

    def invalidate(self, reason):
        with self.condition:
            self.fault = reason
            self._note('invalid_observation', reason=reason)

    def _freeze(self, eligible, reason, witness=None):
        self.decision = dict(eligible=eligible, reason=reason, decided_at=self.clock(),
                             first_output_at=self.output_at, witness=deepcopy(witness))
        return deepcopy(self.decision)

    def consider(self, end, current=True):
        """Deterministic transition used by the actual waiting guard and event tests."""
        with self.condition:
            if self.decision is not None:
                return deepcopy(self.decision)
            if not current:
                return self._freeze(False, 'fenced')
            if self.fault:
                return self._freeze(False, self.fault)
            if self.terminal:
                return self._freeze(False, 'completed_before_cancel')
            if self.clock() >= end:
                return self._freeze(False, 'witness_deadline')
            row = self.latest
            fresh = row and self.output_at is not None and row['query_started_at'] >= self.output_at
            if fresh:
                if row['is_processing'] is False:
                    return self._freeze(False, 'idle_before_cancel')
                count = row['observed_decoded']
                if row['is_processing'] is True and type(count) is int and 0 < count < self.cap:
                    return self._freeze(True, 'live_owned_decoder', row)
            return None

    def wait(self, end, current):
        with self.condition:
            while True:
                decision = self.consider(end, current())
                if decision is not None:
                    return decision
                self.condition.wait(timeout=min(.05, max(0, end-self.clock())))

    def snapshot(self):
        with self.condition:
            return deepcopy(dict(first_output_at=self.output_at, latest=self.latest,
                                 decision=self.decision, trace=self.trace))

    def reject(self, reason):
        with self.condition:
            return deepcopy(self.decision) if self.decision is not None else self._freeze(False, reason)


def cancel_probe(witness, session, native, deadline, current):
    """The exact guard/persist/cancel/reconcile sequence. No cleanup-as-success path."""
    # The original30s includes23s acquisition, <=5s reconciliation and <=2s audit.
    decision = witness.wait(deadline-7, current)
    decision['cancel_deadline'] = min(deadline-2, witness.clock()+5)
    session.capture_probe_decision(decision)  # Durable BEFORE cancellation, including failures.
    if not decision['eligible']:
        raise PrimaryHeld('Probe witness unavailable: '+decision['reason'])
    session.bind_observed_task(decision['witness']['task'])
    cancellation = native(decision['witness'], decision['cancel_deadline'])
    session.cancel_and_reconcile(cancellation.delete, cancellation.observe,
                               timeout=max(0, decision['cancel_deadline']-witness.clock()))
    return decision


def classify_probe(event, audit, elapsed):
    """Lane release differs from a capability pass; missing audit cannot mean success."""
    decision = event.get('probe_decision') or {}
    passed = (decision.get('eligible') is True and event.get('canceled') is True
              and event.get('completed') is False and event.get('fence_state') == 'released'
              and elapsed <= 30 and audit.get('journal_readable') is True
              and audit.get('owned_cancel_queued_logged') is True
              and audit.get('cancel_precedes_stop') is True
              and audit.get('owned_stop_logged') is True
              and audit.get('normal_completion_timing_logged') is False
              and audit.get('error_or_loss_observed') is False)
    return bool(passed)
