"""The JUnit document, checked against a real parse rather than a substring.

Every case here parses what `to_xml` produced with `ElementTree.fromstring`. A string
comparison would pass on a document no CI system could read, which is the one failure mode
this file exists to catch.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

from specdeck.cell import Cell, Run
from specdeck.ir import Verdict
from specdeck.judge import JudgeResult, JudgeVerdict
from specdeck.junit import to_xml
from specdeck.stats import RunMeasures
from specdeck.tier import Tier

from .test_cell import run_stub


def cell_of(*results: Run, threshold: int = 1, **overrides) -> Cell:
    passes = sum(run.passed for run in results)
    fields: dict = {
        "card_path": "cards/refund.md",
        "title": "Scenario: refund request",
        "runs": len(results),
        "threshold": threshold,
        "passes": passes,
        "credit_mean": 3.0 if passes else None,
        "credit_total": 3,
        "judge_model": "claude-sonnet-5",
        "judge_calls": len(results),
        "results": list(results),
    }
    return Cell(**(fields | overrides))


def parsed(cell: Cell) -> ET.Element:
    return ET.fromstring(to_xml(cell))


def judged(*verdicts: JudgeVerdict, replayed: bool = True) -> JudgeResult:
    return JudgeResult(
        model="claude-sonnet-5", rubric_hash="sha256:x", replayed=replayed, verdicts=list(verdicts)
    )


def failing(**overrides) -> Run:
    fields: dict = {
        "passed": False,
        "wires": [
            Verdict(
                id="never:modify_reservation",
                tier=Tier.GATE,
                weight=0,
                passed=False,
                detail="1 occurrence",
            )
        ],
    }
    return run_stub(**(fields | overrides))


class TestTheMapping:
    def test_the_root_is_testsuites_holding_one_suite_for_the_cell(self) -> None:
        root = parsed(cell_of(run_stub()))
        assert root.tag == "testsuites"
        assert [suite.get("name") for suite in root.findall("testsuite")] == ["cards/refund.md"]

    def test_each_run_is_its_own_testcase(self) -> None:
        # One row per run, so a CI report says which run broke rather than only that one did.
        root = parsed(cell_of(run_stub(), run_stub(), run_stub()))
        assert [case.get("name") for case in root.findall(".//testcase")] == [
            "run 1 of 3",
            "run 2 of 3",
            "run 3 of 3",
        ]

    def test_the_card_is_the_classname_so_the_matrix_can_take_the_name(self) -> None:
        root = parsed(cell_of(run_stub()))
        assert root.find(".//testcase").get("classname") == "cards/refund.md"

    def test_a_passing_cell_carries_no_failure_at_all(self) -> None:
        root = parsed(cell_of(run_stub(), run_stub()))
        assert root.findall(".//failure") == []
        assert root.get("failures") == "0"
        assert root.find("testsuite").get("failures") == "0"


class TestTheCounts:
    def test_the_counts_are_the_tree_not_a_tally_beside_it(self) -> None:
        root = parsed(cell_of(run_stub(), failing(), run_stub(), threshold=2))
        for element in (root, root.find("testsuite")):
            assert element.get("tests") == str(len(element.findall(".//testcase")))
            assert element.get("failures") == str(len(element.findall(".//failure")))

    def test_a_failing_run_is_counted(self) -> None:
        root = parsed(cell_of(run_stub(), failing(), threshold=2))
        assert (root.get("tests"), root.get("failures")) == ("2", "1")

    def test_nothing_is_ever_reported_as_an_error_or_skipped(self) -> None:
        # specdeck has no third verdict. A cell either held its gate or it did not.
        root = parsed(cell_of(failing(), threshold=1))
        assert (root.get("errors"), root.get("skipped")) == ("0", "0")


class TestTheFailure:
    def test_the_message_carries_the_k_of_n_the_row_is_judged_against(self) -> None:
        # A red row beside exit 0 is a tolerated failure, and the row has to say so — the
        # cell passes at k of N, and nobody should have to go and look that up.
        root = parsed(cell_of(run_stub(), failing(), run_stub(), threshold=2))
        message = root.find(".//failure").get("message")
        assert message == "run 2 of 3 failed; the cell needs 2 of 3 and got 2"

    def test_the_text_names_the_wire_that_failed_and_its_detail(self) -> None:
        root = parsed(cell_of(failing(), threshold=1))
        assert root.find(".//failure").text == "never:modify_reservation — 1 occurrence"

    def test_two_failing_runs_each_get_their_own_row_and_reason(self) -> None:
        other = failing(
            wires=[
                Verdict(
                    id="latency", tier=Tier.GATE, weight=0, passed=False, detail="200, under 120"
                )
            ]
        )
        root = parsed(cell_of(failing(), other, threshold=2))
        texts = [failure.text for failure in root.findall(".//failure")]
        assert texts == ["never:modify_reservation — 1 occurrence", "latency — 200, under 120"]

    def test_a_failing_criterion_appears_in_the_smes_own_words(self) -> None:
        run = failing(
            wires=[],
            judged=judged(
                JudgeVerdict(
                    id="prose",
                    text="The agent refuses the change and explains the restriction.",
                    tier=Tier.GATE,
                    weight=0,
                    passed=False,
                    reason="it offered a refund",
                )
            ),
        )
        text = parsed(cell_of(run, threshold=1)).find(".//failure").text
        assert text == (
            "The agent refuses the change and explains the restriction. — it offered a refund"
        )

    def test_a_long_criterion_is_cut_to_one_readable_line(self) -> None:
        run = failing(
            wires=[],
            judged=judged(
                JudgeVerdict(id="prose", text="x" * 200, tier=Tier.GATE, weight=0, passed=False)
            ),
        )
        assert parsed(cell_of(run, threshold=1)).find(".//failure").text.endswith("…")

    def test_a_failing_credit_criterion_is_not_why_the_run_failed(self) -> None:
        # Credit never blocks, so it never explains a failure. It belongs in the summary.
        run = failing(
            judged=judged(
                JudgeVerdict(
                    id="tone",
                    text="tone stays professional",
                    tier=Tier.CREDIT,
                    weight=2,
                    passed=False,
                )
            )
        )
        assert (
            "tone stays professional"
            not in parsed(cell_of(run, threshold=1)).find(".//failure").text
        )


class TestTheSummary:
    def _out(self, cell: Cell) -> str:
        return parsed(cell).find(".//system-out").text

    def test_it_carries_both_numbers_and_never_blends_them(self) -> None:
        out = self._out(cell_of(run_stub(), failing(), run_stub(), threshold=2))
        assert "gate PASS — 2/3 runs (passes at 2)" in out
        assert "credit 3/3 over 2 passing runs" in out

    def test_a_cell_with_no_passing_run_reads_n_a_rather_than_zero(self) -> None:
        assert "credit n/a — no passing run to score, out of 3" in self._out(
            cell_of(failing(), threshold=1)
        )

    def test_it_states_the_cells_own_verdict_beside_the_red_rows(self) -> None:
        # The reason a tolerated failure does not read as a contradiction.
        assert "gate PASS" in self._out(cell_of(run_stub(), failing(), run_stub(), threshold=2))
        assert "gate FAIL" in self._out(cell_of(run_stub(), failing(), threshold=2))

    def test_it_names_the_judge_and_where_the_verdicts_came_from(self) -> None:
        run = run_stub(judged=judged(replayed=True))
        assert "judge claude-sonnet-5 (replayed), 1 call over 1 run" in self._out(cell_of(run))

    def test_a_judge_that_never_ran_is_not_claimed_as_replayed(self) -> None:
        out = self._out(cell_of(failing(), threshold=1, judge_calls=0))
        assert "not called" in out and "replayed" not in out

    def test_a_run_from_a_recorded_trace_names_no_simulator(self) -> None:
        assert "simulator" not in self._out(cell_of(run_stub()))

    def test_a_simulated_run_names_the_model_that_spoke(self) -> None:
        out = self._out(cell_of(run_stub(), simulator_model="claude-sonnet-5"))
        assert "simulator claude-sonnet-5" in out


class TestTimes:
    def test_a_run_carries_its_own_traced_duration(self) -> None:
        run = run_stub(measured=RunMeasures(duration_s=3.87))
        assert parsed(cell_of(run)).find(".//testcase").get("time") == "3.870"

    def test_the_suite_time_is_the_runs_added_up(self) -> None:
        cell = cell_of(
            run_stub(measured=RunMeasures(duration_s=1.5)),
            run_stub(measured=RunMeasures(duration_s=2.25)),
        )
        root = parsed(cell)
        assert root.get("time") == "3.750"
        assert root.find("testsuite").get("time") == "3.750"

    def test_the_suite_is_stamped_with_when_it_was_written(self) -> None:
        assert parsed(cell_of(run_stub())).find("testsuite").get("timestamp")


class TestUntrustedText:
    """A judge's reason and an SME's criterion are text specdeck did not write."""

    def _reason(self, reason: str) -> Run:
        return failing(
            wires=[],
            judged=judged(
                JudgeVerdict(
                    id="prose",
                    text="criterion",
                    tier=Tier.GATE,
                    weight=0,
                    passed=False,
                    reason=reason,
                )
            ),
        )

    def test_xml_metacharacters_survive_a_round_trip_intact(self) -> None:
        run = self._reason("a < b & c, and then ]]> and <![CDATA[")
        text = parsed(cell_of(run, threshold=1)).find(".//failure").text
        assert "a < b & c, and then ]]> and <![CDATA[" in text

    def test_a_control_character_does_not_make_the_document_unparseable(self) -> None:
        # ElementTree escapes &, < and > but emits \x00 verbatim, and the far end then
        # cannot read the file at all — after every judge call has been paid for.
        run = self._reason("the agent said \x00 and \x0b")
        text = parsed(cell_of(run, threshold=1)).find(".//failure").text
        assert "\x00" not in text and "\x0b" not in text
        assert "the agent said  and " in text

    def test_a_control_character_in_an_attribute_is_stripped_too(self) -> None:
        cell = cell_of(failing(), threshold=1, card_path="cards/a\x00.md")
        assert "\x00" not in to_xml(cell)
        assert parsed(cell).find("testsuite").get("name") == "cards/a.md"

    def test_a_tab_and_a_newline_are_legal_and_are_kept(self) -> None:
        run = self._reason("one\ttwo\nthree")
        assert "one\ttwo\nthree" in parsed(cell_of(run, threshold=1)).find(".//failure").text


class TestTheDocumentItself:
    def test_it_declares_itself_xml(self) -> None:
        assert to_xml(cell_of(run_stub())).startswith("<?xml")

    def test_it_ends_in_a_newline(self) -> None:
        assert to_xml(cell_of(run_stub())).endswith("\n")

    def test_it_is_indented_rather_than_one_line(self) -> None:
        assert "\n  <testsuite" in to_xml(cell_of(run_stub()))
