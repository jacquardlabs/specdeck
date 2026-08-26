from itertools import groupby
from pathlib import Path

import pytest

from specdeck.agent import AgentDescription
from specdeck.introspect import Depth, Introspection, introspect
from specdeck.lint import (
    AGENT_DEF,
    Severity,
    Vocabulary,
    cards_under,
    lint_card,
    lint_paths,
)
from specdeck.lockfile import Lockfile

from .fake_agent import BareAgent, FakeAgent
from .fake_graph import acyclic_graph, nodeless_graph, refund_graph, side_tool_graph

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


class TestDeclaredTraces:
    def _card(self, tmp_path: Path, pattern: str, *names: str) -> Path:
        (tmp_path / "traces").mkdir(exist_ok=True)
        for name in names:
            (tmp_path / "traces" / name).write_text("{}")
        card = tmp_path / "x.md"
        card.write_text(f"# Scenario: x\ncontext:\n  traces: {pattern}\n\nThe agent answers.\n")
        return card

    def test_a_glob_matching_nothing_is_a_dead_path_error(self, tmp_path: Path) -> None:
        # ERROR, not a warning: a card evaluating zero traces passes every wire it has,
        # so a deck of them reports green — the drift a deck exists to catch.
        card = self._card(tmp_path, "traces/*.otlp.json")
        assert "dead-path" in rules(lint_card(card), Severity.ERROR)

    def test_the_message_names_the_pattern_and_where_it_looked(self, tmp_path: Path) -> None:
        card = self._card(tmp_path, "traces/*.otlp.json")
        message = next(f.message for f in lint_card(card) if f.rule == "dead-path")
        assert "traces/*.otlp.json" in message and str(tmp_path) in message

    def test_a_glob_that_matches_reports_nothing(self, tmp_path: Path) -> None:
        card = self._card(tmp_path, "traces/*.otlp.json", "a.otlp.json")
        assert [f for f in lint_card(card) if f.rule == "dead-path"] == []

    def test_a_glob_matching_only_a_directory_is_a_dead_path_error(self, tmp_path: Path) -> None:
        # Lint is the pre-flight for a bad `traces:` value. A directory is not a
        # recording, so this card evaluates zero traces and must not lint clean.
        (tmp_path / "traces" / "archive").mkdir(parents=True)
        card = self._card(tmp_path, "traces/arch*")
        assert "dead-path" in rules(lint_card(card), Severity.ERROR)

    def test_a_card_declaring_no_traces_reports_nothing(self, tmp_path: Path) -> None:
        card = tmp_path / "x.md"
        card.write_text("# Scenario: x\n\nThe agent answers.\n")
        assert [f for f in lint_card(card) if f.rule == "dead-path"] == []

    def test_a_glob_that_escapes_is_a_finding_not_the_end_of_the_lint_run(
        self, tmp_path: Path
    ) -> None:
        # `trace_paths` raises for a glob outside the card's directory. Propagated, one
        # such card aborts the whole deck's lint with zero findings for its neighbours.
        (tmp_path / "outside.json").write_text("{}")
        deck = tmp_path / "deck"
        deck.mkdir()
        (deck / "a.md").write_text(
            "# Scenario: a\ncontext:\n  traces: ../*.json\n\nThe agent answers.\n"
        )
        (deck / "b.md").write_text("# Scenario: b\n\nThe agent answers.\n")
        findings = lint_paths([deck]).findings
        escaping = [f for f in findings if f.rule == "dead-path"]
        assert [f.severity for f in escaping] == [Severity.ERROR]
        assert "outside the card's directory" in escaping[0].message
        # The neighbour was still linted, which is the whole point.
        assert any(f.card.endswith("b.md") for f in findings)

    def test_an_absolute_glob_is_a_finding_too(self, tmp_path: Path) -> None:
        card = tmp_path / "x.md"
        card.write_text("# Scenario: x\ncontext:\n  traces: /etc/*\n\nThe agent answers.\n")
        assert "dead-path" in rules(lint_card(card), Severity.ERROR)

    def test_a_trace_is_never_mistaken_for_a_card(self, tmp_path: Path) -> None:
        # `cards_under` walks `*.md` and traces are `.json`, so they were never
        # candidates — the reason `_referenced` needed no change for `traces:`.
        self._card(tmp_path, "traces/*.otlp.json", "a.otlp.json")
        assert [p.name for p in cards_under([tmp_path])] == ["x.md"]


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


class TestRequestedVersusExecuted:
    def _card(self, tmp_path: Path, *wires: str) -> Path:
        card = tmp_path / "x.md"
        lines = "".join(f"  - {wire}\n" for wire in wires)
        card.write_text(f"# Scenario: x\nThe agent answers.\nwire:\n{lines}")
        return card

    def test_never_requested_with_a_budget_above_zero_contradicts(self, tmp_path: Path) -> None:
        card = self._card(tmp_path, "search: never_requested", "search: at_most 1")
        findings = lint_card(card)
        assert "contradictory-wires" in rules(findings, Severity.ERROR)
        assert any("never_requested" in f.message for f in findings)

    def test_never_requested_beside_never_is_redundant_not_contradictory(
        self, tmp_path: Path
    ) -> None:
        card = self._card(tmp_path, "search: never_requested", "search: never")
        findings = lint_card(card)
        assert rules(findings, Severity.ERROR) == []
        assert "redundant-wires" in rules(findings, Severity.WARNING)

    def test_never_requested_alone_reports_nothing(self, tmp_path: Path) -> None:
        findings = lint_card(self._card(tmp_path, "search: never_requested"))
        assert rules(findings, Severity.ERROR) == []
        assert rules(findings, Severity.WARNING) == []

    def test_both_spellings_of_never_on_one_tool_is_redundant(self, tmp_path: Path) -> None:
        # They compile to one id, so nothing downstream would ever say it twice.
        card = self._card(tmp_path, "search: never", "search: never_executed")
        assert "redundant-wires" in rules(lint_card(card), Severity.WARNING)

    def test_a_misspelled_tool_on_a_never_requested_wire_is_still_unknown(
        self, tmp_path: Path
    ) -> None:
        # Without this the typo compiles to a wire that can never fire and lint says
        # nothing — the exact failure `unknown-tool` exists to catch.
        card = self._card(tmp_path, "cancel_reservtion: never_requested")
        findings = lint_card(card, vocabulary=Vocabulary(tools={"pay_invoice"}))
        assert "unknown-tool" in rules(findings, Severity.ERROR)


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
        # cards/policy/ap.md is markdown, and it is nobody's card.
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
        nested = tmp_path / "payable"
        nested.mkdir()
        copy(source / "over-threshold-second-approval.md", nested / "refund.md")
        (nested / "fixtures").mkdir()
        copy(source / "fixtures" / "data.json", nested / "fixtures")
        (nested / "policy").mkdir()
        copy(source / "policy" / "ap.md", nested / "policy")
        # The card declares its own `traces:`, so a workspace without them is a card whose
        # glob matches nothing — a `dead-path` error about the fixture, not about the key
        # this class is here to check.
        (nested / "traces").mkdir()
        copy(source / "traces" / "over-threshold-second-approval.1.otlp.json", nested / "traces")
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
                str(tmp_path / "payable" / "refund.md"),
                "--trace",
                str(source / "traces" / "over-threshold-second-approval.1.otlp.json"),
                "--lock",
                str(lock),
                "--relock",
            ],
        )
        return lock

    def test_the_runner_locks_a_nested_card_under_its_subdirectory(self, tmp_path: Path) -> None:
        lock = self._relock(self._workspace(tmp_path))
        assert "payable/refund.md" in Lockfile.load(lock).cards

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


class TestAgentDefinition:
    """The two definition-fed obligations, over the whole deck rather than per card."""

    def _deck(self, directory: Path, *wire_blocks: str) -> Path:
        for index, block in enumerate(wire_blocks):
            (directory / f"card{index}.md").write_text(
                f"# Scenario: {index}\nThe agent answers.\n{block}"
            )
        return directory

    def _found(self, directory: Path, target: object, rule: str) -> list:
        result = lint_paths([directory], agent_def=introspect(target, reference="x:y"))
        return [f for f in result.findings if f.rule == rule]

    def test_without_the_flag_both_obligations_report_themselves_skipped(
        self, card_dir: Path
    ) -> None:
        result = lint_paths([card_dir])
        skipped = {f.rule for f in result.findings if f.severity is Severity.SKIPPED}
        assert {"unbounded-cycle", "unreferenced-binding"} <= skipped
        assert result.introspection is None

    def test_without_the_flag_the_default_lint_exit_code_does_not_move(
        self, card_dir: Path
    ) -> None:
        # SKIPPED never counts toward `errors`, so adding a group cannot red an existing deck.
        assert lint_paths([card_dir]).ok is True

    def test_the_skipped_message_names_the_flag_that_would_fix_it(self, card_dir: Path) -> None:
        messages = [
            f.message for f in lint_paths([card_dir]).findings if f.rule == "unbounded-cycle"
        ]
        assert any("--agent-def" in message for message in messages)

    def test_the_introspection_hangs_off_the_result_for_consumers_that_do_not_read_text(
        self, card_dir: Path
    ) -> None:
        result = lint_paths([card_dir], agent_def=introspect(refund_graph(), reference="x:y"))
        assert result.introspection is not None
        assert result.introspection.depth is Depth.TOPOLOGY

    def test_at_tools_depth_the_cycle_rule_is_skipped_and_says_why(self, card_dir: Path) -> None:
        found = self._found(card_dir, FakeAgent([], tools=["a_tool"]), "unbounded-cycle")
        assert [f.severity for f in found] == [Severity.SKIPPED]
        assert "no edges" in found[0].message

    def test_at_tools_depth_the_binding_rule_still_runs_over_the_tool_list(
        self, card_dir: Path
    ) -> None:
        found = self._found(
            card_dir, FakeAgent([], tools=["never_mentioned"]), "unreferenced-binding"
        )
        warnings = [f for f in found if f.severity is Severity.WARNING]
        assert [f.message for f in warnings] and "never_mentioned" in warnings[0].message
        # And it says out loud that hand-offs and HITL points were not visible at all.
        assert any(f.severity is Severity.SKIPPED for f in found)

    def test_at_no_depth_the_binding_rule_is_skipped_rather_than_reporting_nothing_missing(
        self, card_dir: Path
    ) -> None:
        found = self._found(card_dir, BareAgent(), "unreferenced-binding")
        assert [f.severity for f in found] == [Severity.SKIPPED]

    def test_a_cycle_no_wire_touches_is_an_error_and_fails_the_run(self, tmp_path: Path) -> None:
        deck = self._deck(tmp_path, "\nwire:\n  - latency: under 30s\n")
        result = lint_paths([deck], agent_def=introspect(refund_graph(), reference="x:y"))
        assert "unbounded-cycle" in rules(result.findings, Severity.ERROR)
        assert result.ok is False

    def test_a_trace_level_bound_does_not_satisfy_the_cycle_rule(self, tmp_path: Path) -> None:
        """The recorded reading of "bounded", pinned so a later change is a decision.

        Under the looser reading — a `latency` or `response_tokens` bound counts — this
        ERROR is unreachable on any deck that bounds latency, and the card format's own
        example card bounds it. See DECISIONS.md, 2026-08-25.
        """
        deck = self._deck(
            tmp_path,
            "\nwire:\n  - latency: under 120s\n  - response_tokens under 400\n"
            "  - stop_reason: not truncated\n",
        )
        result = lint_paths([deck], agent_def=introspect(refund_graph(), reference="x:y"))
        assert "unbounded-cycle" in rules(result.findings, Severity.ERROR)

    def test_a_wire_naming_a_tool_outside_the_cycle_does_not_clear_it(self, tmp_path: Path) -> None:
        """Only a wire on a tool the cycle can call counts.

        `send_certificate` is a real tool of this graph and a real wire subject, but it
        hangs off a node the loop never reaches, so bounding it says nothing about the loop.
        """
        deck = self._deck(tmp_path, "\nwire:\n  - send_certificate: never\n")
        result = lint_paths([deck], agent_def=introspect(side_tool_graph(), reference="x:y"))
        assert "unbounded-cycle" in rules(result.findings, Severity.ERROR)

    def test_an_at_most_on_a_tool_the_cycle_calls_clears_it(self, tmp_path: Path) -> None:
        deck = self._deck(tmp_path, "\nwire:\n  - pay_invoice: at_most 3\n")
        result = lint_paths([deck], agent_def=introspect(refund_graph(), reference="x:y"))
        assert rules(result.findings, Severity.ERROR) == []

    def test_a_never_on_a_tool_the_cycle_calls_clears_it(self, tmp_path: Path) -> None:
        # A tool that can never be called cannot spin.
        deck = self._deck(tmp_path, "\nwire:\n  - get_invoice: never\n")
        result = lint_paths([deck], agent_def=introspect(refund_graph(), reference="x:y"))
        assert rules(result.findings, Severity.ERROR) == []

    def test_a_wire_naming_the_graph_node_itself_does_not_clear_it(self, tmp_path: Path) -> None:
        """`tools: at_most 3` is not a check. `wires.compile_wire` matches `execute_tool`
        spans by tool name, so a wire on a node compiles to a property no trace can ever
        satisfy — and `unknown-tool` rejects the name besides. See DECISIONS.md,
        2026-08-25."""
        deck = self._deck(tmp_path, "\nwire:\n  - tools: at_most 3\n  - agent: never\n")
        result = lint_paths([deck], agent_def=introspect(refund_graph(), reference="x:y"))
        assert "unbounded-cycle" in rules(result.findings, Severity.ERROR)

    def test_a_cycle_through_nodes_that_bind_no_tool_is_skipped_not_errored(
        self, tmp_path: Path
    ) -> None:
        """No wire can name anything inside it, so an ERROR would instruct the impossible."""
        deck = self._deck(tmp_path, "")
        result = lint_paths([deck], agent_def=introspect(nodeless_graph(), reference="x:y"))
        found = [f for f in result.findings if f.rule == "unbounded-cycle"]
        assert [f.severity for f in found] == [Severity.SKIPPED]
        assert "binds a tool" in found[0].message

    def test_a_declared_cycle_is_checked_even_with_no_edges_to_derive_it_from(
        self, tmp_path: Path
    ) -> None:
        """#21(c): the gate is the data, not the label. A raw-SDK `describe()` that names
        its own loop gets the obligation, rather than a skip claiming none was found."""
        deck = self._deck(tmp_path, "\nwire:\n  - latency: under 30s\n")
        declared = Introspection(
            source="describe()",
            depth=Depth.TOOLS,
            description=AgentDescription(tools=["do_thing"], cycles=[["do_thing"]]),
        )
        result = lint_paths([deck], agent_def=declared)
        assert "unbounded-cycle" in rules(result.findings, Severity.ERROR)
        other = tmp_path / "other"
        other.mkdir()
        cleared = lint_paths(
            [self._deck(other, "\nwire:\n  - do_thing: at_most 2\n")], agent_def=declared
        )
        assert rules(cleared.findings, Severity.ERROR) == []

    def test_an_escalation_whose_follow_up_the_cycle_calls_clears_it(self, tmp_path: Path) -> None:
        deck = self._deck(tmp_path, "\nwire:\n  - pay_invoice: after 3 non_agreement\n")
        result = lint_paths([deck], agent_def=introspect(refund_graph(), reference="x:y"))
        assert rules(result.findings, Severity.ERROR) == []

    def test_the_obligation_is_deck_wide_not_per_card(self, tmp_path: Path) -> None:
        # One card bounds the loop; the card beside it does not, and the deck is clear.
        deck = self._deck(
            tmp_path,
            "\nwire:\n  - latency: under 30s\n",
            "\nwire:\n  - pay_invoice: never\n",
        )
        result = lint_paths([deck], agent_def=introspect(refund_graph(), reference="x:y"))
        assert rules(result.findings, Severity.ERROR) == []

    def test_the_error_names_the_cycle_and_the_tools_that_would_satisfy_it(
        self, tmp_path: Path
    ) -> None:
        deck = self._deck(tmp_path, "")
        result = lint_paths([deck], agent_def=introspect(refund_graph(), reference="x:y"))
        message = next(f.message for f in result.findings if f.rule == "unbounded-cycle")
        assert "agent" in message and "tools" in message
        assert "pay_invoice" in message and "get_invoice" in message
        assert "at_most" in message and "after" in message

    def test_nothing_is_ever_written_to_a_card(self, tmp_path: Path) -> None:
        deck = self._deck(tmp_path, "\nwire:\n  - latency: under 30s\n")
        before = (deck / "card0.md").read_text()
        lint_paths([deck], agent_def=introspect(refund_graph(), reference="x:y"))
        assert (deck / "card0.md").read_text() == before

    def test_an_unreferenced_binding_is_a_warning_never_an_error(self, tmp_path: Path) -> None:
        deck = self._deck(tmp_path, "")
        result = lint_paths([deck], agent_def=introspect(refund_graph(), reference="x:y"))
        bindings = [f for f in result.findings if f.rule == "unreferenced-binding"]
        assert bindings and all(f.severity is Severity.WARNING for f in bindings)

    def test_a_tool_wired_on_any_card_in_the_deck_is_referenced(self, tmp_path: Path) -> None:
        deck = self._deck(tmp_path, "", "\nwire:\n  - pay_invoice: never\n  - tools: never\n")
        result = lint_paths([deck], agent_def=introspect(refund_graph(), reference="x:y"))
        named = " ".join(f.message for f in result.findings if f.rule == "unreferenced-binding")
        assert "pay_invoice" not in named

    def test_an_escalation_target_counts_as_a_reference(self, tmp_path: Path) -> None:
        # The `AfterKThen.then.tool` path. Without it an escalation target reads unwired.
        deck = self._deck(tmp_path, "\nwire:\n  - pay_invoice: after 3 non_agreement\n")
        result = lint_paths([deck], agent_def=introspect(refund_graph(), reference="x:y"))
        named = " ".join(f.message for f in result.findings if f.rule == "unreferenced-binding")
        assert "pay_invoice" not in named

    def test_a_name_appearing_in_a_cards_context_counts_as_a_reference(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / "c.md").write_text(
            '# Scenario: x\ncontext:\n  simulator: "the traveller asks to escalate now"\n'
            "\nThe agent answers.\n"
        )
        result = lint_paths([tmp_path], agent_def=introspect(refund_graph(), reference="x:y"))
        named = " ".join(f.message for f in result.findings if f.rule == "unreferenced-binding")
        assert "'escalate'" not in named

    def test_a_structural_node_is_never_reported_as_an_unreferenced_hand_off(
        self, tmp_path: Path
    ) -> None:
        deck = self._deck(tmp_path, "")
        result = lint_paths([deck], agent_def=introspect(refund_graph(), reference="x:y"))
        named = " ".join(f.message for f in result.findings if f.rule == "unreferenced-binding")
        assert "__start__" not in named and "__end__" not in named

    def test_an_empty_deck_reports_every_binding_and_still_exits_ok(self, tmp_path: Path) -> None:
        result = lint_paths([tmp_path], agent_def=introspect(acyclic_graph(), reference="x:y"))
        assert result.ok is True
        assert len([f for f in result.findings if f.rule == "unreferenced-binding"]) == 3

    def test_no_definition_fed_finding_reads_the_prose_block(self, tmp_path: Path) -> None:
        """A card whose prose is rewritten wholesale produces identical findings here."""
        deck = self._deck(tmp_path, "\nwire:\n  - tools: never\n")
        graph = refund_graph()
        before = [
            f
            for f in lint_paths([deck], agent_def=introspect(graph)).findings
            if f.card == AGENT_DEF
        ]
        (deck / "card0.md").write_text(
            "# Scenario: 0\nescalate pay_invoice get_invoice agent tools\n"
            "\nwire:\n  - tools: never\n"
        )
        after = [
            f
            for f in lint_paths([deck], agent_def=introspect(graph)).findings
            if f.card == AGENT_DEF
        ]
        assert after == before

    def test_the_deck_level_findings_sit_under_one_contiguous_key(self, tmp_path: Path) -> None:
        # `_render_lint` groups without sorting, so a split key opens two blocks.
        deck = self._deck(tmp_path, "")
        findings = lint_paths([deck], agent_def=introspect(refund_graph())).findings
        keys = [key for key, _ in groupby(f.card for f in findings)]
        assert keys.count(AGENT_DEF) == 1

    def test_a_card_that_does_not_compile_does_not_stop_the_obligations(
        self, tmp_path: Path
    ) -> None:
        self._deck(tmp_path, "\nwire:\n  - pay_invoice: never\n")
        (tmp_path / "bad.md").write_text("# Scenario: bad\nx\n\nwire:\n  - a: eventually b\n")
        result = lint_paths([tmp_path], agent_def=introspect(refund_graph()))
        assert "unbounded-cycle" not in rules(result.findings, Severity.ERROR)
