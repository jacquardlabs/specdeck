"""What a cell measured, alongside what it verdicted.

Three figures qualify the two headline numbers rather than competing with them. Variance
is the spread of per-run credit over the passing runs — the same set `credit_mean` is
taken over, so the two cannot contradict each other. A cell printing "credit 3/3 over 4
passing runs" today hides the sequence 1, 3, 3, 5, and the headline alone cannot say
which it was. Latency is the `invoke_agent` span's end-to-end duration, which is exactly
what `Measure.AGENT_DURATION_S` means, so the report and a card's `latency: under 120s`
wire can never disagree about what was timed.

Percentiles interpolate linearly between the order statistics at `(n-1)q` — numpy's
default and `statistics.quantiles(method="inclusive")`. Over a cell of five runs a p95 is
the fourth-largest sample leaning on the maximum and is no kind of tail estimate, which is
why `Latency` carries its own `n` and the report always prints it.

Nothing here defines a cost type. `rates.Estimate` is the one dollar figure in the
codebase, it already knows how to say "partial", and `Estimate.label` is the only
sanctioned way to render one — this module folds per-model estimates and hands the fold
back.
"""

from __future__ import annotations

import statistics
from collections.abc import Iterable

from pydantic import BaseModel, ConfigDict, Field

from .rates import Estimate, Rates
from .trace import Trace, reported_sum

#: Reported input and output tokens per model, as `Trace.usage_by_model` groups them.
Usage = dict[str, tuple[int | None, int | None]]


class RunMeasures(BaseModel):
    """What one run cost in time and tokens, fixed at the trace boundary.

    Held on the `Run` rather than recomputed from the trace by each reader, because a run
    that failed a gate wire is dropped from the credit arithmetic and must still be
    counted here: it took just as long and burned just as many tokens.
    """

    model_config = ConfigDict(frozen=True)

    duration_s: float
    usage: Usage = Field(default_factory=dict)

    @classmethod
    def nothing(cls) -> RunMeasures:
        """A run that measured nothing — for a `Run` built by hand in a test.

        The field itself has no default on purpose: a defaulted 0.0 would let a hand-built
        run contribute a duration nobody measured to the cell's p50.
        """
        return cls(duration_s=0.0)


class Spread(BaseModel):
    """How far apart the per-run credit scores were."""

    model_config = ConfigDict(frozen=True)

    low: int
    high: int
    sd: float
    n: int


class Latency(BaseModel):
    """End-to-end run duration across a cell, with the sample count it rests on."""

    model_config = ConfigDict(frozen=True)

    p50: float
    p95: float
    n: int


def measure(trace: Trace) -> RunMeasures:
    return RunMeasures(duration_s=trace.root.duration_s, usage=trace.usage_by_model)


def percentile(values: list[float], q: float) -> float:
    """The q-quantile, interpolated between the order statistics at `(n-1)q`."""
    if not values:
        raise ValueError("no values to take a percentile of")
    ordered = sorted(values)
    position = (len(ordered) - 1) * q
    below = int(position)
    above = min(below + 1, len(ordered) - 1)
    return ordered[below] + (ordered[above] - ordered[below]) * (position - below)


def latency(durations: list[float]) -> Latency:
    return Latency(
        p50=percentile(durations, 0.5), p95=percentile(durations, 0.95), n=len(durations)
    )


def credit_spread(earned: list[int]) -> Spread | None:
    """The spread of credit over the runs given, or None below two of them.

    One run has no spread, and reporting sd 0.0 for it would read as "every run agreed"
    rather than "there was nothing to compare".
    """
    if len(earned) < 2:
        return None
    return Spread(low=min(earned), high=max(earned), sd=statistics.pstdev(earned), n=len(earned))


def total_usage(measures: Iterable[RunMeasures]) -> Usage:
    """One usage table over a cell's runs, folded the way `Trace.usage_by_model` folds."""
    totals: Usage = {}
    for measured in measures:
        for model, (used_input, used_output) in measured.usage.items():
            seen_input, seen_output = totals.get(model, (None, None))
            totals[model] = (
                reported_sum(seen_input, used_input),
                reported_sum(seen_output, used_output),
            )
    return totals


def unreported(usage: Usage) -> tuple[str, ...]:
    """Models whose chat spans did not report both halves of their usage.

    Named separately from the estimate so the report can say a model went unpriced because
    the trace stayed silent, which is a different statement from having no rate for it.
    """
    return tuple(
        sorted(m for m, (used_in, used_out) in usage.items() if None in (used_in, used_out))
    )


def cost_estimate(usage: Usage, rates: Rates) -> Estimate | None:
    """What the agent's traced tokens cost, or None when nothing may be priced.

    A model reporting only one half of its usage is skipped rather than charged zero for
    the other: it is the same "did not say" the trace keeps out of every other sum, and
    half a figure under an "estimate" label still reads as the whole run's cost.
    """
    priced = [
        rates.estimate(model, input_tokens=used_input, output_tokens=used_output)
        for model, (used_input, used_output) in sorted(usage.items())
        if used_input is not None and used_output is not None
    ]
    if not priced:
        return None
    return sum(priced, start=Estimate.nothing(rates.verified))
