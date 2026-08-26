"""The agent under test, as one protocol.

The adapter returns the events. That is the fact the execution-backend decision turned on
(DECISIONS.md, 2026-08-16): a harness transcript is never the agent's trace source, so
this protocol — not the runner — owns trace fidelity. Whatever the agent actually did,
including tool calls the runner never sees, arrives here or not at all.

`run` is required and `describe` is not. Requiring `describe` would exclude a raw-SDK
loop, which is the majority of agents anyone would point at specdeck; a declared graph can
answer it fully, and the Phase-2 lint that consumes it (#21) is written to report which
tier it saw rather than to assume the richest one.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from pydantic import BaseModel, Field

from .trace import Message


class Chat(BaseModel):
    """One model call the agent made."""

    content: str
    finish_reason: str = "stop"
    model: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None


class ToolCall(BaseModel):
    """One tool the agent executed, and what came back — or one a runtime refused.

    A denial is deliberately *not* its own event type. The format encodes one as an
    `execute_tool` span carrying a reserved attribute (docs/card-format.md), so an event
    that produced such a span under another name would be a name disagreeing with what it
    writes. Setting `denied_tool` is what makes this a denial:

        ToolCall(
            name="ap_guardrail",                     # the component that refused
            denied_tool="update_vendor_bank_details",  # what the model asked for
            arguments={...},                          # what it asked with
            result="denied: bank details are changed out of band by Finance",
        )

    `name` then carries the policy component rather than anything that ran, which is why
    `Span.executed_tool` returns None for it: nothing executed, so a card forbidding
    execution has not been violated — while a card forbidding the *request* has.
    """

    name: str
    arguments: dict = Field(default_factory=dict)
    result: str = ""
    call_id: str | None = None
    #: The tool a runtime refused at dispatch. None for an ordinary call.
    denied_tool: str | None = None


#: What one agent turn is made of, in the order it happened. A turn ends when the agent
#: yields to the user, so a single turn may carry several chats and several tool calls.
AgentEvent = Chat | ToolCall


class AgentDescription(BaseModel):
    """What `describe()` returns. Every field optional: introspection depth varies by
    framework, and a check that silently degrades is worse than one that reports its own
    blindness."""

    tools: list[str] = Field(default_factory=list)
    edges: list[tuple[str, str]] = Field(default_factory=list)
    cycles: list[list[str]] = Field(default_factory=list)
    hitl_points: list[str] = Field(default_factory=list)
    #: Which node binds which tools. Edges and cycles name *nodes*; a wire names a *tool*
    #: and matches `execute_tool` spans by name, so without this mapping the two
    #: vocabularies never meet and no wire can be shown to bound a loop. Empty for a
    #: raw-SDK description, where the tool is the only node there is.
    node_tools: dict[str, list[str]] = Field(default_factory=dict)


@runtime_checkable
class AgentAdapter(Protocol):
    """The one thing specdeck needs an agent to be."""

    async def run(
        self, messages: list[Message], tools: list[str], config: dict
    ) -> list[AgentEvent]:
        """Take the conversation so far, act, and return what happened."""
        ...


@runtime_checkable
class Describable(Protocol):
    """The optional half. `isinstance(adapter, Describable)` is the whole check."""

    def describe(self) -> AgentDescription: ...
