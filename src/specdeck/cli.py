"""The `specdeck` command."""

from __future__ import annotations

import asyncio
import importlib
from datetime import date
from itertools import groupby
from pathlib import Path

import typer
from rich.console import Console
from rich.text import Text

from specdeck import __version__
from specdeck.agent import AgentAdapter
from specdeck.card import Card, CardError, parse
from specdeck.cell import DEFAULT_CONCURRENCY, DEFAULT_K, DEFAULT_N, CellError, run_cell
from specdeck.judge import DEFAULT_JUDGE_MODEL, JudgeError, criteria_of, rubric_text
from specdeck.lint import Result, Severity, Vocabulary, lint_paths
from specdeck.lockfile import LOCKFILE_NAME, RELOCK_HINT, Lockfile, StaleLock, lock_key
from specdeck.loop import DEFAULT_MAX_TURNS, LoopError, run_agent
from specdeck.provider import ProviderError
from specdeck.rates import RATES_FILE, RateError, Rates, load_rates
from specdeck.report import render
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

USER_ERRORS = (
    CardError,
    CellError,
    JudgeError,
    LoopError,
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
) -> None:
    """Evaluate one card — against recorded traces, or by running the agent."""
    console = Console()
    try:
        if bool(trace) == bool(agent):
            raise CardError("pass exactly one of --trace and --agent")
        card = parse(card_path)
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
        traces = recordings or _drive(
            card,
            agent or "",
            cassettes=cassette_dir,
            lock=lock,
            runs=n,
            markers=_markers(vocabulary_path),
            max_turns=max_turns,
            live=live,
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
            # Named in the report only when one actually spoke: a run from recorded traces
            # had no simulated user, and the pin describes nothing that happened.
            simulator_model=lock.simulator_model if agent else "",
            live=live,
            concurrency=concurrency,
        )
    except USER_ERRORS as error:
        console.print(f"[red]error[/red] {error}")
        raise typer.Exit(2) from None
    except Exception as error:
        # Exit 3, not 1. A `TOMLDecodeError` from a conflict-marked lockfile used to leave
        # Python exiting 1 — the same code as a card that honestly failed — so a caller
        # reading the exit code routed a broken lockfile to the SME as an eval regression.
        console.print(f"[red]internal error[/red] {type(error).__name__}: {error}")
        raise typer.Exit(3) from None

    render(cell, console)
    raise typer.Exit(0 if cell.passed else 1)


def _drive(
    card: Card,
    reference: str,
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
    if not lock.simulator_model:
        # Naming --relock alone would loop the reader back here: the pin only moves when
        # --simulator-model is passed too (see `_lock`), so a bare relock writes it empty
        # and the next run dies on this same line (#76).
        raise StaleLock(
            "the lockfile pins no simulator model — run with "
            "--relock --simulator-model <model> to pin one"
        )
    adapter = _adapter(reference)

    async def all_runs() -> list[Trace]:
        return [
            await run_agent(
                card,
                adapter,
                cassettes=cassettes,
                simulator_model=lock.simulator_model,
                semconv=lock.semconv,
                markers=markers,
                max_turns=max_turns,
                live=live,
            )
            for _ in range(runs)
        ]

    return asyncio.run(all_runs())


def _adapter(reference: str) -> AgentAdapter:
    """Resolve `module:attribute` to something that satisfies the protocol.

    A class is instantiated and a factory is called, both with no arguments; an adapter
    instance is taken as it stands. The class case is not a nicety — a class satisfies a
    `runtime_checkable` protocol check on attribute presence alone, so passing one through
    uninstantiated fails at the first turn with `run() missing 1 required positional
    argument`, long after the guard said it was fine.
    """
    module_name, _, attribute = reference.partition(":")
    if not module_name or not attribute:
        raise CardError(f"--agent {reference!r} is not `module:attribute`")
    try:
        module = importlib.import_module(module_name)
    except ImportError as error:
        raise CardError(f"--agent {reference!r}: {error}") from None
    try:
        found = getattr(module, attribute)
    except AttributeError:
        raise CardError(f"--agent {reference!r}: {module_name} has no {attribute!r}") from None
    adapter = found() if isinstance(found, type) or not isinstance(found, AgentAdapter) else found
    if not isinstance(adapter, AgentAdapter):
        raise CardError(f"--agent {reference!r} has no async run(messages, tools, config)")
    return adapter


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
) -> None:
    """Check cards. Zero tokens, no network."""
    console = Console()
    try:
        result = lint_paths(
            paths or [Path("cards")],
            lock=Lockfile.load(lock_path) if lock_path else None,
            lock_path=lock_path,
            vocabulary=_vocabulary(vocabulary_path),
        )
    except USER_ERRORS as error:
        console.print(f"[red]error[/red] {error}")
        raise typer.Exit(2) from None

    _render_lint(result, console)
    raise typer.Exit(0 if result.ok else 1)


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
        console.print(f"[red]error[/red] {error}")
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


#: Skipped is dim rather than absent: a rule that could not run is not a rule that passed.
_STYLES = {
    Severity.ERROR: "red",
    Severity.WARNING: "yellow",
    Severity.SUGGESTION: "cyan",
    Severity.SKIPPED: "dim",
}


def _render_lint(result: Result, console: Console) -> None:
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
        column = max(len(model) for model in entries) + 2
        for model in sorted(entries):
            rate = entries[model]
            line = Text("    ")
            line.append(f"{model:<{column}}")
            line.append(f"{rate.input:>7.2f} in {rate.output:>8.2f} out", style="dim")
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
