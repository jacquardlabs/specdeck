"""The single-cell report.

Two numbers, never blended. Gate pass rate is the fraction of runs where every gate held;
credit score is weighted binary credit over the passing runs only. A cell that scores 9/10
on credit and fails one gate is a failing cell, and the layout has to make that obvious
rather than leaving it to be read out of two numbers side by side.

The detail shown is the first *failing* run, not the first run: a cell that fails 4 of 5
and prints run one's all-green checks tells the reader nothing they can act on.

Model-authored text — a judge's reason — is printed as `Text`, never as markup. A reason
containing `[/tmp]` would otherwise raise MarkupError and discard the whole report after
every wire and judge call has already been paid for.
"""

from __future__ import annotations

from rich.console import Console
from rich.text import Text

from .cell import Cell, Run
from .tier import Tier

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
    if cell.credit_mean is None:
        console.print(
            f"  credit   [dim]n/a — no passing run to score, out of {cell.credit_total}[/dim]"
        )
    else:
        console.print(
            f"  credit   {cell.credit_mean:g}/{cell.credit_total}"
            f"   [dim](over {cell.passes} passing run{'' if cell.passes == 1 else 's'})[/dim]"
        )

    shown, index = _detail_run(cell)
    label = f"run {index + 1} of {cell.runs}"
    if shown.wires:
        console.print(f"\n  [dim]wires, {label}[/dim]")
        # Padded to the widest id present rather than a fixed column: after-K-then-Y ids
        # run past any constant, and a detail welded to its label reads as one word. See #71.
        column = max(len(w.id) for w in shown.wires) + 2
        for wire in shown.wires:
            line = _verdict_line(wire.passed)
            line.append(f"{wire.id:<{column}}")
            line.append(wire.detail, style="dim")
            console.print(line)
    if shown.judged:
        console.print(f"\n  [dim]criteria, {label}[/dim]")
        for criterion in shown.judged.verdicts:
            line = _verdict_line(criterion.passed)
            # The SME's own sentence, not the slug: the primary persona has to recognise
            # their own words in the report for their own card.
            line.append(_headline(criterion.text))
            if criterion.tier is Tier.CREDIT:
                line.append(f"  (credit {criterion.weight})", style="dim")
            console.print(line)
            if criterion.reason:
                console.print(Text(f"         {criterion.reason}", style="dim"))
    elif not shown.passed:
        console.print("\n  [dim]criteria not reached — a gate wire failed first[/dim]")

    judged = [r.judged for r in cell.results if r.judged]
    console.print(
        f"\n  [dim]judge {cell.judge_model} ({_source(judged)}), "
        f"{cell.judge_calls} call{'' if cell.judge_calls == 1 else 's'} "
        f"over {cell.runs} run{'' if cell.runs == 1 else 's'}[/dim]"
    )
    # DECISIONS.md pins the simulator like the judge, "with its version printed in every
    # report". Printed only when there is one: a card run from a recorded trace had no
    # simulated user, and naming a model that did not speak would be a claim about the run.
    if cell.simulator_model:
        console.print(f"  [dim]simulator {cell.simulator_model}[/dim]")
    # Resampling is never silent: a criterion the judge had to be asked twice for is
    # ambiguous prose, and the SME can only reword what the report admits to.
    resamples = sum(j.resamples for j in judged)
    if resamples:
        console.print(
            f"  [dim]{resamples} repl{'y' if resamples == 1 else 'ies'} discarded as "
            f"ungradable before one parsed[/dim]"
        )
    console.print()


def _verdict_line(passed: bool) -> Text:
    """An indented `ok`/`FAIL` mark, built as Text so what follows is never markup."""
    line = Text("    ")
    line.append("ok   " if passed else "FAIL ", style="green" if passed else "red")
    return line


def _detail_run(cell: Cell) -> tuple[Run, int]:
    """The run worth reading: the first failing one, else the first."""
    for index, run in enumerate(cell.results):
        if not run.passed:
            return run, index
    return cell.results[0], 0


def _headline(text: str) -> str:
    """One line of the criterion, so a multi-paragraph prose block stays readable."""
    first = text.strip().splitlines()[0] if text.strip() else "(empty)"
    return first if len(first) <= 72 else f"{first[:71]}…"


def _source(judged: list) -> str:
    if not judged:
        return "not called"
    if all(j.replayed for j in judged):
        return "replayed"
    return "live" if not any(j.replayed for j in judged) else "mixed replay and live"
