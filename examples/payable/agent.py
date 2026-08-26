"""The Meridian accounts payable agent, as one `AgentAdapter`.

The tutorial's agent, and the example the README's `yourpkg.adapter:Agent` stands in for.
It pays vendor invoices against purchase orders for an invented company, so that the rules
it is graded against are Meridian's and cannot have been learned from anywhere (#110).

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

#: Tools this runtime refuses at dispatch, whatever the model asks and whatever the prompt
#: says, with the refusal it hands back. This is a guardrail, not the tool enforcing its own
#: policy: the model still decides to call it, the call is still recorded, and nothing runs.
#: Repointing where a vendor's money goes is the canonical thing to put behind one — the
#: prompt says do not, and the runtime makes sure. Defence in depth, and the two halves are
#: separately observable: `never_executed` holds because of this, `never_requested` holds
#: only if the agent never reached for it.
#: The payment rail's own hard ceiling, in dollars. Distinct from the $5,000 second-approver
#: threshold in the policy, and enforced somewhere the model cannot reach: an approval rule
#: is something the agent is asked to follow, a rail limit is something it cannot exceed
#: however it behaves. Real AP systems have both, for the reason this deck demonstrates —
#: the first is advice and the second is a fact.
RAIL_CEILING_USD = 10_000.00

#: What `gen_ai.tool.name` names on a denial span: the component that refused, never the
#: tool that did not run. See docs/card-format.md.
GUARDRAIL = "ap_guardrail"


def _refusal(name: str, arguments: dict, data: dict) -> str | None:
    """What this runtime refuses at dispatch, or None to let the call through.

    A guardrail, not the tool enforcing its own policy: the model still decides to make the
    call, the attempt is still recorded, and nothing runs. Guarding a capability the model
    already declines on instinct would be a guardrail that never fires — bank-detail changes
    turned out to be exactly that, refused on the model's own initiative with no rule in
    sight. So this one sits on the payment rail, where an agent with no rules will
    absolutely try to spend.
    """
    if name != "pay_invoice":
        return None
    invoice = data["invoices"].get(str(arguments.get("invoice_id")))
    if invoice is None or float(invoice.get("amount", 0)) <= RAIL_CEILING_USD:
        return None
    return (
        f"denied by {GUARDRAIL}: ${invoice['amount']:,.2f} is over the "
        f"${RAIL_CEILING_USD:,.0f} payment ceiling. Nothing was paid. A payment this size "
        "is released by Finance, not from this agent."
    )


HERE = Path(__file__).resolve().parent
#: The deck this example ships beside. An in-repo path because the example and the deck
#: are one artefact — your own adapter reads your own database, wherever that lives.
DATA = HERE.parent.parent / "cards" / "fixtures" / "data.json"
PROMPTS = HERE / "prompts"

DEFAULT_MODEL = "claude-sonnet-5"
#: One policy, serving as both the agent's prompt and the deck's `policy:` context. Two
#: copies of the same rules is two things that can disagree, and a spec that disagrees with
#: the prompt it grades is worse than no spec.
DEFAULT_PROMPT = HERE.parent.parent / "cards" / "policy" / "ap.md"

ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
OPENAI_URL = "https://api.openai.com/v1/chat/completions"
ANTHROPIC_VERSION = "2023-06-01"

MAX_TOKENS = 2048
TIMEOUT_S = 120

#: How many model calls one turn may make before the agent yields. A turn is a tool-use
#: loop, so this bounds the loop rather than the conversation — `--max-turns` bounds that.
#: A cycle with no bound is the thing `specdeck lint --agent-def` exists to catch, so the
#: example that ships with it does not contain one.
MAX_STEPS = 12


class PayableError(RuntimeError):
    """The agent could not run. Never raised for a tool that merely returned an error:
    τ-bench tools report failure as a string the model is expected to read and recover
    from, and turning that into an exception would grade the harness, not the agent."""


class PayableAgent:
    """The adapter. One instance serves every run and every column of an invocation.

    `cli._adapter` resolves `--agent` once, so the database has to be re-copied per
    conversation rather than per instance — otherwise run 2 of a cell would start from
    whatever run 1's cancellation left behind, and a column would be graded against a
    world the previous column mutated. The first turn of a conversation is the one whose
    `messages` carries a single opening line, which is what `_fresh` keys on.
    """

    def __init__(
        self, *, data_path: Path | None = None, default_prompt: Path | None = None
    ) -> None:
        #: What this agent runs on when a column does not name a prompt. The tutorial's
        #: two factories differ in exactly this and nothing else.
        self._default_prompt = default_prompt or DEFAULT_PROMPT
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
                arguments = dict(call.get("input") or {})
                refusal = _refusal(call["name"], arguments, self._data)
                if refusal is not None:
                    # Nothing runs. The call is still reported, because a model that tried
                    # is a different fact from a model that never did — and the only place
                    # that difference survives is the trace.
                    events.append(
                        ToolCall(
                            name=GUARDRAIL,
                            denied_tool=call["name"],
                            arguments=arguments,
                            result=refusal,
                            call_id=call.get("id"),
                        )
                    )
                    result = refusal
                else:
                    result = self._invoke(call["name"], arguments)
                    events.append(
                        ToolCall(
                            name=call["name"],
                            arguments=arguments,
                            result=result,
                            call_id=call.get("id"),
                        )
                    )
                results.append(
                    {"type": "tool_result", "tool_use_id": call["id"], "content": result}
                )
            self._history.append({"role": "user", "content": results})

        raise PayableError(
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

    def _system(self, config: dict) -> str:
        literal = config.get("system_prompt")
        if literal:
            return str(literal)
        named = config.get("prompt")
        path = Path(str(named)) if named else self._default_prompt
        if not path.is_absolute() and not path.exists():
            # A prompt named relative to this example resolves against it, so a matrix
            # file can say `prompt = "prompts/trimmed.md"` from anywhere.
            path = HERE / path
        if not path.exists():
            raise PayableError(f"no prompt file at {path}")
        return path.read_text()

    def _invoke(self, name: str, arguments: dict) -> str:
        tool = TOOLS.get(name)
        if tool is None:
            # The model asked for something outside the domain. Reported to it as a result
            # rather than raised: an agent recovering from its own bad call is behaviour a
            # card may legitimately want to grade.
            return f"Error: unknown tool {name}"
        try:
            return str(tool(self._data, **arguments))
        except TypeError as error:
            return f"Error: {error}"

    async def _call(
        self, model: str, system: str, messages: list[dict], allowed: list[str]
    ) -> dict:
        """One model call, normalised to the Anthropic reply shape the loop above reads.

        Which vendor is decided by the model string, using specdeck's own
        `provider/model` convention — `openai/gpt-5-nano` against a bare
        `claude-sonnet-5`. The agent under test is the user's code, so it may speak to
        anyone; specdeck's judge and simulator stay on one provider by decision, and the
        two facts are unrelated. That seam is the whole point of `AgentAdapter`, and this
        is the only example that exercises it.
        """
        provider, _, name = model.rpartition("/")
        if (provider or "anthropic") == "openai":
            return await self._call_openai(name, system, messages, allowed)
        return await self._call_anthropic(name or model, system, messages, allowed)

    async def _call_openai(
        self, model: str, system: str, messages: list[dict], allowed: list[str]
    ) -> dict:
        """OpenAI chat completions, translated both ways.

        The conversation is kept in Anthropic shape because that is what this adapter's
        history is; translating at the boundary rather than branching everywhere is the
        same discipline that keeps `role: "tool"` out of the history in the first place.
        """
        key = os.environ.get("OPENAI_API_KEY")
        if not key:
            raise PayableError("OPENAI_API_KEY is not set")

        chat: list[dict] = [{"role": "system", "content": system}]
        for message in messages:
            content = message["content"]
            if isinstance(content, str):
                chat.append({"role": message["role"], "content": content})
                continue
            # An Anthropic-shaped turn: assistant text plus tool_use, or a user turn
            # carrying tool_result blocks.
            calls = [b for b in content if b.get("type") == "tool_use"]
            text = "".join(b["text"] for b in content if b.get("type") == "text")
            results = [b for b in content if b.get("type") == "tool_result"]
            if calls or (message["role"] == "assistant" and text):
                entry: dict = {"role": "assistant", "content": text or None}
                if calls:
                    entry["tool_calls"] = [
                        {
                            "id": c["id"],
                            "type": "function",
                            "function": {"name": c["name"], "arguments": json.dumps(c["input"])},
                        }
                        for c in calls
                    ]
                chat.append(entry)
            for result in results:
                chat.append(
                    {
                        "role": "tool",
                        "tool_call_id": result["tool_use_id"],
                        "content": str(result["content"]),
                    }
                )

        payload = {
            "model": model,
            "max_completion_tokens": MAX_TOKENS,
            "messages": chat,
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": s["name"],
                        "description": s["description"],
                        "parameters": s["input_schema"],
                    },
                }
                for s in schemas()
                if s["name"] in allowed
            ],
        }
        async with httpx.AsyncClient(timeout=TIMEOUT_S) as client:
            response = await client.post(
                OPENAI_URL,
                headers={"authorization": f"Bearer {key}", "content-type": "application/json"},
                json=payload,
            )
        if response.status_code != httpx.codes.OK:
            raise PayableError(f"call failed: {response.status_code} {response.text[:200]}")
        body = response.json()
        message = body["choices"][0]["message"]
        usage = body.get("usage") or {}
        blocks: list[dict] = []
        if message.get("content"):
            blocks.append({"type": "text", "text": message["content"]})
        for call in message.get("tool_calls") or []:
            blocks.append(
                {
                    "type": "tool_use",
                    "id": call["id"],
                    "name": call["function"]["name"],
                    # OpenAI hands arguments back as a JSON string; a model that emits
                    # malformed JSON is reported to itself as a tool error, not a crash.
                    "input": _loads(call["function"].get("arguments")),
                }
            )
        return {
            "content": blocks,
            "stop_reason": body["choices"][0].get("finish_reason") or "stop",
            "model": body.get("model") or model,
            "usage": {
                "input_tokens": usage.get("prompt_tokens"),
                "output_tokens": usage.get("completion_tokens"),
            },
        }

    async def _call_anthropic(
        self, model: str, system: str, messages: list[dict], allowed: list[str]
    ) -> dict:
        key = os.environ.get("ANTHROPIC_API_KEY")
        if not key:
            raise PayableError("ANTHROPIC_API_KEY is not set")
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
            raise PayableError(f"call failed: {response.status_code} {response.text[:200]}")
        return response.json()


def _loads(raw: object) -> dict:
    """Tool arguments as a dict. A model that emitted invalid JSON gets an empty call and
    the tool's own error back, which is a turn it can recover from."""
    try:
        parsed = json.loads(str(raw or "{}"))
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _count(value: object) -> int | None:
    """A reported token count, or None. Absent rather than zero when the reply did not say,
    on the rule `provider.Completion` holds: a call nobody counted is not a free call."""
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def agent() -> PayableAgent:
    """The agent as it should be: Meridian's policy in its prompt."""
    return PayableAgent()


def naive() -> PayableAgent:
    """The same agent on the first afternoon of building it.

    Its prompt says "be efficient and helpful" and names the tools, which is what an AP
    assistant looks like before anyone writes the rules down. It is not a strawman: the
    model is the same, every tool works, and it behaves the way an eager assistant
    behaves. That is the point of the tutorial — the failure a card catches here is the
    one a real agent has, and no amount of reading the prompt reveals it.
    """
    return PayableAgent(default_prompt=PROMPTS / "naive.md")
