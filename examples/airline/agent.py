"""A τ-bench airline agent, as one `AgentAdapter`.

This is the example the README's `yourpkg.adapter:Agent` stands in for. It exists so that
`--agent` — half of what specdeck does — is demonstrable by something in this repo rather
than described, and so #24's migration report has an agent to vary.

It is deliberately caller code, not runner code: it lives under `examples/`, ships in no
wheel (`[tool.hatch.build.targets.wheel] packages = ["src/specdeck"]`), and carries its own
tool-use loop over `httpx`. `specdeck.provider.complete` is single-turn with no tools on
purpose — "neither caller needs either" — and an agent is exactly the caller that does, so
borrowing it would have meant widening the runner's provider seam for an example.

What a matrix column varies arrives in `config`, verbatim:

    model           the model this column runs        (default DEFAULT_MODEL)
    system_prompt   the prompt text itself, or
    prompt          a path to read it from            (default: the bundled full prompt)

Those two are the axes #24 compares — provider column and prompt variant — and nothing
else about the agent moves between columns.
"""

from __future__ import annotations

import json
import os
from copy import deepcopy
from pathlib import Path
from typing import Any

import httpx

from specdeck.agent import AgentDescription, Chat, ToolCall

from .tools import TOOLS, schemas

HERE = Path(__file__).resolve().parent
DATA = HERE / "data.json"
PROMPTS = HERE / "prompts"

DEFAULT_MODEL = "claude-opus-5"
DEFAULT_PROMPT = PROMPTS / "full.md"

ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"

MAX_TOKENS = 2048
TIMEOUT_S = 120

#: How many model calls one turn may make before the agent yields. A turn is a tool-use
#: loop, so this bounds the loop rather than the conversation — `--max-turns` bounds that.
#: A cycle with no bound is the thing `specdeck lint --agent-def` exists to catch, so the
#: example that ships with it does not contain one.
MAX_STEPS = 12


class AirlineError(RuntimeError):
    """The agent could not run. Never raised for a tool that merely returned an error:
    τ-bench tools report failure as a string the model is expected to read and recover
    from, and turning that into an exception would grade the harness, not the agent."""


class AirlineAgent:
    """The adapter. One instance serves every run and every column of an invocation.

    `cli._adapter` resolves `--agent` once, so the database has to be re-copied per
    conversation rather than per instance — otherwise run 2 of a cell would start from
    whatever run 1's cancellation left behind, and a column would be graded against a
    world the previous column mutated. The first turn of a conversation is the one whose
    `messages` carries a single opening line, which is what `_fresh` keys on.
    """

    def __init__(self, *, data_path: Path | None = None) -> None:
        self._seed = json.loads((data_path or DATA).read_text())
        self._data: dict[str, Any] = deepcopy(self._seed)
        #: The conversation in the provider's shape, kept across the turns of one run.
        #: Not derived from the `messages` the runner passes: those carry a `tool` role,
        #: which specdeck uses to record a tool result and the messages API rejects
        #: outright ("Allowed roles are user or assistant"). Rebuilding tool_result blocks
        #: from them is impossible anyway — the `tool_use_id` each one has to echo is gone
        #: by then. So the adapter owns its own history and takes only the new user line
        #: from the runner, which is what an adapter bridging two message models is for.
        self._history: list[dict] = []

    # -- the protocol ---------------------------------------------------------

    async def run(self, messages: list[dict], tools: list[str], config: dict) -> list:
        if self._fresh(messages):
            self._data = deepcopy(self._seed)
            self._history = []

        model = str(config.get("model") or DEFAULT_MODEL)
        system = self._system(config)
        # The card's vocabulary narrows what this agent may reach for, so a card can test
        # an agent holding fewer tools than the domain defines. An empty list means the
        # card named none, which is the whole domain rather than none of it.
        allowed = [name for name in (tools or list(TOOLS)) if name in TOOLS]

        # Only the latest line: `loop.run_agent` appends one simulator turn and calls,
        # so `messages[-1]` is what is new since this adapter last spoke.
        if messages:
            self._history.append({"role": "user", "content": str(messages[-1]["content"])})

        events: list = []
        for _ in range(MAX_STEPS):
            reply = await self._call(model, system, self._history, allowed)
            text = "".join(
                block["text"] for block in reply["content"] if block.get("type") == "text"
            )
            calls = [block for block in reply["content"] if block.get("type") == "tool_use"]
            usage = reply.get("usage") or {}
            events.append(
                Chat(
                    content=text,
                    finish_reason=str(reply.get("stop_reason") or "stop"),
                    model=str(reply.get("model") or model),
                    input_tokens=_count(usage.get("input_tokens")),
                    output_tokens=_count(usage.get("output_tokens")),
                )
            )
            if not calls:
                self._history.append({"role": "assistant", "content": text or "(no reply)"})
                return events

            self._history.append({"role": "assistant", "content": reply["content"]})
            results = []
            for call in calls:
                result = self._invoke(call["name"], call.get("input") or {})
                events.append(
                    ToolCall(
                        name=call["name"],
                        arguments=dict(call.get("input") or {}),
                        result=result,
                        call_id=call.get("id"),
                    )
                )
                results.append(
                    {"type": "tool_result", "tool_use_id": call["id"], "content": result}
                )
            self._history.append({"role": "user", "content": results})

        raise AirlineError(
            f"the agent made {MAX_STEPS} model calls in one turn without yielding — "
            "raise MAX_STEPS if a scenario legitimately needs more"
        )

    def describe(self) -> AgentDescription:
        """A raw-SDK description: the tools are the only nodes there are.

        No edges and no cycles, deliberately and not as an omission. This is a tool-use
        loop, not a declared graph, so `specdeck lint --agent-def` should report the lower
        tier against it — the example is the thing that proves the tier is reported rather
        than assumed.
        """
        return AgentDescription(tools=sorted(TOOLS))

    # -- the parts ------------------------------------------------------------

    @staticmethod
    def _fresh(messages: list[dict]) -> bool:
        """Whether this is the opening turn of a new conversation. `loop.run_agent` starts
        each run with one simulator line and appends from there."""
        return len(messages) <= 1

    @staticmethod
    def _system(config: dict) -> str:
        literal = config.get("system_prompt")
        if literal:
            return str(literal)
        named = config.get("prompt")
        path = Path(str(named)) if named else DEFAULT_PROMPT
        if not path.is_absolute() and not path.exists():
            # A prompt named relative to this example resolves against it, so a matrix
            # file can say `prompt = "prompts/trimmed.md"` from anywhere.
            path = HERE / path
        if not path.exists():
            raise AirlineError(f"no prompt file at {path}")
        return path.read_text()

    def _invoke(self, name: str, arguments: dict) -> str:
        tool = TOOLS.get(name)
        if tool is None:
            # The model asked for something outside the domain. Reported to it as a result
            # rather than raised: an agent recovering from its own bad call is behaviour a
            # card may legitimately want to grade.
            return f"Error: unknown tool {name}"
        try:
            return str(tool.invoke(self._data, **arguments))
        except TypeError as error:
            return f"Error: {error}"

    async def _call(
        self, model: str, system: str, messages: list[dict], allowed: list[str]
    ) -> dict:
        key = os.environ.get("ANTHROPIC_API_KEY")
        if not key:
            raise AirlineError("ANTHROPIC_API_KEY is not set")
        payload = {
            "model": model,
            "max_tokens": MAX_TOKENS,
            "system": system,
            "messages": messages,
            "tools": [s for s in schemas() if s["name"] in allowed],
        }
        async with httpx.AsyncClient(timeout=TIMEOUT_S) as client:
            response = await client.post(
                ANTHROPIC_URL,
                headers={
                    "x-api-key": key,
                    "anthropic-version": ANTHROPIC_VERSION,
                    "content-type": "application/json",
                },
                json=payload,
            )
        if response.status_code != httpx.codes.OK:
            raise AirlineError(f"call failed: {response.status_code} {response.text[:200]}")
        return response.json()


def _count(value: object) -> int | None:
    """A reported token count, or None. Absent rather than zero when the reply did not say,
    on the rule `provider.Completion` holds: a call nobody counted is not a free call."""
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def agent() -> AirlineAgent:
    """The factory `--agent examples.airline.agent:agent` resolves to."""
    return AirlineAgent()
