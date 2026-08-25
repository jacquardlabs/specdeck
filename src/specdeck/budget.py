"""The hard cap. A matrix that silently spends is not shippable (#15).

WHAT THE CAP COUNTS, AND WHERE IT CAN ACTUALLY PREVENT ANYTHING

It counts everything: specdeck's own judge and simulator calls, and the agent's own model
calls read back off the trace. But the two halves are not symmetric, and pretending they
are would be the dishonest part.

specdeck's own spend is genuinely *prevented*. Both calls go through `provider.complete`,
`check()` runs before each one, and both cost nothing at all in replay.

The agent's spend is only ever *reactive*. Its model calls happen inside the user's
`adapter.run`, which spends the money and then reports what it spent afterwards, as
optional `Chat.input_tokens`/`Chat.output_tokens`. specdeck sees the number when the run
is already over. So: the pre-flight refuses to start a matrix it cannot price, `check()`
refuses to start a column the cap can no longer afford, and an overshoot aborts what is
left — but **a single agent run can exceed the whole remaining budget before specdeck ever
sees a token count**. That is stated in the report and in the README rather than glossed.

THE OVERSHOOT IS BOUNDED AND PRINTED

When the cap trips, in-flight work is allowed to finish. `judge.Cassette.write` records
only after the reply parses, so cancelling mid-flight throws away a fixture that has
already been paid for. Only *new* work is refused, and the overshoot is bounded by
matrix concurrency x cell concurrency calls plus whatever one agent run costs.

FAIL-CLOSED, THREE WAYS. Charging zero for a run nobody can price is the exact failure the
cap exists to prevent, so under a cap each of these refuses instead:

1. a column whose declared model has no rate never starts (`preflight`);
2. a trace whose model is `loop`'s `unknown` placeholder aborts, naming the adapter;
3. a trace reporting no `gen_ai.usage.output_tokens` aborts, naming the adapter.

Every figure is priced through `rates.Rates.estimate` and carried as one `rates.Estimate`,
so the cap and the report cannot disagree about what a run cost.
"""

from __future__ import annotations

from collections.abc import Iterable

from .matrix import Column
from .rates import Estimate, Rates
from .trace import UNKNOWN_MODEL, GenAI, Trace


class BudgetError(Exception):
    """The matrix cannot be governed by the cap it was given. Refused before it starts."""


class BudgetStop(Exception):
    """The matrix must not continue: the cap is reached, or a run cannot be priced.

    Its own class, and not a `USER_ERROR`. A run that could not start and a run that
    stopped part-way are different facts, and the exit code has to keep them apart — the
    answer to a stopped matrix is unknown, which must not reach CI as an eval regression.
    """


class Budget:
    """What has been spent, and whether anything more may be.

    Mutable, shared by every column, and deliberately holding no lock. The runner's one
    concurrency primitive is asyncio (see `matrix_run`), so every charge happens on one
    thread with no await between reading `_spent` and writing it back. A lock here would
    be ceremony that implies a thread-safety this object does not have and does not need;
    if a thread pool ever appears, this comment is the thing that has to change first.
    """

    def __init__(self, *, cap_usd: float | None, rates: Rates) -> None:
        if cap_usd is not None and cap_usd <= 0:
            raise BudgetError(f"a budget cap must be a positive number of dollars, got {cap_usd:g}")
        self.cap_usd = cap_usd
        self.rates = rates
        self._spent = Estimate.nothing(rates.verified)
        #: Models whose calls reported no usage at all. Counted, never charged as zero:
        #: the footer names them so the spend figure reads as the floor it is.
        self.unmetered: dict[str, int] = {}

    @property
    def capped(self) -> bool:
        return self.cap_usd is not None

    @property
    def spent(self) -> Estimate:
        """What has been charged so far, as wave 1's `Estimate`. `label` is how it prints."""
        return self._spent

    @property
    def stopped(self) -> bool:
        """Whether the cap has been reached. Not the same as "the matrix stopped early":
        a cap reached on the very last charge stopped nothing."""
        return self.cap_usd is not None and self._spent.usd >= self.cap_usd

    def check(self, what: str) -> None:
        """Refuse `what` if the cap is already reached. Before every unit of live work.

        Called before the work, never after: a call already in flight is money already
        committed, and killing it loses the cassette it was about to record.
        """
        if self.stopped:
            raise BudgetStop(
                f"the ${self.cap_usd:g} budget cap is reached ({self._spent.label}) — "
                f"{what} was not started"
            )

    def charge(self, model: str, *, input_tokens: int | None, output_tokens: int | None) -> None:
        """Record what one call cost, priced through the same table the report prints from.

        A call reporting neither half is counted as unmetered rather than charged as zero:
        "did not say" and "spent nothing" are different answers, and the second one is how
        a cap quietly stops working.
        """
        if input_tokens is None and output_tokens is None:
            self.unmetered[model] = self.unmetered.get(model, 0) + 1
            return
        self._spent = self._spent + self.rates.estimate(
            model, input_tokens=input_tokens or 0, output_tokens=output_tokens or 0
        )

    def preflight(self, columns: Iterable[Column]) -> None:
        """Refuse a matrix whose columns the cap cannot govern, before any of them starts.

        The whole matrix, not the offending column alone. Skipping it would keep the cap
        enforceable over what did run, so fail-closed is not the argument — the exit code
        is. There is no honest code for "ran three of four because the rate table was
        incomplete": 4 would claim the budget stopped it and 2 would claim nothing
        started, and both would be false. Refusing here makes 2 literally true, and the
        message names the column so the user knows which line to fix.
        """
        if not self.capped:
            return
        unpriced = [
            f"{column.name} ({column.model})"
            for column in columns
            if self.rates.rate_for(column.model) is None
        ]
        if unpriced:
            raise BudgetError(
                f"no rate for {', '.join(unpriced)} — a budget cap cannot be held over a "
                "column nobody can price. Add the model to a rates.toml beside the card, "
                "or drop the cap."
            )

    def charge_trace(self, trace: Trace, *, adapter: str) -> None:
        """Charge one agent run, from what its own trace reported. Reactive, by necessity.

        The adapter has already spent this money. All this can do is record it, refuse to
        record a fiction, and let `check()` stop the next column.
        """
        usage = trace.usage_by_model
        if self.capped:
            self._refuse_unpriceable(trace, usage, adapter=adapter)
        for model, (input_tokens, output_tokens) in usage.items():
            self.charge(model, input_tokens=input_tokens, output_tokens=output_tokens)

    def _refuse_unpriceable(
        self, trace: Trace, usage: dict[str, tuple[int | None, int | None]], *, adapter: str
    ) -> None:
        """The three fail-closed rules, in the order that gives the clearest instruction."""
        if UNKNOWN_MODEL in usage:
            raise BudgetStop(
                f"{adapter} reported no model on a chat span, so the run records "
                f"{GenAI.REQUEST_MODEL} = {UNKNOWN_MODEL!r} and has no rate — under a "
                "budget cap that is a run nobody can price, not a run that was free. Set "
                "`model` on the Chat events the adapter returns."
            )
        if not trace.reports_output_tokens:
            raise BudgetStop(
                f"{adapter} reported no {GenAI.USAGE_OUTPUT_TOKENS} on any chat span — "
                "under a budget cap that run would be charged zero, which is exactly the "
                "silent spend the cap exists to refuse. Report usage on the Chat events "
                "the adapter returns."
            )
        for model, (input_tokens, output_tokens) in usage.items():
            if input_tokens is None or output_tokens is None:
                half = "input" if input_tokens is None else "output"
                raise BudgetStop(
                    f"{adapter} reported no gen_ai.usage.{half}_tokens for {model} — half a "
                    "run's cost is not a cost, and a budget cap will not charge the other "
                    "half as the whole."
                )
            if self.rates.rate_for(model) is None:
                raise BudgetStop(
                    f"{adapter} called {model}, which the rate table does not price — the "
                    "column declared a different model, and a cap that guards a model "
                    "nobody ran is not a cap."
                )
