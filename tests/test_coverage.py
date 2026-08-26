"""Coverage denominators: the clause inventory, and the tools nothing touches."""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from specdeck import coverage as coverage_module
from specdeck.card import parse, parse_text
from specdeck.coverage import (
    CoverageError,
    PathCoverage,
    collect,
    extract_clauses,
    path_coverage,
    policy_coverage,
    vocabulary_coverage,
)
from specdeck.introspect import Depth, introspect
from specdeck.lint import Vocabulary, cards_under
from specdeck.trace import GenAI, Operation, Trace

from .fake_agent import BareAgent, FakeAgent
from .test_trace import span, trace

REPO = Path(__file__).resolve().parent.parent
POLICY = REPO / "cards" / "policy" / "ap.md"

CARD = """\
# Scenario: refund
context:
  policy: policy.md

The agent refuses.

wire:
  - pay_invoice: never
  - escalate_to_human: after 3 non_agreement
"""


def deck() -> list:
    """Every committed card, through the one discovery rule."""
    return [parse(path) for path in cards_under([REPO / "cards"])]


class TestTheRealPolicyDocument:
    """A golden against the file in the repo, on tests/test_docs.py's precedent."""

    def clauses(self) -> list:
        return extract_clauses(POLICY.read_text(), document=str(POLICY))

    def test_every_column_zero_bullet_is_one_clause(self) -> None:
        assert len(self.clauses()) == 13

    def test_the_sections_come_out_in_document_order_with_their_counts(self) -> None:
        found = [(c.section, c.id.split("/")[0]) for c in self.clauses()]
        counts: dict[str, int] = {}
        for section, _ in found:
            counts[section] = counts.get(section, 0) + 1
        assert list(counts.items()) == [
            ("Before paying anything", 4),
            ("The second-approver threshold", 3),
            ("Bank details", 3),
            ("Escalating", 3),
        ]

    def test_a_wrapped_bullet_is_one_clause_and_not_several(self) -> None:
        """Continuation lines belong to the bullet that opened, not to themselves."""
        clause = next(c for c in self.clauses() if c.line == 13)
        assert "within $50" in clause.text
        assert "partial amount" in clause.text, "the wrapped tail is part of the clause"
        assert [c for c in self.clauses() if c.line in (14, 15)] == []

    def test_column_zero_prose_is_never_a_clause(self) -> None:
        text = " ".join(c.text for c in self.clauses())
        assert "The current date is" not in text
        assert "You are the accounts payable assistant" not in text

    def test_an_id_is_the_section_slug_and_a_one_based_index(self) -> None:
        assert next(c for c in self.clauses() if c.line == 24).id == (
            "the_second_approver_threshold/1"
        )
        assert self.clauses()[0].id == "before_paying_anything/1"

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
        assert Path(found[0].path).name == "ap.md"
        assert len(found[0].cards) == 5
        assert len(found[0].clauses) == 13

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
        table = vocabulary_coverage(deck(), Vocabulary(tools={"pay_invoice"}), [])
        assert table.rows[0].wired_by
        assert all(path.endswith(".md") for path in table.rows[0].wired_by)

    def test_an_escalation_target_counts_as_wired(self) -> None:
        table = vocabulary_coverage(deck(), Vocabulary(tools={"escalate_to_human"}), [])
        assert table.rows[0].wired_by

    def test_a_tool_with_neither_a_wire_nor_a_run_is_the_uncovered_row(self) -> None:
        table = vocabulary_coverage(deck(), Vocabulary(tools={"list_all_airports"}), [])
        assert table.rows[0].wired_by == []
        assert table.rows[0].exercised is False

    def test_a_tool_a_trace_executed_is_exercised_even_when_no_card_wires_it(self) -> None:
        traces = [_recorded()]
        table = vocabulary_coverage(deck(), Vocabulary(tools={"get_invoice"}), traces)
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
        table = vocabulary_coverage(cards, Vocabulary(tools={"pay_invoice"}), [])
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
    """A committed OTLP export, which executes `get_invoice`."""
    from specdeck.traceio import load_trace

    return load_trace(REPO / "cards" / "traces" / "over-threshold-second-approval.1.otlp.json")


class TestPathCoverage:
    """The denominator comes from introspection, never from the trace."""

    def _trace(self, *tools: str) -> Trace:
        """A run that executed these tools in this order, all under one chat span.

        Parented the way `loop` parents them — every tool under the same chat — so a
        reading that used the span tree instead of temporal order would find no adjacency.
        """
        spans = [
            span("root", Operation.INVOKE_AGENT, parent=None, duration=60.0),
            span("chat-0", Operation.CHAT),
        ]
        spans += [
            span(f"tool-{index}", Operation.EXECUTE_TOOL, parent="chat-0", offset=1.0 + index)
            for index in range(len(tools))
        ]
        for one, name in zip(spans[2:], tools, strict=True):
            one.attributes[GenAI.TOOL_NAME] = name
        return trace(*spans)

    def test_a_recorded_trace_run_has_no_denominator_and_says_so(self) -> None:
        found = path_coverage(None, [self._trace("a")])
        assert found.depth is Depth.NONE
        assert found.total == 0
        assert "recorded traces" in found.blind

    def test_an_adapter_with_no_describe_reports_no_depth(self) -> None:
        found = path_coverage(introspect(BareAgent()), [])
        assert found.depth is Depth.NONE
        assert found.blind

    def test_tools_depth_states_the_graph_was_not_seen_rather_than_reporting_zero(self) -> None:
        found = path_coverage(introspect(FakeAgent([], tools=["a", "b"])), [])
        assert found.depth is Depth.TOOLS
        assert found.edges == []
        assert "'tools' depth" in found.blind

    def test_one_declared_edge_of_two_traversed_is_the_headline(self) -> None:
        agent = FakeAgent([], edges=[("a", "b"), ("b", "c")])
        found = path_coverage(introspect(agent), [self._trace("a", "b")])
        assert found.depth is Depth.TOPOLOGY
        assert found.covered == 1
        assert found.total == 2
        assert found.missed == [("b", "c")]

    def test_coverage_is_the_union_across_runs_not_a_per_run_intersection(self) -> None:
        agent = FakeAgent([], edges=[("a", "b"), ("b", "c")])
        traces = [self._trace("a", "b"), self._trace("x"), self._trace("b", "c")]
        assert path_coverage(introspect(agent), traces).covered == 2

    def test_an_observed_transition_the_graph_never_declared_does_not_inflate_anything(
        self,
    ) -> None:
        agent = FakeAgent([], edges=[("a", "b")])
        found = path_coverage(introspect(agent), [self._trace("q", "r", "a", "b")])
        assert found.total == 1
        assert found.hit == [("a", "b")]

    def test_the_evidence_is_temporal_order_never_the_span_tree(self) -> None:
        """`loop` parents every tool span under the last chat span, so the tree gives no
        tool-to-tool adjacency at all — reading it would find nothing."""
        agent = FakeAgent([], edges=[("a", "b")])
        trace = self._trace("a", "b")
        parents = {span.parent_span_id for span in trace.of(Operation.EXECUTE_TOOL)}
        assert len(parents) == 1  # one shared parent, so adjacency must come from time
        assert path_coverage(introspect(agent), [trace]).covered == 1

    def test_one_tool_span_is_not_an_edge(self) -> None:
        agent = FakeAgent([], edges=[("a", "b")])
        assert path_coverage(introspect(agent), [self._trace("a")]).covered == 0

    def test_a_trace_with_no_tool_spans_yields_nothing(self) -> None:
        agent = FakeAgent([], edges=[("a", "b")])
        assert path_coverage(introspect(agent), [self._trace()]).covered == 0

    def test_it_round_trips_as_json_with_its_tuples_intact(self) -> None:
        agent = FakeAgent([], edges=[("a", "b")])
        found = path_coverage(introspect(agent), [self._trace("a", "b")])
        assert PathCoverage.model_validate_json(found.model_dump_json()) == found

    def test_a_full_graph_still_reports_a_figure(self) -> None:
        # "Fully covered" must be distinguishable from "not measured".
        agent = FakeAgent([], edges=[("a", "b")])
        found = path_coverage(introspect(agent), [self._trace("a", "b")])
        assert found.total == 1
        assert found.missed == []
        assert found.blind == ""
