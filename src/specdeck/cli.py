"""The `specdeck` command."""

from __future__ import annotations

import os
from itertools import groupby
from pathlib import Path

import typer
from rich.console import Console
from rich.text import Text

from specdeck import __version__
from specdeck.card import Card, CardError, parse
from specdeck.cell import DEFAULT_CONCURRENCY, DEFAULT_K, DEFAULT_N, CellError, run_cell
from specdeck.judge import DEFAULT_JUDGE_MODEL, JudgeError, criteria_of, rubric_text
from specdeck.lint import Result, Severity, Vocabulary, lint_paths
from specdeck.lockfile import LOCKFILE_NAME, RELOCK_HINT, Lockfile, StaleLock
from specdeck.report import render
from specdeck.trace import Trace
from specdeck.traceio import TraceError, load_trace
from specdeck.wires import WireError

app = typer.Typer(
    name="specdeck",
    help="Card-based eval runner for LLM systems.",
    no_args_is_help=True,
    add_completion=False,
)

USER_ERRORS = (CardError, CellError, JudgeError, StaleLock, TraceError, WireError)


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
        ..., "--trace", help="A recorded event log. Repeat once per run of the cell."
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
) -> None:
    """Evaluate one card against recorded traces and report the cell."""
    console = Console()
    try:
        card = parse(card_path)
        traces = [load_trace(path) for path in trace]
        lock = _lock(card_path, lock_path, card, traces, relock=relock, judge_model=judge_model)
        cell = run_cell(
            card,
            traces,
            # Every other card input resolves against the card; the cassette directory
            # has to as well, or where you stand changes which recordings are found.
            cassettes=cassettes or card_path.parent / "cassettes",
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


def _lock(
    card_path: Path,
    lock_path: Path | None,
    card: Card,
    traces: list[Trace],
    *,
    relock: bool,
    judge_model: str | None,
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
                semconv=traces[0].semconv,
                judge_model=judge_model or DEFAULT_JUDGE_MODEL,
                # The simulator does not exist yet, so there is nothing to pin. Copying
                # the judge model here would ship a real-looking pin nobody chose, and
                # the first real simulator would not read as drift.
                simulator_model="",
                cards={},
            )
        )
        lock = base.relock(key, rubric=rubric, simulator=card.context.simulator)
        # --relock is the only path that may move a pin, and it moves every pin the run
        # was given. Silently keeping the old judge is what made --judge-model inert.
        lock = lock.model_copy(
            update={"semconv": traces[0].semconv}
            | ({"judge_model": judge_model} if judge_model else {})
        )
        lock.save(path)
        return lock

    lock = Lockfile.load(path)
    if judge_model and judge_model != lock.judge_model:
        raise StaleLock(
            f"--judge-model {judge_model} disagrees with the pinned "
            f"{lock.judge_model} — {RELOCK_HINT}"
        )
    lock.verify(key, rubric=rubric, simulator=card.context.simulator)
    for one in traces:
        lock.verify_semconv(one.semconv)
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
