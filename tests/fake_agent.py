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


#: Every config a `ConfigAgent` was handed, across every column of a matrix. Module-level
#: because `--agent module:attribute` builds a fresh adapter for each column, so an
#: instance attribute could only ever show what one column did.
CONFIG_CALLS: list[dict] = []


class ConfigAgent:
    """An adapter whose whole reply is built from the `config` its column handed it.

    The same faking pattern as `FakeAgent` — a real adapter, a faked model — extended so a
    matrix column can vary what the agent says and what it declares having spent. The
    declared token counts are what make a budget cap testable with no network at all.
    """

    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def run(self, messages: list[dict], tools: list[str], config: dict) -> list:
        self.calls.append(dict(config))
        CONFIG_CALLS.append(dict(config))
        return [
            Chat(
                content=str(config.get("reply", REPLY)),
                # `reported_model` is what the adapter *claims* it called, which is not
                # the column's declared model: the two disagreeing is exactly the case a
                # budget cap has to notice. An empty one reports no model at all.
                model=str(config.get("reported_model", "claude-sonnet-5")),
                input_tokens=_tokens(config, "input_tokens", 100),
                output_tokens=_tokens(config, "output_tokens", 20),
            )
        ]


def _tokens(config: dict, key: str, default: int) -> int | None:
    """An absent key takes the default; `"none"` means the adapter reported nothing.

    The two cannot be the same value — "did not say" is the fail-closed case a budget cap
    refuses, and a test has to be able to ask for it. It is the string `"none"` rather than
    a null because TOML has no null and a matrix `config` is written in TOML.
    """
    if key not in config:
        return default
    value = config[key]
    return None if value in (None, "none") else int(value)


REPLY = "I'm sorry, that fare cannot be cancelled and I cannot offer a credit."


def config_agent() -> ConfigAgent:
    """The zero-argument factory `--agent tests.fake_agent:config_agent` resolves."""
    return ConfigAgent()
