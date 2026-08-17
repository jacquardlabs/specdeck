"""The single-cell report.

Two numbers, never blended. Gate pass rate is the fraction of runs where every gate held;
credit score is weighted binary credit over the passing runs only. A cell that scores 9/10
on credit and fails one gate is a failing cell, and the layout has to make that obvious
rather than leaving it to be read out of two numbers side by side.
"""

from __future__ import annotations

from rich.console import Console
from rich.text import Text

from .cell import Cell
from .ir import Tier

PASS = "[green]PASS[/green]"
FAIL = "[red]FAIL[/red]"


def render(cell: Cell, console: Console) -> None:
    console.print()
    console.print(Text(cell.title, style="bold"), Text(cell.card_path, style="dim"))
    console.print()

    verdict = PASS if cell.passed else FAIL
    console.print(
        f"  gate     {verdict}   {cell.passes}/{cell.runs} runs"
        f"   [dim](passes at {cell.threshold})[/dim]"
    )
    if cell.credit_score is None:
        console.print(
            f"  credit   [dim]n/a — no passing run to score, out of {cell.credit_total}[/dim]"
        )
    else:
        console.print(
            f"  credit   {cell.credit_score:g}/{cell.credit_total}"
            f"   [dim](over {cell.passes} passing run{'' if cell.passes == 1 else 's'})[/dim]"
        )

    first = cell.results[0]
    if first.wires:
        console.print("\n  [dim]wires, first run[/dim]")
        for wire in first.wires:
            console.print(f"    {_mark(wire.passed)} {wire.id:<34} [dim]{wire.detail}[/dim]")
    if first.judged:
        console.print("\n  [dim]criteria, first run[/dim]")
        for criterion in first.judged.verdicts:
            weight = (
                f" [dim](credit {criterion.weight})[/dim]" if criterion.tier is Tier.CREDIT else ""
            )
            console.print(f"    {_mark(criterion.passed)} {criterion.id}{weight}")
            if criterion.reason:
                # The reason wraps; giving it its own line keeps the verdict column readable.
                console.print(f"         [dim]{criterion.reason}[/dim]")

    replayed = all(r.judged.replayed for r in cell.results if r.judged)
    source = "replayed" if replayed else "live"
    console.print(
        f"\n  [dim]judge {cell.judge_model} ({source}), "
        f"{cell.judge_calls} call{'' if cell.judge_calls == 1 else 's'} "
        f"over {cell.runs} run{'' if cell.runs == 1 else 's'}[/dim]"
    )
    console.print()


def _mark(passed: bool) -> str:
    return "[green]ok  [/green]" if passed else "[red]FAIL[/red]"
