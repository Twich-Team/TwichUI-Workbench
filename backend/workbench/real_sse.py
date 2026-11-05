"""Bounded SSE decoding for the fixed real-task adapter; never reconnect/replay."""

import json

from workbench.real_primary import PrimaryHeld
from workbench.real_execution import LIMITS

STREAM_BYTES_LIMIT = LIMITS["response_sse_bytes"]
LINE_BYTES_LIMIT = LIMITS["response_line_bytes"]
ASSEMBLED_BYTES_LIMIT = LIMITS["response_assembled_bytes"]


class AssembledLimit(PrimaryHeld):
    def __init__(self, size):
        self.size = size
        super().__init__("Stream exceeds bounded output: assembled_bytes")


def fail(report, code):
    report["stream_error"] = code
    if code in ("stream_bytes", "line_bytes", "assembled_bytes"):
        report["output_limits_exceeded"] = [code]
        raise PrimaryHeld("Stream exceeds bounded output: " + code)
    raise PrimaryHeld("Malformed or incomplete stream: " + code)


def events(response, report, allowed):
    """Join data lines only at an event boundary; decode UTF8 after byte framing.

    readline on HTTPResponse already spans HTTP chunks/socket fragments. The small
    pending buffer also handles short reads in scripted/custom readers. No raw
    payload enters diagnostics; buffered bytes remain under the raw response cap.
    """
    pending = b""
    data = []
    while True:
        if not allowed():
            raise PrimaryHeld("Lease/grant no longer current")
        raw = response.readline(
            min(LINE_BYTES_LIMIT + 1 - len(pending), STREAM_BYTES_LIMIT + 1 - report["wire_bytes"])
        )
        if not raw:
            if pending or data:
                fail(report, "incomplete_event")
            return
        report["wire_bytes"] += len(raw)
        pending += raw
        report["largest_line_bytes"] = max(report["largest_line_bytes"], len(pending))
        if report["wire_bytes"] > STREAM_BYTES_LIMIT:
            fail(report, "stream_bytes")
        if len(pending) > LINE_BYTES_LIMIT:
            fail(report, "line_bytes")
        if not pending.endswith(b"\n"):
            continue
        report["wire_lines"] += 1
        line, pending = pending.removesuffix(b"\n").removesuffix(b"\r"), b""
        if not line:
            if data:
                report["event_count"] += 1
                yield b"\n".join(data)
                data = []
        elif line.startswith(b":"):
            report["comment_lines"] += 1
        else:
            field, _, value = line.partition(b":")
            if field == b"data":
                data.append(value.removeprefix(b" "))
            else:
                # SSE event/id/retry metadata is not generated model content and
                # never enables reconnect/replay. Unknown fields are ignored.
                report["metadata_lines"] += 1
                if field == b"event" and value.removeprefix(b" ") not in (b"", b"message"):
                    fail(report, "event_type")


def decode(data, report):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError()
            result[key] = value
        return result

    def constant(_):
        raise ValueError()

    try:
        result = json.loads(data.decode("utf-8"), object_pairs_hook=pairs, parse_constant=constant)
    except ValueError, RecursionError:
        fail(report, "event_json")
    if not isinstance(result, dict):
        fail(report, "event_object")
    return result


def numeric_usage(raw):
    """Retain only known numeric counters, never arbitrary provider metadata."""
    if not isinstance(raw, dict):
        raise PrimaryHeld("Malformed streamed usage")

    def counter(value):
        if type(value) is not int or not 0 <= value <= 2**31 - 1:
            raise PrimaryHeld("Malformed streamed usage counter")
        return value

    result = {
        key: counter(raw[key])
        for key in ("prompt_tokens", "completion_tokens", "total_tokens")
        if key in raw
    }
    for key, field in [
        ("prompt_tokens_details", "cached_tokens"),
        ("completion_tokens_details", "reasoning_tokens"),
    ]:
        if key in raw:
            if not isinstance(raw[key], dict):
                raise PrimaryHeld("Malformed streamed usage details")
            result[key] = {field: counter(raw[key][field])} if field in raw[key] else {}
    if all(k in result for k in ("prompt_tokens", "completion_tokens", "total_tokens")) and (
        result["total_tokens"] != result["prompt_tokens"] + result["completion_tokens"]
    ):
        raise PrimaryHeld("Inconsistent streamed usage totals")
    return result


def merge_chunk(result, chunk):
    """Append supported delta fields once; metadata never joins generated text."""

    def size(value):
        return len(
            json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode(
                "utf-8"
            )
        )

    def check(extra):
        projected = assembled_size(result) + extra
        if projected > ASSEMBLED_BYTES_LIMIT:
            raise AssembledLimit(projected)

    def put(container, key, value, append=False):
        if key in container:
            extra = size(value) - 2 if append else size(value) - size(container[key])
        else:
            extra = size(key) + 1 + size(value) + (1 if container else 0)
        check(extra)
        container[key] = container[key] + value if append else value

    if not isinstance(chunk, dict) or "error" in chunk:
        raise PrimaryHeld("Primary returned an invalid/error stream event")
    model = chunk.get("model")
    if model is not None:
        if (
            not isinstance(model, str)
            or not model
            or (result.get("model") and result["model"] != model)
        ):
            raise PrimaryHeld("Stream model identity changed or malformed")
        put(result, "model", model)
    usage = chunk.get("usage")
    if usage is not None:
        usage = numeric_usage(usage)
        if result.get("usage") is not None and result["usage"] != usage:
            raise PrimaryHeld("Conflicting or malformed streamed usage")
        put(result, "usage", usage)
    choices = chunk.get("choices", [])
    if not isinstance(choices, list) or len(choices) > 1:
        raise PrimaryHeld("Multiple or malformed streamed choices refused")
    for choice in choices:
        if (
            not isinstance(choice, dict)
            or type(choice.get("index", 0)) is not int
            or choice.get("index", 0) != 0
        ):
            raise PrimaryHeld("Multiple or malformed streamed choices refused")
        delta = choice.get("delta", {})
        if not isinstance(delta, dict):
            raise PrimaryHeld("Malformed streamed delta")
        selected = result["choices"][0]
        if selected["finish_reason"] is not None and delta:
            raise PrimaryHeld("Model delta after stream finish refused")
        message = selected["message"]
        for key in ("content", "reasoning_content", "role"):
            fragment = delta.get(key)
            if fragment is None:
                continue
            if not isinstance(fragment, str) or (key == "role" and fragment != "assistant"):
                raise PrimaryHeld("Malformed streamed text/role")
            put(message, key, fragment, key != "role" and key in message)
        calls = delta.get("tool_calls")
        if calls is not None:
            if not isinstance(calls, list) or len(calls) > 8:
                raise PrimaryHeld("Malformed streamed tools")
            if "tool_calls" not in message:
                put(message, "tool_calls", [])
            current_calls = message["tool_calls"]
            for call in calls:
                index = call.get("index") if isinstance(call, dict) else None
                if type(index) is not int or not 0 <= index < 8 or index > len(current_calls):
                    raise PrimaryHeld("Invalid streamed tool index")
                if index == len(current_calls):
                    empty = dict(id="", type="function", function=dict(name="", arguments=""))
                    check(size(empty) + (1 if current_calls else 0))
                    current_calls.append(empty)
                current = current_calls[index]
                if call.get("type", "function") != "function":
                    raise PrimaryHeld("Unsupported streamed tool type")
                function = call.get("function", {})
                if not isinstance(function, dict):
                    raise PrimaryHeld("Malformed streamed tool function")
                for key, container, value in [
                    ("id", current, call.get("id")),
                    ("name", current["function"], function.get("name")),
                    ("arguments", current["function"], function.get("arguments")),
                ]:
                    if value is not None:
                        if not isinstance(value, str):
                            raise PrimaryHeld("Malformed streamed tool fragment")
                        # Installed common_chat_msg_diff sends full updated name/
                        # id when discovery grows; ONLY arguments are deltas.
                        if (
                            key != "arguments"
                            and container[key]
                            and not value.startswith(container[key])
                        ):
                            raise PrimaryHeld("Streamed tool identity changed incompatibly")
                        put(container, key, value, key == "arguments")
        reason = choice.get("finish_reason")
        if reason is not None:
            if (
                reason not in ("stop", "tool_calls", "length", "content_filter")
                or selected["finish_reason"] is not None
            ):
                raise PrimaryHeld("Invalid or repeated stream finish")
            put(selected, "finish_reason", reason)


def assembled_size(result):
    # Wire and assembled limits measure UTF8 bytes. Escapes/JSON structure count;
    # repeated per-event metadata discarded by merge_chunk does not accumulate.
    return len(
        json.dumps(result, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode(
            "utf-8"
        )
    )


def validate_finish(result):
    """Malformed/ambiguous tool identities cannot dispatch even one tool."""
    calls = result["choices"][0]["message"].get("tool_calls", [])
    identities = []
    for call in calls:
        identity = call["id"]
        if not identity or len(identity) > 256 or identity in identities:
            raise PrimaryHeld("Missing, oversized or duplicated completed tool identity")
        identities.append(identity)
        if not call["function"]["name"]:
            raise PrimaryHeld("Missing completed tool name")
