#!/usr/bin/env python3
"""Regenerate the recorded traces under `cards/traces/`.

The traces are fixtures five cards depend on. Without this script they are unreproducible:
a schema change would silently invalidate them, and a hand-edit would be invisible.

Output is OTLP/JSON — the shape an agent already emitting OpenTelemetry exports, and the
branch of `traceio.load_trace` that reads `resourceSpans`. Writing specdeck's own format
here would let the "an agent already emitting OTel needs no adapter" claim go untested by
the very fixtures that are supposed to demonstrate it.

Deterministic by construction: fixed base timestamp, fixed span ids, no clock, no
randomness. `--check` regenerates and diffs against what is committed, so CI catches both
a hand-edited fixture and a schema change that invalidates one.

    python tools/make_traces.py            # write
    python tools/make_traces.py --check    # verify, non-zero on drift

Stdlib only, deliberately: this runs in CI before the package is necessarily installed.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TRACES = ROOT / "cards" / "traces"
FIXTURES = ROOT / "cards" / "fixtures"

#: Must be byte-identical to the pin in cards/spec.lock.toml. `cli._lock`'s relock branch
#: rewrites the *global* semconv pin from whichever trace it was handed, so one trace
#: declaring a different version flips the pin for the whole suite and every other card
#: then fails verify_semconv. See #56.
SEMCONV = "semantic-conventions-genai@1.38.0"

TRACE_ID = "4bf92f3577b34da6a3ce929d0e0e4736"
ROOT_SPAN = "00f067aa0ba902b7"
BASE_NANOS = 1786968000_000_000_000  # 2026-08-16T12:00:00Z
AGENT = "airline-support"
MODEL = "claude-sonnet-5"
PROVIDER = "anthropic"


# -- OTLP encoding -------------------------------------------------------------


def _s(value: str) -> dict:
    return {"stringValue": value}


def _i(value: int) -> dict:
    return {"intValue": str(value)}


def _arr(values: list[dict]) -> dict:
    return {"arrayValue": {"values": values}}


def _kv(mapping: dict[str, str]) -> dict:
    return {"kvlistValue": {"values": [{"key": k, "value": _s(v)} for k, v in mapping.items()]}}


def _attrs(mapping: dict[str, dict]) -> list[dict]:
    return [{"key": k, "value": v} for k, v in mapping.items()]


def _span(
    span_id: str,
    parent: str,
    name: str,
    start_ms: int,
    end_ms: int,
    attributes: dict[str, dict],
    events: list[dict] | None = None,
) -> dict:
    span = {
        "traceId": TRACE_ID,
        "spanId": span_id,
        "parentSpanId": parent,
        "name": name,
        "kind": "SPAN_KIND_INTERNAL",
        "startTimeUnixNano": str(BASE_NANOS + start_ms * 1_000_000),
        "endTimeUnixNano": str(BASE_NANOS + end_ms * 1_000_000),
        "attributes": _attrs(attributes),
    }
    if events:
        span["events"] = events
    return span


# -- the conversation a trace records ------------------------------------------


@dataclass
class Turn:
    """One agent turn: what it said, and the tool it called to say it."""

    content: str
    tool: str | None = None
    arguments: dict = field(default_factory=dict)
    result: str = ""
    #: The tool a hardened runtime refused at dispatch. When set, `tool` is the *policy
    #: component* that refused rather than anything that ran, and the span carries
    #: `specdeck.denied_tool`. That attribute, not a magic tool name, is what makes a span
    #: a denial — see docs/card-format.md. `result` is the refusal the model was handed.
    denied: str | None = None
    #: A `specdeck.marker` domain event on the *user* turn that preceded this one. The
    #: simulator stamps these at runtime; here the recorded conversation declares them.
    marker: str | None = None
    user: str = ""


@dataclass
class Conversation:
    slug: str
    opening: str
    turns: list[Turn]


def build(conversation: Conversation) -> dict:
    """One conversation -> one OTLP export."""
    spans: list[dict] = []
    messages: list[dict[str, str]] = [{"role": "user", "content": conversation.opening}]
    clock = 30

    for index, turn in enumerate(conversation.turns):
        chat_id = f"a1b2c3d4e5f6{index:04d}"
        chat_start, chat_end = clock, clock + 1800
        attributes = {
            "gen_ai.operation.name": _s("chat"),
            "gen_ai.provider.name": _s(PROVIDER),
            "gen_ai.request.model": _s(MODEL),
            "gen_ai.response.model": _s(MODEL),
            "gen_ai.response.finish_reasons": _arr([_s("tool_calls" if turn.tool else "stop")]),
            "gen_ai.usage.input_tokens": _i(sum(len(m["content"]) for m in messages) // 4),
            "gen_ai.usage.output_tokens": _i(max(1, len(turn.content) // 4)),
        }
        if turn.marker:
            attributes["specdeck.marker"] = _s(turn.marker)
        spans.append(
            _span(
                chat_id,
                ROOT_SPAN,
                f"chat {MODEL}",
                chat_start,
                chat_end,
                attributes,
                events=[
                    {
                        "name": "gen_ai.client.inference.operation.details",
                        "timeUnixNano": str(BASE_NANOS + chat_end * 1_000_000),
                        "attributes": _attrs(
                            {
                                "gen_ai.input.messages": _arr([_kv(m) for m in messages]),
                                "gen_ai.output.messages": _arr(
                                    [_kv({"role": "assistant", "content": turn.content})]
                                ),
                            }
                        ),
                    }
                ],
            )
        )
        messages.append({"role": "assistant", "content": turn.content})
        clock = chat_end + 10

        if turn.tool:
            spans.append(
                _span(
                    f"a1b2c3d4e5f7{index:04d}",
                    chat_id,
                    f"execute_tool {turn.tool}",
                    clock,
                    clock + 210,
                    {
                        "gen_ai.operation.name": _s("execute_tool"),
                        "gen_ai.tool.name": _s(turn.tool),
                        "gen_ai.tool.type": _s("function"),
                        "gen_ai.tool.call.id": _s(f"toolu_{index:02d}FhK2mQ"),
                        "gen_ai.tool.call.arguments": _s(json.dumps(turn.arguments)),
                        "gen_ai.tool.call.result": _s(turn.result),
                        **(
                            {"specdeck.denied_tool": _s(turn.denied)}
                            if turn.denied is not None
                            else {}
                        ),
                    },
                )
            )
            messages.append({"role": "tool", "content": turn.result})
            clock += 220

        if turn.user:
            messages.append({"role": "user", "content": turn.user})

    spans.insert(
        0,
        _span(
            ROOT_SPAN,
            "",
            f"invoke_agent {AGENT}",
            0,
            clock,
            {
                "gen_ai.operation.name": _s("invoke_agent"),
                "gen_ai.provider.name": _s(PROVIDER),
                "gen_ai.agent.name": _s(AGENT),
            },
        ),
    )
    return {
        "resourceSpans": [
            {
                "resource": {"attributes": _attrs({"service.name": _s(AGENT)})},
                "scopeSpans": [
                    {
                        "scope": {
                            "name": "opentelemetry.instrumentation.genai",
                            "version": SEMCONV,
                        },
                        "spans": spans,
                    }
                ],
            }
        ]
    }


def fixture(name: str, pointer: str) -> str:
    """A tool result read straight out of the committed fixture, so the two cannot drift."""
    data = json.loads((FIXTURES / name).read_text())
    for key in pointer.split("/"):
        data = data[key]
    return json.dumps(data)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Verify, do not write.")
    args = parser.parse_args()

    from conversations import CONVERSATIONS

    drift = []
    for conversation in CONVERSATIONS:
        path = TRACES / f"{conversation.slug}.otlp.json"
        rendered = json.dumps(build(conversation), indent=2) + "\n"
        if args.check:
            if not path.exists() or path.read_text() != rendered:
                drift.append(path.relative_to(ROOT))
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(rendered)
        print(f"wrote {path.relative_to(ROOT)}")

    if drift:
        print("traces differ from what this script generates:", file=sys.stderr)
        for path in drift:
            print(f"  {path}", file=sys.stderr)
        print("\nrun `python tools/make_traces.py` and commit the result", file=sys.stderr)
        return 1
    if args.check:
        print(f"{len(CONVERSATIONS)} traces match")
    return 0


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    raise SystemExit(main())
