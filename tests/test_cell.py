from pathlib import Path

import pytest

from specdeck.card import parse_text
from specdeck.cell import DEFAULT_K, DEFAULT_N, CellError, run_cell
from specdeck.judge import Cassette, build_prompt, criteria_of
from specdeck.trace import GenAI, Operation, SpanEvent

from .test_trace import span, trace

CARD = """\
# Scenario: refund request on basic economy
context:
  simulator: "frustrated customer"

The agent refuses the change and explains the restriction.

wire:
  - modify_reservation: never
  - latency: under 120s

credit:
  - "tone remains professional": 2
  - wire: response_tokens under 400: 1
"""


def conversation(*, forbidden: bool = False, tokens: int = 120, seconds: float = 5.0):
    chat = span("chat-0", Operation.CHAT, **{GenAI.USAGE_OUTPUT_TOKENS: tokens})
    chat.events.append(
        SpanEvent(
            name="details",
            attributes={GenAI.OUTPUT_MESSAGES: [{"role": "assistant", "content": "I cannot"}]},
        )
    )
    spans = [span("root", Operation.INVOKE_AGENT, parent=None, duration=seconds), chat]
    if forbidden:
        tool = span("tool-0", Operation.EXECUTE_TOOL, offset=1.0)
        tool.attributes[GenAI.TOOL_NAME] = "modify_reservation"
        spans.append(tool)
    return trace(*spans)


@pytest.fixture
def card():
    return parse_text(CARD, path="cards/refund.md")


def record(tmp_path: Path, card, traces, verdicts: dict) -> None:
    import json

    for one in traces:
        prompt = build_prompt(criteria_of(card), one, policy="")
        Cassette(tmp_path).write(
            prompt, model="claude-sonnet-5", response=json.dumps({"verdicts": verdicts})
        )


class TestExecutionOrder:
    def test_a_failed_gate_wire_costs_no_judge_call(self, tmp_path: Path, card) -> None:
        # No cassette is recorded: if the judge were called, this would raise.
        cell = run_cell(card, [conversation(forbidden=True)], cassettes=tmp_path, n=1, k=1)
        assert cell.judge_calls == 0
        assert cell.passed is False

    def test_the_judge_runs_when_every_gate_wire_holds(self, tmp_path: Path, card) -> None:
        traces = [conversation()]
        record(tmp_path, card, traces, {"prose": True, "tone_remains_professional": True})
        cell = run_cell(card, traces, cassettes=tmp_path, n=1, k=1)
        assert cell.judge_calls == 1
        assert cell.passed is True

    def test_a_failed_prose_criterion_fails_the_run(self, tmp_path: Path, card) -> None:
        traces = [conversation()]
        record(tmp_path, card, traces, {"prose": False})
        assert run_cell(card, traces, cassettes=tmp_path, n=1, k=1).passed is False


class TestTwoNumbers:
    def test_gate_pass_rate_counts_runs_not_checks(self, tmp_path: Path, card) -> None:
        traces = [conversation(), conversation(seconds=6.0), conversation(forbidden=True)]
        record(tmp_path, card, traces, {"prose": True, "tone_remains_professional": True})
        cell = run_cell(card, traces, cassettes=tmp_path, n=3, k=2)
        assert (cell.passes, cell.runs) == (2, 3)
        assert cell.passed is True

    def test_the_cell_fails_below_the_threshold(self, tmp_path: Path, card) -> None:
        traces = [conversation(), conversation(forbidden=True), conversation(forbidden=True)]
        record(tmp_path, card, traces, {"prose": True})
        assert run_cell(card, traces, cassettes=tmp_path, n=3, k=2).passed is False

    def test_credit_is_scored_over_passing_runs_only(self, tmp_path: Path, card) -> None:
        traces = [conversation(), conversation(forbidden=True)]
        record(tmp_path, card, traces, {"prose": True, "tone_remains_professional": True})
        cell = run_cell(card, traces, cassettes=tmp_path, n=2, k=1)
        # Both credit checks earn on the one passing run: the criterion (2) and the wire (1).
        assert (cell.credit_score, cell.credit_total) == (3.0, 3)

    def test_credit_never_offsets_a_failed_gate(self, tmp_path: Path, card) -> None:
        traces = [conversation(forbidden=True)]
        record(tmp_path, card, traces, {"prose": True, "tone_remains_professional": True})
        cell = run_cell(card, traces, cassettes=tmp_path, n=1, k=1)
        assert cell.passed is False
        assert cell.credit_score is None  # no passing run to score

    def test_a_credit_wire_that_fails_still_leaves_the_gate_alone(
        self, tmp_path: Path, card
    ) -> None:
        traces = [conversation(tokens=900)]
        record(tmp_path, card, traces, {"prose": True, "tone_remains_professional": True})
        cell = run_cell(card, traces, cassettes=tmp_path, n=1, k=1)
        assert cell.passed is True
        assert (cell.credit_score, cell.credit_total) == (2.0, 3)


class TestThresholds:
    def test_the_locked_defaults_are_five_and_four(self) -> None:
        assert (DEFAULT_N, DEFAULT_K) == (5, 4)

    def test_too_few_traces_says_how_many_the_cell_needs(self, tmp_path: Path, card) -> None:
        with pytest.raises(CellError, match="5 runs but 3"):
            run_cell(card, [conversation()] * 3, cassettes=tmp_path)

    def test_a_threshold_above_the_run_count_is_rejected(self, tmp_path: Path, card) -> None:
        with pytest.raises(CellError, match="threshold"):
            run_cell(card, [conversation()], cassettes=tmp_path, n=1, k=2)


class TestProseOnly:
    def test_a_prose_only_card_runs_judge_only(self, tmp_path: Path) -> None:
        card = parse_text("# Scenario: x\nThe agent answers.\n", path="cards/x.md")
        traces = [conversation()]
        record(tmp_path, card, traces, {"prose": True})
        cell = run_cell(card, traces, cassettes=tmp_path, n=1, k=1)
        assert cell.passed is True
        assert cell.credit_total == 0
