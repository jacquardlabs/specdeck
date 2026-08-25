"""One cell: one card x one provider x one prompt, over N runs.

The cell is where the locked execution order is enforced, because it is the only place a
call can be skipped: gate wires, then gate criteria, then credit. A card that touched a
forbidden tool needs no judge, and gate wires are free.

It reports two numbers and never blends them. Gate pass rate is the fraction of runs where
every gate held, and the cell passes at >=k of N. Credit score is the weighted sum of
binary credit verdicts over the passing runs only — credit never offsets a failed gate, so
a run that failed a gate contributes nothing rather than contributing its credit.

Every cell evaluates three wires the card did not author — `stop_reason`, a latency budget,
and a token regression against a recorded baseline — merged in here and nowhere else. See
`builtin.py` for why the compiler does not know about them.

Beneath those two it reports three secondary figures — the spread of credit over the
passing runs, latency p50/p95, and a dollar estimate over the agent's traced tokens — and
any waste the run's trace shows. None of them moves the verdict: a card that passed while
burning four times the tokens is a finding, not a failure.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from pydantic import BaseModel, Field

from .budget import Budget
from .builtin import BuiltinConfig, builtin_properties, merge_wires
from .card import Card
from .ir import Property, Verdict, evaluate_all
from .judge import DEFAULT_JUDGE_MODEL, Criterion, JudgeResult, criteria_of, judge
from .stats import RunMeasures, measure
from .tier import Tier
from .trace import Trace, reported_sum
from .waste import Finding, Kind, classify
from .wires import compile_wires, gates_pass

DEFAULT_N = 5
DEFAULT_K = 4

#: Runs in flight at once. Bounded rather than unbounded: a gather over a provider x prompt
#: matrix is a rate-limit incident, not parallelism.
DEFAULT_CONCURRENCY = 4


class CellError(Exception):
    """The cell cannot be run as specified."""


class Run(BaseModel):
    """One run of the card against one trace."""

    passed: bool
    wires: list[Verdict]
    judged: JudgeResult | None
    credit_earned: int
    #: Time and tokens. Required rather than defaulted: a run nobody measured would
    #: otherwise contribute a 0.0 second duration to the cell's p50 as if it were timed.
    #: `RunMeasures.nothing()` is how a hand-built run says it measured nothing.
    measured: RunMeasures
    #: What the run spent and did not need to. Defaulted, because a run built without it
    #: is a run nothing classified, not a run that was found clean.
    waste: list[Finding] = Field(default_factory=list)

    @property
    def judge_called(self) -> bool:
        return self.judged is not None


class Cell(BaseModel):
    """One card x one provider x one prompt, over N runs.

    Two numbers, never blended: `passes` out of `runs` is the gate rate and decides
    `passed`; `credit_mean` is the weighted credit over the passing runs only. A run that
    failed a gate contributes nothing to credit rather than contributing its own.
    """

    card_path: str
    title: str
    runs: int
    threshold: int
    passes: int
    credit_mean: float | None
    credit_total: int
    judge_model: str
    judge_calls: int
    #: Empty when the runs came from recorded traces: no simulated user spoke, and the
    #: report should not name a model that did not run.
    simulator_model: str = ""
    results: list[Run]

    @property
    def passed(self) -> bool:
        return self.passes >= self.threshold

    @property
    def waste(self) -> list[Finding]:
        """Every run's findings, in one list. Derived, like `passed`, so nothing syncs it."""
        return [finding for run in self.results for finding in run.waste]

    @property
    def waste_tokens(self) -> dict[Kind, int | None]:
        """What the waste cost, one total per kind, in each kind's own unit.

        Not one number: a retry loop is measured in tokens and a stale result in
        token-turns, so a single sum would be a figure in no unit at all. Kept apart here
        rather than at each reader, since only this module knows the two are different.
        """
        return {
            kind: reported_sum(*(f.waste_tokens for f in self.waste if f.kind is kind))
            for kind in dict.fromkeys(finding.kind for finding in self.waste)
        }


def run_cell(
    card: Card,
    traces: list[Trace],
    *,
    cassettes: Path | str,
    n: int = DEFAULT_N,
    k: int = DEFAULT_K,
    judge_model: str = DEFAULT_JUDGE_MODEL,
    simulator_model: str = "",
    live: bool = False,
    concurrency: int = DEFAULT_CONCURRENCY,
    builtin: BuiltinConfig | None = None,
    budget: Budget | None = None,
) -> Cell:
    """Synchronous entry point. The CLI's single-cell path and tests call this.

    It owns the event loop, which is why the matrix cannot: `asyncio.run` does not nest,
    so a matrix awaiting several cells goes through `run_cell_async` directly.
    """
    return asyncio.run(
        run_cell_async(
            card,
            traces,
            cassettes=cassettes,
            n=n,
            k=k,
            judge_model=judge_model,
            simulator_model=simulator_model,
            live=live,
            concurrency=concurrency,
            builtin=builtin,
            budget=budget,
        )
    )


async def run_cell_async(
    card: Card,
    traces: list[Trace],
    *,
    cassettes: Path | str,
    n: int = DEFAULT_N,
    k: int = DEFAULT_K,
    judge_model: str = DEFAULT_JUDGE_MODEL,
    simulator_model: str = "",
    live: bool = False,
    concurrency: int = DEFAULT_CONCURRENCY,
    builtin: BuiltinConfig | None = None,
    budget: Budget | None = None,
) -> Cell:
    if len(traces) != n:
        raise CellError(
            f"{card.path}: the cell is {n} runs but {len(traces)} trace(s) were supplied — "
            "record one trace per run, or set --runs to what you have"
        )
    if not 1 <= k <= n:
        raise CellError(f"{card.path}: pass threshold {k} must be between 1 and {n} runs")

    # The one place authored and built-in wires meet. `compile_wires` stays authored-only
    # because its output is hashed into the lockfile — a default moving under a card nobody
    # edited must not read as drift — so the merge lives here rather than in the compiler.
    properties = merge_wires(compile_wires(card), builtin_properties(builtin or BuiltinConfig()))
    gate_wires = [p for p in properties if p.tier is Tier.GATE]
    credit_wires = [p for p in properties if p.tier is Tier.CREDIT]
    criteria = criteria_of(card)
    policy = _policy(card)
    credit_total = sum(p.weight for p in credit_wires) + sum(
        c.weight for c in criteria if c.tier is Tier.CREDIT
    )

    # The one place runs fan out. Bounding it here keeps concurrency a property of the
    # cell rather than something each caller has to remember.
    limit = asyncio.Semaphore(max(1, concurrency))

    async def one(trace: Trace) -> Run:
        async with limit:
            return await _run(
                trace,
                gate_wires=gate_wires,
                credit_wires=credit_wires,
                criteria=criteria,
                policy=policy,
                cassettes=cassettes,
                judge_model=judge_model,
                live=live,
                slug=card.slug,
                budget=budget,
            )

    results = list(await asyncio.gather(*(one(trace) for trace in traces)))
    passing = [r for r in results if r.passed]
    return Cell(
        card_path=card.path,
        title=card.title,
        runs=n,
        threshold=k,
        passes=len(passing),
        credit_mean=(sum(r.credit_earned for r in passing) / len(passing) if passing else None),
        credit_total=credit_total,
        judge_model=judge_model,
        judge_calls=sum(r.judge_called for r in results),
        simulator_model=simulator_model,
        results=results,
    )


def _policy(card: Card) -> str:
    """The policy the judge grades against, or an error naming the card's own value.

    Falling back to an empty policy would have the judge grade a run with no rules and
    say nothing about it — and under --live that verdict is then recorded as the fixture.
    """
    path = card.policy_path
    if path is None:
        return ""
    if not path.exists():
        raise CellError(f"{card.path}: policy {card.context.policy!r} does not exist at {path}")
    return path.read_text()


async def _run(
    trace: Trace,
    *,
    gate_wires: list[Property],
    credit_wires: list[Property],
    criteria: list[Criterion],
    policy: str,
    cassettes: Path | str,
    judge_model: str,
    live: bool,
    slug: str = "",
    budget: Budget | None = None,
) -> Run:
    # Measured before anything can return: a run that failed a gate wire still took time,
    # still burned tokens, and still shows whatever it wasted doing it.
    measured = measure(trace)
    waste = classify(trace)

    # 1. Gate wires. Free, and a failure here means the judge is never called.
    wire_verdicts = evaluate_all(gate_wires, trace)
    if not gates_pass(wire_verdicts):
        return Run(
            passed=False,
            wires=wire_verdicts,
            judged=None,
            credit_earned=0,
            measured=measured,
            waste=waste,
        )

    # 2. Gate criteria.
    judged = await judge(
        criteria,
        trace,
        policy=policy,
        cassettes=cassettes,
        model=judge_model,
        live=live,
        slug=slug,
        budget=budget,
    )
    if not judged.gate_passed:
        return Run(
            passed=False,
            wires=wire_verdicts,
            judged=judged,
            credit_earned=0,
            measured=measured,
            waste=waste,
        )

    # 3. Credit, only once every gate has held.
    credit = sum(v.weight for v in evaluate_all(credit_wires, trace) if v.passed)
    credit += sum(v.weight for v in judged.verdicts if v.tier is Tier.CREDIT and v.passed)
    return Run(
        passed=True,
        wires=wire_verdicts,
        judged=judged,
        credit_earned=credit,
        measured=measured,
        waste=waste,
    )
