import inspect
from pathlib import Path

from typer.testing import CliRunner

from specdeck import __version__, cli
from specdeck.cli import app

runner = CliRunner()


def test_version_flag_prints_the_version() -> None:
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert result.stdout.strip() == __version__


def test_bare_invocation_shows_help_rather_than_failing() -> None:
    result = runner.invoke(app, [])
    assert "Card-based eval runner" in result.stdout


class TestTheCoverageCommand:
    """`specdeck coverage` reports denominators and can never gate CI."""

    CARD = "# Scenario: x\ncontext:\n  policy: policy.md\n\nThe agent answers.\n"
    WIRED = CARD + "\nwire:\n  - cancel_reservation: never\n"
    VOCABULARY = "[tools]\ncancel_reservation\nlist_all_airports\n"

    def _deck(self, tmp_path: Path, card: str) -> Path:
        (tmp_path / "policy.md").write_text("- the agent must confirm before booking\n")
        (tmp_path / "card.md").write_text(card)
        return tmp_path

    def test_nothing_covered_still_exits_zero(self, tmp_path: Path) -> None:
        deck = self._deck(tmp_path, self.CARD)
        (tmp_path / "vocabulary.txt").write_text(self.VOCABULARY)
        result = runner.invoke(
            app, ["coverage", str(deck), "--vocabulary", str(tmp_path / "vocabulary.txt")]
        )
        assert result.exit_code == 0
        assert "2 of 2 declared tools have neither" in " ".join(result.stdout.split())

    def test_a_fully_covered_deck_exits_zero_too(self, tmp_path: Path) -> None:
        # The exit code carries no coverage information at all, in either direction.
        deck = self._deck(tmp_path, self.WIRED)
        (tmp_path / "vocabulary.txt").write_text("[tools]\ncancel_reservation\n")
        result = runner.invoke(
            app, ["coverage", str(deck), "--vocabulary", str(tmp_path / "vocabulary.txt")]
        )
        assert result.exit_code == 0
        assert "0 of 1 declared tool have neither" in " ".join(result.stdout.split())

    def test_without_a_vocabulary_it_prints_blindness_rather_than_a_percentage(
        self, tmp_path: Path
    ) -> None:
        result = runner.invoke(app, ["coverage", str(self._deck(tmp_path, self.CARD))])
        assert result.exit_code == 0
        text = " ".join(result.stdout.split())
        assert "no tool vocabulary supplied" in text
        assert "0 of 0" not in text

    def test_it_offers_no_way_to_turn_a_figure_into_an_exit_code(self) -> None:
        """An absence held by a test, so adding one is a decision rather than a feature."""
        options = " ".join(inspect.signature(cli.coverage).parameters)
        for flag in ("fail", "min", "strict", "threshold", "gate"):
            assert flag not in options
        assert "--fail-under" not in " ".join(
            runner.invoke(app, ["coverage", "--help"]).stdout.split()
        )

    def test_a_dead_policy_path_is_a_user_error_not_a_coverage_regression(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / "card.md").write_text(self.CARD)
        result = runner.invoke(app, ["coverage", str(tmp_path)])
        assert result.exit_code == 2
        assert "policy.md" in " ".join(result.stdout.split())

    def test_a_trace_marks_a_tool_exercised(self, tmp_path: Path) -> None:
        deck = self._deck(tmp_path, self.CARD)
        (tmp_path / "vocabulary.txt").write_text("[tools]\nget_reservation_details\n")
        recorded = (
            Path(__file__).resolve().parent.parent
            / "cards"
            / "traces"
            / "basic-economy-return-change.otlp.json"
        )
        result = runner.invoke(
            app,
            [
                "coverage",
                str(deck),
                "--vocabulary",
                str(tmp_path / "vocabulary.txt"),
                "--trace",
                str(recorded),
            ],
        )
        assert result.exit_code == 0
        assert "get_reservation_details no wire, exercised" in " ".join(result.stdout.split())
