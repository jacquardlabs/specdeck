"""The `specdeck` command."""

from __future__ import annotations

import typer

from specdeck import __version__

app = typer.Typer(
    name="specdeck",
    help="Card-based eval runner for LLM systems.",
    no_args_is_help=True,
    add_completion=False,
)


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
