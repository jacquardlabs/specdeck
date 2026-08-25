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

#: The semconv version specdeck's own loop emits. A recorded trace declares its own and is
#: read as it stands; this is only what `loop.run_agent` stamps on a trace it just built.
SEMCONV = "semantic-conventions-genai@1.38.0"


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
    def total_output_tokens(self) -> int:
        return sum(
            int(s.attributes.get(GenAI.USAGE_OUTPUT_TOKENS) or 0) for s in self.of(Operation.CHAT)
        )

    @property
    def final_response(self) -> str:
        """The last assistant message of the last `chat` span — what the judge reads."""
        for span in reversed(self.of(Operation.CHAT)):
            for message in reversed(span.output_messages):
                if message.get("role") == "assistant" and message.get("content"):
                    return str(message["content"])
        return ""
