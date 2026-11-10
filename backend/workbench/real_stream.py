"""Bounded named SSE for the fixed primary. Closing HTTP never releases ownership."""
import json
import threading
import time
import urllib.request

from workbench.planning_transport import LocalHTTP
from workbench.real_primary import NativeCancellation, PrimaryHeld, PrimarySession
from workbench.real_probe import ProbeWitness, cancel_probe

from workbench.real_sse import (STREAM_BYTES_LIMIT, LINE_BYTES_LIMIT, ASSEMBLED_BYTES_LIMIT,
    events, decode, merge_chunk, assembled_size, fail, AssembledLimit, validate_finish)

WIRE_COMPLETION_LIMIT = 2496  # authorized2500; conservative four-token reserve


class NamedStream:
    """One client with per-request fences; caller owns authenticated maintenance lease.

    The lease predicate and server epoch are checked before sending, while reading,
    and before releasing. Probe uses the identical request/cancellation transport.
    """
    def __init__(self, secret, state, epoch, permitted, evidence):
        self.secret, self.state, self.epoch = secret, state, epoch
        self.permitted, self.evidence = permitted, evidence
        self.expected_epoch = epoch()
        self.validated = False
        self.sequence = 0

    def current(self):
        return self.permitted() and self.epoch() == self.expected_epoch

    def count_input_tokens(self, body, deadline):
        """Installed non-generating template/tokenizer, including tools/history."""
        if not self.current() or time.monotonic() >= deadline:
            raise PrimaryHeld('Token count fenced before request')
        body = {**body, 'stream': True, 'stream_options': {'include_usage': True}, 'n': 1}
        request = urllib.request.Request('http://127.0.0.1:8080/v1/chat/completions/input_tokens',
            data=json.dumps(body).encode(), headers={'Authorization': 'Bearer '+self.secret,
                                                    'Content-Type': 'application/json'}, method='POST')
        with LocalHTTP() as client:
            client.ends_at = min(client.ends_at, deadline)
            raw = client.read(request, 4096, timeout=min(3, max(.01, deadline-time.monotonic())))
        try:
            count = json.loads(raw).get('input_tokens')
        except (ValueError, AttributeError):
            raise PrimaryHeld('Malformed input-token count') from None
        if type(count) is not int or count <= 0 or not self.current() or time.monotonic() >= deadline:
            raise PrimaryHeld('Unverified or fenced input-token count')
        return count

    def request(self, body, permitted, deadline, *, probe=False):
        self.sequence += 1
        # The caller already binds this deadline to the current operation and
        # grant. Keep time for native cancellation before that absolute bound.
        read_end = deadline-(7 if probe else 5)
        def allowed():
            return self.current() and permitted() and time.monotonic() < deadline
        if not allowed() or time.monotonic() >= read_end:
            raise PrimaryHeld('Lease/attempt deadline or epoch changed before request')
        body = {**body, 'stream': True, 'stream_options': {'include_usage': True}, 'n': 1}
        if not 1 <= body.get('max_tokens', 0) <= (60 if probe else WIRE_COMPLETION_LIMIT):
            raise PrimaryHeld('Wire generation ceiling exceeded')
        result = dict(choices=[dict(message={'role': 'assistant'}, finish_reason=None)])
        report = dict(sequence=self.sequence, purpose='cancellation-probe' if probe else 'implementation',
                      max_generated=body['max_tokens'], started_at=time.time(), wire_started=False,
                      canceled=False, completed=False, usage=None, wire_bytes=0,
                      largest_line_bytes=0, wire_lines=0, event_count=0, comment_lines=0,
                      metadata_lines=0, assembled_bytes=assembled_size(result),
                      output_limits_exceeded=[], stream_error=None,
                      limits=dict(stream_bytes=STREAM_BYTES_LIMIT, line_bytes=LINE_BYTES_LIMIT,
                                  assembled_bytes=ASSEMBLED_BYTES_LIMIT))
        try:
            with PrimarySession(self.state, [], str(self.sequence), allowed) as session:
                # Persist native identity independently of generated content.
                # Sequence/task numbers can recur after a server restart.
                session.record.update(server_epoch=self.expected_epoch)
                session._save()
                report.update(server_epoch=self.expected_epoch, conversation_nonce=session.record['nonce'])
                headers = session.begin(streaming=True)
                headers.update({'Authorization': 'Bearer '+self.secret, 'Content-Type': 'application/json'})
                request = urllib.request.Request('http://127.0.0.1:8080/v1/chat/completions',
                                               data=json.dumps(body).encode(), headers=headers, method='POST')
                done = threading.Event()
                binding = {}
                witness = ProbeWitness(self.expected_epoch, body['max_tokens']) if probe else None
                def observe():
                    while not done.wait(.04):
                        try:
                            observed_start = time.monotonic()
                            before_epoch = self.epoch()
                            req = urllib.request.Request('http://127.0.0.1:8080/slots',
                                         headers={'Authorization': 'Bearer '+self.secret})
                            with LocalHTTP() as c:
                                c.ends_at = min(c.ends_at, deadline)
                                slots = json.loads(c.read(req, 65536, timeout=1))
                            after_epoch = self.epoch()
                            if witness:
                                if before_epoch != after_epoch or after_epoch != self.expected_epoch:
                                    witness.invalidate('epoch_changed')
                                elif len(slots) != 1:
                                    witness.invalidate('ambiguous_slots')
                                else:
                                    s = slots[0]
                                    # Startup idle may precede the request; not an owned-task witness.
                                    if s.get('is_processing') is True or witness.output_at is not None:
                                        witness.observe(after_epoch, s.get('id'), s.get('id_task'),
                                            s.get('is_processing'), s.get('next_token', [{}])[0].get('n_decoded'),
                                            observed_start)
                            busy = [s for s in slots if s.get('is_processing') is True]
                            if len(busy) == 1:
                                s = busy[0]
                                # Persist task ownership while the grant is current, so revocation
                                # can still cancel this task without authorizing a late result.
                                if 'task_id' not in session.record and allowed():
                                    session.bind_observed_task(s['id_task'])
                                if session.record.get('task_id') != s['id_task']:
                                    raise PrimaryHeld('Observed task changed within exclusive request')
                                binding.update(task=s['id_task'], slot=s['id'],
                                               observed_at=time.time(),
                                               observed_decoded=s.get('next_token', [{}])[0].get('n_decoded'))
                        except Exception:
                            pass  # Unknown observation cannot release the fence.
                observer = threading.Thread(target=observe, daemon=True, name='owned-task-observer')
                observer.start()
                cancellation = None
                try:
                    # Do not inherit LocalHTTP's unrelated planning timeout.
                    # Set the exact stream read limit before its socket monitor
                    # starts. Planning and the short metadata calls are unchanged.
                    class ReadFence:
                        @staticmethod
                        def is_set():
                            try:
                                return not permitted()
                            except Exception:
                                return True
                    client = LocalHTTP()
                    client.ends_at = read_end
                    client.stop_event = ReadFence()
                    with client:
                        report['wire_started'] = True
                        with client.opener.open(request, timeout=max(.01, read_end-time.monotonic())) as response:
                            for data in events(response, report, lambda: allowed() and time.monotonic() < read_end):
                                if data == b'[DONE]':
                                    if result['choices'][0]['finish_reason'] is None:
                                        fail(report, 'done_before_finish')
                                    validate_finish(result)
                                    report['completed'] = True
                                    if witness:
                                        witness.finished()
                                    break
                                chunk = decode(data, report)
                                try:
                                    merge_chunk(result, chunk)
                                except AssembledLimit as error:
                                    report['rejected_assembled_bytes'] = error.size
                                    fail(report, 'assembled_bytes')
                                except (ValueError, TypeError, RecursionError):
                                    fail(report, 'event_shape')
                                report['assembled_bytes'] = assembled_size(result)
                                report['usage'] = result.get('usage')
                                if witness and result['choices'][0]['finish_reason'] is not None:
                                    witness.finished()
                                has_output = any(c.get('delta', {}).get(k) for c in chunk.get('choices', [])
                                                 for k in ('content', 'reasoning_content'))
                                if probe and has_output:
                                    witness.output()
                                    def native(selected, cancel_end):
                                        return NativeCancellation(self.secret, session.record['nonce'],
                                            selected['task'], selected['slot'], self.epoch, self.expected_epoch,
                                            deadline=cancel_end)
                                    cancel_probe(witness, session, native, deadline, allowed)
                                    report['canceled'] = True
                                    break
                finally:
                    done.set()
                    observer.join(timeout=1)
                    report['busy_observation'] = dict(binding)  # Final/debug only, never the guard decision.
                    if witness:
                        if 'probe_decision' not in session.record:
                            session.capture_probe_decision(witness.reject('no_probe_cancellation_decision'))
                        report['probe_evidence'] = witness.snapshot()
                        report['probe_decision'] = session.record.get('probe_decision')
                    try:
                        if session.record['state'] == 'in_flight':
                            cancel_end = min(deadline-(2 if probe else 0), time.monotonic()+5)
                            if binding:
                                if 'task_id' not in session.record:
                                    session.bind_observed_task(binding['task'])
                                cancellation = NativeCancellation(self.secret, session.record['nonce'],
                                    binding['task'], binding['slot'], self.epoch, self.expected_epoch, deadline=cancel_end)
                                session.cancel_and_reconcile(cancellation.delete, cancellation.observe,
                                    timeout=max(0, cancel_end-time.monotonic()))
                            else:
                                # Own nonce is safe to stop, but acknowledgment cannot discharge uncertainty.
                                try:
                                    NativeCancellation(self.secret, session.record['nonce'], -1, -1,
                                        self.epoch, self.expected_epoch, deadline=cancel_end).delete(session.record['nonce'])
                                finally:
                                    session.record['state'] = 'quarantined'
                                    session._save()
                                raise PrimaryHeld('No owned task observed; producer remains quarantined')
                    finally:
                        report['fence_state'] = session.record['state']
                        report['cessation_observations'] = session.record['observations']
            report['usage'] = result.get('usage')
            if probe:
                if not report['canceled'] or report['completed']:
                    raise PrimaryHeld('Probe did not demonstrate cancellation during generation')
            elif not report['completed'] or not self.current() or not permitted():
                raise PrimaryHeld('Incomplete or fenced late result refused')
            return result
        except Exception as error:
            # A received usage receipt survives a later framing/cleanup failure.
            # Caller validates identity/counts; incomplete work remains a failure.
            error.stream_response = result if result.get('usage') is not None else None
            error.stream_partial_response = result
            if report['stream_error'] is None:
                report['stream_error'] = ('request_deadline' if time.monotonic() >= read_end
                                          else 'transport_or_protocol_failure')
            raise
        finally:
            selected = result['choices'][0]
            message = selected['message']
            report['finish_reason'] = selected['finish_reason']
            report['response_parts'] = dict(
                content_bytes=len(message.get('content','').encode('utf-8')),
                reasoning_bytes=len(message.get('reasoning_content','').encode('utf-8')),
                tool_argument_bytes=sum(len(c['function']['arguments'].encode('utf-8')) for c in message.get('tool_calls',[])),
                tool_count=len(message.get('tool_calls',[])))
            report['assembled_bytes'] = assembled_size(result)
            report['usage'] = result.get('usage')
            report['finished_at'] = time.time()
            report['elapsed_s'] = report['finished_at']-report['started_at']
            self.evidence(report)
