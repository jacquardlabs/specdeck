"""The wires every card gets without authoring them.

Three properties the runner adds to every cell: the model did not stop because it ran out
of room, the run finished inside a budget, and it did not cost materially more than the
last time anyone recorded what it cost. They compile to the same property IR a card's own
wires do — one `Never` and two `Bound`s — so the evaluator, the report and the dedup rule
all see one kind of thing and there is no second mechanism to keep in step.

A card overrides a built-in by authoring the same subject: `merge_wires` drops any built-in
whose id an authored property already carries. That is the whole opt-out, and it costs the
card format no new syntax. Its one gap, accepted rather than solved: `stop_reason: not
truncated` is a `Never` with no looser form — the grammar rejects every other rule — so a
card that genuinely cannot avoid truncation has no escape hatch today.

Built-ins are evaluated, never pinned. `compile_wires` stays authored-only, because its
output is hashed into the lockfile's `wires_hash`; a runner upgrade that moved a default
would otherwise read as card drift in every repo, with a `--relock` hint for a card nobody
edited. The merge happens in `cell.run_cell_async` and nowhere else.
"""

from __future__ import annotations

import math

from pydantic import BaseModel, ConfigDict, model_validator

from .ir import Bound, Measure, Never, Operation, Property, Selector
from .wires import TRUNCATED_FINISH_REASON

#: Chosen, not derived. Every committed card already authors a latency wire, so the number
#: only ever applies to a card that authored none — where a budget loose enough to be
#: uncontroversial beats a tight one that reds a first install.
DEFAULT_LATENCY_BUDGET_S = 120.0

#: Also chosen, not derived: a run may cost 10% more than the baseline before it fails.
#: There is no measurement behind the figure; it is a number a human owns.
DEFAULT_TOLERANCE = 0.10


class BuiltinConfig(BaseModel):
    """What the free wires are configured with for this run.

    A baseline of `None` produces no regression wire at all. A repo that has recorded
    nothing must still run: inventing a limit would gate a card on a number nobody chose.
    """

    model_config = ConfigDict(frozen=True)

    latency_budget_s: float = DEFAULT_LATENCY_BUDGET_S
    token_baseline: int | None = None
    tolerance: float = DEFAULT_TOLERANCE

    @model_validator(mode="after")
    def _check(self) -> BuiltinConfig:
        if self.latency_budget_s <= 0:
            raise ValueError(f"a latency budget must be positive, got {self.latency_budget_s:g}")
        if self.tolerance < 0:
            raise ValueError(f"a tolerance must not be negative, got {self.tolerance:g}")
        if self.token_baseline is not None and self.token_baseline <= 0:
            raise ValueError(f"a token baseline must be positive, got {self.token_baseline}")
        return self

    @property
    def token_limit(self) -> float | None:
        """The bound a recorded baseline implies, or None when nothing is recorded.

        A run fails when it exceeds the baseline *by more than* the tolerance, and tokens
        are whole numbers, so the allowance is floored and the bound sits one token above
        it: `Bound` is strictly-under, and a run at exactly the tolerance has not exceeded
        it. Flooring also absorbs the float — `100 * 1.1` is 110.00000000000001, which as a
        limit would let a run at 110 through while reading as though it had not.
        """
        if self.token_baseline is None:
            return None
        return float(math.floor(self.token_baseline * (1 + self.tolerance)) + 1)


def builtin_properties(config: BuiltinConfig) -> list[Property]:
    """The wires every card gets for free.

    Built directly rather than by feeding default text through `compile_wire`:
    `token_baseline` has no wire text that yields its id, and formatting a budget back into
    `under {x}s` breaks on a float the grammar cannot read back — `1e-05` is a legal budget
    and not a legal wire. The objects are identical to what the authored text compiles to,
    which tests/test_builtin.py pins against `compile_wire` itself.
    """
    properties = [
        Property(
            id="stop_reason",
            rule=Never(
                selector=Selector(operation=Operation.CHAT, finish_reason=TRUNCATED_FINISH_REASON)
            ),
        ),
        Property(
            id="latency",
            rule=Bound(measure=Measure.AGENT_DURATION_S, limit=config.latency_budget_s),
        ),
    ]
    if (limit := config.token_limit) is not None:
        properties.append(
            Property(
                id="token_baseline", rule=Bound(measure=Measure.TOTAL_OUTPUT_TOKENS, limit=limit)
            )
        )
    return properties


def merge_wires(authored: list[Property], builtin: list[Property]) -> list[Property]:
    """Authored wires, then whichever built-ins the card did not already speak for.

    Dedup is on `Property.id` alone, and tier is deliberately no part of the key: a card
    writing `stop_reason: not truncated` under `credit:` has asked, in a reviewed PR, for
    that check to be scored rather than gated, and gets exactly that.

    `token_baseline` survives a card's own `response_tokens under 400`, because they are
    different ids for different assertions — an absolute cap, and a regression against what
    this card used to cost. Lint's `contradictory-wires` rule never sees this list, so two
    bounds on one measure raise nothing here; that is intended, not an oversight.
    """
    spoken_for = {p.id for p in authored}
    return authored + [p for p in builtin if p.id not in spoken_for]
