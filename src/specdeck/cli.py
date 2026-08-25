"""The `specdeck` command."""

from __future__ import annotations

import asyncio
import importlib
import inspect
from datetime import date
from itertools import groupby
from pathlib import Path

import typer
from pydantic import BaseModel, ConfigDict
from rich.console import Console
from rich.text import Text

from specdeck import __version__
from specdeck.agent import AgentAdapter
from specdeck.baseline import BASELINE_NAME, DEFAULT_CELL, Baseline, BaselineError, observed
from specdeck.budget import Budget, BudgetError
from specdeck.builtin import DEFAULT_LATENCY_BUDGET_S, BuiltinConfig
from specdeck.card import Card, CardError, parse
from specdeck.cell import (
    DEFAULT_CONCURRENCY,
    DEFAULT_K,
    DEFAULT_N,
    Cell,
    CellError,
    run_cell,
    run_cell_async,
)
from specdeck.coverage import Coverage, CoverageError, collect, path_coverage
from specdeck.introspect import Introspection, introspect
from specdeck.judge import DEFAULT_JUDGE_MODEL, JudgeError, criteria_of, rubric_text
from specdeck.junit import to_xml
from specdeck.lint import Result, Severity, Vocabulary, cards_under, lint_paths
from specdeck.lockfile import LOCKFILE_NAME, RELOCK_HINT, Lockfile, StaleLock, lock_key
from specdeck.loop import DEFAULT_MAX_TURNS, LoopError, run_agent
from specdeck.matrix import Column, MatrixError, cell_key, columns, load_matrix
from specdeck.matrix_run import DEFAULT_MATRIX_CONCURRENCY, MatrixResult, Status, run_matrix
from specdeck.provider import ProviderError
from specdeck.rates import RATES_FILE, RateError, Rates, load_rates
from specdeck.report import depth_line, render, render_coverage, render_matrix
from specdeck.simulator import SimulatorError
from specdeck.trace import SEMCONV, Trace
from specdeck.traceio import TraceError, load_trace
from specdeck.wires import WireError, compile_wires, wires_text

app = typer.Typer(
    name="specdeck",
    help="Card-based eval runner for LLM systems.",
    no_args_is_help=True,
    add_completion=False,
)

#: The exit-code registry. Nothing here reads it — it is the written record the runner is
#: held to, so a later command extends it instead of colliding with it. A caller routes on
#: the code alone, so a code means one thing forever and a genuinely new state takes a new
#: number. A run that could not start and a run that answered are different facts, hence 2
#: and 3; a matrix that stopped part-way answered neither, hence 4 — reserved by #17/#18
#: and issued here, because routing "the budget ran out" to CI as an eval regression is the
#: same confusion 3 exists to prevent.
EXIT_CODES = {
    0: "the cell passed",
    1: "the cell failed its gate",
    2: "the run could not start — a user error, one of USER_ERRORS below",
    3: "specdeck itself broke",
    4: "the matrix did not complete: budget",
}

#: The matrix's budget abort. Its own code because the answer is unknown: the columns that
#: did not run neither passed nor failed, and 1 would claim they regressed.
BUDGET_EXIT = 4

USER_ERRORS = (
    BaselineError,
    BudgetError,
    CardError,
    CellError,
    CoverageError,
    JudgeError,
    LoopError,
    MatrixError,
    ProviderError,
    RateError,
    SimulatorError,
    StaleLock,
    TraceError,
    WireError,
)


def _version(value: bool) -> None:
    if value:
        typer.echo(__version__)
        raise typer.Exit


@app.callback()
def main(
    version: bool = typer.Option(
        False, "--version", callback=_version, is_eager=True, help="Print the version and exit."
    ),
) -> None:
    """Card-based eval runner for LLM systems."""


@app.command()
def run(
    card_path: Path = typer.Argument(..., help="The card to run."),  # noqa: B008
    trace: list[Path] = typer.Option(  # noqa: B008
        None, "--trace", help="A recorded event log. Repeat once per run of the cell."
    ),
    agent: str | None = typer.Option(
        None,
        "--agent",
        help="Run the agent instead of reading traces, as `module:attribute`.",
    ),
    runs: int | None = typer.Option(
        None,
        "--runs",
        help=f"Runs in the cell (default: one per --trace, or {DEFAULT_N} with --agent).",
    ),
    threshold: int | None = typer.Option(
        None,
        "--pass-threshold",
        help=f"Runs that must pass for the cell to pass (default: {DEFAULT_K}, or all runs "
        "when the cell is smaller than that).",
    ),
    cassettes: Path | None = typer.Option(  # noqa: B008
        None,
        "--cassettes",
        help="Where recorded judge calls live (default: cassettes/ beside the card).",
    ),
    lock_path: Path | None = typer.Option(  # noqa: B008
        None,
        "--lock",
        help=f"Lockfile to verify against (default: {LOCKFILE_NAME} beside the card).",
    ),
    relock: bool = typer.Option(False, "--relock", help="Record the current state and continue."),
    live: bool = typer.Option(False, "--live", help="Call the judge for real and record it."),
    concurrency: int = typer.Option(
        DEFAULT_CONCURRENCY, "--concurrency", help="Runs of the cell in flight at once."
    ),
    judge_model: str | None = typer.Option(
        None,
        "--judge-model",
        help=f"Judge to pin, with --relock (default: {DEFAULT_JUDGE_MODEL}).",
    ),
    simulator_model: str | None = typer.Option(
        None, "--simulator-model", help="Simulator to pin, with --relock and --agent."
    ),
    max_turns: int = typer.Option(
        DEFAULT_MAX_TURNS, "--max-turns", help="Turn cap per conversation, with --agent."
    ),
    vocabulary_path: Path | None = typer.Option(  # noqa: B008
        None, "--vocabulary", help="Declared tools and markers, needed by --agent."
    ),
    rates_path: Path | None = typer.Option(  # noqa: B008
        None, "--rates", help=f"A {RATES_FILE} to merge over the built-in table."
    ),
    latency_budget: float = typer.Option(
        DEFAULT_LATENCY_BUDGET_S,
        "--latency-budget",
        help="Seconds the built-in latency wire allows, for a card that authors none.",
    ),
    baseline_path: Path | None = typer.Option(  # noqa: B008
        None,
        "--baseline",
        help=f"Recorded token costs (default: {BASELINE_NAME} beside the card).",
    ),
    update_baseline: bool = typer.Option(
        False, "--update-baseline", help="Record this run's token cost and continue."
    ),
    junit_xml: Path | None = typer.Option(  # noqa: B008
        None, "--junit-xml", help="Write a JUnit XML report here, for CI to render."
    ),
    matrix_path: Path | None = typer.Option(  # noqa: B008
        None, "--matrix", help="A matrix of providers x prompt variants, with --agent."
    ),
    budget_usd: float | None = typer.Option(
        None, "--budget-usd", help="Hard spend cap for the matrix. Overrides [budget] usd."
    ),
    matrix_concurrency: int = typer.Option(
        DEFAULT_MATRIX_CONCURRENCY,
        "--matrix-concurrency",
        help="Matrix columns in flight at once. Forced to 1 under --live.",
    ),
) -> None:
    """Evaluate one card — against recorded traces, or by running the agent."""
    console = Console()
    try:
        if matrix_path and trace:
            # A recorded trace was produced by one provider running one prompt. A column
            # over it could vary nothing, so the grid would print the same run N times.
            raise CardError("--matrix runs the agent, so it cannot be combined with --trace")
        if matrix_path and not agent:
            raise CardError("--matrix needs --agent: a column is a run of the agent")
        if budget_usd is not None and not matrix_path:
            # A cap with nothing to cap is dead surface that reads as protection.
            raise CardError("--budget-usd applies to --matrix, which was not given")
        if bool(trace) == bool(agent):
            raise CardError("pass exactly one of --trace and --agent")
        if latency_budget <= 0:
            # Checked here rather than left to pydantic: a `ValidationError` is not a
            # `USER_ERROR`, so a number the user typed would exit 3, "specdeck itself
            # broke". Checked on the invocation rather than where `BuiltinConfig` is
            # built, because the matrix builds one per column, inside a column's own
            # try/except and after that column's agent has already run — so a typo'd flag
            # would be scored as specdeck breaking, once per column, with the money spent.
            raise CardError(
                f"--latency-budget takes a positive number of seconds, got {latency_budget:g}"
            )
        card = parse(card_path)
        rates = _rates(rates_path, card_path, console)
        recordings = [load_trace(path) for path in trace or []]
        # A cell of five is the locked statistic, not a default that fits every invocation:
        # one recorded trace with --runs unset would fail on arithmetic before anything ran.
        # The statistic is untouched; what changes is guessing N when the input states it.
        n = runs if runs is not None else (len(recordings) or DEFAULT_N)
        k = threshold if threshold is not None else min(DEFAULT_K, n)
        lock = _lock(
            card_path,
            lock_path,
            card,
            semconv=recordings[0].semconv if recordings else SEMCONV,
            relock=relock,
            judge_model=judge_model,
            simulator_model=simulator_model,
        )
        for one in recordings:
            lock.verify_semconv(one.semconv)
        cassette_dir = cassettes or card_path.parent / "cassettes"
        if matrix_path is not None:
            if junit_xml is not None:
                # `junit.to_xml` takes one cell and writes one `<testsuites>` document.
                # Widening it to a matrix is a real mapping decision (#18 owns the file),
                # not a parameter, so it is refused rather than half-answered.
                raise CardError(
                    "--junit-xml does not take a matrix yet — see "
                    "https://github.com/jacquardlabs/specdeck/issues/85"
                )
            matrix, pending, unproven = _matrix(
                _Invocation(
                    card=card,
                    card_path=card_path,
                    reference=agent or "",
                    cassettes=cassette_dir,
                    lock=lock,
                    n=n,
                    k=k,
                    markers=_markers(vocabulary_path),
                    max_turns=max_turns,
                    live=live,
                    concurrency=concurrency,
                    latency_budget=latency_budget,
                    update=update_baseline,
                ),
                matrix_path,
                baseline_path,
                rates=rates,
                budget_usd=budget_usd,
                matrix_concurrency=matrix_concurrency,
                console=console,
            )
        else:
            matrix, pending, unproven = None, None, []
            # Resolved once, here rather than inside `_drive`, because the same object is
            # both what runs and what the path denominator is read off. Introspecting a
            # second resolution would build a second adapter of the user's just to look
            # at it.
            adapter = _adapter(agent) if agent else None
            introspection = introspect(adapter, reference=agent or "") if adapter else None
            traces = (
                _drive(
                    card,
                    adapter,
                    cassettes=cassette_dir,
                    lock=lock,
                    runs=n,
                    markers=_markers(vocabulary_path),
                    max_turns=max_turns,
                    live=live,
                )
                if adapter is not None
                else recordings
            )
            builtin, pending = _builtin(
                card_path,
                baseline_path,
                traces,
                latency_budget=latency_budget,
                update=update_baseline,
            )
            cell = run_cell(
                card,
                traces,
                # Every other card input resolves against the card; the cassette directory
                # has to as well, or where you stand changes which recordings are found.
                cassettes=cassette_dir,
                n=n,
                k=k,
                judge_model=lock.judge_model,
                # Named in the report only when one actually spoke: a run from recorded
                # traces had no simulated user, and the pin describes nothing that happened.
                simulator_model=lock.simulator_model if agent else "",
                live=live,
                concurrency=concurrency,
                builtin=builtin,
            )
            # Serialised inside the funnel though it is written outside it: a bug in the
            # serializer is specdeck breaking, and escaping to typer's default handler
            # would surface it as exit 1 — a card that honestly failed (#56).
            report = to_xml(cell) if junit_xml else None
            # Computed inside the funnel though it is printed outside it, on `to_xml`'s
            # rule: past the funnel an exception escapes to typer's default handler and
            # exits 1, "the cell failed its gate" — which is a coverage computation
            # reaching the exit code, the one thing coverage may never do.
            covered = Coverage(path=path_coverage(introspection, traces))
    except USER_ERRORS as error:
        _fail(console, error)
        raise typer.Exit(2) from None
    except Exception as error:
        # Exit 3, not 1. A `TOMLDecodeError` from a conflict-marked lockfile used to leave
        # Python exiting 1 — the same code as a card that honestly failed — so a caller
        # reading the exit code routed a broken lockfile to the SME as an eval regression.
        console.print(f"[red]internal error[/red] {type(error).__name__}: {error}")
        raise typer.Exit(3) from None

    if matrix is not None:
        render_matrix(matrix, console, rates=rates)
        _write_baseline(pending, console)
        if unproven:
            # The single-cell path's note, one grid wider. A column records its cost before
            # its own gate is judged — it has to, because the wire that cost produces is
            # evaluated in that same cell — so a column that then failed, or raised, has
            # already written the cost of behaviour nobody wants as the new normal. Whether
            # that should be refused outright is still the open product question wave 3
            # left; a silent bad baseline is the outcome neither answer wants.
            console.print(
                "[yellow]note[/yellow]",
                Text(
                    f"the baseline for {', '.join(unproven)} was recorded from a column "
                    "that did not pass — re-record it once the column passes, or the cost "
                    "of a broken run becomes the normal"
                ),
            )
        # Raised out here, never inside the funnel: `typer.Exit` subclasses `RuntimeError`,
        # so an exit raised in the try above would be caught by `except Exception` and
        # reported as exit 3, "specdeck itself broke".
        raise typer.Exit(_matrix_exit(matrix))

    render(cell, console, rates=rates)
    # Printed after the cell report, never inside it. Coverage is not a scored figure and
    # must not sit beside the two that are. Only the path table: policy and vocabulary are
    # suite-level denominators, and one card answering "1 of 14 tools wired" would read as
    # 7% coverage of a deck it never looked at. `specdeck coverage` asks all three.
    render_coverage(covered, console)
    # The JUnit report first: an unwritable --baseline path must not also deny CI the file
    # it asked for, and the two failures are independent.
    _write_junit(junit_xml, report, console)
    _write_baseline(pending, console)
    if pending is not None and not cell.passed:
        # A cell that fails its gate is still a cell that ran, so its cost is still
        # recorded. Said out loud rather than refused: whether a failing run may set a
        # baseline is a product question nobody has answered, and a silent bad baseline is
        # the outcome neither answer wants.
        console.print(
            "[yellow]note[/yellow]",
            Text(
                "the baseline was recorded from a run whose gate failed — re-record it "
                "once the card passes, or the cost of a broken run becomes the normal"
            ),
        )
    raise typer.Exit(0 if cell.passed else 1)


class _Invocation(BaseModel):
    """Everything a column needs that the command line settled once, for every column.

    A model rather than fourteen positional arguments: the matrix path multiplies the
    parameter list by the number of helpers it passes through, and a mis-ordered pair of
    same-typed arguments is the kind of bug no test names.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    card: Card
    card_path: Path
    reference: str
    cassettes: Path
    lock: Lockfile
    n: int
    k: int
    markers: list[str]
    max_turns: int
    live: bool
    concurrency: int
    latency_budget: float
    update: bool


def _matrix(
    invocation: _Invocation,
    matrix_path: Path,
    baseline_path: Path | None,
    *,
    rates: Rates,
    budget_usd: float | None,
    matrix_concurrency: int,
    console: Console,
) -> tuple[MatrixResult, tuple[Path, Baseline] | None, list[str]]:
    """Run every column under one budget; hand back what to write and what to warn about.

    The lockfile is verified once, by `run`, before this is reached — never per column.
    `_lock` writes the file under `--relock`, and N columns relocking concurrently would
    be N writers on one `spec.lock.toml`. The card is the same card in every column;
    only what the adapter is handed differs.
    """
    grid = load_matrix(matrix_path)
    declared = columns(grid)
    cap = budget_usd if budget_usd is not None else grid.budget_usd
    budget = Budget(cap_usd=cap, rates=rates)
    # Before anything runs, and over every column: a matrix with one unpriceable column
    # is refused whole. See `Budget.preflight` for why that beats skipping the column.
    # specdeck's own two models go in beside them — they are the spend the cap can really
    # prevent, and one the table cannot price is charged $0.00 for the whole run.
    budget.preflight(
        declared,
        judge_model=invocation.lock.judge_model,
        simulator_model=invocation.lock.simulator_model,
    )

    if invocation.live and matrix_concurrency > 1:
        # Not a performance choice — see `matrix_run`'s docstring. Turn 1 of every column
        # builds the identical simulator prompt, so two live columns race one cassette.
        console.print(
            "[yellow]note[/yellow]",
            Text(
                "--live serialises the columns: the simulator's first turn is the same "
                "prompt in every column, so two of them would race one cassette"
            ),
        )
        matrix_concurrency = 1

    # Resolved once before anything runs, purely to fail fast: each column builds its own
    # adapter for isolation, so a bad --agent would otherwise arrive as one identical
    # CardError per column in the grid rather than one refusal before the grid exists.
    _adapter(invocation.reference)

    baseline_file = baseline_path or invocation.card_path.parent / BASELINE_NAME
    key = lock_key(invocation.card_path, baseline_file)
    recorded = Baseline.load(baseline_file)
    _warn_default_baseline(recorded, key, declared, console, update=invocation.update)
    fresh: dict[str, int] = {}

    async def one_column(column: Column) -> Cell:
        traces = await _drive_async(
            invocation.card,
            _adapter(invocation.reference),
            cassettes=invocation.cassettes,
            lock=invocation.lock,
            runs=invocation.n,
            markers=invocation.markers,
            max_turns=invocation.max_turns,
            live=invocation.live,
            config=column.config,
            budget=budget,
        )
        baseline = recorded.get(key, cell_key(column))
        if invocation.update:
            # Recorded then verified against, the way `--relock` verifies against the lock
            # it just wrote. `observed` refuses before returning anything unrecordable.
            baseline = observed(traces)
            fresh[cell_key(column)] = baseline
        return await run_cell_async(
            invocation.card,
            traces,
            cassettes=invocation.cassettes,
            n=invocation.n,
            k=invocation.k,
            judge_model=invocation.lock.judge_model,
            simulator_model=invocation.lock.simulator_model,
            live=invocation.live,
            concurrency=invocation.concurrency,
            builtin=BuiltinConfig(
                latency_budget_s=invocation.latency_budget, token_baseline=baseline
            ),
            budget=budget,
        )

    result = asyncio.run(
        run_matrix(
            declared,
            one_column,
            budget=budget,
            concurrency=matrix_concurrency,
            user_errors=USER_ERRORS,
        )
    )
    # One write for the whole matrix, after every column has finished. N columns each
    # saving their own copy would be N writers on one file, and the last one would win.
    pending = None
    if fresh:
        for cell, tokens in sorted(fresh.items()):
            recorded = recorded.record(key, tokens, cell=cell)
        pending = (baseline_file, recorded)
    # A column records its cost before its own gate is judged, so a column that then
    # failed — or raised on the way — recorded one anyway. Not `not passed` on the cell:
    # an ERRORED column never produced one and is exactly as unproven.
    unproven = [
        one.column.name
        for one in result.columns
        if one.status is not Status.PASSED and cell_key(one.column) in fresh
    ]
    return result, pending, unproven


def _warn_default_baseline(
    recorded: Baseline, key: str, declared: list[Column], console: Console, *, update: bool
) -> None:
    """Say so when a column of this matrix runs with no baseline of its own.

    A baseline recorded by a single-cell run sits in the `"default"` slot, and no matrix
    column keys there — so every column silently gets no regression wire, and a card that
    fails single-cell passes under `--matrix`. Silence there is the worst outcome.

    A half-recorded matrix reaches the same outcome one column at a time, and it is
    reachable by design: a budget stop mid-matrix is an expected way for
    `--update-baseline` to record some columns and not others, and the columns it missed
    then run wireless beside the ones it did. Named rather than counted, because which
    column is missing is the whole of what the user has to act on.

    Silent under `--update-baseline`: every column that runs records and is judged against
    its own fresh number, so there is nothing missing to warn about. Silent too when the
    card has no baseline at all — a first install must not go loud over a number nobody
    has written down yet, which is the single-cell path's rule.
    """
    missing = [column.name for column in declared if recorded.get(key, cell_key(column)) is None]
    if update or not missing:
        return
    if len(missing) < len(declared):
        console.print(
            "[yellow]note[/yellow]",
            Text(
                f"no baseline is recorded for {', '.join(missing)}, so those columns get "
                "no token-regression wire while the rest of the matrix does — re-record "
                "with --matrix --update-baseline"
            ),
        )
        return
    if recorded.get(key, DEFAULT_CELL) is None:
        return
    console.print(
        "[yellow]note[/yellow]",
        Text(
            f"the recorded baseline is the single-cell '{DEFAULT_CELL}' one and no column "
            "has its own, so no column gets a token-regression wire — re-record with "
            "--matrix --update-baseline"
        ),
    )


def _matrix_exit(result: MatrixResult) -> int:
    """One code for the whole grid, worst first.

    A budget stop outranks a gate failure, because a matrix missing a column has not
    answered the question that was asked, and a CI reading 1 would call that an eval
    regression. A column that raised outranks a gate failure for the same reason, and
    keeps the 2-vs-3 split: a user error is theirs to fix, anything else is ours.
    """
    if result.stopped_early:
        return BUDGET_EXIT
    if errored := [one for one in result.columns if one.status is Status.ERRORED]:
        return 2 if all(one.user_error for one in errored) else 3
    return 0 if result.passed else 1


def _builtin(
    card_path: Path,
    baseline_path: Path | None,
    traces: list[Trace],
    *,
    latency_budget: float,
    update: bool,
) -> tuple[BuiltinConfig, tuple[Path, Baseline] | None]:
    """What the free wires are configured with, and the baseline still owed to disk.

    The baseline file sits beside the card like the lockfile and the cassettes, so where
    the runner was invoked from cannot change which costs a card is compared against.

    A repo with no baseline recorded runs green and simply gets no regression wire. The
    free gates exist to catch a card getting worse; there is nothing yet to be worse than,
    and a first install must not go red for a number nobody has written down.

    Nothing is written here. `--update-baseline` folds the fresh median into the config, so
    this run is judged against what it just recorded — the way `--relock` verifies against
    the lock it just wrote — but the file itself is handed back for the caller to write
    once the cell has actually run. Written here, a run refused further down (a trace count
    that disagrees with `--runs`, a missing cassette) would still have overwritten a
    committed baseline with a number from a cell that never ran.
    """
    path = baseline_path or card_path.parent / BASELINE_NAME
    key = lock_key(card_path, path)
    recorded = Baseline.load(path)
    pending = None
    if update:
        # `observed` refuses before anything is recorded, so a run that cannot honestly be
        # recorded leaves no file behind claiming it was.
        recorded = recorded.record(key, observed(traces))
        pending = (path, recorded)
    return (
        BuiltinConfig(latency_budget_s=latency_budget, token_baseline=recorded.get(key)),
        pending,
    )


def _write_baseline(pending: tuple[Path, Baseline] | None, console: Console) -> None:
    """The recorded cost, once the cell it describes has actually run.

    Deliberately outside the funnel above and after the cell, on `_write_junit`'s rule: a
    path the user named is part of the invocation, so a broken one exits 2 loudly rather
    than surfacing as exit 3, "specdeck itself broke". The cell's own report has already
    printed by the time this runs, so a typo in `--baseline` costs the reader nothing but
    the file.
    """
    if pending is None:
        return
    path, baseline = pending
    try:
        baseline.save(path)
    except OSError as error:
        console.print("[red]error[/red]", Text(f"cannot write the baseline to {path}: {error}"))
        raise typer.Exit(2) from None


def _write_junit(path: Path | None, report: str | None, console: Console) -> None:
    """The CI report, when one was asked for.

    Written on pass and on fail alike — a cell that failed is exactly what CI needs to
    render. Reported here rather than through the funnel above, because the cell has
    already run and its report is already on screen: this is not "the run could not
    start". It exits 2 all the same, on the rule `--rates` already follows — a file named
    on the invocation is part of it, and CI silently receiving no report is worse than a
    loud refusal.
    """
    if path is None or report is None:
        return
    try:
        # UTF-8 named, not left to the locale: the document declares `encoding='utf-8'` in
        # band and every report carries an em dash, so a non-UTF-8 default either raises
        # `UnicodeEncodeError` — exit 1, a card that honestly failed (#56) — or writes
        # bytes that contradict the declaration and reach CI silently unparseable.
        path.write_text(report, encoding="utf-8")
    except OSError as error:
        console.print("[red]error[/red]", Text(f"cannot write the JUnit report to {path}: {error}"))
        raise typer.Exit(2) from None


def _rates(rates_path: Path | None, card_path: Path, console: Console) -> Rates:
    """The table that prices this run, resolved against the card, not the shell.

    Which table priced a run must not depend on where the runner was invoked from — the
    same rule the lockfile and the cassettes follow.

    A table named on --rates is part of the invocation, so a broken one stops the run. One
    merely *found* beside the card is not: it prices a dim secondary figure, and letting it
    abort turns a card that would have passed into exit 2, which README documents as "the
    run could not start". Said out loud and priced from the built-in table instead — that
    table carries its own `verified` date, so nothing here invents a rate.
    """
    try:
        return load_rates(rates_path, beside=card_path.parent)
    except RateError as error:
        if rates_path is not None:
            raise
        console.print(
            "[yellow]note[/yellow]", Text(f"{error} — pricing with the built-in table instead")
        )
        return Rates.builtin()


def _drive(
    card: Card,
    adapter: AgentAdapter,
    *,
    cassettes: Path,
    lock: Lockfile,
    runs: int,
    markers: list[str],
    max_turns: int,
    live: bool,
) -> list[Trace]:
    """Run the agent `runs` times and return the traces it produced.

    Sequential rather than fanned out like the judge: the simulator's cassettes are keyed
    on a growing transcript, so two conversations racing would interleave recordings that
    are meant to be one conversation each.
    """
    return asyncio.run(
        _drive_async(
            card,
            adapter,
            cassettes=cassettes,
            lock=lock,
            runs=runs,
            markers=markers,
            max_turns=max_turns,
            live=live,
        )
    )


async def _drive_async(
    card: Card,
    adapter: AgentAdapter,
    *,
    cassettes: Path,
    lock: Lockfile,
    runs: int,
    markers: list[str],
    max_turns: int,
    live: bool,
    config: dict | None = None,
    budget: Budget | None = None,
) -> list[Trace]:
    """The body of `_drive`, as a coroutine the matrix can await beside its siblings.

    `_drive` owns the event loop for the single-cell path and cannot be reused by the
    matrix for exactly that reason: `asyncio.run` does not nest.

    `config` is what a column varies. `run_agent` has always forwarded it to `adapter.run`;
    the CLI simply never had anything to put in it until a column did.
    """
    if not lock.simulator_model:
        # Naming --relock alone would loop the reader back here: the pin only moves when
        # --simulator-model is passed too (see `_lock`), so a bare relock writes it empty
        # and the next run dies on this same line (#76).
        raise StaleLock(
            "the lockfile pins no simulator model — run with "
            "--relock --simulator-model <model> to pin one"
        )
    traces: list[Trace] = []
    for index in range(runs):
        if budget is not None:
            # Between the runs, not only between the columns. Run 1 has already charged
            # what it spent by the time run 2 would start, so a column that carried on
            # regardless would begin `--runs` fresh conversations after the cap is known
            # blown — and the bound stated everywhere else would be low by that factor.
            budget.check(f"run {index + 1} of {runs} in this column")
        traces.append(
            await run_agent(
                card,
                adapter,
                cassettes=cassettes,
                simulator_model=lock.simulator_model,
                semconv=lock.semconv,
                markers=markers,
                max_turns=max_turns,
                live=live,
                config=config,
                budget=budget,
            )
        )
    return traces


def _resolve(reference: str, *, flag: str) -> object:
    """Resolve `module:attribute` to the object the user meant.

    A class is instantiated and a factory is called, both with no arguments; anything else
    is taken as it stands. The class case is not a nicety — a class satisfies a
    `runtime_checkable` protocol check on attribute presence alone, so passing one through
    uninstantiated fails at the first turn with `run() missing 1 required positional
    argument`, long after the guard said it was fine.

    "A class or a routine" is the discriminator, and it is deliberately not "anything that
    is not already an adapter". `--agent-def` points at objects that satisfy no protocol
    of ours — a compiled LangGraph graph is a plain object with a `get_graph()` — and
    calling one because it failed an `isinstance` check would invoke the user's agent
    just to look at it.
    """
    module_name, _, attribute = reference.partition(":")
    if not module_name or not attribute:
        raise CardError(f"{flag} {reference!r} is not `module:attribute`")
    try:
        module = importlib.import_module(module_name)
    except ImportError as error:
        raise CardError(f"{flag} {reference!r}: {error}") from None
    try:
        found = getattr(module, attribute)
    except AttributeError:
        raise CardError(f"{flag} {reference!r}: {module_name} has no {attribute!r}") from None
    return found() if isinstance(found, type) or inspect.isroutine(found) else found


def _adapter(reference: str) -> AgentAdapter:
    """Resolve `--agent` to something that satisfies the adapter protocol."""
    adapter = _resolve(reference, flag="--agent")
    if not isinstance(adapter, AgentAdapter):
        raise CardError(f"--agent {reference!r} has no async run(messages, tools, config)")
    return adapter


def _introspected(reference: str | None) -> Introspection | None:
    """Read the agent definition `--agent-def` names, or nothing when it was not given.

    None and a `Depth.NONE` introspection are different facts and both are reported: one
    says nobody asked, the other says we looked and could read nothing.

    This is the only path on which lint imports a user's module, and it is opt-in for
    exactly that reason — `specdeck lint` is otherwise pure reading. A module with
    import-time side effects now runs inside pre-commit when this flag is passed.
    """
    if reference is None:
        return None
    return introspect(_resolve(reference, flag="--agent-def"), reference=reference)


def _markers(path: Path | None) -> list[str]:
    vocabulary = _vocabulary(path)
    return sorted(vocabulary.markers) if vocabulary else []


def _lock(
    card_path: Path,
    lock_path: Path | None,
    card: Card,
    *,
    semconv: str,
    relock: bool,
    judge_model: str | None,
    simulator_model: str | None = None,
) -> Lockfile:
    """Verify the run against the lock, or record it. An unpinned judge is not a test."""
    path = lock_path or card_path.parent / LOCKFILE_NAME
    key = lock_key(card_path, path)
    rubric = rubric_text(criteria_of(card))
    wires = wires_text(compile_wires(card))
    if relock:
        base = (
            Lockfile.load(path)
            if path.exists()
            else Lockfile(
                semconv=semconv,
                judge_model=judge_model or DEFAULT_JUDGE_MODEL,
                # Empty until a run actually uses a simulator. Copying the judge model in
                # would ship a real-looking pin nobody chose, and the first real simulator
                # would then not read as drift.
                simulator_model=simulator_model or "",
                cards={},
            )
        )
        lock = base.relock(key, rubric=rubric, wires=wires, simulator=card.context.simulator)
        # --relock is the only path that may move a pin, and it moves every pin the run
        # was given. Silently keeping the old judge is what made --judge-model inert.
        # The pin is rewritten from whichever trace was handed in, so a trace declaring a
        # different semconv silently becomes the new truth for every card. Said out loud
        # rather than blocked: --relock is the operator asking for exactly this, but the
        # drift detection the lockfile exists for should not move without a line in the log.
        if path.exists() and base.semconv != semconv:
            Console().print(
                f"[yellow]note[/yellow] semconv pin moves {base.semconv} -> {semconv}, "
                "taken from the trace supplied"
            )
        lock = lock.model_copy(
            update={"semconv": semconv}
            | ({"judge_model": judge_model} if judge_model else {})
            | ({"simulator_model": simulator_model} if simulator_model else {})
        )
        lock.save(path)
        return lock

    lock = Lockfile.load(path)
    if judge_model and judge_model != lock.judge_model:
        raise StaleLock(
            f"--judge-model {judge_model} disagrees with the pinned "
            f"{lock.judge_model} — {RELOCK_HINT}"
        )
    if simulator_model and simulator_model != lock.simulator_model:
        raise StaleLock(
            f"--simulator-model {simulator_model} disagrees with the pinned "
            f"{lock.simulator_model or '(none)'} — {RELOCK_HINT}"
        )
    lock.verify(key, rubric=rubric, wires=wires, simulator=card.context.simulator)
    return lock


@app.command()
def lint(
    paths: list[Path] = typer.Argument(None, help="Cards, or directories holding them."),  # noqa: B008
    lock_path: Path | None = typer.Option(  # noqa: B008
        None, "--lock", help=f"Verify freshness against this {LOCKFILE_NAME}."
    ),
    vocabulary_path: Path | None = typer.Option(  # noqa: B008
        None,
        "--vocabulary",
        help="Known tool and marker names. Without it, those rules report themselves skipped.",
    ),
    agent_def: str | None = typer.Option(
        None,
        "--agent-def",
        help="Agent definition to introspect, as `module:attribute`. Feeds the "
        "definition-fed obligations.",
    ),
) -> None:
    """Check cards. Zero tokens, no network."""
    console = Console()
    try:
        result = lint_paths(
            paths or [Path("cards")],
            lock=Lockfile.load(lock_path) if lock_path else None,
            lock_path=lock_path,
            vocabulary=_vocabulary(vocabulary_path),
            agent_def=_introspected(agent_def),
        )
    except USER_ERRORS as error:
        _fail(console, error)
        raise typer.Exit(2) from None

    _render_lint(result, console)
    raise typer.Exit(0 if result.ok else 1)


@app.command()
def coverage(
    paths: list[Path] = typer.Argument(None, help="Cards, or directories holding them."),  # noqa: B008
    vocabulary_path: Path | None = typer.Option(  # noqa: B008
        None,
        "--vocabulary",
        help="Declared tools. Without it the vocabulary table reports itself blind.",
    ),
    trace: list[Path] = typer.Option(  # noqa: B008
        None, "--trace", help="A recorded event log. Repeat; traces are pooled across the deck."
    ),
    agent_def: str | None = typer.Option(
        None,
        "--agent-def",
        help="Agent definition to introspect, as `module:attribute`. Without it the path "
        "table has no denominator and says so.",
    ),
) -> None:
    """Report the coverage denominators. Zero tokens, no network, always exits 0."""
    # The exit code carries no coverage information at all, in either direction — this
    # command exits 0 on any computed result, whatever the numbers say. DECISIONS.md,
    # 2026-08-15: coverage percentages never gate CI. There is deliberately no
    # `--fail-under`, no `--min-coverage` and no `--strict`; adding one would be a
    # decision, not a feature. The binary definition obligations are the one carve-out and
    # they live behind `specdeck lint`, which does gate.
    console = Console()
    try:
        cards = [parse(path) for path in cards_under(paths or [Path("cards")])]
        found = collect(
            cards,
            vocabulary=_vocabulary(vocabulary_path),
            traces=[load_trace(path) for path in trace or []],
            introspection=_introspected(agent_def),
        )
    except USER_ERRORS as error:
        _fail(console, error)
        raise typer.Exit(2) from None
    except Exception as error:
        console.print(f"[red]internal error[/red] {type(error).__name__}: {error}")
        raise typer.Exit(3) from None

    render_coverage(found, console)
    console.print()
    raise typer.Exit(0)


@app.command()
def rates(
    rates_path: Path | None = typer.Option(  # noqa: B008
        None, "--rates", help=f"A {RATES_FILE} to merge over the built-in table."
    ),
) -> None:
    """Print the cost rate table. Estimates, never billing."""
    console = Console()
    try:
        table = load_rates(rates_path, beside=Path.cwd())
    except USER_ERRORS as error:
        _fail(console, error)
        raise typer.Exit(2) from None

    _render_rates(table, console)
    raise typer.Exit(0)


def _vocabulary(path: Path | None) -> Vocabulary | None:
    """Read the declared vocabulary: `[tools]` and `[markers]` sections, one name a line.

    Deliberately not TOML or JSON. This file is a placeholder for introspection, and a
    format with no nesting is one nobody has to migrate when introspection replaces it.
    """
    if path is None:
        return None
    section = "tools"
    found: dict[str, set[str]] = {"tools": set(), "markers": set()}
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1]
            if section not in found:
                raise CardError(f"{path}: unknown vocabulary section [{section}]")
            continue
        found[section].add(line)
    return Vocabulary(tools=found["tools"], markers=found["markers"])


def _fail(console: Console, error: Exception) -> None:
    """Print a user error. The message is Text, not markup.

    These messages quote what the user wrote — `[rates.openai]`, a card heading, a path —
    and Rich reads a bracket as a style tag, so an interpolated message loses exactly the
    part that says where to look.
    """
    console.print("[red]error[/red]", Text(str(error)))


#: Skipped is dim rather than absent: a rule that could not run is not a rule that passed.
_STYLES = {
    Severity.ERROR: "red",
    Severity.WARNING: "yellow",
    Severity.SUGGESTION: "cyan",
    Severity.SKIPPED: "dim",
}


def _render_lint(result: Result, console: Console) -> None:
    console.print()
    console.print(depth_line(result.introspection))
    console.print()
    for card, findings in groupby(result.findings, key=lambda f: f.card):
        listed = list(findings)
        console.print(Text(card, style="bold"))
        for finding in listed:
            line = Text("  ")
            line.append(f"{finding.severity.value:<10}", style=_STYLES[finding.severity])
            line.append(f"{finding.rule:<22}")
            line.append(finding.message, style="dim")
            console.print(line)
        console.print()
    counts = result.counts()
    tally = ", ".join(f"{counts[s.value]} {s.value}" for s in Severity if counts[s.value])
    console.print(f"[dim]{tally or 'nothing to report'}[/dim]\n")


def _render_rates(table: Rates, console: Console) -> None:
    console.print()
    console.print(
        f"[dim]USD per million tokens — estimates, not billing. {_age(table.verified)}.[/dim]"
    )
    for provider in sorted(table.table):
        entries = table.table[provider]
        console.print()
        console.print(Text(f"  {provider}", style="bold"))
        # A vendor id and a provider name are external text, printed as Text rather than
        # markup for the same reason a judge's reason is: a bracket would eat the report.
        column = max((len(model) for model in entries), default=0) + 2
        for model in sorted(entries):
            rate = entries[model]
            line = Text("    ")
            line.append(f"{model:<{column}}")
            # Four places, like every `Estimate.label`: a sub-cent rate rounded to cents
            # prints a number that is not the rate, and 0.004 would print as free.
            line.append(f"{rate.input:>9.4f} in {rate.output:>10.4f} out", style="dim")
            console.print(line)
    console.print(
        "\n[dim]A model with no entry here reports n/a naming the model, never $0.00.[/dim]\n"
    )


def _age(verified: date) -> str:
    """The table dates itself in every render, which is the only staleness signal there is."""
    days = (date.today() - verified).days
    if days < 0:
        return f"Verified {verified}, which is ahead of today"
    return f"Verified {verified} ({days} day{'' if days == 1 else 's'} ago)"
