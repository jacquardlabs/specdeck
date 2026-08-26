"""Reading a recorded event log off disk.

Two shapes, one type out. specdeck's own JSON is the `Trace` model verbatim. OTLP/JSON is
what an agent already emitting OTel exports, and accepting it directly is the point of the
locked trace decision: that agent needs no adapter.

The span tree is the contract, not the file format. A third source — an Inspect `.eval`
log, a live adapter — plugs in behind the same `Trace`.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from .trace import Span, SpanEvent, Trace

NANOS_PER_SECOND = 1_000_000_000

#: Every span of one saved trace shares a trace id. A constant rather than a random one so
#: a saved trace is byte-stable across runs of the same conversation — the property that
#: lets `tools/make_traces.py --check` exist, and the one a committed fixture needs.
TRACE_ID = "4bf92f3577b34da6a3ce929d0e0e4736"


class TraceError(Exception):
    """The file is not a readable event log. Always names the file."""


def load_trace(path: Path | str, *, semconv: str | None = None) -> Trace:
    path = Path(path)
    try:
        payload = json.loads(path.read_text())
    except FileNotFoundError:
        raise TraceError(f"no trace at {path}") from None
    except json.JSONDecodeError as error:
        raise TraceError(f"{path}: not JSON ({error.msg} at line {error.lineno})") from None

    try:
        if "resourceSpans" in payload:
            return _from_otlp(payload, semconv)
        if "spans" in payload:
            return Trace.model_validate(payload | ({"semconv": semconv} if semconv else {}))
    except ValidationError as error:
        raise TraceError(f"{path}: {_first_message(error)}") from None
    except TraceError as error:
        raise TraceError(f"{path}: {error}") from None
    raise TraceError(f"{path}: expected a `spans` list or an OTLP `resourceSpans` export")


def _first_message(error: ValidationError) -> str:
    first = error.errors()[0]
    return str(first.get("msg", "")).removeprefix("Value error, ")


def _from_otlp(payload: dict[str, Any], semconv: str | None) -> Trace:
    spans: list[Span] = []
    version = semconv
    for resource in payload.get("resourceSpans") or []:
        for scope_spans in resource.get("scopeSpans") or []:
            version = version or (scope_spans.get("scope") or {}).get("version")
            spans += [_span(raw) for raw in scope_spans.get("spans") or []]
    if not version:
        raise TraceError(
            "the instrumentation scope carries no version; pass the semconv the trace "
            "was recorded against"
        )
    return Trace(semconv=version, spans=spans)


def _span(raw: dict[str, Any]) -> Span:
    for required in ("spanId", "startTimeUnixNano", "endTimeUnixNano"):
        if required not in raw:
            raise TraceError(f"OTLP span {raw.get('spanId', '<unnamed>')!r} is missing {required}")
    return Span(
        span_id=raw["spanId"],
        # OTLP writes the absent parent as an empty string, not as null
        parent_span_id=raw.get("parentSpanId") or None,
        name=raw.get("name", ""),
        start_time=_timestamp(raw["startTimeUnixNano"]),
        end_time=_timestamp(raw["endTimeUnixNano"]),
        attributes=_attributes(raw.get("attributes")),
        events=[
            SpanEvent(name=event.get("name", ""), attributes=_attributes(event.get("attributes")))
            for event in raw.get("events") or []
        ],
    )


def _timestamp(nanos: str | int) -> datetime:
    return datetime.fromtimestamp(int(nanos) / NANOS_PER_SECOND, tz=UTC)


def _attributes(raw: list[dict[str, Any]] | None) -> dict[str, Any]:
    for entry in raw or []:
        if "key" not in entry or "value" not in entry:
            raise TraceError(f"OTLP attribute entry is missing key or value: {entry!r}")
    return {entry["key"]: _value(entry["value"]) for entry in raw or []}


def _value(value: dict[str, Any]) -> Any:
    """One OTLP AnyValue. Ints arrive as strings, and lists and maps nest."""
    if "stringValue" in value:
        return value["stringValue"]
    if "intValue" in value:
        return int(value["intValue"])
    if "doubleValue" in value:
        return float(value["doubleValue"])
    if "boolValue" in value:
        return bool(value["boolValue"])
    if "arrayValue" in value:
        return [_value(item) for item in value["arrayValue"].get("values") or []]
    if "kvlistValue" in value:
        return {
            entry["key"]: _value(entry["value"])
            for entry in value["kvlistValue"].get("values") or []
        }
    return None


def dump_trace(trace: Trace, *, service_name: str | None = None) -> dict[str, Any]:
    """A trace as an OTLP/JSON export — the round trip of `load_trace` (#112).

    OTLP rather than specdeck's own `{"spans": [...]}` shape, which `load_trace` also
    reads, because a saved trace has to be interchangeable with one a real exporter
    produced. A file only this project can read would let "an agent already emitting
    OpenTelemetry needs no adapter" go untested by the very artefacts meant to demonstrate
    it — the same reason `tools/make_traces.py` writes OTLP rather than the easier format.

    `load_trace(dump_trace(t))` is the property that matters and the one the tests hold.
    """
    return {
        "resourceSpans": [
            {
                "resource": {
                    "attributes": _dump_attributes({"service.name": service_name})
                    if service_name
                    else []
                },
                "scopeSpans": [
                    {
                        "scope": {
                            "name": "opentelemetry.instrumentation.genai",
                            "version": trace.semconv,
                        },
                        "spans": [_dump_span(span) for span in trace.spans],
                    }
                ],
            }
        ]
    }


def _dump_span(span: Span) -> dict[str, Any]:
    raw: dict[str, Any] = {
        "traceId": TRACE_ID,
        "spanId": span.span_id,
        "name": span.name,
        "startTimeUnixNano": _dump_timestamp(span.start_time),
        "endTimeUnixNano": _dump_timestamp(span.end_time),
        "attributes": _dump_attributes(span.attributes),
    }
    # Absent rather than empty for the root: OTLP marks a root by having no parent, and a
    # parent id of "" is a different claim from no parent at all.
    if span.parent_span_id:
        raw["parentSpanId"] = span.parent_span_id
    if span.events:
        raw["events"] = [
            {
                "name": event.name,
                "timeUnixNano": _dump_timestamp(span.end_time),
                "attributes": _dump_attributes(event.attributes),
            }
            for event in span.events
        ]
    return raw


def _dump_timestamp(when: datetime) -> str:
    """Nanoseconds since the epoch, as the string OTLP carries them in."""
    return str(int(when.timestamp() * NANOS_PER_SECOND))


def _dump_attributes(mapping: dict[str, Any]) -> list[dict[str, Any]]:
    return [{"key": key, "value": _dump_value(value)} for key, value in mapping.items()]


def _dump_value(value: Any) -> dict[str, Any]:
    """One Python value as an OTLP AnyValue. The mirror of `_value`, and bool is checked
    before int because `bool` is a subclass of it and `True` is not the integer 1 here."""
    if isinstance(value, bool):
        return {"boolValue": value}
    if isinstance(value, int):
        return {"intValue": str(value)}
    if isinstance(value, float):
        return {"doubleValue": value}
    if isinstance(value, list):
        return {"arrayValue": {"values": [_dump_value(item) for item in value]}}
    if isinstance(value, dict):
        return {"kvlistValue": {"values": _dump_attributes(value)}}
    return {"stringValue": str(value)}
