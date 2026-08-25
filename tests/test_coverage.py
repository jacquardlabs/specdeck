"""Coverage denominators: the clause inventory, and the tools nothing touches."""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from specdeck import coverage as coverage_module
from specdeck.card import parse, parse_text
from specdeck.coverage import (
    CoverageError,
    collect,
    extract_clauses,
    policy_coverage,
    vocabulary_coverage,
)
from specdeck.lint import Vocabulary, cards_under

REPO = Path(__file__).resolve().parent.parent
AIRLINE = REPO / "cards" / "policy" / "airline.md"

CARD = """\
# Scenario: refund
context:
  policy: policy.md

The agent refuses.

wire:
  - cancel_reservation: never
  - transfer_to_human_agents: after 3 non_agreement
"""


def deck() -> list:
    """Every committed card, through the one discovery rule."""
    return [parse(path) for path in cards_under([REPO / "cards"])]


class TestTheRealPolicyDocument:
    """A golden against the file in the repo, on tests/test_docs.py's precedent."""

    def clauses(self) -> list:
        return extract_clauses(AIRLINE.read_text(), document=str(AIRLINE))

    def test_every_column_zero_bullet_is_one_clause(self) -> None:
        assert len(self.clauses()) == 26

    def test_the_sections_come_out_in_document_order_with_their_counts(self) -> None:
        found = [(c.section, c.id.split("/")[0]) for c in self.clauses()]
        counts: dict[str, int] = {}
        for section, _ in found:
            counts[section] = counts.get(section, 0) + 1
        assert list(counts.items()) == [
            ("Airline Agent Policy", 5),
            ("Domain Basic", 3),
            ("Book flight", 5),
            ("Modify flight", 6),
            ("Cancel flight", 4),
            ("Refund", 3),
        ]

    def test_nested_bullets_qualify_their_parent_and_are_not_clauses(self) -> None:
        # airline.md lines 24-26 nest under the flight-status clause opened at line 23.
        parent = next(c for c in self.clauses() if c.line == 23)
        assert "available" in parent.text
        assert "delayed" in parent.text
        assert "flying" in parent.text
        assert [c for c in self.clauses() if c.line in (24, 25, 26)] == []

    def test_column_zero_prose_is_never_a_clause(self) -> None:
        text = " ".join(c.text for c in self.clauses())
        assert "The current time is" not in text
        assert "As an airline agent" not in text

    def test_an_id_is_the_section_slug_and_a_one_based_index(self) -> None:
        assert next(c for c in self.clauses() if c.line == 44).id == "modify_flight/2"
        assert self.clauses()[0].id == "airline_agent_policy/1"

    def test_no_two_clauses_share_an_id(self) -> None:
        ids = [c.id for c in self.clauses()]
        assert len(set(ids)) == len(ids)


class TestTheExtractionRule:
    def test_every_list_marker_starts_a_clause(self) -> None:
        text = "- dash\n* star\n+ plus\n1. dotted\n2) parens\n"
        assert [c.text for c in extract_clauses(text)] == [
            "dash",
            "star",
            "plus",
            "dotted",
            "parens",
        ]

    def test_a_wrapped_continuation_line_joins_the_clause_above_it(self) -> None:
        found = extract_clauses("- the agent must\n  keep going\n")
        assert len(found) == 1
        assert found[0].text == "the agent must\n  keep going"

    def test_a_blank_line_followed_by_an_indented_line_stays_inside_the_clause(self) -> None:
        found = extract_clauses("- one\n\n  still one\n")
        assert len(found) == 1
        assert "still one" in found[0].text

    def test_a_blank_line_followed_by_a_column_zero_paragraph_ends_it(self) -> None:
        found = extract_clauses("- one\n\nA paragraph.\n")
        assert [c.text for c in found] == ["one"]

    def test_a_heading_ends_the_clause_and_opens_a_section(self) -> None:
        found = extract_clauses("- before\n## Later\n- after\n")
        assert [c.id for c in found] == ["preamble/1", "later/1"]

    def test_clauses_before_any_heading_sit_in_the_preamble(self) -> None:
        assert extract_clauses("- first\n")[0].id == "preamble/1"

    def test_two_headings_that_slugify_alike_do_not_collide(self) -> None:
        found = extract_clauses("## The rules!\n- a\n## The rules?\n- b\n")
        assert [c.id for c in found] == ["the_rules/1", "the_rules_2/1"]

    def test_the_line_number_is_where_the_clause_started(self) -> None:
        found = extract_clauses("intro\n\n- the clause\n  wrapped\n")
        assert found[0].line == 3

    def test_a_document_of_prose_yields_nothing(self) -> None:
        assert extract_clauses("Just words.\n\nMore words.\n") == []

    def test_an_indented_bullet_with_no_parent_is_not_a_clause(self) -> None:
        assert extract_clauses("A paragraph.\n  - orphan\n") == []


class TestPolicyCoverage:
    def _card(self, tmp_path: Path, body: str, name: str = "card.md") -> Path:
        path = tmp_path / name
        path.write_text(body)
        return path

    def test_the_committed_deck_groups_five_cards_onto_one_document(self) -> None:
        found = policy_coverage(deck())
        assert len(found) == 1
        assert Path(found[0].path).name == "airline.md"
        assert len(found[0].cards) == 5
        assert len(found[0].clauses) == 26

    def test_a_card_with_no_policy_contributes_nothing(self, tmp_path: Path) -> None:
        assert policy_coverage([parse_text("# Scenario: x\nThe agent answers.\n")]) == []

    def test_a_policy_path_that_does_not_exist_is_a_user_error_naming_the_card(
        self, tmp_path: Path
    ) -> None:
        # Silently reporting "no clauses" would hide what lint's `dead-path` rule diagnoses.
        card = self._card(tmp_path, CARD)
        with pytest.raises(CoverageError, match=r"card\.md"):
            policy_coverage([parse(card)])

    def test_a_document_with_no_list_items_reports_itself_blind_not_zero(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / "policy.md").write_text("Just prose, no obligations.\n")
        found = policy_coverage([parse(self._card(tmp_path, CARD))])
        assert found[0].clauses == []
        assert found[0].blind
        assert "0%" not in found[0].blind

    def test_a_policy_document_no_card_names_is_reported(self, tmp_path: Path) -> None:
        """The one deterministic uncovered signal this table can give today."""
        (tmp_path / "policy.md").write_text("- one obligation\n")
        (tmp_path / "orphan.md").write_text("- an obligation nobody points at\n")
        found = collect([parse(self._card(tmp_path, CARD))], vocabulary=None, traces=[])
        orphan = next(one for one in found.policy if Path(one.path).name == "orphan.md")
        assert orphan.cards == []
        assert len(orphan.clauses) == 1

    def test_a_card_is_never_reported_as_an_unnamed_policy(self, tmp_path: Path) -> None:
        (tmp_path / "policy.md").write_text("- one obligation\n")
        card = self._card(tmp_path, CARD)
        found = collect([parse(card)], vocabulary=None, traces=[])
        assert str(card) not in [one.path for one in found.policy]


class TestVocabularyCoverage:
    def test_a_tool_named_by_a_wire_lists_the_cards_that_wire_it(self) -> None:
        table = vocabulary_coverage(deck(), Vocabulary(tools={"cancel_reservation"}), [])
        assert table.rows[0].wired_by
        assert all(path.endswith(".md") for path in table.rows[0].wired_by)

    def test_an_escalation_target_counts_as_wired(self) -> None:
        table = vocabulary_coverage(deck(), Vocabulary(tools={"transfer_to_human_agents"}), [])
        assert table.rows[0].wired_by

    def test_a_tool_with_neither_a_wire_nor_a_run_is_the_uncovered_row(self) -> None:
        table = vocabulary_coverage(deck(), Vocabulary(tools={"list_all_airports"}), [])
        assert table.rows[0].wired_by == []
        assert table.rows[0].exercised is False

    def test_a_tool_a_trace_executed_is_exercised_even_when_no_card_wires_it(self) -> None:
        traces = [_recorded()]
        table = vocabulary_coverage(deck(), Vocabulary(tools={"get_reservation_details"}), traces)
        assert table.rows[0].exercised is True
        assert table.rows[0].wired_by == []

    def test_the_denominator_is_the_declaration_not_what_the_cards_happen_to_name(self) -> None:
        table = vocabulary_coverage(deck(), Vocabulary(tools={"never_declared_anywhere"}), [])
        assert [row.tool for row in table.rows] == ["never_declared_anywhere"]

    def test_no_vocabulary_is_blindness_not_zero_of_zero(self) -> None:
        table = vocabulary_coverage(deck(), None, [])
        assert table.rows == []
        assert "--vocabulary" in table.blind

    def test_no_traces_says_exercising_was_not_checked(self) -> None:
        table = vocabulary_coverage(deck(), Vocabulary(tools={"calculate"}), [])
        assert table.traces_blind
        assert table.traces_seen == 0
        assert table.rows[0].exercised is False

    def test_a_card_whose_wires_do_not_compile_is_skipped_without_raising(
        self, tmp_path: Path
    ) -> None:
        broken = tmp_path / "broken.md"
        broken.write_text("# Scenario: x\nThe agent answers.\n\nwire:\n  - a: eventually b\n")
        cards = [*deck(), parse(broken)]
        table = vocabulary_coverage(cards, Vocabulary(tools={"cancel_reservation"}), [])
        assert table.rows[0].wired_by


class TestCoverageCanNeverGate:
    def test_the_module_imports_no_severity_and_no_finding(self) -> None:
        """A coverage figure that acquires a severity acquires an exit code.

        The names appear in the module docstring, which is where the rule is written down;
        what must not exist is a binding for either of them.
        """
        code = "".join(
            line
            for line in inspect.getsource(coverage_module).splitlines(keepends=True)
            if line.startswith(("import ", "from "))
        )
        assert "Severity" not in code
        assert "Finding" not in code
        assert not hasattr(coverage_module, "Severity")
        assert not hasattr(coverage_module, "Finding")

    def test_no_model_here_exposes_anything_a_caller_could_route_on(self) -> None:
        from specdeck.coverage import Coverage, PolicyDocument, ToolRow, VocabularyTable

        for model in (Coverage, PolicyDocument, ToolRow, VocabularyTable):
            names = set(dir(model))
            assert not names & {"passed", "ok", "failed", "errors"}


def _recorded():
    """The committed OTLP export, which executes `get_reservation_details`."""
    from specdeck.traceio import load_trace

    return load_trace(REPO / "cards" / "traces" / "basic-economy-return-change.otlp.json")
