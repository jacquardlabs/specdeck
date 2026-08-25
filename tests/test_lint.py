from pathlib import Path

import pytest

from specdeck.lint import Severity, Vocabulary, lint_card, lint_paths
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

    def test_a_pattern_the_palette_names_but_does_not_implement_says_so(
        self, tmp_path: Path
    ) -> None:
        card = tmp_path / "x.md"
        card.write_text("# Scenario: x\nThe agent answers.\nwire:\n  - t: eventually\n")
        message = next(f.message for f in lint_card(card) if f.rule == "wire-syntax")
        assert "not implemented" in message

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
        findings = lint_card(card_dir / "refund.md", vocabulary=Vocabulary(tools={"web_search"}))
        assert "unknown-tool" in rules(findings, Severity.ERROR)

    def test_a_known_tool_passes(self, card_dir: Path) -> None:
        vocabulary = Vocabulary(tools={"web_search", "modify_reservation"})
        findings = lint_card(card_dir / "refund.md", vocabulary=vocabulary)
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
        from specdeck.wires import compile_wires, wires_text

        card = parse(card_dir / "refund.md")
        base = Lockfile(
            semconv="semantic-conventions-genai@1.38.0",
            judge_model="claude-sonnet-5",
            simulator_model="",
            cards={
                "refund.md": CardLock(
                    rubric_hash=fingerprint(rubric_text(criteria_of(card))),
                    wires_hash=fingerprint(wires_text(compile_wires(card))),
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
        # Skipped findings are expected and are not dirt: `orphan-cassette` reports that
        # it cannot see prompt staleness without the trace, which is #70.
        blocking = [f for f in result.findings if f.severity is not Severity.SKIPPED]
        assert blocking == [], blocking

    def test_the_committed_vocabulary_covers_every_tool_the_cards_wire(self) -> None:
        # A vocabulary that drifts behind the cards turns unknown-tool into noise.
        from specdeck.cli import _vocabulary

        cards = Path(__file__).resolve().parent.parent / "cards"
        result = lint_paths([cards], vocabulary=_vocabulary(cards / "vocabulary.txt"))
        assert [f for f in result.findings if f.rule == "unknown-tool"] == []


class TestMarkerVocabulary:
    def test_an_undeclared_marker_is_an_error(self, tmp_path: Path) -> None:
        card = tmp_path / "x.md"
        card.write_text(
            "# Scenario: x\nThe agent answers.\nwire:\n  - transfer_to_human: after 3 impatience\n"
        )
        findings = lint_card(card, vocabulary=Vocabulary(tools={"transfer_to_human"}))
        assert "unknown-marker" in rules(findings, Severity.ERROR)

    def test_a_declared_marker_passes(self, tmp_path: Path) -> None:
        card = tmp_path / "x.md"
        card.write_text(
            "# Scenario: x\nThe agent answers.\n"
            "wire:\n  - transfer_to_human: after 3 non_agreement\n"
        )
        vocabulary = Vocabulary(tools={"transfer_to_human"}, markers={"non_agreement"})
        assert rules(lint_card(card, vocabulary=vocabulary), Severity.ERROR) == []

    def test_the_follow_up_tool_of_an_escalation_is_checked_too(self, tmp_path: Path) -> None:
        card = tmp_path / "x.md"
        card.write_text(
            "# Scenario: x\nThe agent answers.\nwire:\n  - not_a_tool: after 3 non_agreement\n"
        )
        findings = lint_card(card, vocabulary=Vocabulary(markers={"non_agreement"}))
        assert "unknown-tool" in rules(findings, Severity.ERROR)

    def test_both_rules_report_themselves_skipped_without_a_vocabulary(
        self, card_dir: Path
    ) -> None:
        skipped = {
            f.rule for f in lint_card(card_dir / "refund.md") if f.severity is Severity.SKIPPED
        }
        assert {"unknown-tool", "unknown-marker"} <= skipped


def _card(prose: str) -> str:
    return f"""\
# Scenario: refund request on basic economy
context:
  policy: airline.md

{prose}

wire:
  - modify_reservation: never
"""


class TestCardMechanics:
    """The one rule that reads inside the prose block, and why it is allowed to.

    Not style: each pattern was observed making the judge answer with commentary instead
    of a verdict, and an ungraded criterion fails closed. See #67 and DECISIONS.md.
    """

    def _lint(self, tmp_path: Path, prose: str):
        (tmp_path / "airline.md").write_text("the policy")
        (tmp_path / "card.md").write_text(_card(prose))
        return lint_card(tmp_path / "card.md")

    def test_prose_that_grades_itself_is_flagged(self, tmp_path: Path) -> None:
        findings = self._lint(tmp_path, "Do not fail this card if the agent apologises.")
        assert "card-mechanics" in rules(findings, Severity.WARNING)

    def test_a_pass_fail_condition_is_flagged(self, tmp_path: Path) -> None:
        findings = self._lint(tmp_path, "The card fails only if the agent cancels the booking.")
        assert "card-mechanics" in rules(findings, Severity.WARNING)

    def test_naming_specdecks_own_tiers_is_flagged(self, tmp_path: Path) -> None:
        findings = self._lint(tmp_path, "This is a gate check for tone.")
        assert "card-mechanics" in rules(findings, Severity.WARNING)

    def test_shouting_is_flagged(self, tmp_path: Path) -> None:
        findings = self._lint(tmp_path, "The agent MUST NEVER OFFER a refund of any kind.")
        assert "card-mechanics" in rules(findings, Severity.WARNING)

    def test_it_warns_and_never_errors(self, tmp_path: Path) -> None:
        # The SME's words stay theirs: this is a strong signal, not a defect. A rule that
        # rejects the prose block is a rule they turn off.
        findings = self._lint(tmp_path, "Do not fail this card.")
        assert rules(findings, Severity.ERROR) == []
        assert lint_paths([tmp_path / "card.md"]).ok is True

    def test_ordinary_expected_behaviour_is_left_alone(self, tmp_path: Path) -> None:
        findings = self._lint(
            tmp_path,
            "The agent refuses the change, explains the basic economy restriction, and "
            "never promises an exception.",
        )
        assert "card-mechanics" not in [f.rule for f in findings]

    def test_airport_codes_are_not_shouting(self, tmp_path: Path) -> None:
        # An airline card legitimately says MIA and PHX; the run has to be long enough
        # not to fire on a pair of them.
        findings = self._lint(tmp_path, "The agent books MIA to PHX and explains the fare.")
        assert "card-mechanics" not in [f.rule for f in findings]

    def test_the_word_fails_on_its_own_is_not_a_verdict(self, tmp_path: Path) -> None:
        findings = self._lint(tmp_path, "The agent explains why the payment fails.")
        assert "card-mechanics" not in [f.rule for f in findings]

    def test_the_committed_cards_do_not_trip_it(self) -> None:
        cards = Path(__file__).resolve().parent.parent / "cards"
        findings = lint_paths([cards]).findings
        assert [f for f in findings if f.rule == "card-mechanics"] == []


class TestOrphanCassettes:
    def _dir(self, tmp_path: Path, names: list[str]) -> Path:
        (tmp_path / "airline.md").write_text("the policy")
        (tmp_path / "refund.md").write_text(GOOD)
        recordings = tmp_path / "cassettes"
        recordings.mkdir()
        for name in names:
            (recordings / name).write_text("{}")
        return tmp_path

    def test_a_cassette_owned_by_a_card_that_is_here_is_fine(self, tmp_path: Path) -> None:
        directory = self._dir(tmp_path, ["refund.judge-abc123.json"])
        assert rules(lint_paths([directory]).findings, Severity.WARNING) == []

    def test_a_cassette_naming_no_card_is_reported(self, tmp_path: Path) -> None:
        directory = self._dir(tmp_path, ["deleted-card.judge-abc123.json"])
        findings = lint_paths([directory]).findings
        assert "orphan-cassette" in rules(findings, Severity.WARNING)
        assert any("deleted-card" in f.message for f in findings)

    def test_a_bare_hash_is_reported_as_unattributable(self, tmp_path: Path) -> None:
        # The pre-#69 layout: nothing maps the file to the card that replays it.
        directory = self._dir(tmp_path, ["judge-abc123.json"])
        findings = lint_paths([directory]).findings
        assert "orphan-cassette" in rules(findings, Severity.WARNING)
        assert any("names no card" in f.message for f in findings)

    def test_simulator_cassettes_are_owned_the_same_way(self, tmp_path: Path) -> None:
        directory = self._dir(tmp_path, ["refund.simulator-abc123.json"])
        assert rules(lint_paths([directory]).findings, Severity.WARNING) == []

    def test_nothing_is_deleted(self, tmp_path: Path) -> None:
        # Cassettes are the Phase-3 mutation substrate; a linter that removes one removes
        # evidence. Reporting is the whole job.
        directory = self._dir(tmp_path, ["deleted-card.judge-abc123.json"])
        lint_paths([directory])
        assert (directory / "cassettes" / "deleted-card.judge-abc123.json").exists()

    def test_staleness_reports_itself_unchecked(self, tmp_path: Path) -> None:
        # A cassette whose card still exists but whose prompt moved needs the trace, which
        # is #70. A check that silently degrades is worse than one that says so.
        directory = self._dir(tmp_path, ["refund.judge-abc123.json"])
        findings = lint_paths([directory]).findings
        assert "orphan-cassette" in rules(findings, Severity.SKIPPED)

    def test_no_cassette_directory_means_no_finding_at_all(self, tmp_path: Path) -> None:
        (tmp_path / "airline.md").write_text("the policy")
        (tmp_path / "refund.md").write_text(GOOD)
        assert "orphan-cassette" not in [f.rule for f in lint_paths([tmp_path]).findings]


class TestTheRunnerAndLintAgreeOnTheKey:
    """The test #61 says would have caught it: a card in a subdirectory, relocked by the
    runner, then linted. Neither existed while `cards/` was flat, which is why a runner
    that verified clean and a lint that reported `not in the lockfile` could coexist.
    """

    def _workspace(self, tmp_path: Path) -> Path:
        from shutil import copy

        source = Path(__file__).resolve().parent.parent / "cards"
        nested = tmp_path / "airline"
        nested.mkdir()
        copy(source / "basic-economy-return-change.md", nested / "refund.md")
        (nested / "fixtures").mkdir()
        copy(source / "fixtures" / "airline_seed.json", nested / "fixtures")
        (nested / "policy").mkdir()
        copy(source / "policy" / "airline.md", nested / "policy")
        return tmp_path

    def _relock(self, tmp_path: Path) -> Path:
        from typer.testing import CliRunner

        from specdeck.cli import app

        source = Path(__file__).resolve().parent.parent / "cards"
        lock = tmp_path / "spec.lock.toml"
        CliRunner().invoke(
            app,
            [
                "run",
                str(tmp_path / "airline" / "refund.md"),
                "--trace",
                str(source / "traces" / "basic-economy-return-change.otlp.json"),
                "--lock",
                str(lock),
                "--relock",
            ],
        )
        return lock

    def test_the_runner_locks_a_nested_card_under_its_subdirectory(self, tmp_path: Path) -> None:
        lock = self._relock(self._workspace(tmp_path))
        assert "airline/refund.md" in Lockfile.load(lock).cards

    def test_lint_finds_the_card_the_runner_locked(self, tmp_path: Path) -> None:
        workspace = self._workspace(tmp_path)
        lock = self._relock(workspace)
        findings = lint_paths([workspace], lock=Lockfile.load(lock), lock_path=lock).findings
        assert rules(findings, Severity.ERROR) == [], findings


class TestLintSeesAuthoredWiresOnly:
    """Lint reads the card, never the runner's free wires.

    If a built-in reached `_consistency`, every committed card would gain a
    `contradictory-wires` error for a measure it bounds exactly once.
    """

    def _card(self, tmp_path: Path, body: str) -> Path:
        path = tmp_path / "x.md"
        path.write_text(f"# Scenario: x\nThe agent answers.\n{body}")
        return path

    def test_a_card_bounding_a_measure_once_reports_no_contradiction(self, tmp_path: Path) -> None:
        # The runner also bounds `agent_duration_s` here, from the built-in latency wire.
        path = self._card(tmp_path, "\nwire:\n  - latency: under 30s\n")
        assert "contradictory-wires" not in rules(lint_card(path))

    def test_a_card_with_no_wires_at_all_reports_no_wire_finding(self, tmp_path: Path) -> None:
        path = self._card(tmp_path, "")
        assert rules(lint_card(path), Severity.ERROR) == []
