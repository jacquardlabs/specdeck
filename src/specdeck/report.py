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
from rich.table import Table
from rich.text import Text

from . import stats
from .cell import Cell, Run, Suite
from .coverage import UNDERSTATED, Coverage, PathCoverage, PolicyDocument, VocabularyTable
from .introspect import Introspection
from .judge import JudgeResult
from .matrix_run import ColumnResult, MatrixResult, Status
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
            line.append(headline(criterion.text))
            if criterion.tier is Tier.CREDIT:
                line.append(f"  (credit {criterion.weight})", style="dim")
            console.print(line)
            if criterion.reason:
                console.print(Text(f"         {criterion.reason}", style="dim"))
    elif not shown.passed:
        console.print("\n  [dim]criteria not reached — a gate wire failed first[/dim]")

    judged = [r.judged for r in cell.results if r.judged]
    console.print(
        f"\n  [dim]judge {cell.judge_model} ({judge_source(judged)}), "
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


def render_coverage(coverage: Coverage, console: Console) -> None:
    """The coverage denominators, each table on its own and never blended into one number.

    Printed after `render` rather than inside it. Coverage is not a scored figure and must
    not sit beside the two that are: measurement.md keeps the tiers apart, and a percentage
    printed under "gate" and "credit" would be read as a third verdict.

    A table that is `None` is not printed at all — this invocation did not ask that
    question. A table that is present but blind prints its blindness, because "we did not
    check" and "we checked and found nothing" are the two facts a reader must never have to
    guess between.
    """
    if coverage.unreadable:
        # Above the tables it thinned: every figure below was taken over the cards that
        # could be read, and this says which ones could not.
        console.print("\n  [dim]not read as cards[/dim]")
        for reason in coverage.unreadable:
            # A parser message quoting a user's file. Text, never markup.
            console.print(Text(f"    {reason}", style="yellow"))
    if coverage.policy is not None:
        _policy_coverage(coverage.policy, console)
    if coverage.vocabulary is not None:
        _vocabulary_coverage(coverage.vocabulary, console)
    if coverage.path is not None:
        _path_coverage(coverage.path, console)


def _policy_coverage(documents: list[PolicyDocument], console: Console) -> None:
    """The clause inventory. Deliberately not a clauses x cards matrix — see coverage.py."""
    console.print("\n  [dim]policy coverage[/dim]")
    if not documents:
        console.print(_figure("", "no card names a policy document, so there is nothing to count"))
        return
    for document in documents:
        # The path, the headings and the clause text all come out of a user-supplied file,
        # so every one of them reaches the console as Text rather than as markup.
        line = Text("    ")
        line.append(document.path)
        if document.blind:
            line.append(f"   {document.blind}", style="yellow")
            console.print(line)
            continue
        line.append(f"   {_plural(len(document.clauses), 'clause')}", style="dim")
        console.print(line)
        for section in document.sections:
            entry = Text("      ")
            entry.append(f"{section.section or '(preamble)':<24}", style="dim")
            entry.append(_plural(section.clauses, "clause"), style="dim")
            console.print(entry)
        named = Text("      ")
        if document.cards:
            named.append(f"named by {_plural(len(document.cards), 'card')}", style="dim")
        else:
            # The one deterministic uncovered signal this table can give today.
            named.append("named by no card — nothing in this deck exercises it", style="yellow")
        console.print(named)
    console.print(
        Text(
            "    clause-to-card attribution is not reported: a card's `context` names a "
            "document, not clauses, so no per-clause predicate exists yet",
            style="dim",
        )
    )


def _vocabulary_coverage(table: VocabularyTable, console: Console) -> None:
    """One row per declared tool: which cards wire it, and whether any run ran it."""
    console.print("\n  [dim]vocabulary coverage[/dim]")
    if table.blind:
        console.print(_figure("", table.blind))
        return
    if not table.rows:
        console.print(_figure("", "the declared vocabulary names no tools"))
        return
    width = max(len(row.tool) for row in table.rows) + 2
    for row in table.rows:
        line = Text("    ")
        # An uncovered row is the one a reader is here for, so it is the one that is not dim.
        uncovered = not row.wired_by and not row.exercised
        line.append(f"{row.tool:<{width}}", style="yellow" if uncovered else None)
        wired = f"wired by {len(row.wired_by)}" if row.wired_by else "no wire"
        exercised = "exercised" if row.exercised else "not exercised"
        line.append(f"{wired}, {exercised}", style="dim")
        console.print(line)
    uncovered = [row.tool for row in table.rows if not row.wired_by and not row.exercised]
    console.print(
        _figure(
            "",
            f"{len(uncovered)} of {_plural(len(table.rows), 'declared tool')} have neither a "
            f"wire nor an exercising run",
        )
    )
    if table.traces_blind:
        console.print(_figure("", table.traces_blind))


def _path_coverage(path: PathCoverage, console: Console) -> None:
    """Declared graph edges against the ones runs actually traversed.

    A figure is printed even at 100%, so "fully covered" cannot be mistaken for "not
    measured", and the depth is always named — at every depth, including none.
    """
    console.print("\n  [dim]path coverage[/dim]")
    if path.reference:
        # A user-supplied reference, so Text rather than markup.
        console.print(
            Text(
                f"    {path.reference} via {path.source} — {path.depth.value} depth",
                style="dim",
            )
        )
    if path.blind:
        console.print(_figure("", path.blind))
        return
    console.print(
        _figure(
            "",
            f"{path.covered} of {_plural(path.total, 'declared edge')} hit "
            f"over {_plural(path.runs, 'run')}",
        )
    )
    for source, target in path.missed:
        # A node name is agent-authored, so it reaches the console as Text. `[/tmp]` in one
        # would raise MarkupError and discard a report already paid for.
        line = Text("      ")
        line.append("never hit  ", style="yellow")
        line.append(f"{source} -> {target}")
        console.print(line)
    console.print(_figure("", UNDERSTATED))
    # "No run has ever hit it" is a suite claim, and neither path here makes one: `run`
    # sees one card's runs, `coverage` sees whatever traces were handed to it.
    console.print(_figure("", "over the traces seen here — a suite-wide 'no run ever' needs #70"))


def _plural(n: int, noun: str) -> str:
    return f"{n} {noun}{'' if n == 1 else 's'}"


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


def headline(text: str) -> str:
    """One line of the criterion, so a multi-paragraph prose block stays readable.

    Public: a JUnit failure names the criterion the same way the console does, and the SME
    has to recognise their own sentence in either one.
    """
    first = text.strip().splitlines()[0] if text.strip() else "(empty)"
    return first if len(first) <= 72 else f"{first[:71]}…"


def judge_source(judged: list[JudgeResult]) -> str:
    """Where the cell's judge verdicts came from. Public: the JUnit report says it too."""
    if not judged:
        return "not called"
    if all(j.replayed for j in judged):
        return "replayed"
    return "live" if not any(j.replayed for j in judged) else "mixed replay and live"


def depth_line(introspection: Introspection | None) -> Text:
    """What the agent definition gave up, stated at every depth including none at all.

    Public, and printed by every consumer of an introspection — the lint header and the
    coverage report both call this, so the two cannot describe the same reading in
    different words. An obligation that ran against half a graph and one that ran against
    all of it must not read the same, and a reader must never have to infer which they got.

    Dim: it is the context the findings were produced under, not a finding.
    """
    if introspection is None:
        line = Text("  agent definition  ", style="dim")
        line.append("not introspected — pass --agent-def <module:attribute>", style="dim")
        return line
    description = introspection.description
    line = Text("  agent definition  ", style="dim")
    # The reference and the source are user-supplied strings, so they are appended to a
    # Text rather than interpolated into markup, on `_fail`'s rule.
    line.append(f"{introspection.reference or '(unnamed)'} via {introspection.source}", style="dim")
    line.append(f" — {introspection.depth.value} depth: ", style="dim")
    line.append(
        ", ".join(
            _plural(len(found), noun)
            for found, noun in (
                (description.tools, "tool"),
                (description.edges, "edge"),
                (description.cycles, "cycle"),
                (description.hitl_points, "HITL point"),
            )
        ),
        style="dim",
    )
    if introspection.note:
        line.append(f" ({introspection.note})", style="dim")
    return line


#: How a column's outcome reads in the grid. A skipped column is never a FAIL: it did not
#: answer, and rendering "the budget ran out" as a card regression is the one confusion
#: the whole status enum exists to prevent.
_STATUS = {
    Status.PASSED: ("PASS", "green"),
    Status.FAILED: ("FAIL", "red"),
    Status.SKIPPED_BUDGET: ("skipped", "yellow"),
    Status.STOPPED_BUDGET: ("stopped", "yellow"),
    Status.ERRORED: ("error", "red"),
}


def render_suite(suite: Suite, console: Console, *, rates: Rates | None = None) -> None:
    """A deck, one line per card, with the full report only under the cards that failed.

    `render_matrix`'s layout rule, one axis over: a green deck is a table you skim, and a
    red one carries the detail that makes it actionable. A second, thinner failure layout
    would be a second thing to keep in step with the first, so failing cards get `render`
    itself.

    A card that could not start prints above the table rather than inside it. It has no
    gate rate to sit in a column, and printing a dash where a verdict goes reads as a
    result.
    """
    console.print()
    for error in suite.errors:
        # A path and an error message both come out of the user's own files, so they are
        # Text: a bracket in either would be read as a style tag and eat the line.
        console.print("[red]error[/red]", Text(f"{error.card_path}  {error.message}"))
    if suite.errors:
        console.print()
    console.print(_deck(suite))
    for cell in suite.cells:
        if not cell.passed:
            console.print()
            console.print(Text(cell.card_path, style="bold"))
            render(cell, console, rates=rates)
    console.print()
    passes = sum(1 for cell in suite.cells if cell.passed)
    tally = f"{len(suite.cells)} card{'' if len(suite.cells) == 1 else 's'}, {passes} passed"
    if suite.errors:
        tally += f", {len(suite.errors)} could not run"
    console.print(f"  [dim]{tally}[/dim]")
    console.print()


def _deck(suite: Suite) -> Table:
    table = Table(show_edge=False, pad_edge=False, box=None, padding=(0, 2))
    table.add_column("")
    # Folded rather than ellipsised: the path is what identifies the row, and a narrow
    # terminal truncating it leaves a verdict attached to no card the reader can name.
    table.add_column("", style="dim", overflow="fold")
    table.add_column("", style="dim")
    for cell in suite.cells:
        credit = (
            f"credit {cell.credit_mean:g}/{cell.credit_total}"
            if cell.credit_mean is not None
            else "credit n/a"
        )
        table.add_row(
            Text("PASS" if cell.passed else "FAIL", style="green" if cell.passed else "red"),
            Text(cell.card_path),
            f"gate {cell.passes}/{cell.runs}  {credit}",
        )
    return table


def render_matrix(result: MatrixResult, console: Console, *, rates: Rates | None = None) -> None:
    """The provider x prompt grid, then what it spent, then why any column failed.

    The footer sits above the per-column detail, not below it: the spend and the overshoot
    are what a budget cap is for, and five pages of failing-run detail would bury them.

    Full `render` per failing column rather than a condensed one. A failing column is
    exactly the thing the reader has to act on, and a second, thinner failure layout would
    be a second thing to keep in step with the first.
    """
    console.print()
    console.print(_grid(result))
    _notes(result, console)
    console.print()
    for line in _footer(result):
        console.print(line)
    for shown in result.columns:
        if shown.status is Status.FAILED and shown.cell is not None:
            console.print()
            console.print(Text(f"column {shown.column.name}", style="bold"))
            render(shown.cell, console, rates=rates)


def _grid(result: MatrixResult) -> Table:
    """Rows are prompt variants, columns are providers — the shape the matrix was declared
    in, so a reader can find the entry they wrote. A degenerate one-axis matrix keeps the
    same table with one row or one column, rather than a second layout."""
    providers = list(dict.fromkeys(one.column.provider for one in result.columns))
    prompts = list(dict.fromkeys(one.column.prompt for one in result.columns))
    found = {(one.column.provider, one.column.prompt): one for one in result.columns}
    table = Table(show_edge=False, pad_edge=False, box=None, padding=(0, 2))
    table.add_column("", style="dim")
    for provider in providers:
        # A provider name comes out of the user's own file, so it is Text, not markup.
        table.add_column(Text(provider or "—", style="bold"))
    for prompt in prompts:
        table.add_row(
            Text(prompt or "—"),
            *(_cell_summary(found.get((provider, prompt))) for provider in providers),
        )
    return table


def _cell_summary(one: ColumnResult | None) -> Text:
    if one is None:
        return Text("—", style="dim")
    word, style = _STATUS[one.status]
    line = Text()
    line.append(f"{word:<8}", style=style)
    if one.cell is None:
        return line
    line.append(f"gate {one.cell.passes}/{one.cell.runs}", style="dim")
    credit = (
        f"  credit {one.cell.credit_mean:g}/{one.cell.credit_total}"
        if one.cell.credit_mean is not None
        else "  credit n/a"
    )
    line.append(credit, style="dim")
    return line


def _notes(result: MatrixResult, console: Console) -> None:
    """Why any column did not simply pass or fail. Never silently fewer columns."""
    for one in result.columns:
        if one.status in (Status.PASSED, Status.FAILED) or not one.detail:
            continue
        console.print(Text(f"  {one.column.name}: {one.detail}", style="dim"))


def _footer(result: MatrixResult) -> list[Text]:
    """The spend, the cap, and every way this matrix was less than what was asked for."""
    lines = [_figure("spent", f"{result.spent_label}, {_runs(len(result.columns), 'column')}")]
    if result.cap_usd is not None:
        lines.append(_figure("cap", f"${result.cap_usd:g}, hard"))
    if result.unmetered:
        named = ", ".join(f"{model} x{count}" for model, count in sorted(result.unmetered.items()))
        # Stated, not folded in as zero: an uncounted call is spend this figure is short by.
        lines.append(_figure("", f"{named} reported no usage and are not in that figure"))
    # Counted per status, and never rolled into one "incomplete" number: a column nobody
    # could afford and a column that raised are different facts with different fixes.
    counts = {
        status: sum(1 for one in result.columns if one.status is status)
        for status in (Status.SKIPPED_BUDGET, Status.STOPPED_BUDGET, Status.ERRORED)
    }
    if listed := ", ".join(
        f"{count} {_STATUS[status][0]}" for status, count in counts.items() if count
    ):
        lines.append(_figure("", listed))
    if result.stopped_early or result.overspent:
        # The asymmetry, said out loud on the run it happened to. The cap prevents
        # specdeck's own next call; it cannot prevent an agent call already in flight.
        lines.append(
            _figure(
                "",
                "the cap stops new work, it cannot recall work in flight — an agent "
                "conversation already under way can exceed the remaining budget before "
                "specdeck sees its token counts",
            )
        )
    return lines
