from pathlib import Path

import pytest

from specdeck.builtin import BuiltinConfig
from specdeck.card import parse, parse_text
from specdeck.cell import DEFAULT_K, DEFAULT_N, CellError, Run, run_cell
from specdeck.judge import Cassette, build_prompt, criteria_of
from specdeck.stats import RunMeasures
from specdeck.trace import GenAI, Operation, SpanEvent
from specdeck.waste import Kind

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


def run_stub(**overrides) -> Run:
    """A `Run` built by hand, for tests about the report rather than about running a cell.

    One constructor, so a field added to `Run` is filled here instead of at every site that
    hand-builds one. `measured` has no default on the model itself on purpose.
    """
    fields: dict = {
        "passed": True,
        "wires": [],
        "judged": None,
        "credit_earned": 0,
        "measured": RunMeasures.nothing(),
    }
    return Run(**(fields | overrides))


def retries(one, *, tokens: int | None = 120):
    """The same failing tool call twice, appended to a conversation.

    `get_reservation_details`, not the card's forbidden `modify_reservation`: the point is
    a run that wastes tokens while every gate still holds.
    """
    spans = list(one.spans)
    for index in (0, 1):
        call = span(f"retry-{index}", Operation.EXECUTE_TOOL, offset=2.0 + index)
        call.attributes[GenAI.TOOL_CALL_ARGUMENTS] = '{"id": "abc"}'
        call.attributes[GenAI.TOOL_CALL_RESULT] = "Error: reservation not found"
        spans.append(call)
    if tokens is None:
        spans = [s for s in spans if GenAI.USAGE_OUTPUT_TOKENS not in s.attributes] + [
            span("chat-1", Operation.CHAT, offset=0.5)
        ]
    return trace(*spans)


@pytest.fixture
def card():
    return parse_text(CARD, path="cards/refund.md")


def record(tmp_path: Path, card, traces, verdicts: dict, reasons: dict | None = None) -> None:
    import json

    for one in traces:
        prompt = build_prompt(criteria_of(card), one, policy="")
        Cassette(tmp_path, slug=card.slug).write(
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
        assert (cell.credit_mean, cell.credit_total) == (3.0, 3)

    def test_credit_never_offsets_a_failed_gate(self, tmp_path: Path, card) -> None:
        traces = [conversation(forbidden=True)]
        record(tmp_path, card, traces, {"prose": True, "tone_remains_professional": True})
        cell = run_cell(card, traces, cassettes=tmp_path, n=1, k=1)
        assert cell.passed is False
        assert cell.credit_mean is None  # no passing run to score

    def test_a_credit_wire_that_fails_still_leaves_the_gate_alone(
        self, tmp_path: Path, card
    ) -> None:
        traces = [conversation(tokens=900)]
        record(tmp_path, card, traces, {"prose": True, "tone_remains_professional": True})
        cell = run_cell(card, traces, cassettes=tmp_path, n=1, k=1)
        assert cell.passed is True
        assert (cell.credit_mean, cell.credit_total) == (2.0, 3)


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
        assert cell.credit_mean == 2.5

    def test_a_run_that_failed_a_gate_contributes_nothing_to_the_divisor(
        self, tmp_path: Path, card
    ) -> None:
        traces = [conversation(), conversation(forbidden=True)]
        record(tmp_path, card, traces, {"prose": True, "tone_remains_professional": True})
        cell = run_cell(card, traces, cassettes=tmp_path, n=2, k=1)
        assert cell.credit_mean == 3.0  # the one passing run's total, not halved


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


class TestConcurrency:
    def test_runs_are_bounded_not_unbounded(self, tmp_path: Path, card) -> None:
        # A gather over a provider x prompt matrix is a rate-limit incident, not
        # parallelism, so the cell caps how many runs are in flight.
        import specdeck.cell as cell_module

        traces = [conversation() for _ in range(6)]
        record(tmp_path, card, traces, {"prose": True, "tone_remains_professional": True})
        peak = 0
        live = 0
        original = cell_module._run

        async def counting(*args, **kwargs):
            nonlocal peak, live
            live += 1
            peak = max(peak, live)
            try:
                return await original(*args, **kwargs)
            finally:
                live -= 1

        cell_module._run = counting
        try:
            run_cell(card, traces, cassettes=tmp_path, n=6, k=1, concurrency=2)
        finally:
            cell_module._run = original
        assert peak <= 2

    def test_every_run_still_completes_under_the_bound(self, tmp_path: Path, card) -> None:
        traces = [conversation() for _ in range(6)]
        record(tmp_path, card, traces, {"prose": True, "tone_remains_professional": True})
        cell = run_cell(card, traces, cassettes=tmp_path, n=6, k=6, concurrency=2)
        assert (cell.passes, len(cell.results)) == (6, 6)

    def test_the_gate_short_circuit_survives_overlapping_runs(self, tmp_path: Path, card) -> None:
        # No cassette for the failing traces: if concurrency broke the ordering and the
        # judge were called for them, this would raise.
        traces = [conversation(), conversation(forbidden=True), conversation(forbidden=True)]
        record(tmp_path, card, traces, {"prose": True, "tone_remains_professional": True})
        cell = run_cell(card, traces, cassettes=tmp_path, n=3, k=1, concurrency=3)
        assert cell.judge_calls == 1

    def test_results_stay_in_the_order_the_traces_were_given(self, tmp_path: Path, card) -> None:
        # The report details "the first failing run"; that is only meaningful if the
        # results keep the caller's order rather than completion order.
        traces = [conversation(), conversation(forbidden=True), conversation()]
        record(tmp_path, card, traces, {"prose": True, "tone_remains_professional": True})
        cell = run_cell(card, traces, cassettes=tmp_path, n=3, k=1, concurrency=3)
        assert [r.passed for r in cell.results] == [True, False, True]


class TestEveryRunIsMeasured:
    def test_a_run_carries_the_root_spans_duration(self, tmp_path: Path, card) -> None:
        traces = [conversation(seconds=6.0)]
        record(tmp_path, card, traces, {"prose": True, "tone_remains_professional": True})
        cell = run_cell(card, traces, cassettes=tmp_path, n=1, k=1)
        assert cell.results[0].measured.duration_s == 6.0

    def test_a_run_that_failed_a_gate_wire_is_measured_all_the_same(
        self, tmp_path: Path, card
    ) -> None:
        # It took just as long and burned just as many tokens; dropping it would make the
        # cell's latency and cost describe only the runs that went well.
        cell = run_cell(card, [conversation(forbidden=True)], cassettes=tmp_path, n=1, k=1)
        assert cell.passed is False
        assert cell.results[0].measured.duration_s == 5.0
        assert cell.results[0].measured.usage == {"claude-sonnet-5": (None, 120)}

    def test_usage_is_what_the_trace_reported_not_a_zero(self, tmp_path: Path, card) -> None:
        traces = [conversation(tokens=120)]
        record(tmp_path, card, traces, {"prose": True, "tone_remains_professional": True})
        cell = run_cell(card, traces, cassettes=tmp_path, n=1, k=1)
        assert cell.results[0].measured.usage == {"claude-sonnet-5": (None, 120)}


class TestWasteIsNeverAGate:
    def test_a_passing_run_still_reports_its_waste(self, tmp_path: Path, card) -> None:
        traces = [retries(conversation())]
        record(tmp_path, card, traces, {"prose": True, "tone_remains_professional": True})
        cell = run_cell(card, traces, cassettes=tmp_path, n=1, k=1)
        assert cell.passed is True
        assert [f.kind.value for f in cell.waste] == ["retry_loop"]

    def test_a_run_that_failed_a_gate_wire_reports_its_waste(self, tmp_path: Path, card) -> None:
        cell = run_cell(card, [retries(conversation(forbidden=True))], cassettes=tmp_path, n=1, k=1)
        assert cell.passed is False
        assert cell.results[0].waste  # the early return still carries the findings

    def test_the_verdict_is_the_same_with_and_without_waste(self, tmp_path: Path, card) -> None:
        clean = [conversation()]
        wasteful = [retries(conversation())]
        record(tmp_path, card, clean, {"prose": True, "tone_remains_professional": True})
        record(tmp_path, card, wasteful, {"prose": True, "tone_remains_professional": True})
        one = run_cell(card, clean, cassettes=tmp_path, n=1, k=1)
        other = run_cell(card, wasteful, cassettes=tmp_path, n=1, k=1)
        assert one.waste == [] and other.waste != []
        assert (one.passed, one.credit_mean) == (other.passed, other.credit_mean)

    def test_the_cell_flattens_the_findings_of_every_run(self, tmp_path: Path, card) -> None:
        traces = [retries(conversation()), retries(conversation())]
        record(tmp_path, card, traces, {"prose": True, "tone_remains_professional": True})
        cell = run_cell(card, traces, cassettes=tmp_path, n=2, k=2)
        assert len(cell.waste) == 2
        # 120 output tokens on the chat that issued each repeat attempt.
        assert cell.waste_tokens == {Kind.RETRY_LOOP: 240}

    def test_waste_tokens_is_none_when_no_trace_reported_usage(self, tmp_path: Path, card) -> None:
        traces = [retries(conversation(), tokens=None)]
        record(tmp_path, card, traces, {"prose": True, "tone_remains_professional": True})
        cell = run_cell(card, traces, cassettes=tmp_path, n=1, k=1)
        assert cell.waste
        assert cell.waste_tokens == {Kind.RETRY_LOOP: None}


class TestWasteUnitsAreNotSummed:
    def test_two_kinds_are_two_totals(self, tmp_path: Path, card) -> None:
        # A retry burned tokens; a stale result burned token-turns. One number over both
        # would be a figure in no unit at all — cctx priced each kind separately too.
        large = ("The search results show many TODO items across the codebase. " * 160).strip()
        spans = list(retries(conversation()).spans)
        stale = span("stale-0", Operation.EXECUTE_TOOL, offset=4.0)
        stale.attributes[GenAI.TOOL_CALL_ARGUMENTS] = '{"command": "grep -r TODO ."}'
        stale.attributes[GenAI.TOOL_CALL_RESULT] = large
        spans.append(stale)
        spans += [span(f"quiet-{i}", Operation.CHAT, offset=5.0 + i) for i in range(6)]
        traces = [trace(*spans)]
        record(tmp_path, card, traces, {"prose": True, "tone_remains_professional": True})
        totals = run_cell(card, traces, cassettes=tmp_path, n=1, k=1).waste_tokens
        assert set(totals) == {Kind.RETRY_LOOP, Kind.STALE_CONTEXT}
        assert totals[Kind.RETRY_LOOP] != totals[Kind.STALE_CONTEXT]


class TestTheFreeWiresAreEvaluated:
    """Three wires no card authored, merged in here and nowhere else. See builtin.py."""

    PROSE_ONLY = "# Scenario: x\nThe agent answers.\n"

    def _prose_card(self):
        return parse_text(self.PROSE_ONLY, path="cards/x.md")

    def test_a_card_authoring_nothing_still_evaluates_two_wires(self, tmp_path: Path, card) -> None:
        prose = self._prose_card()
        traces = [conversation()]
        record(tmp_path, prose, traces, {"prose": True})
        cell = run_cell(prose, traces, cassettes=tmp_path, n=1, k=1)
        assert [w.id for w in cell.results[0].wires] == ["stop_reason", "latency"]

    def test_a_truncated_run_fails_a_card_that_never_asked_about_truncation(
        self, tmp_path: Path
    ) -> None:
        # The headline behaviour of #17: the card says nothing about stop_reason, and a
        # run that ran out of room still fails rather than being graded on a cut-off answer.
        prose = self._prose_card()
        one = conversation()
        chat = next(s for s in one.spans if s.operation is Operation.CHAT)
        chat.attributes[GenAI.RESPONSE_FINISH_REASONS] = ["max_tokens"]
        cell = run_cell(prose, [one], cassettes=tmp_path, n=1, k=1)
        assert cell.passed is False
        failed = [w.id for w in cell.results[0].wires if not w.passed]
        assert failed == ["stop_reason"]

    def test_a_free_gate_short_circuits_the_judge_like_an_authored_one(
        self, tmp_path: Path
    ) -> None:
        # No cassette is recorded, so a judge call here would raise rather than replay.
        prose = self._prose_card()
        one = conversation()
        chat = next(s for s in one.spans if s.operation is Operation.CHAT)
        chat.attributes[GenAI.RESPONSE_FINISH_REASONS] = ["max_tokens"]
        cell = run_cell(prose, [one], cassettes=tmp_path, n=1, k=1)
        assert cell.judge_calls == 0
        assert cell.results[0].judged is None

    def test_the_budget_reaches_the_run(self, tmp_path: Path) -> None:
        prose = self._prose_card()
        cell = run_cell(
            prose,
            [conversation(seconds=200.0)],
            cassettes=tmp_path,
            n=1,
            k=1,
            builtin=BuiltinConfig(latency_budget_s=120.0),
        )
        assert cell.passed is False
        assert [w.id for w in cell.results[0].wires if not w.passed] == ["latency"]

    def test_an_authored_latency_wire_wins_over_the_default(self, tmp_path: Path, card) -> None:
        # The card says 120s; the default here is one second and must not apply.
        traces = [conversation(seconds=5.0)]
        record(tmp_path, card, traces, {"prose": True, "tone_remains_professional": True})
        cell = run_cell(
            card,
            traces,
            cassettes=tmp_path,
            n=1,
            k=1,
            builtin=BuiltinConfig(latency_budget_s=1.0),
        )
        assert cell.passed is True
        assert [w.id for w in cell.results[0].wires].count("latency") == 1

    def test_a_free_wire_never_appears_twice(self, tmp_path: Path, card) -> None:
        traces = [conversation()]
        record(tmp_path, card, traces, {"prose": True, "tone_remains_professional": True})
        cell = run_cell(card, traces, cassettes=tmp_path, n=1, k=1)
        ids = [w.id for w in cell.results[0].wires]
        assert len(ids) == len(set(ids))

    def test_the_free_wires_add_nothing_to_the_credit_denominator(
        self, tmp_path: Path, card
    ) -> None:
        # They are gate tier and carry no weight, so the card's own total is unchanged.
        traces = [conversation()]
        record(tmp_path, card, traces, {"prose": True, "tone_remains_professional": True})
        assert run_cell(card, traces, cassettes=tmp_path, n=1, k=1).credit_total == 3


class TestTheTokenRegression:
    def _card(self):
        return parse_text("# Scenario: x\nThe agent answers.\n", path="cards/x.md")

    def test_no_baseline_recorded_is_no_regression_wire(self, tmp_path: Path) -> None:
        # A first install must not go red for a number nobody has written down.
        traces = [conversation(tokens=100_000)]
        record(tmp_path, self._card(), traces, {"prose": True})
        cell = run_cell(self._card(), traces, cassettes=tmp_path, n=1, k=1)
        assert cell.passed is True
        assert "token_baseline" not in [w.id for w in cell.results[0].wires]

    def test_a_run_inside_the_tolerance_passes(self, tmp_path: Path) -> None:
        traces = [conversation(tokens=105)]
        record(tmp_path, self._card(), traces, {"prose": True})
        cell = run_cell(
            self._card(),
            traces,
            cassettes=tmp_path,
            n=1,
            k=1,
            builtin=BuiltinConfig(token_baseline=100, tolerance=0.1),
        )
        assert cell.passed is True

    def test_a_run_past_the_tolerance_fails_the_cell(self, tmp_path: Path) -> None:
        cell = run_cell(
            self._card(),
            [conversation(tokens=200)],
            cassettes=tmp_path,
            n=1,
            k=1,
            builtin=BuiltinConfig(token_baseline=100, tolerance=0.1),
        )
        assert cell.passed is False
        failed = [(w.id, w.detail) for w in cell.results[0].wires if not w.passed]
        assert failed == [("token_baseline", "200, under 111")]

    def test_a_trace_that_reports_no_usage_fails_closed_once_a_baseline_exists(
        self, tmp_path: Path
    ) -> None:
        # Documented rather than discovered: an emitter that stops reporting usage reds
        # the card, and the detail names the attribute rather than a cost.
        silent = trace(
            span("root", Operation.INVOKE_AGENT, parent=None, duration=1.0),
            span("chat-0", Operation.CHAT),
        )
        cell = run_cell(
            self._card(),
            [silent],
            cassettes=tmp_path,
            n=1,
            k=1,
            builtin=BuiltinConfig(token_baseline=100),
        )
        assert cell.passed is False
        detail = next(w.detail for w in cell.results[0].wires if w.id == "token_baseline")
        assert GenAI.USAGE_OUTPUT_TOKENS in detail

    def test_a_cards_own_token_cap_and_the_regression_both_evaluate(
        self, tmp_path: Path, card
    ) -> None:
        # An absolute ceiling and a comparison against what this card used to cost are
        # different assertions, so both are checked. Lint never sees this list.
        traces = [conversation(tokens=105)]
        record(tmp_path, card, traces, {"prose": True, "tone_remains_professional": True})
        cell = run_cell(
            card,
            traces,
            cassettes=tmp_path,
            n=1,
            k=1,
            builtin=BuiltinConfig(token_baseline=100),
        )
        assert cell.passed is True
        # The regression is a gate wire on the run; the card's own cap is a credit wire
        # and still earns its weight beside the criterion's two.
        assert "token_baseline" in [w.id for w in cell.results[0].wires]
        assert cell.credit_mean == 3.0
