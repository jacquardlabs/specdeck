from pathlib import Path

import pytest

from specdeck.card import parse, parse_text
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


def record(tmp_path: Path, card, traces, verdicts: dict, reasons: dict | None = None) -> None:
    import json

    for one in traces:
        prompt = build_prompt(criteria_of(card), one, policy="")
        Cassette(tmp_path).write(
            prompt,
            model="claude-sonnet-5",
            response=json.dumps({"verdicts": verdicts, "reasons": reasons or {}}),
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
        record(tmp_path, card, traces, {"prose": False, "tone_remains_professional": False})
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
        record(tmp_path, card, traces, {"prose": True, "tone_remains_professional": True})
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
        with pytest.raises(CellError, match=r"5 runs but 3"):
            run_cell(card, [conversation()] * 3, cassettes=tmp_path)

    def test_a_threshold_above_the_run_count_is_rejected(self, tmp_path: Path, card) -> None:
        with pytest.raises(CellError, match=r"threshold"):
            run_cell(card, [conversation()], cassettes=tmp_path, n=1, k=2)


class TestProseOnly:
    def test_a_prose_only_card_runs_judge_only(self, tmp_path: Path) -> None:
        card = parse_text("# Scenario: x\nThe agent answers.\n", path="cards/x.md")
        traces = [conversation()]
        record(tmp_path, card, traces, {"prose": True})
        cell = run_cell(card, traces, cassettes=tmp_path, n=1, k=1)
        assert cell.passed is True
        assert cell.credit_total == 0


class TestCreditAggregation:
    def test_credit_is_the_mean_over_passing_runs_not_a_sum(self, tmp_path: Path, card) -> None:
        # Two passing runs earning different credit: 3 and 2, so the cell reports 2.5.
        # Without this, deleting `/ len(passing)` leaves the whole suite green.
        traces = [conversation(), conversation(tokens=900)]
        record(tmp_path, card, traces, {"prose": True, "tone_remains_professional": True})
        cell = run_cell(card, traces, cassettes=tmp_path, n=2, k=1)
        assert cell.passes == 2
        assert cell.credit_score == 2.5

    def test_a_run_that_failed_a_gate_contributes_nothing_to_the_divisor(
        self, tmp_path: Path, card
    ) -> None:
        traces = [conversation(), conversation(forbidden=True)]
        record(tmp_path, card, traces, {"prose": True, "tone_remains_professional": True})
        cell = run_cell(card, traces, cassettes=tmp_path, n=2, k=1)
        assert cell.credit_score == 3.0  # the one passing run's total, not halved


class TestPolicy:
    def test_a_named_policy_that_does_not_exist_is_an_error(self, tmp_path: Path) -> None:
        card_path = tmp_path / "x.md"
        card_path.write_text("# Scenario: x\ncontext:\n  policy: absent.md\n\nThe agent answers.\n")
        with pytest.raises(CellError, match=r"absent.md"):
            run_cell(parse(card_path), [conversation()], cassettes=tmp_path, n=1, k=1)


class TestVacuousBounds:
    def test_a_token_bound_fails_when_the_trace_reports_no_usage(self, tmp_path: Path) -> None:
        card = parse_text(
            "# Scenario: x\nThe agent answers.\nwire:\n  - response_tokens under 400\n",
            path="cards/x.md",
        )
        silent = trace(
            span("root", Operation.INVOKE_AGENT, parent=None),
            span("chat-0", Operation.CHAT),  # no usage attribute at all
        )
        cell = run_cell(card, [silent], cassettes=tmp_path, n=1, k=1)
        assert cell.passed is False
        assert cell.judge_calls == 0
        assert "reports" in cell.results[0].wires[0].detail
