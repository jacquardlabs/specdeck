from pathlib import Path

import pytest

from specdeck.lint import Severity, lint_card, lint_paths
from specdeck.lockfile import Lockfile

GOOD = """\
# Scenario: refund request on basic economy
context:
  policy: airline.md
  simulator: "frustrated customer"

The agent refuses the change and explains the restriction.

wire:
  - modify_reservation: never
  - web_search: at_most 2

credit:
  - "tone remains professional": 2
"""


@pytest.fixture
def card_dir(tmp_path: Path) -> Path:
    (tmp_path / "airline.md").write_text("the policy")
    (tmp_path / "refund.md").write_text(GOOD)
    return tmp_path


def rules(findings, severity: Severity | None = None) -> list[str]:
    return sorted(f.rule for f in findings if severity is None or f.severity is severity)


class TestCleanCard:
    def test_a_well_formed_card_with_no_lock_reports_only_blindness(self, card_dir: Path) -> None:
        findings = lint_card(card_dir / "refund.md")
        assert rules(findings, Severity.ERROR) == []
        # Lint says what it could not check rather than staying quiet about it.
        assert "unknown-tool" in [f.rule for f in findings if f.severity is Severity.SKIPPED]

    def test_errors_are_what_decides_the_exit_code(self, card_dir: Path) -> None:
        assert lint_paths([card_dir / "refund.md"]).ok is True


class TestStructure:
    def test_a_card_that_does_not_parse_is_one_error_not_a_traceback(self, tmp_path: Path) -> None:
        broken = tmp_path / "broken.md"
        broken.write_text("no heading\n")
        findings = lint_card(broken)
        assert rules(findings, Severity.ERROR) == ["parse"]

    def test_an_empty_prose_block_warns(self, tmp_path: Path) -> None:
        card = tmp_path / "wires-only.md"
        card.write_text("# Scenario: x\nwire:\n  - a_tool: never\n")
        assert "empty-prose" in rules(lint_card(card), Severity.WARNING)

    def test_a_prose_only_card_is_never_flagged_for_missing_wires(self, tmp_path: Path) -> None:
        card = tmp_path / "prose.md"
        card.write_text("# Scenario: x\nThe agent answers and stops.\n")
        assert rules(lint_card(card), Severity.ERROR) == []
        assert rules(lint_card(card), Severity.WARNING) == []


class TestDeadPaths:
    def test_a_policy_that_does_not_exist_is_an_error(self, tmp_path: Path) -> None:
        card = tmp_path / "x.md"
        card.write_text("# Scenario: x\ncontext:\n  policy: absent.md\n\nThe agent answers.\n")
        assert "dead-path" in rules(lint_card(card), Severity.ERROR)

    def test_a_fixture_that_does_not_exist_is_an_error(self, tmp_path: Path) -> None:
        card = tmp_path / "x.md"
        card.write_text("# Scenario: x\ncontext:\n  fixture: gone.json\n\nThe agent answers.\n")
        assert "dead-path" in rules(lint_card(card), Severity.ERROR)

    def test_the_message_names_the_card_value_not_the_resolved_path(self, tmp_path: Path) -> None:
        card = tmp_path / "x.md"
        card.write_text("# Scenario: x\ncontext:\n  policy: absent.md\n\nThe agent answers.\n")
        message = next(f.message for f in lint_card(card) if f.rule == "dead-path")
        assert "absent.md" in message


class TestWires:
    def test_a_wire_that_does_not_compile_is_an_error(self, tmp_path: Path) -> None:
        card = tmp_path / "x.md"
        card.write_text("# Scenario: x\nThe agent answers.\nwire:\n  - t: wibble\n")
        assert "wire-syntax" in rules(lint_card(card), Severity.ERROR)

    def test_a_deferred_pattern_names_the_issue_it_waits_on(self, tmp_path: Path) -> None:
        card = tmp_path / "x.md"
        card.write_text("# Scenario: x\nThe agent answers.\nwire:\n  - t: after 3 non_agreement\n")
        message = next(f.message for f in lint_card(card) if f.rule == "wire-syntax")
        assert "#47" in message

    def test_never_and_at_most_on_the_same_tool_contradict(self, tmp_path: Path) -> None:
        card = tmp_path / "x.md"
        card.write_text(
            "# Scenario: x\nThe agent answers.\nwire:\n  - search: never\n  - search: at_most 2\n"
        )
        assert "contradictory-wires" in rules(lint_card(card), Severity.ERROR)

    def test_two_bounds_on_the_same_measure_contradict(self, tmp_path: Path) -> None:
        card = tmp_path / "x.md"
        card.write_text(
            "# Scenario: x\nThe agent answers.\n"
            "wire:\n  - latency: under 120s\n  - latency: under 60s\n"
        )
        assert "contradictory-wires" in rules(lint_card(card), Severity.ERROR)

    def test_never_plus_at_most_zero_is_redundant_not_contradictory(self, tmp_path: Path) -> None:
        card = tmp_path / "x.md"
        card.write_text(
            "# Scenario: x\nThe agent answers.\nwire:\n  - search: never\n  - search: at_most 0\n"
        )
        findings = lint_card(card)
        assert rules(findings, Severity.ERROR) == []
        assert "redundant-wires" in rules(findings, Severity.WARNING)

    def test_the_same_wire_twice_is_redundant(self, tmp_path: Path) -> None:
        card = tmp_path / "x.md"
        card.write_text(
            "# Scenario: x\nThe agent answers.\n"
            "wire:\n  - search: at_most 2\n  - search: at_most 2\n"
        )
        assert "redundant-wires" in rules(lint_card(card), Severity.WARNING)


class TestVocabulary:
    def test_an_unknown_tool_is_an_error_when_a_vocabulary_is_supplied(
        self, card_dir: Path
    ) -> None:
        findings = lint_card(card_dir / "refund.md", vocabulary={"web_search"})
        assert "unknown-tool" in rules(findings, Severity.ERROR)

    def test_a_known_tool_passes(self, card_dir: Path) -> None:
        findings = lint_card(
            card_dir / "refund.md", vocabulary={"web_search", "modify_reservation"}
        )
        assert rules(findings, Severity.ERROR) == []

    def test_the_rule_reports_itself_skipped_without_a_vocabulary(self, card_dir: Path) -> None:
        skipped = [f for f in lint_card(card_dir / "refund.md") if f.severity is Severity.SKIPPED]
        message = next(f.message for f in skipped if f.rule == "unknown-tool")
        assert "vocabulary" in message


class TestLockfile:
    def _lock(self, card_dir: Path, **overrides) -> Lockfile:
        from specdeck.card import parse
        from specdeck.judge import criteria_of, rubric_text
        from specdeck.lockfile import CardLock, fingerprint

        card = parse(card_dir / "refund.md")
        base = Lockfile(
            semconv="semantic-conventions-genai@1.38.0",
            judge_model="claude-sonnet-5",
            simulator_model="",
            cards={
                "refund.md": CardLock(
                    rubric_hash=fingerprint(rubric_text(criteria_of(card))),
                    simulator_hash=fingerprint(card.context.simulator),
                )
            },
        )
        return base.model_copy(update=overrides)

    def test_a_fresh_lock_passes(self, card_dir: Path) -> None:
        findings = lint_card(card_dir / "refund.md", lock=self._lock(card_dir))
        assert rules(findings, Severity.ERROR) == []

    def test_an_unlocked_card_is_an_error(self, card_dir: Path) -> None:
        lock = self._lock(card_dir, cards={})
        assert "stale-lock" in rules(lint_card(card_dir / "refund.md", lock=lock), Severity.ERROR)

    def test_an_edited_rubric_is_an_error(self, card_dir: Path) -> None:
        card = card_dir / "refund.md"
        lock = self._lock(card_dir)
        card.write_text(GOOD.replace("refuses", "declines"))
        assert "stale-lock" in rules(lint_card(card, lock=lock), Severity.ERROR)

    def test_the_rule_reports_itself_skipped_without_a_lock(self, card_dir: Path) -> None:
        skipped = [
            f.rule for f in lint_card(card_dir / "refund.md") if f.severity is Severity.SKIPPED
        ]
        assert "stale-lock" in skipped


class TestProseIsNeverStylePoliced:
    def test_no_rule_reads_the_content_of_the_prose_block(self, tmp_path: Path) -> None:
        # The SME zone is off limits: only its presence is checked, never its wording.
        card = tmp_path / "x.md"
        card.write_text("# Scenario: x\ni think maybe the agent should probably be nice, idk!!!\n")
        assert lint_card(card) == [f for f in lint_card(card) if f.severity is Severity.SKIPPED]


class TestManyCards:
    def test_lint_paths_walks_a_directory_and_orders_by_card(self, card_dir: Path) -> None:
        (card_dir / "b.md").write_text("# Scenario: b\nThe agent answers.\n")
        result = lint_paths([card_dir])
        assert {Path(f.card).name for f in result.findings} >= {"refund.md", "b.md"}

    def test_one_error_fails_the_whole_run(self, card_dir: Path) -> None:
        (card_dir / "broken.md").write_text("no heading\n")
        result = lint_paths([card_dir])
        assert result.ok is False
        assert result.errors == 1

    def test_a_directory_with_no_cards_is_not_an_error(self, tmp_path: Path) -> None:
        assert lint_paths([tmp_path]).ok is True


class TestDiscovery:
    def test_a_policy_document_beside_a_card_is_not_linted_as_one(self, card_dir: Path) -> None:
        # cards/policy/airline.md is markdown, and it is nobody's card.
        linted = {Path(f.card).name for f in lint_paths([card_dir]).findings}
        assert "airline.md" not in linted
        assert "refund.md" in linted

    def test_a_named_file_is_always_linted_even_without_the_heading(self, card_dir: Path) -> None:
        result = lint_paths([card_dir / "airline.md"])
        assert result.ok is False  # it does not parse as a card, and you asked


class TestThisReposOwnCards:
    """Dogfooding. If our own cards do not survive our own linter, the linter is wrong."""

    def test_every_committed_card_is_clean_against_its_lock_and_vocabulary(self) -> None:
        from specdeck.cli import _vocabulary

        cards = Path(__file__).resolve().parent.parent / "cards"
        result = lint_paths(
            [cards],
            lock=Lockfile.load(cards / "spec.lock.toml"),
            vocabulary=_vocabulary(cards / "vocabulary.txt"),
        )
        assert result.findings == [], result.findings

    def test_the_committed_vocabulary_covers_every_tool_the_cards_wire(self) -> None:
        # A vocabulary that drifts behind the cards turns unknown-tool into noise.
        from specdeck.cli import _vocabulary

        cards = Path(__file__).resolve().parent.parent / "cards"
        result = lint_paths([cards], vocabulary=_vocabulary(cards / "vocabulary.txt"))
        assert [f for f in result.findings if f.rule == "unknown-tool"] == []
