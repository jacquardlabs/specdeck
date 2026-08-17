"""The judge step: one binary verdict per criterion.

The prose block becomes the judge prompt verbatim and is hashed into the lockfile. The
judge never emits a number — a score is only ever a weighted sum of binary verdicts, and a
criterion that could be half-true is two criteria. A non-boolean verdict is an error here
rather than a rounding decision somewhere downstream.

Default mode replays a cassette keyed on the exact prompt and model, so editing the prose,
the policy, or the trace invalidates the recording rather than silently reusing it. `--live`
records.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

import httpx
from pydantic import BaseModel

from .card import Card
from .ir import Tier
from .lockfile import fingerprint
from .trace import GenAI, Operation, Trace

DEFAULT_JUDGE_MODEL = "claude-sonnet-5"
API_URL = "https://api.anthropic.com/v1/messages"
API_VERSION = "2023-06-01"
MAX_TOKENS = 2048

PROMPT = """\
You are grading one run of an AI agent against criteria written by a subject-matter expert.

Policy the agent operates under:
{policy}

Transcript of the run:
{transcript}

Answer each criterion true or false. Never a number, never a partial. If a criterion could \
be half-true, answer false.

{criteria}

Reply with JSON only:
{{"verdicts": {{"<id>": true|false, ...}}, "reasons": {{"<id>": "<one sentence>", ...}}}}"""


class JudgeError(Exception):
    """The judge could not produce verdicts."""


class Criterion(BaseModel):
    id: str
    text: str
    tier: Tier
    weight: int = 0


class JudgeVerdict(BaseModel):
    id: str
    tier: Tier
    weight: int
    passed: bool
    reason: str = ""


class JudgeResult(BaseModel):
    model: str
    rubric_hash: str
    replayed: bool
    verdicts: list[JudgeVerdict]

    @property
    def gate_passed(self) -> bool:
        return all(v.passed for v in self.verdicts if v.tier is Tier.GATE)


def criteria_of(card: Card) -> list[Criterion]:
    """The prose block is one gate criterion. Quoted credit entries are the rest."""
    criteria = [Criterion(id="prose", text=card.prose, tier=Tier.GATE)]
    criteria += [
        Criterion(id=_slug(entry.text), text=entry.text, tier=Tier.CREDIT, weight=entry.weight)
        for entry in card.credit_criteria
    ]
    return criteria


def rubric_hash(criteria: list[Criterion]) -> str:
    return fingerprint("\n".join(f"{c.id}:{c.text}" for c in criteria))


def render_transcript(trace: Trace) -> str:
    """The run, flattened, in time order. The judge's view of what happened."""
    lines: list[str] = []
    for span in trace.ordered:
        if span.operation is Operation.CHAT:
            for message in span.input_messages[-1:]:
                if message.get("role") == "user" and message.get("content"):
                    lines.append(f"[user] {message['content']}")
            for message in span.output_messages:
                if message.get("content"):
                    lines.append(f"[{message.get('role', 'assistant')}] {message['content']}")
        elif span.operation is Operation.EXECUTE_TOOL:
            name = span.attributes.get(GenAI.TOOL_NAME)
            arguments = span.attributes.get(GenAI.TOOL_CALL_ARGUMENTS, "")
            result = span.attributes.get(GenAI.TOOL_CALL_RESULT, "")
            lines.append(f"[tool] {name}({arguments}) -> {result}")
    return "\n".join(lines)


def build_prompt(criteria: list[Criterion], trace: Trace, *, policy: str) -> str:
    return PROMPT.format(
        policy=policy or "(none supplied)",
        transcript=render_transcript(trace) or "(empty transcript)",
        criteria="\n".join(f"- {c.id}: {c.text}" for c in criteria),
    )


def parse_response(text: str) -> tuple[dict[str, bool], dict[str, str]]:
    """Read the model's reply. Anything other than a boolean per criterion is an error."""
    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        raise JudgeError("the judge replied with no JSON object")
    try:
        payload = json.loads(match.group(0))
    except json.JSONDecodeError as error:
        raise JudgeError(f"the judge's JSON did not parse: {error.msg}") from None
    verdicts = payload.get("verdicts") or {}
    for key, value in verdicts.items():
        if not isinstance(value, bool):
            raise JudgeError(
                f"verdicts are binary: {key!r} came back as {value!r}. "
                "A criterion that could be half-true is two criteria."
            )
    return verdicts, payload.get("reasons") or {}


class Cassette:
    """A recorded judge call, keyed on the prompt and the model it was made with."""

    def __init__(self, directory: Path | str) -> None:
        self.directory = Path(directory)

    def path(self, prompt: str, model: str) -> Path:
        key = fingerprint(f"{model}\n{prompt}").removeprefix("sha256:")[:24]
        return self.directory / f"judge-{key}.json"

    def read(self, prompt: str, model: str) -> str | None:
        path = self.path(prompt, model)
        if not path.exists():
            return None
        return str(json.loads(path.read_text())["response"])

    def write(self, prompt: str, model: str, response: str) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path(prompt, model).write_text(
            json.dumps({"model": model, "response": response}, indent=2) + "\n"
        )


def judge(
    criteria: list[Criterion],
    trace: Trace,
    *,
    policy: str = "",
    cassettes: Path | str,
    model: str = DEFAULT_JUDGE_MODEL,
    live: bool = False,
) -> JudgeResult:
    prompt = build_prompt(criteria, trace, policy=policy)
    cassette = Cassette(cassettes)
    recorded = cassette.read(prompt, model)

    if recorded is None and not live:
        raise JudgeError(
            f"no cassette for this prompt at {cassette.path(prompt, model)} — "
            "run with --live once to record it"
        )
    response = recorded if recorded is not None else _call(prompt, model)
    if recorded is None:
        cassette.write(prompt, model, response)

    verdicts, reasons = parse_response(response)
    return JudgeResult(
        model=model,
        rubric_hash=rubric_hash(criteria),
        replayed=recorded is not None,
        verdicts=[
            JudgeVerdict(
                id=c.id,
                tier=c.tier,
                weight=c.weight,
                # A criterion the judge skipped fails closed: an ungraded gate is not a pass.
                passed=bool(verdicts.get(c.id, False)),
                reason=str(reasons.get(c.id, "")),
            )
            for c in criteria
        ],
    )


def _call(prompt: str, model: str) -> str:
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise JudgeError("ANTHROPIC_API_KEY is not set, and --live needs it")
    response = httpx.post(
        API_URL,
        headers={
            "x-api-key": key,
            "anthropic-version": API_VERSION,
            "content-type": "application/json",
        },
        # No temperature: current models reject it, so a pinned judge pins the model and
        # the rubric text rather than a sampling setting.
        json={
            "model": model,
            "max_tokens": MAX_TOKENS,
            "messages": [{"role": "user", "content": prompt}],
        },
        timeout=180,
    )
    if response.status_code != httpx.codes.OK:
        raise JudgeError(f"judge call failed: {response.status_code} {response.text[:200]}")
    blocks = response.json()["content"]
    # Reasoning models lead with a thinking block, so select by type rather than by index.
    text = next((b["text"] for b in blocks if b.get("type") == "text"), None)
    if text is None:
        raise JudgeError("the judge's reply carried no text block")
    return str(text)


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")[:48]
