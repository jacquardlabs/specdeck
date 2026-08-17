from typer.testing import CliRunner

from specdeck import __version__
from specdeck.cli import app

runner = CliRunner()


def test_version_flag_prints_the_version() -> None:
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert result.stdout.strip() == __version__


def test_bare_invocation_shows_help_rather_than_failing() -> None:
    result = runner.invoke(app, [])
    assert "Card-based eval runner" in result.stdout
