"""A scripted agent adapter, so the loop can be exercised without a model or a network.

It is not a mock of the protocol — it implements it, and `isinstance(agent, AgentAdapter)`
holds. What it fakes is the model behind the agent, which is the part that costs money and
varies run to run.

The script is a list of turns, each a list of `AgentEvent`. Turn N is returned on the Nth
call to `run`, and the last turn repeats if the conversation outlasts the script.
"""

from __future__ import annotations

from specdeck.agent import AgentDescription, Chat, ToolCall


class FakeAgent:
    """Replays a fixed script and records what it was asked."""

    def __init__(self, script: list[list], *, tools: list[str] | None = None) -> None:
        self.script = script
        self.tools = tools or []
        self.calls: list[dict] = []

    async def run(self, messages: list[dict], tools: list[str], config: dict) -> list:
        self.calls.append({"messages": list(messages), "tools": list(tools), "config": config})
        index = min(len(self.calls) - 1, len(self.script) - 1)
        return self.script[index]

    def describe(self) -> AgentDescription:
        return AgentDescription(tools=self.tools)


class BareAgent:
    """`run` and nothing else — the raw-SDK case `describe()` is optional for."""

    async def run(self, messages: list[dict], tools: list[str], config: dict) -> list:
        return [Chat(content="I can help with that.", model="fake-1")]


def refuses(reservation: str = "SI5UKW") -> list[list]:
    """An agent that looks the reservation up, then refuses three times."""
    refusal = "I'm sorry, that fare cannot be cancelled and I cannot offer a credit."
    return [
        [
            Chat(content="Let me pull that up.", finish_reason="tool_calls", model="fake-1"),
            ToolCall(
                name="get_reservation_details",
                arguments={"reservation_id": reservation},
                result='{"cabin": "basic_economy", "insurance": "no"}',
            ),
            Chat(content=refusal, model="fake-1", output_tokens=14),
        ],
        [Chat(content=refusal, model="fake-1", output_tokens=14)],
    ]


def refusing_agent() -> FakeAgent:
    """A zero-argument factory, which is what `--agent module:attribute` resolves."""
    return FakeAgent(refuses(), tools=["get_reservation_details", "cancel_reservation"])
