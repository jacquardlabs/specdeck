"""The execution loop: a simulated user and an agent, taking turns, emitting a trace.

Our own loop, per the #2 spike (DECISIONS.md, 2026-08-16). It is deliberately small — the
adapter returns the events, so this file owns turn-taking and span assembly and nothing
else. Every fact about what the agent did comes from `AgentAdapter.run`, never from what
the loop observed passing through it.

The trace it builds is the same OTel GenAI shape `traceio` reads back, so a run under this
loop and a raw OTLP export from an instrumented agent are the same input to everything
downstream. That is what makes the loop replaceable without touching a card.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from .agent import AgentAdapter, Chat, ToolCall
from .budget import Budget
from .card import Card
from .simulator import Turn as UserTurn
from .simulator import turn as simulate
from .trace import (
    SEMCONV,
    UNKNOWN_MODEL,
    UNKNOWN_PROVIDER,
    GenAI,
    Message,
    Span,
    SpanEvent,
    Specdeck,
    Trace,
)

DEFAULT_MAX_TURNS = 12
DEFAULT_AGENT_NAME = "agent-under-test"


class LoopError(Exception):
    """The run could not be carried out."""


async def run_agent(
    card: Card,
    adapter: AgentAdapter,
    *,
    cassettes: Path | str,
    simulator_model: str,
    semconv: str = SEMCONV,
    markers: list[str] | None = None,
    tools: list[str] | None = None,
    config: dict | None = None,
    agent_name: str = DEFAULT_AGENT_NAME,
    max_turns: int = DEFAULT_MAX_TURNS,
    live: bool = False,
    budget: Budget | None = None,
) -> Trace:
    """Drive one conversation and return its trace.

    The budget, when there is one, governs the simulator's turns before they happen and
    charges the agent's own tokens after they have. That asymmetry is the shape of the
    problem, not a shortcut: `adapter.run` spends the money and only then reports what it
    spent, so this run's cost can stop the *next* one and never this one.
    """
    if max_turns < 1:
        raise LoopError("a run needs at least one turn")
    markers = markers or []
    started = datetime.now(UTC)
    builder = _Builder(agent_name=agent_name, started=started)
    messages: list[Message] = []

    for _ in range(max_turns):
        spoken = await simulate(
            card.context.simulator,
            messages,
            markers=markers,
            cassettes=cassettes,
            model=simulator_model,
            live=live,
            slug=card.slug,
            budget=budget,
        )
        # The marker lands on the agent turn the user is answering, which is the span it
        # describes: "the traveller did not accept *that*". On the opening turn there is
        # nothing to describe yet, so a marker there is dropped rather than misattributed.
        builder.mark(spoken)
        messages.append({"role": "user", "content": spoken.reply})

        events = await adapter.run(list(messages), list(tools or []), dict(config or {}))
        if not events:
            raise LoopError("the adapter returned no events for a turn it was asked to take")
        messages.extend(builder.record(events, inputs=list(messages)))
        # Checked after the agent has answered, never before it (#108). A conversation
        # ends on the agent's turn, not the user's: the policy of every deck we have makes
        # the agent ask before it writes, so the traveller's last word is "yes" — and a
        # loop that broke on `done` discarded exactly the turn that does the booking. The
        # cap still bounds this: the closing reply is part of this iteration, not an extra
        # one, so `max_turns` remains the number of times the agent speaks.
        if spoken.done:
            break

    trace = Trace(semconv=semconv, spans=builder.finish())
    if budget is not None:
        # Charged from the trace, which is the adapter's own account of what it called —
        # never from the column's declared model, which is what was agreed to be paid for
        # rather than what was spent. Under a cap this refuses a trace it cannot price.
        budget.charge_trace(trace, adapter=type(adapter).__name__)
    return trace


class _Builder:
    """Assembles spans as the conversation happens. Ids are positional and the clock is
    real: latency wires measure the run, so the times cannot be synthesised."""

    ROOT = "0" * 16

    def __init__(self, *, agent_name: str, started: datetime) -> None:
        self.agent_name = agent_name
        self.started = started
        self.spans: list[Span] = []
        self.last_chat: Span | None = None

    def mark(self, spoken: UserTurn) -> None:
        if spoken.marker and self.last_chat is not None:
            self.last_chat.attributes[Specdeck.MARKER] = spoken.marker

    def record(self, events: list, *, inputs: list[Message]) -> list[Message]:
        """One agent turn's events, in order, as spans. Returns the messages they add."""
        added: list[Message] = []
        for event in events:
            if isinstance(event, Chat):
                span = self._chat(event, inputs=inputs + added)
                self.spans.append(span)
                self.last_chat = span
                added.append({"role": "assistant", "content": event.content})
            elif isinstance(event, ToolCall):
                self.spans.append(self._tool(event))
                added.append({"role": "tool", "content": event.result})
            else:
                raise LoopError(f"the adapter returned {type(event).__name__}, not an AgentEvent")
        return added

    def _chat(self, event: Chat, *, inputs: list[Message]) -> Span:
        start = self._now()
        attributes: dict = {
            GenAI.OPERATION_NAME: "chat",
            GenAI.PROVIDER_NAME: UNKNOWN_PROVIDER,
            GenAI.REQUEST_MODEL: event.model or UNKNOWN_MODEL,
            GenAI.RESPONSE_FINISH_REASONS: [event.finish_reason],
        }
        if event.model:
            attributes[GenAI.RESPONSE_MODEL] = event.model
        # Usage is only recorded when the adapter reported it. A zero written in for an
        # adapter that did not say would make a token bound pass forever.
        if event.input_tokens is not None:
            attributes[GenAI.USAGE_INPUT_TOKENS] = event.input_tokens
        if event.output_tokens is not None:
            attributes[GenAI.USAGE_OUTPUT_TOKENS] = event.output_tokens
        return Span(
            span_id=self._id("c"),
            parent_span_id=self.ROOT,
            name=f"chat {event.model or 'agent'}",
            start_time=start,
            end_time=self._now(),
            attributes=attributes,
            events=[
                SpanEvent(
                    name="gen_ai.client.inference.operation.details",
                    attributes={
                        GenAI.INPUT_MESSAGES: inputs,
                        GenAI.OUTPUT_MESSAGES: [{"role": "assistant", "content": event.content}],
                    },
                )
            ],
        )

    def _tool(self, event: ToolCall) -> Span:
        start = self._now()
        return Span(
            span_id=self._id("t"),
            parent_span_id=self.last_chat.span_id if self.last_chat else self.ROOT,
            name=f"execute_tool {event.name}",
            start_time=start,
            end_time=self._now(),
            attributes={
                GenAI.OPERATION_NAME: "execute_tool",
                GenAI.TOOL_NAME: event.name,
                GenAI.TOOL_TYPE: "function",
                GenAI.TOOL_CALL_ID: event.call_id or self._id("call"),
                GenAI.TOOL_CALL_ARGUMENTS: json.dumps(event.arguments),
                GenAI.TOOL_CALL_RESULT: event.result,
                # Present only for a denial, because its presence is what makes the span
                # one. An attribute written as None on every ordinary call would make
                # `Span.denied_tool` answer "not denied" and "denied by nobody" alike.
                **(
                    {Specdeck.DENIED_TOOL: event.denied_tool}
                    if event.denied_tool is not None
                    else {}
                ),
            },
        )

    def finish(self) -> list[Span]:
        root = Span(
            span_id=self.ROOT,
            parent_span_id=None,
            name=f"invoke_agent {self.agent_name}",
            start_time=self.started,
            # A run with no agent turn at all still needs an end at or after its start.
            end_time=max([s.end_time for s in self.spans] + [self.started]),
            attributes={
                GenAI.OPERATION_NAME: "invoke_agent",
                GenAI.AGENT_NAME: self.agent_name,
            },
        )
        return [root, *self.spans]

    def _id(self, prefix: str) -> str:
        return f"{prefix}{len(self.spans):015d}"[:16]

    def _now(self) -> datetime:
        now = datetime.now(UTC)
        # Strictly monotonic: two spans sharing a timestamp make `ordered` fall back to
        # span id, and after-K-then-Y would read the wrong one as first.
        if self.spans and now <= self.spans[-1].end_time:
            return self.spans[-1].end_time + timedelta(microseconds=1)
        return now
