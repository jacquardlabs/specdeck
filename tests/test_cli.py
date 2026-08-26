import inspect
from pathlib import Path

import pytest
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


def test_run_offers_the_diff_selection_flags() -> None:
    """A cheap guard that the options are wired onto the command, not just defined."""
    text = " ".join(runner.invoke(app, ["run", "--help"]).stdout.split())
    assert "--affected-by" in text and "--diff-root" in text


class TestTheCoverageCommand:
    """`specdeck coverage` reports denominators and can never gate CI."""

    CARD = "# Scenario: x\ncontext:\n  policy: policy.md\n\nThe agent answers.\n"
    WIRED = CARD + "\nwire:\n  - pay_invoice: never\n"
    VOCABULARY = "[tools]\npay_invoice\nget_invoice\n"

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
        (tmp_path / "vocabulary.txt").write_text("[tools]\npay_invoice\n")
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

    def _policy_deck(self, tmp_path: Path, orphan: str) -> Path:
        """The repo's own layout: cards in the deck root, policies in `policy/`."""
        (tmp_path / "policy").mkdir()
        (tmp_path / "policy" / "airline.md").write_text("# Airline Policy\n\n- confirm first\n")
        (tmp_path / "policy" / "refunds.md").write_text(orphan)
        (tmp_path / "card.md").write_text(
            "# Scenario: x\ncontext:\n  policy: policy/ap.md\n\nThe agent answers.\n"
        )
        return tmp_path

    def test_a_policy_document_no_card_names_is_reported_through_the_command(
        self, tmp_path: Path
    ) -> None:
        """#19(a)'s one deterministic signal, over the walk the command actually does.

        The orphan parses as a card — an `# ` heading and nothing else is a legal card —
        so it arrives in the card list and used to filter itself out of its own report.
        """
        deck = self._policy_deck(tmp_path, "# Refund Policy\n\n- refunds go to the card\n")
        result = runner.invoke(app, ["coverage", str(deck)])
        assert result.exit_code == 0
        text = " ".join(result.stdout.split())
        assert "refunds.md" in text and "named by no card" in text

    def test_a_reported_path_is_never_broken_across_lines(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Rich word-wraps, and a hard break inside a filename is not a cosmetic bug.

        The two tests around this one passed on macOS and failed in CI purely because the
        temp path is shorter there, so the wrap landed inside `refunds.md` rather than
        beside it — `refund s.md`, which no reader can copy and no assertion can find. The
        deck name here is long enough to push the path past 80 columns on any platform.
        """
        monkeypatch.setenv("COLUMNS", "80")
        deck = tmp_path / ("a-deck-with-a-deliberately-long-name-" * 3).rstrip("-")
        deck.mkdir()
        self._policy_deck(deck, "# Refund Policy\n\n- refunds go to the card\n")
        result = runner.invoke(app, ["coverage", str(deck)])
        assert result.exit_code == 0
        text = " ".join(result.stdout.split())
        assert "refunds.md" in text
        assert "named by no card" in text

    def test_a_policy_document_that_is_not_a_card_is_reported_rather_than_aborting(
        self, tmp_path: Path
    ) -> None:
        """`lint` reports a `parse` finding and carries on; the denominators do too."""
        deck = self._policy_deck(tmp_path, "## Refund Policy\n\n- refunds go to the card\n")
        result = runner.invoke(app, ["coverage", str(deck)])
        assert result.exit_code == 0
        text = " ".join(result.stdout.split())
        assert "not read as cards" in text
        assert "refunds.md" in text and "named by no card" in text

    def test_a_trace_marks_a_tool_exercised(self, tmp_path: Path) -> None:
        deck = self._deck(tmp_path, self.CARD)
        (tmp_path / "vocabulary.txt").write_text("[tools]\nget_purchase_order\n")
        recorded = (
            Path(__file__).resolve().parent.parent
            / "cards"
            / "traces"
            / "over-threshold-second-approval.1.otlp.json"
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
        assert "get_purchase_order no wire, exercised" in " ".join(result.stdout.split())
