"""Property IR — the intermediate representation wires compile to.

A wire is text the developer writes; a property is the data the engine checks. This module
is that data: pattern x scope x event selector, and nothing else.

A fixed palette, not a general logic. Anything the palette cannot express is a gap to
discuss, not a reason to widen the language.

The IR reads a `Trace` and nothing else — no runner, no backend, no card. That is what
lets one property compile to three deployment modes: an eval assertion, a CI gate, and
later an AgentSpec-style runtime monitor. It serialises to plain JSON, discriminated on
`pattern`, so the monitor needs no format change.

**Tracer scope** (#48): `never`, `at_most`, and `bound`, scoped `globally`. The rest of the
Dwyer set — `eventually`, after-K-then-Y, precedence — and the `between` / `after K` scopes
are deferred; after-K-then-Y additionally waits on #47, since its trigger is a domain event
the semconv does not define.

`bound` is not in the Dwyer set. The card format's own example card carries two wires —
`latency: under 120s` and `response_tokens under 400` — that the stated palette cannot
express, so the tracer names the shape rather than contorting `never` around a scalar.
The format gap is #53; `bound` is the working assumption until it closes.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, Field, model_validator

from .trace import GenAI, Operation, Span, Specdeck, Trace


class Tier(StrEnum):
    """Gate defines pass and blocks. Credit is weighted, reported, never blocking."""

    GATE = "gate"
    CREDIT = "credit"


class Measure(StrEnum):
    """The scalars a `bound` compares against. Trace-level, not per-span."""

    AGENT_DURATION_S = "agent_duration_s"
    TOTAL_OUTPUT_TOKENS = "total_output_tokens"


class Selector(BaseModel):
    """Which spans a rule is about, in OTel GenAI vocabulary.

    Every field is a conjunct: an empty selector matches every span, and each field set
    narrows it. Unset is not a wildcard to be reasoned about — it is simply not a filter.
    """

    operation: Operation | None = None
    tool: str | None = None
    finish_reason: str | None = None
    #: A `specdeck.*` domain event. Not semconv vocabulary, by decision: the semconv has
    #: no place for "the traveller disagreed", and after-K-then-Y needs exactly that.
    marker: str | None = None

    def matches(self, span: Span) -> bool:
        if self.operation is not None and span.operation is not self.operation:
            return False
        if self.tool is not None and span.attributes.get(GenAI.TOOL_NAME) != self.tool:
            return False
        if self.finish_reason is not None:
            reasons = span.attributes.get(GenAI.RESPONSE_FINISH_REASONS) or []
            if self.finish_reason not in reasons:
                return False
        return self.marker is None or span.attributes.get(Specdeck.MARKER) == self.marker

    def describe(self) -> str:
        fields = (self.operation, self.tool, self.finish_reason, self.marker)
        return " ".join(str(v) for v in fields if v is not None) or "any span"


class Scope(BaseModel):
    """Where in the trace a rule applies. `globally` is the whole event log."""

    kind: Literal["globally"] = "globally"

    def restrict(self, trace: Trace) -> list[Span]:
        return trace.ordered


class Never(BaseModel):
    """The selected event does not occur."""

    pattern: Literal["never"] = "never"
    selector: Selector

    def check(self, spans: list[Span], trace: Trace) -> tuple[bool, str]:
        hits = [s for s in spans if self.selector.matches(s)]
        return not hits, f"{len(hits)} occurrence{'' if len(hits) == 1 else 's'}"


class AtMost(BaseModel):
    """The selected event occurs no more than `n` times."""

    pattern: Literal["at_most"] = "at_most"
    n: int
    selector: Selector

    @model_validator(mode="after")
    def _check_budget(self) -> AtMost:
        if self.n < 0:
            raise ValueError(f"at_most budget must not be negative, got {self.n}")
        return self

    def check(self, spans: list[Span], trace: Trace) -> tuple[bool, str]:
        count = sum(1 for s in spans if self.selector.matches(s))
        detail = f"{count} call{'' if count == 1 else 's'}, budget {self.n}"
        return count <= self.n, detail


class Bound(BaseModel):
    """A trace-level measure stays strictly under a limit.

    Exclusive, because the card says `under 120s` and `at_most 2` in the same palette:
    the two words mean different things, and a run that took exactly 120s is not under it.
    """

    pattern: Literal["bound"] = "bound"
    measure: Measure
    limit: float

    def check(self, spans: list[Span], trace: Trace) -> tuple[bool, str]:
        if self.measure is Measure.TOTAL_OUTPUT_TOKENS and not trace.reports_output_tokens:
            # Summing an absent attribute to 0 would pass this bound on every trace that
            # does not report usage — a gate that can never fire is worse than no gate.
            return False, f"no chat span reports {GenAI.USAGE_OUTPUT_TOKENS}"
        actual = self._measure(trace)
        return actual < self.limit, f"{actual:g}, under {self.limit:g}"

    def _measure(self, trace: Trace) -> float:
        if self.measure is Measure.AGENT_DURATION_S:
            return trace.root.duration_s
        return float(trace.total_output_tokens)


class AfterKThen(BaseModel):
    """Once the trigger has occurred k times, the follow-up must occur after it.

    Vacuously true below k: a card saying "escalate after 3 pushbacks" asserts nothing
    about a run with two. Reporting that as a pass would be right but unreadable, so the
    detail says the trigger never reached k rather than leaving it implied.
    """

    pattern: Literal["after_k_then"] = "after_k_then"
    k: int
    trigger: Selector
    then: Selector

    @model_validator(mode="after")
    def _check_k(self) -> AfterKThen:
        if self.k < 1:
            raise ValueError(f"after-K-then-Y needs k of at least 1, got {self.k}")
        return self

    def check(self, spans: list[Span], trace: Trace) -> tuple[bool, str]:
        triggers = [s for s in spans if self.trigger.matches(s)]
        if len(triggers) < self.k:
            return True, f"{self.trigger.describe()} occurred {len(triggers)}x, under k={self.k}"
        cutoff = triggers[self.k - 1].start_time
        after = [s for s in spans if self.then.matches(s) and s.start_time >= cutoff]
        detail = f"k={self.k} reached, {len(after)} follow-up{'' if len(after) == 1 else 's'}"
        return bool(after), detail


Rule = Annotated[Never | AtMost | Bound | AfterKThen, Field(discriminator="pattern")]


class Property(BaseModel):
    """One wire, compiled. The unit the engine evaluates and the report prints."""

    id: str
    tier: Tier = Tier.GATE
    weight: int = 0
    scope: Scope = Scope()
    rule: Rule

    @model_validator(mode="after")
    def _check_weight(self) -> Property:
        if self.tier is Tier.GATE and self.weight:
            raise ValueError(f"{self.id}: a gate property carries no weight, got {self.weight}")
        if self.tier is Tier.CREDIT and self.weight <= 0:
            raise ValueError(f"{self.id}: a credit property needs a positive weight")
        return self


class Verdict(BaseModel):
    id: str
    tier: Tier
    weight: int
    passed: bool
    detail: str


def evaluate(prop: Property, trace: Trace) -> Verdict:
    passed, detail = prop.rule.check(prop.scope.restrict(trace), trace)
    return Verdict(id=prop.id, tier=prop.tier, weight=prop.weight, passed=passed, detail=detail)


def evaluate_all(props: list[Property], trace: Trace) -> list[Verdict]:
    return [evaluate(p, trace) for p in props]
