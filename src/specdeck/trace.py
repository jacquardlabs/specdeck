"""The trace: an OTel GenAI event log.

Everything downstream reads this — the wires engine, the judge, the report, and every
coverage denominator. An `invoke_agent` -> `chat` -> `execute_tool` span tree with
`gen_ai.*` attributes and content in span events, so an agent already emitting OTel needs
no adapter.

Attribute names come from `open-telemetry/semantic-conventions-genai`. Every one of them
is Development status, which is why the semconv version is a lockfile pin rather than an
assumption.

Validation is strict and happens here, at the boundary. A malformed trace fails on the way
in, naming the span and the field, rather than reading as a passing wire further down.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, model_validator

from .provider import DEFAULT_PROVIDER

#: The semconv version specdeck's own loop emits. A recorded trace declares its own and is
#: read as it stands; this is only what `loop.run_agent` stamps on a trace it just built.
SEMCONV = "semantic-conventions-genai@1.38.0"

#: What `loop.run_agent` writes to `gen_ai.provider.name` when the adapter named none. Not
#: a semconv value — the well-known set has no "unknown" — so it is a placeholder to read
#: past rather than a provider to key a rate table on.
UNKNOWN_PROVIDER = "unknown"

#: And what it writes to `gen_ai.request.model` for an adapter that reported none. Named
#: here rather than spelled at each reader, because a budget cap has to recognise it: a
#: span saying "unknown" prices at no rate at all, and charging it zero is the silent
#: spend the cap exists to refuse.
UNKNOWN_MODEL = "unknown"

#: OTel's general-purpose span attribute, not a `gen_ai.*` one, which is why it sits
#: outside `GenAI`. An adapter sets it to mark a tool call that failed; nothing specdeck
#: writes today does, so the waste classifiers fall back to reading the result text.
ERROR_TYPE = "error.type"


class Operation(StrEnum):
    """The three `gen_ai.operation.name` values the card palette selects on."""

    INVOKE_AGENT = "invoke_agent"
    CHAT = "chat"
    EXECUTE_TOOL = "execute_tool"


class GenAI:
    """Semconv attribute names, so nothing downstream free-types one."""

    OPERATION_NAME = "gen_ai.operation.name"
    PROVIDER_NAME = "gen_ai.provider.name"
    AGENT_NAME = "gen_ai.agent.name"
    REQUEST_MODEL = "gen_ai.request.model"
    RESPONSE_MODEL = "gen_ai.response.model"
    RESPONSE_FINISH_REASONS = "gen_ai.response.finish_reasons"
    USAGE_INPUT_TOKENS = "gen_ai.usage.input_tokens"
    USAGE_OUTPUT_TOKENS = "gen_ai.usage.output_tokens"
    TOOL_NAME = "gen_ai.tool.name"
    TOOL_TYPE = "gen_ai.tool.type"
    TOOL_CALL_ID = "gen_ai.tool.call.id"
    TOOL_CALL_ARGUMENTS = "gen_ai.tool.call.arguments"
    TOOL_CALL_RESULT = "gen_ai.tool.call.result"
    INPUT_MESSAGES = "gen_ai.input.messages"
    OUTPUT_MESSAGES = "gen_ai.output.messages"


class Specdeck:
    """The reserved namespace for domain events the GenAI semconv does not define.

    A marker is stamped on the span it describes. In an eval the simulator stamps it; in
    production the agent's own instrumentation does, which is what lets one property serve
    the runtime monitor as well as the eval. Legal names are declared alongside the tool
    vocabulary, so an unknown marker is a lint error rather than a wire that never fires.
    """

    MARKER = "specdeck.marker"


#: Required attributes per operation. The semconv marks more as Recommended; only the ones
#: a wire or the judge cannot do without are enforced.
REQUIRED_ATTRIBUTES: dict[Operation, tuple[str, ...]] = {
    Operation.INVOKE_AGENT: (GenAI.AGENT_NAME,),
    Operation.CHAT: (GenAI.PROVIDER_NAME, GenAI.REQUEST_MODEL),
    Operation.EXECUTE_TOOL: (GenAI.TOOL_NAME,),
}

Message = dict[str, Any]


class SpanEvent(BaseModel):
    """Content, which the semconv keeps out of attributes and in span events."""

    name: str
    attributes: dict[str, Any] = Field(default_factory=dict)


class Span(BaseModel):
    span_id: str
    parent_span_id: str | None = None
    name: str
    start_time: datetime
    end_time: datetime
    attributes: dict[str, Any] = Field(default_factory=dict)
    events: list[SpanEvent] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_semconv(self) -> Span:
        raw = self.attributes.get(GenAI.OPERATION_NAME)
        try:
            operation = Operation(raw)
        except ValueError:
            raise ValueError(
                f"span {self.span_id!r}: {GenAI.OPERATION_NAME} must be one of "
                f"{', '.join(o.value for o in Operation)}, got {raw!r}"
            ) from None
        for required in REQUIRED_ATTRIBUTES[operation]:
            if self.attributes.get(required) is None:
                raise ValueError(
                    f"span {self.span_id!r}: {operation.value} spans require {required}"
                )
        if self.end_time < self.start_time:
            raise ValueError(f"span {self.span_id!r}: end_time is before start_time")
        return self

    @property
    def operation(self) -> Operation:
        return Operation(self.attributes[GenAI.OPERATION_NAME])

    @property
    def duration_s(self) -> float:
        return (self.end_time - self.start_time).total_seconds()

    @property
    def marker(self) -> str | None:
        value = self.attributes.get(Specdeck.MARKER)
        return str(value) if value is not None else None

    @property
    def input_messages(self) -> list[Message]:
        return self._content(GenAI.INPUT_MESSAGES)

    @property
    def output_messages(self) -> list[Message]:
        return self._content(GenAI.OUTPUT_MESSAGES)

    def _content(self, key: str) -> list[Message]:
        for event in self.events:
            if key in event.attributes:
                return list(event.attributes[key])
        return []


class Trace(BaseModel):
    """One run of one card, as an event log."""

    semconv: str
    spans: list[Span]

    @model_validator(mode="after")
    def _check_tree(self) -> Trace:
        if not self.spans:
            raise ValueError("a trace needs at least one span")
        ids: set[str] = set()
        for span in self.spans:
            if span.span_id in ids:
                raise ValueError(f"duplicate span id {span.span_id!r}")
            ids.add(span.span_id)
        roots = [s for s in self.spans if s.parent_span_id is None]
        if len(roots) != 1:
            raise ValueError(
                f"a trace needs exactly one root span, found {len(roots)}: "
                f"{', '.join(sorted(s.span_id for s in roots)) or 'none'}"
            )
        for span in self.spans:
            if span.parent_span_id is not None and span.parent_span_id not in ids:
                raise ValueError(
                    f"span {span.span_id!r}: parent {span.parent_span_id!r} is not in the trace"
                )
        return self

    @property
    def ordered(self) -> list[Span]:
        """Every span, oldest first. The order every temporal pattern reads."""
        return sorted(self.spans, key=lambda s: (s.start_time, s.span_id))

    def of(self, operation: Operation) -> list[Span]:
        return [s for s in self.ordered if s.operation is operation]

    @property
    def root(self) -> Span:
        return next(s for s in self.spans if s.parent_span_id is None)

    @property
    def reports_output_tokens(self) -> bool:
        """Whether any chat span carries usage at all.

        The attribute is Recommended, not Required, so a trace without it is valid. A
        token bound over such a trace would read 0 and pass forever, which is why the
        distinction between "used none" and "did not say" is kept rather than summed away.
        """
        return any(
            s.attributes.get(GenAI.USAGE_OUTPUT_TOKENS) is not None for s in self.of(Operation.CHAT)
        )

    @property
    def unreported_chat_spans(self) -> dict[str, int]:
        """How many chat spans reported no usage at all, per model.

        `usage_by_model` folds a model's spans into one pair, so one span carrying counts
        makes every silent span beside it look metered. An adapter that attaches usage to
        the final message of a turn and to nothing else is a shape, not a bug —
        `tests/fake_agent.refuses` has it — so the count is kept rather than summed away,
        the way `reports_output_tokens` keeps "used none" apart from "did not say".
        """
        silent: dict[str, int] = {}
        for span in self.of(Operation.CHAT):
            if any(
                span.attributes.get(name) is not None
                for name in (GenAI.USAGE_INPUT_TOKENS, GenAI.USAGE_OUTPUT_TOKENS)
            ):
                continue
            model = qualified_model(span)
            silent[model] = silent.get(model, 0) + 1
        return silent

    @property
    def total_output_tokens(self) -> int:
        return sum(
            int(s.attributes.get(GenAI.USAGE_OUTPUT_TOKENS) or 0) for s in self.of(Operation.CHAT)
        )

    @property
    def usage_by_model(self) -> dict[str, tuple[int | None, int | None]]:
        """Reported input and output tokens per model, over this trace's `chat` spans.

        The one place a token count is read off a trace: the cost estimate, the token
        baseline and the budget cap all group the same way, so they cannot disagree about
        which model spent what. Keyed by `qualified_model`, so the provider the span named
        survives into the key and a rate table can price a model the default never serves.

        A half stays `None` when no span of that model reported it — `reports_output_tokens`
        keeps "used none" and "did not say" apart, and summing an absent count to 0 here
        would put the second back as the first further down.
        """
        totals: dict[str, tuple[int | None, int | None]] = {}
        for span in self.of(Operation.CHAT):
            model = qualified_model(span)
            seen_input, seen_output = totals.get(model, (None, None))
            totals[model] = (
                reported_sum(seen_input, span.attributes.get(GenAI.USAGE_INPUT_TOKENS)),
                reported_sum(seen_output, span.attributes.get(GenAI.USAGE_OUTPUT_TOKENS)),
            )
        return totals

    @property
    def final_response(self) -> str:
        """The last assistant message of the last `chat` span — what the judge reads."""
        for span in reversed(self.of(Operation.CHAT)):
            for message in reversed(span.output_messages):
                if message.get("role") == "assistant" and message.get("content"):
                    return str(message["content"])
        return ""


def qualified_model(span: Span) -> str:
    """What a `chat` span served, prefixed with its provider when it names a real one.

    `gen_ai.response.model` first, falling back to `gen_ai.request.model`: the response
    names what actually served the call, and only the request is Required.

    A bare id is Anthropic's everywhere in specdeck (`provider.split_model`), so the prefix
    marks a departure from that default and `anthropic/x` stays `x`. Carrying it is what
    lets a rate table price a model the default provider never serves: without it a span
    naming `openai` and `gpt-4o` reads as Anthropic's `gpt-4o` and reports n/a against a
    `[rates.openai]` section that prices it.
    """
    name = str(span.attributes.get(GenAI.RESPONSE_MODEL) or span.attributes[GenAI.REQUEST_MODEL])
    provider = str(span.attributes.get(GenAI.PROVIDER_NAME) or "")
    if "/" in name or provider in ("", UNKNOWN_PROVIDER, DEFAULT_PROVIDER):
        return name
    return f"{provider}/{name}"


def reported_sum(*counts: Any) -> int | None:
    """Sum token counts that were actually reported, or None when none of them were.

    The rule every usage total in the codebase folds with. Adding an absent count as zero
    turns a trace that stayed silent into one claiming it spent nothing, which is the
    distinction `Trace.reports_output_tokens` exists to hold.
    """
    reported = [int(count) for count in counts if count is not None]
    return sum(reported) if reported else None
