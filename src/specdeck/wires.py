"""The wires engine: card wire text in, Property IR out.

Wires are gate tier unless they appear under `credit`. The engine only compiles and
evaluates; the ordering rule — gate wires before any judge call, because a card that
touched a forbidden tool needs no judge — belongs to the runner, which is the only place
that can skip a call.

The grammar is small on purpose. Every form the tracer does not implement raises with the
form named and the issue it waits on, rather than compiling to something approximate.
"""

from __future__ import annotations

import json
import re

from .card import Card
from .ir import (
    AfterKThen,
    AtMost,
    Bound,
    Measure,
    Never,
    Operation,
    Property,
    Selector,
    Tier,
    Verdict,
)

TRUNCATED_FINISH_REASON = "max_tokens"

#: Patterns the palette names but the runner does not implement yet. Each raises with the
#: form named, rather than compiling to something approximate — a deferred pattern that
#: silently became a passing wire is the worst available outcome for a gate.
_DEFERRED = {
    r"\beventually\b": "`eventually` is named by the palette but not implemented yet",
    r"\bbefore\b": "precedence is named by the palette but not implemented yet",
}

#: `<subject>: after <k> <marker>` — the escalation shape the card format's example uses.
_AFTER = re.compile(r"after\s+(\d+)\s+([a-z0-9_]+)$")


class WireError(Exception):
    """The wire does not compile. Always names the wire text."""


def compile_wire(text: str, *, tier: Tier = Tier.GATE, weight: int = 0) -> Property:
    """One wire line -> one Property."""
    for pattern, reason in _DEFERRED.items():
        if re.search(pattern, text):
            raise WireError(f"{text!r}: {reason}")

    subject, rule = _split(text)

    if subject == "latency":
        return _bound("latency", Measure.AGENT_DURATION_S, rule, text, tier, weight)
    if subject == "response_tokens":
        return _bound("response_tokens", Measure.TOTAL_OUTPUT_TOKENS, rule, text, tier, weight)
    if subject == "stop_reason":
        if rule != "not truncated":
            raise WireError(f"{text!r}: the only stop_reason rule is `not truncated`")
        return Property(
            id="stop_reason",
            tier=tier,
            weight=weight,
            rule=Never(
                selector=Selector(operation=Operation.CHAT, finish_reason=TRUNCATED_FINISH_REASON)
            ),
        )

    tool = Selector(operation=Operation.EXECUTE_TOOL, tool=subject)
    if rule == "never":
        return Property(id=f"never:{subject}", tier=tier, weight=weight, rule=Never(selector=tool))
    if match := _AFTER.match(rule):
        k, marker = match.groups()
        return Property(
            id=f"after_{k}_{marker}:{subject}",
            tier=tier,
            weight=weight,
            rule=AfterKThen(
                k=int(k),
                trigger=Selector(marker=marker),
                then=Selector(operation=Operation.EXECUTE_TOOL, tool=subject),
            ),
        )
    if rule.startswith("at_most"):
        return Property(
            id=f"at_most:{subject}",
            tier=tier,
            weight=weight,
            rule=AtMost(n=_whole(rule.removeprefix("at_most"), text), selector=tool),
        )
    raise WireError(f"{text!r}: unrecognised rule {rule!r}")


def compile_wires(card: Card) -> list[Property]:
    """Every wire on a card, gate tier first, then credit."""
    try:
        return [compile_wire(text) for text in card.wires] + [
            compile_wire(entry.text, tier=Tier.CREDIT, weight=entry.weight)
            for entry in card.credit_wires
        ]
    except WireError as error:
        raise WireError(f"{card.path}: {error}") from None


def gates_pass(verdicts: list[Verdict]) -> bool:
    """Whether every gate wire held. The input to the runner's judge short circuit."""
    return all(v.passed for v in verdicts if v.tier is Tier.GATE)


def _split(text: str) -> tuple[str, str]:
    """`subject: rule`, or `subject rule` for the credit form written without a colon."""
    subject, separator, rule = text.partition(":")
    if not separator:
        subject, _, rule = text.partition(" ")
    return subject.strip(), rule.strip()


def _bound(id: str, measure: Measure, rule: str, text: str, tier: Tier, weight: int) -> Property:
    if not rule.startswith("under"):
        raise WireError(f"{text!r}: a {id} wire reads `under <limit>`, got {rule!r}")
    return Property(
        id=id,
        tier=tier,
        weight=weight,
        rule=Bound(
            measure=measure,
            # Only a duration takes the unit. `response_tokens under 400s` is nonsense
            # and used to compile to a 400-token bound.
            limit=_number(
                rule.removeprefix("under"), text, seconds=measure is Measure.AGENT_DURATION_S
            ),
        ),
    )


def _number(fragment: str, text: str, *, seconds: bool = False) -> float:
    match = re.fullmatch(rf"\s*(\d+(?:\.\d+)?){'s?' if seconds else ''}\s*", fragment)
    if not match:
        expected = "a number, optionally suffixed `s`" if seconds else "a number"
        raise WireError(f"{text!r}: expected {expected}, got {fragment.strip()!r}")
    return float(match.group(1))


def _whole(fragment: str, text: str) -> int:
    """A count, which unlike a bound cannot be fractional."""
    number = _number(fragment, text)
    if number != int(number):
        raise WireError(f"{text!r}: a call budget must be a whole number, got {number:g}")
    return int(number)


def wires_text(properties: list[Property]) -> str:
    """The canonical pinned text for a card's wires: the compiled IR, stably serialised.

    The IR and not the wire text, so `at_most  2` reformatted is not drift while
    `at_most 20` is. Sorted by id, because the order wires appear in a card is the
    developer's arrangement rather than a claim about behaviour.
    """
    return json.dumps(
        [p.model_dump(mode="json") for p in sorted(properties, key=lambda p: p.id)],
        sort_keys=True,
    )
