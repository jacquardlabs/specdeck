"""The `specdeck` command."""

from __future__ import annotations

import os
from pathlib import Path

import typer
from rich.console import Console

from specdeck import __version__
from specdeck.card import CardError, parse
from specdeck.cell import DEFAULT_K, DEFAULT_N, CellError, run_cell
from specdeck.judge import DEFAULT_JUDGE_MODEL, JudgeError
from specdeck.lockfile import LOCKFILE_NAME, Lockfile, StaleLock
from specdeck.report import render
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
    version: bool = typer.Option(  # noqa: B008 - typer's declarative option style
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
    threshold: int = typer.Option(  # noqa: B008
        DEFAULT_K, "--pass-threshold", help="Runs that must pass for the cell to pass."
    ),
    cassettes: Path = typer.Option(  # noqa: B008
        Path("cassettes"), "--cassettes", help="Where recorded judge calls live."
    ),
    lock_path: Path | None = typer.Option(  # noqa: B008
        None,
        "--lock",
        help=f"Lockfile to verify against (default: {LOCKFILE_NAME} beside the card).",
    ),
    relock: bool = typer.Option(False, "--relock", help="Record the current state and continue."),
    live: bool = typer.Option(False, "--live", help="Call the judge for real and record it."),
    judge_model: str = typer.Option(DEFAULT_JUDGE_MODEL, "--judge-model"),
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
            cassettes=cassettes,
            n=runs,
            k=threshold,
            judge_model=lock.judge_model,
            live=live,
        )
    except USER_ERRORS as error:
        console.print(f"[red]error[/red] {error}")
        raise typer.Exit(2) from None

    render(cell, console)
    raise typer.Exit(0 if cell.passed else 1)


def _lock(
    card_path: Path,
    lock_path: Path | None,
    card,
    traces,
    *,
    relock: bool,
    judge_model: str,
) -> Lockfile:
    """Verify the run against the lock, or record it. An unpinned judge is not a test."""
    path = lock_path or card_path.parent / LOCKFILE_NAME
    key = _lock_key(card_path, path)
    if relock:
        base = (
            Lockfile.load(path)
            if path.exists()
            else Lockfile(
                semconv=traces[0].semconv,
                judge_model=judge_model,
                simulator_model=judge_model,
                cards={},
            )
        )
        lock = base.relock(key, rubric=card.prose, simulator=card.context.simulator)
        lock = lock.model_copy(update={"semconv": traces[0].semconv})
        lock.save(path)
        return lock

    lock = Lockfile.load(path)
    lock.verify(key, rubric=card.prose, simulator=card.context.simulator)
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
