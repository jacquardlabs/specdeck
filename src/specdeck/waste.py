"""Two waste classifiers, ported from cctx onto the trace.

DECISIONS.md (2026-08-15) records the port: cctx's retry-loop and stale-context
classifiers are validated against real sessions, so the detection logic and every
threshold are carried over unchanged and only the input adapter is new. cctx reads
`SessionTrace.turns` — one turn per JSONL line, with a tool call and its result split
across an assistant turn and a following tool_result turn and re-paired on `tool_use_id`.
specdeck's `execute_tool` span carries both halves at once, so the pairing map is deleted
rather than reimplemented; `_steps` is the whole adapter.

The unit of counting shifts and the numbers do not. cctx counts turns, where one tool call
is two of them; specdeck counts span ordinals, where it is one. `N_STALE = 5` span
ordinals is therefore up to twice as strict over a tool-heavy stretch as the same 5 was in
cctx. Preserved as written rather than rescaled, because rescaling a threshold validated
against real sessions is a rewrite wearing a port's clothes. `billed_stale` is unaffected:
a chat span maps one-to-one onto cctx's assistant turn.

A finding is never a gate. `Cell.passed` and the exit code are untouched — a card that
passed while burning four times the tokens is worth saying out loud, and worth saying
without changing the answer.

Not ported: cctx wraps each classifier in a try/except because it runs ten of them over
unvalidated real sessions. `Trace` is strictly validated at its own boundary, so a
classifier raising here is a bug and must surface.
"""

from __future__ import annotations

import json
from collections import defaultdict
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from .trace import ERROR_TYPE, GenAI, Operation, Span, Trace, reported_sum

#: Thresholds, verbatim from cctx's stale_context.py.
T_SIZE = 2_000  #: Estimated tokens a tool result needs to be a staleness candidate.
N_STALE = 5  #: Span ordinals after the last reference before the result counts as stale.
STALE_HIGH_THRESHOLD = 500_000  #: Token-turns above which the finding is HIGH.


class Kind(StrEnum):
    """The two of cctx's eleven finding kinds that were ported."""

    RETRY_LOOP = "retry_loop"
    STALE_CONTEXT = "stale_context"


class Level(StrEnum):
    """Severity and confidence, which cctx keeps as two enums with the same members.

    One enum here because neither ported classifier ever emits LOW — that member came
    from the compaction classifier, which is not ported.
    """

    HIGH = "high"
    MEDIUM = "medium"


#: What `Finding.waste_tokens` counts, per kind. A retry burned tokens; a stale result
#: burned token-turns, which is tokens carried times the requests that carried them. Two
#: units, and adding them would produce a figure of nothing — cctx never summed them
#: either, it priced each kind separately in its orchestrator.
UNITS: dict[Kind, str] = {Kind.RETRY_LOOP: "tokens", Kind.STALE_CONTEXT: "token-turns"}


class Finding(BaseModel):
    """One thing the run spent tokens on and did not need to.

    `first_span` and `last_span` are ordinals into `Trace.ordered`, where cctx carried
    turn numbers. `cost_usd` is not carried: cctx's classifiers always returned None for
    it and the orchestrator priced findings afterwards.
    """

    kind: Kind
    severity: Level
    confidence: Level
    first_span: int
    last_span: int
    evidence: dict[str, Any] = Field(default_factory=dict)
    summary: str
    #: In this kind's own unit (see `UNITS`). None when no chat span reported usage — the
    #: waste happened, and its size is unknown rather than zero.
    waste_tokens: int | None = None


class Step(BaseModel):
    """One span of `Trace.ordered`, flattened into what the classifiers read."""

    index: int
    is_chat: bool
    tool_name: str = ""
    arguments: dict[str, Any] = Field(default_factory=dict)
    #: None when the span recorded no result at all, which is cctx's unpaired tool call.
    result: str | None = None
    failed: bool = False
    text: str = ""
    input_tokens: int | None = None
    output_tokens: int | None = None


def classify(trace: Trace) -> list[Finding]:
    """Every waste finding in one run, earliest first."""
    steps = _steps(trace)
    return sorted(_retry_loops(steps) + _stale_context(steps), key=lambda f: f.first_span)


def _steps(trace: Trace) -> list[Step]:
    """The whole input adapter: spans in, classifier records out.

    Ordinals are 1-based positions in `Trace.ordered`, which sorts on start time and
    breaks ties on span id — so a child sharing the root's start time can precede it. The
    number is a position in that ordering, not a claim about causal nesting.
    """
    return [_step(index, span) for index, span in enumerate(trace.ordered, start=1)]


def _step(index: int, span: Span) -> Step:
    result = span.attributes.get(GenAI.TOOL_CALL_RESULT)
    text = " ".join(
        str(message.get("content", ""))
        for message in span.output_messages
        if message.get("role") == "assistant"
    )
    return Step(
        index=index,
        is_chat=span.operation is Operation.CHAT,
        tool_name=str(span.attributes.get(GenAI.TOOL_NAME) or ""),
        arguments=_arguments(span.attributes.get(GenAI.TOOL_CALL_ARGUMENTS)),
        result=None if result is None else str(result),
        failed=_failed(span, result),
        text=text,
        input_tokens=span.attributes.get(GenAI.USAGE_INPUT_TOKENS),
        output_tokens=span.attributes.get(GenAI.USAGE_OUTPUT_TOKENS),
    )


def _arguments(raw: Any) -> dict[str, Any]:
    """`gen_ai.tool.call.arguments` is a JSON string, and a trace is user-supplied.

    A value that is not an object degrades to `{}` — every call then keys on the same
    empty argument set, which is what cctx's `json.dumps` default does for a tool it has
    no special case for.
    """
    if isinstance(raw, dict):
        return raw
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _failed(span: Span, result: Any) -> bool:
    """cctx's `_is_error`, with `error.type` standing in for `ToolResult.is_error`.

    The prefix test stays exact rather than a substring search: cctx pins that a result
    merely containing "error:" is not a failure, and widening it would make every
    diagnostic mentioning an error read as one.
    """
    if span.attributes.get(ERROR_TYPE) is not None:
        return True
    content = str(result or "")
    return (
        content.startswith("Error:") or content.startswith("error:") or content.startswith("FAILED")
    )


def _similarity_key(tool_name: str, arguments: dict[str, Any]) -> str:
    """Copied from cctx: what makes two calls "the same call"."""
    match tool_name:
        case "Bash":
            return str(arguments.get("command", "")).strip()
        case "Edit" | "Read" | "Write":
            return str(arguments.get("file_path", ""))
        case "Grep" | "Glob":
            return str(arguments.get("pattern", ""))
        case _:
            return json.dumps(arguments, sort_keys=True)


def _estimate_tokens(text: str) -> int:
    return int(len(text.split()) * 1.3)


def _make_3grams(text: str) -> set[tuple[str, ...]]:
    words = text.lower().split()
    if len(words) < 3:
        return set()
    return {tuple(words[i : i + 3]) for i in range(len(words) - 2)}


def _retry_loops(steps: list[Step]) -> list[Finding]:
    """Repeated identical failing tool calls with no intervening success.

    One finding per run, with every loop bundled into its evidence, as cctx bundles them
    per session.
    """
    groups: dict[tuple[str, str], list[Step]] = defaultdict(list)
    for step in steps:
        if step.tool_name and step.result is not None:
            groups[(step.tool_name, _similarity_key(step.tool_name, step.arguments))].append(step)

    loops: list[tuple[str, str, list[Step]]] = []
    for (tool_name, key), group in groups.items():
        errors = [s for s in group if s.failed]
        if len(errors) < 2:
            continue
        # A success between the first and last failure means the agent fixed it and the
        # later failure is a new problem, not the same one retried.
        if any(not s.failed and errors[0].index < s.index < errors[-1].index for s in group):
            continue
        loops.append((tool_name, key, errors))

    if not loops:
        return []

    failures = sorted((s for _, _, errors in loops for s in errors), key=lambda s: s.index)
    loop_length = len(failures)
    # The loop is established at the second failure, not the first: one failed call is a
    # tool call that failed.
    first_span = min(errors[1].index for _, _, errors in loops)
    last_span = max(s.index for s in failures)
    # Waste is the repeat attempts only. Each loop's first try was legitimate.
    waste_spans = [s.index for _, _, errors in loops for s in errors[1:]]

    tool_name, key, first_errors = loops[0]
    return [
        Finding(
            kind=Kind.RETRY_LOOP,
            severity=Level.HIGH if loop_length >= 4 else Level.MEDIUM,
            confidence=Level.HIGH,
            first_span=first_span,
            last_span=last_span,
            evidence={
                "occurrences": [
                    {
                        "span": s.index,
                        "key": _similarity_key(s.tool_name, s.arguments),
                        "call": s.tool_name,
                        "error": (s.result or "")[:120],
                    }
                    for s in failures
                ],
                "loop_length": loop_length,
                "waste_spans": waste_spans,
            },
            # cctx's own wording, kept verbatim: the times sign and the en dash below are
            # the port's, not typos.
            summary=(
                f"{tool_name}({key[:40]}) failed {loop_length}× between spans "  # noqa: RUF001
                f"{first_errors[0].index}–{last_span}"  # noqa: RUF001
            ),
            waste_tokens=reported_sum(*(_issuing_tokens(steps, span) for span in waste_spans)),
        )
    ]


def _issuing_tokens(steps: list[Step], index: int) -> int | None:
    """The tokens of the chat span that asked for the tool call at `index`.

    cctx prices a wasted retry as a whole round-trip rather than as the error text, and
    the request that issued it is the closest thing a trace holds to that round-trip.
    """
    for step in reversed(steps[: index - 1]):
        if step.is_chat:
            return reported_sum(step.input_tokens, step.output_tokens)
    return None


def _stale_context(steps: list[Step]) -> list[Finding]:
    """Large tool results carried well past the last time anything referred to them.

    References are 3-gram overlap against later assistant text, as in cctx. cctx also
    resets staleness at a compaction event; that guard is deleted rather than ported,
    because the trace schema has three operations and no system span or compaction
    concept, so the set it tests is provably empty here.

    cctx reads a tool result's own token count when it has one and estimates otherwise. A
    span carries no per-result count, so the estimate branch is always the one taken.
    """
    candidates = [
        (step, tokens, _make_3grams(step.result or ""))
        for step in steps
        if step.tool_name and step.result and (tokens := _estimate_tokens(step.result)) >= T_SIZE
    ]
    if not candidates:
        return []

    last_ordinal = max(step.index for step in steps)
    items: list[dict[str, Any]] = []
    for step, tokens, grams in candidates:
        # The span it appeared in counts as a reference, at minimum.
        last_ref = step.index
        for later in steps:
            if later.index > step.index and later.is_chat and grams & _make_3grams(later.text):
                last_ref = later.index
        spans_stale = last_ordinal - last_ref
        if spans_stale <= N_STALE:
            continue
        # Charged only to the calls that re-sent the context. In specdeck the API call is
        # the chat span, so the ~2x inflation cctx warns about cannot arise here.
        billed_stale = sum(1 for later in steps if later.index > last_ref and later.is_chat)
        items.append(
            {
                "tool_name": step.tool_name,
                "content_tokens": tokens,
                "first_seen_span": step.index,
                "last_referenced_span": last_ref,
                "spans_stale": spans_stale,
                "token_turns": tokens * billed_stale,
            }
        )

    if not items:
        return []

    total_token_turns = sum(item["token_turns"] for item in items)
    level = Level.HIGH if total_token_turns > STALE_HIGH_THRESHOLD else Level.MEDIUM
    worst = max(items, key=lambda item: item["token_turns"])
    return [
        Finding(
            kind=Kind.STALE_CONTEXT,
            severity=level,
            confidence=level,
            first_span=min(item["last_referenced_span"] + N_STALE for item in items),
            last_span=last_ordinal,
            evidence={"stale_items": items, "total_token_turns": total_token_turns},
            summary=(
                f"{worst['content_tokens'] // 1000}K-token {worst['tool_name']} result stale "
                f"{worst['spans_stale']} spans (~{total_token_turns:,} token-turns)"
            ),
            waste_tokens=total_token_turns,
        )
    ]
