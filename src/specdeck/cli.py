"""The `specdeck` command."""

from __future__ import annotations

import asyncio
import importlib
import os
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
from specdeck.lockfile import LOCKFILE_NAME, RELOCK_HINT, Lockfile, StaleLock
from specdeck.loop import DEFAULT_MAX_TURNS, LoopError, run_agent
from specdeck.provider import ProviderError
from specdeck.report import render
from specdeck.simulator import SimulatorError
from specdeck.trace import SEMCONV, Trace
from specdeck.traceio import TraceError, load_trace
from specdeck.wires import WireError

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
    runs: int = typer.Option(DEFAULT_N, "--runs", help="Runs in the cell."),
    threshold: int = typer.Option(
        DEFAULT_K, "--pass-threshold", help="Runs that must pass for the cell to pass."
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
            runs=runs,
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
            n=runs,
            k=threshold,
            judge_model=lock.judge_model,
            live=live,
            concurrency=concurrency,
        )
    except USER_ERRORS as error:
        console.print(f"[red]error[/red] {error}")
        raise typer.Exit(2) from None

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
        raise StaleLock(f"the lockfile pins no simulator model — {RELOCK_HINT}")
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

    A callable is called with no arguments, so a factory and a class both work. The
    protocol check happens here rather than at the first turn: `--agent` naming the wrong
    thing should fail before a simulator call is paid for.
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
    adapter = found() if callable(found) and not hasattr(found, "run") else found
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
    key = _lock_key(card_path, path)
    rubric = rubric_text(criteria_of(card))
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
        lock = base.relock(key, rubric=rubric, simulator=card.context.simulator)
        # --relock is the only path that may move a pin, and it moves every pin the run
        # was given. Silently keeping the old judge is what made --judge-model inert.
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
    lock.verify(key, rubric=rubric, simulator=card.context.simulator)
    return lock


def _lock_key(card_path: Path, lock_path: Path) -> str:
    """A card's identity in the lock: its path relative to the lockfile.

    Keying on the path as typed would make `specdeck run cards/x.md` and
    `specdeck run /abs/cards/x.md` two different cards, and every clone that checked out
    somewhere else would read as drift.
    """
    relative = os.path.relpath(card_path.resolve(), lock_path.resolve().parent)
    return relative.replace(os.sep, "/")


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
            vocabulary=_vocabulary(vocabulary_path),
        )
    except USER_ERRORS as error:
        console.print(f"[red]error[/red] {error}")
        raise typer.Exit(2) from None

    _render_lint(result, console)
    raise typer.Exit(0 if result.ok else 1)


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
