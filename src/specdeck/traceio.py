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
