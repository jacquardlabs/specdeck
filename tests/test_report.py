from pathlib import Path

import pytest
from rich.console import Console

from specdeck.card import parse_text
from specdeck.cell import Cell, Run, run_cell
from specdeck.ir import Tier, Verdict
from specdeck.report import render

from .test_cell import CARD, conversation, record


@pytest.fixture
def card():
    return parse_text(CARD, path="cards/refund.md")


def rendered(cell) -> str:
    console = Console(record=True, width=100, force_terminal=False)
    render(cell, console)
    return console.export_text()


class TestPassingCell:
    def test_prints_both_numbers_unblended(self, tmp_path: Path, card) -> None:
        traces = [conversation()]
        record(tmp_path, card, traces, {"prose": True, "tone_remains_professional": True})
        text = rendered(run_cell(card, traces, cassettes=tmp_path, n=1, k=1))
        assert "gate" in text and "PASS" in text and "1/1 runs" in text
        assert "credit   3/3" in text

    def test_shows_the_criterion_in_the_smes_own_words(self, tmp_path: Path, card) -> None:
        traces = [conversation()]
        record(tmp_path, card, traces, {"prose": True, "tone_remains_professional": True})
        text = rendered(run_cell(card, traces, cassettes=tmp_path, n=1, k=1))
        assert "tone remains professional" in text
        assert "tone_remains_professional" not in text


class TestFailingCell:
    def test_details_the_failing_run_not_the_first(self, tmp_path: Path, card) -> None:
        traces = [conversation(), conversation(forbidden=True)]
        record(tmp_path, card, traces, {"prose": True, "tone_remains_professional": True})
        text = rendered(run_cell(card, traces, cassettes=tmp_path, n=2, k=2))
        assert "run 2 of 2" in text
        assert "FAIL" in text

    def test_says_why_no_criteria_appear_when_a_gate_wire_failed(
        self, tmp_path: Path, card
    ) -> None:
        traces = [conversation(forbidden=True)]
        text = rendered(run_cell(card, traces, cassettes=tmp_path, n=1, k=1))
        assert "criteria not reached" in text

    def test_credit_reads_n_a_rather_than_zero_with_no_passing_run(
        self, tmp_path: Path, card
    ) -> None:
        traces = [conversation(forbidden=True)]
        text = rendered(run_cell(card, traces, cassettes=tmp_path, n=1, k=1))
        assert "n/a" in text and "credit   0" not in text

    def test_does_not_claim_replayed_when_the_judge_never_ran(self, tmp_path: Path, card) -> None:
        traces = [conversation(forbidden=True)]
        text = rendered(run_cell(card, traces, cassettes=tmp_path, n=1, k=1))
        assert "not called" in text
        assert "replayed" not in text


class TestUntrustedText:
    def test_a_judge_reason_containing_markup_does_not_break_the_report(
        self, tmp_path: Path, card
    ) -> None:
        # rich would raise MarkupError on the unmatched closing tag and discard the
        # whole report, after every wire and judge call has already been paid for.
        traces = [conversation()]
        record(
            tmp_path,
            card,
            traces,
            {"prose": True, "tone_remains_professional": True},
            reasons={"prose": "the agent wrote to [/tmp] and said [bold] things"},
        )
        text = rendered(run_cell(card, traces, cassettes=tmp_path, n=1, k=1))
        assert "[/tmp]" in text


class TestWireColumn:
    """The label column is measured, not assumed. See #71."""

    LONG = "after_3_non_agreement:transfer_to_human_agents"

    def _cell(self, ids: list[str]) -> Cell:
        wires = [
            Verdict(id=i, tier=Tier.GATE, weight=0, passed=False, detail="k=3 reached") for i in ids
        ]
        return Cell(
            card_path="cards/escalation.md",
            title="escalate after repeated refusal",
            runs=1,
            threshold=1,
            passes=0,
            credit_mean=None,
            credit_total=0,
            judge_model="claude-sonnet-5",
            judge_calls=0,
            results=[Run(passed=False, wires=wires, judged=None, credit_earned=0)],
        )

    def test_an_id_past_the_old_fixed_column_keeps_its_separator(self) -> None:
        # 45 characters against a hard-coded 34 printed `...human_agentsk=3 reached`.
        text = rendered(self._cell([self.LONG, "latency"]))
        assert f"{self.LONG}  k=3 reached" in text

    def test_short_ids_stay_aligned_with_each_other(self) -> None:
        text = rendered(self._cell(["latency", "never:cancel_reservation"]))
        columns = [line.index("k=3 reached") for line in text.splitlines() if "k=3" in line]
        assert len(set(columns)) == 1
