"""One cell: one card x one provider x one prompt, over N runs.

The cell is where the locked execution order is enforced, because it is the only place a
call can be skipped: gate wires, then gate criteria, then credit. A card that touched a
forbidden tool needs no judge, and gate wires are free.

It reports two numbers and never blends them. Gate pass rate is the fraction of runs where
every gate held, and the cell passes at >=k of N. Credit score is the weighted sum of
binary credit verdicts over the passing runs only — credit never offsets a failed gate, so
a run that failed a gate contributes nothing rather than contributing its credit.

Variance, latency percentiles, and the dollar estimate are #52.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel

from .card import Card
from .ir import Tier, Verdict, evaluate_all
from .judge import DEFAULT_JUDGE_MODEL, JudgeResult, criteria_of, judge
from .trace import Trace
from .wires import compile_wires, gates_pass

DEFAULT_N = 5
DEFAULT_K = 4


class CellError(Exception):
    """The cell cannot be run as specified."""


class Run(BaseModel):
    """One run of the card against one trace."""

    passed: bool
    wires: list[Verdict]
    judged: JudgeResult | None
    credit_earned: int

    @property
    def judge_called(self) -> bool:
        return self.judged is not None


class Cell(BaseModel):
    card_path: str
    title: str
    runs: int
    threshold: int
    passes: int
    credit_earned: float | None
    credit_total: int
    judge_model: str
    judge_calls: int
    results: list[Run]

    @property
    def passed(self) -> bool:
        return self.passes >= self.threshold

    @property
    def credit_score(self) -> float | None:
        """Weighted credit over the passing runs. None when no run passed."""
        return self.credit_earned


def run_cell(
    card: Card,
    traces: list[Trace],
    *,
    cassettes: Path | str,
    n: int = DEFAULT_N,
    k: int = DEFAULT_K,
    judge_model: str = DEFAULT_JUDGE_MODEL,
    live: bool = False,
) -> Cell:
    if len(traces) != n:
        raise CellError(
            f"{card.path}: the cell is {n} runs but {len(traces)} trace(s) were supplied — "
            "record one trace per run, or set --runs to what you have"
        )
    if not 1 <= k <= n:
        raise CellError(f"{card.path}: pass threshold {k} must be between 1 and {n} runs")

    properties = compile_wires(card)
    gate_wires = [p for p in properties if p.tier is Tier.GATE]
    credit_wires = [p for p in properties if p.tier is Tier.CREDIT]
    criteria = criteria_of(card)
    policy = card.policy_path.read_text() if card.policy_path and card.policy_path.exists() else ""
    credit_total = sum(p.weight for p in credit_wires) + sum(
        c.weight for c in criteria if c.tier is Tier.CREDIT
    )

    results = [
        _run(
            trace,
            gate_wires=gate_wires,
            credit_wires=credit_wires,
            criteria=criteria,
            policy=policy,
            cassettes=cassettes,
            judge_model=judge_model,
            live=live,
        )
        for trace in traces
    ]
    passing = [r for r in results if r.passed]
    return Cell(
        card_path=card.path,
        title=card.title,
        runs=n,
        threshold=k,
        passes=len(passing),
        credit_earned=(sum(r.credit_earned for r in passing) / len(passing) if passing else None),
        credit_total=credit_total,
        judge_model=judge_model,
        judge_calls=sum(r.judge_called for r in results),
        results=results,
    )


def _run(
    trace: Trace,
    *,
    gate_wires,
    credit_wires,
    criteria,
    policy: str,
    cassettes: Path | str,
    judge_model: str,
    live: bool,
) -> Run:
    # 1. Gate wires. Free, and a failure here means the judge is never called.
    wire_verdicts = evaluate_all(gate_wires, trace)
    if not gates_pass(wire_verdicts):
        return Run(passed=False, wires=wire_verdicts, judged=None, credit_earned=0)

    # 2. Gate criteria.
    judged = judge(
        criteria, trace, policy=policy, cassettes=cassettes, model=judge_model, live=live
    )
    if not judged.gate_passed:
        return Run(passed=False, wires=wire_verdicts, judged=judged, credit_earned=0)

    # 3. Credit, only once every gate has held.
    credit = sum(v.weight for v in evaluate_all(credit_wires, trace) if v.passed)
    credit += sum(v.weight for v in judged.verdicts if v.tier is Tier.CREDIT and v.passed)
    return Run(passed=True, wires=wire_verdicts, judged=judged, credit_earned=credit)
