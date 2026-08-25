"""The single-cell report.

Two numbers, never blended. Gate pass rate is the fraction of runs where every gate held;
credit score is weighted binary credit over the passing runs only. A cell that scores 9/10
on credit and fails one gate is a failing cell, and the layout has to make that obvious
rather than leaving it to be read out of two numbers side by side.

Under those two, three secondary figures — the credit spread, latency p50/p95, and a
dollar estimate — printed dim and in the same label column, so they qualify the headline
numbers without competing with them. Waste findings are lower still, with the run detail
between: they are a variable-length list rather than a figure, and a cell with four of
them would push the numbers off the top of the block they belong to.

The detail shown is the first *failing* run, not the first run: a cell that fails 4 of 5
and prints run one's all-green checks tells the reader nothing they can act on.

Model-authored text — a judge's reason — is printed as `Text`, never as markup. A reason
containing `[/tmp]` would otherwise raise MarkupError and discard the whole report after
every wire and judge call has already been paid for. The same holds for text that came out
of a user-supplied trace: a model id and a waste summary both reach the page unescaped.
"""

from __future__ import annotations

from rich.console import Console
from rich.text import Text

from . import stats
from .cell import Cell, Run
from .rates import Rates
from .tier import Tier
from .waste import UNITS, Finding, Level

PASS = "[green]PASS[/green]"
FAIL = "[red]FAIL[/red]"


def render(cell: Cell, console: Console, *, rates: Rates | None = None) -> None:
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

    console.print()
    console.print(_variance_line(cell))
    console.print(_latency_line(cell))
    console.print(_cost_line(cell, rates))

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
    _waste(cell, console)
    console.print()


def _figure(label: str, detail: str) -> Text:
    """One secondary figure, in the headline labels' own column and dim beneath them."""
    return Text(f"  {label:<9}{detail}", style="dim")


def _variance_line(cell: Cell) -> Text:
    """How far apart the passing runs' credit scores were.

    Taken over exactly the runs `credit_mean` is taken over, so the two cannot disagree:
    the figure exists to say what "credit 3/3 over 4 passing runs" hides when the runs
    scored 1, 3, 3, 5.
    """
    spread = stats.credit_spread([run.credit_earned for run in cell.results if run.passed])
    if spread is None:
        detail = f"n/a — {_runs(cell.passes, 'passing run')}, a spread needs two"
    else:
        detail = (
            f"credit {spread.low}-{spread.high}, sd {spread.sd:.2f} "
            f"over {_runs(spread.n, 'passing run')}"
        )
    # Only when the gate itself went both ways. "5 pass / 0 fail" restates the headline.
    if 0 < cell.passes < cell.runs:
        detail += f"; gate mixed, {cell.passes} pass / {cell.runs - cell.passes} fail"
    return _figure("variance", detail)


def _latency_line(cell: Cell) -> Text:
    """End-to-end run duration, always printed with the sample count it rests on.

    A p95 over five runs is the fourth-largest sample leaning on the maximum. Stating n
    lets the reader discount it; suppressing p95 at small n would give them a figure that
    appears and disappears without saying why.
    """
    measured = stats.latency([run.measured.duration_s for run in cell.results])
    return _figure(
        "latency",
        f"p50 {measured.p50:g}s, p95 {measured.p95:g}s over {_runs(measured.n, 'run')}",
    )


def _cost_line(cell: Cell, rates: Rates | None) -> Text:
    """The agent's traced tokens, priced.

    Agent tokens only, and the line says so: specdeck's own judge and simulator calls
    return bare text, so pricing those would mean inventing what they spent. Every dollar
    branch goes through `Estimate.label`, which is the one render path that always says
    "estimate" and always carries the date the rates were checked.
    """
    usage = stats.total_usage([run.measured for run in cell.results])
    silent = stats.unreported(usage)
    if rates is None:
        detail = "n/a — no rate table"
    elif (estimate := stats.cost_estimate(usage, rates)) is None:
        # "incomplete", not "no": `stats.unreported` flags a model when either half is
        # missing, and a trace reporting a million input tokens and no output count did
        # emit gen_ai.usage. Sending the reader to instrument what they already emit is
        # the wrong instruction. Only an empty table means nothing was reported at all.
        detail = (
            f"n/a — incomplete gen_ai.usage from {', '.join(silent)}"
            if silent
            else "n/a — no gen_ai.usage from any model"
        )
    else:
        detail = estimate.label
        if silent:
            detail += f"; incomplete gen_ai.usage from {', '.join(silent)}"
    # Scope on the line, not in the docs: the figure covers what the agent's own trace
    # reported and nothing else, and it is a total over the cell rather than one run's.
    return _figure("cost", f"{detail}, agent tokens only, {_runs(cell.runs, 'run')}")


def _waste(cell: Cell, console: Console) -> None:
    """What the runs spent and did not need to. Never a verdict, never an exit code.

    Nothing is printed when nothing was found, so a clean report means one thing. Findings
    are collapsed by summary: five runs of a scripted agent produce the same finding five
    times, and five identical lines say no more than one line and a count.
    """
    if not cell.waste:
        return
    console.print("\n  [dim]waste[/dim]")
    grouped: dict[str, list[Finding]] = {}
    for finding in cell.waste:
        grouped.setdefault(finding.summary, []).append(finding)
    worst_first = sorted(
        grouped.items(),
        key=lambda item: (item[1][0].severity is not Level.HIGH, item[1][0].first_span),
    )
    for summary, findings in worst_first:
        line = Text("    ")
        line.append(f"{findings[0].severity.value:<7}", style="yellow")
        line.append(summary)
        line.append(f"   in {len(findings)} of {_runs(cell.runs, 'run')}", style="dim")
        console.print(line)
    # One total per kind, never one number: tokens and token-turns are different units.
    # Each is named and stated across the runs it covers — a finding in four of five runs
    # was paid for four times, and the line above it says four, not five.
    for kind in dict.fromkeys(findings[0].kind for _, findings in worst_first):
        tokens = cell.waste_tokens[kind]
        total = (
            f"~{tokens:,} {UNITS[kind]}, estimated, across {_runs(cell.runs, 'run')}"
            if tokens is not None
            else "not reported by the trace"
        )
        console.print(Text(f"    {kind.value:<15}{total}", style="dim"))


def _runs(n: int, noun: str) -> str:
    return f"{n} {noun}{'' if n == 1 else 's'}"


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
