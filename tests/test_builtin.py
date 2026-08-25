"""The wires every card gets for free, and the rule that lets a card take one back."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from specdeck.builtin import (
    DEFAULT_LATENCY_BUDGET_S,
    DEFAULT_TOLERANCE,
    BuiltinConfig,
    builtin_properties,
    merge_wires,
)
from specdeck.card import parse_text
from specdeck.ir import Bound, Measure, Never, Property, evaluate
from specdeck.tier import Tier
from specdeck.trace import GenAI, Operation
from specdeck.wires import compile_wire, compile_wires

from .test_trace import span, trace


def ids(properties: list[Property]) -> list[str]:
    return [p.id for p in properties]


def by_id(properties: list[Property], wanted: str) -> Property:
    return next(p for p in properties if p.id == wanted)


class TestTheFreeWires:
    def test_a_card_that_recorded_nothing_gets_two_of_them(self) -> None:
        assert ids(builtin_properties(BuiltinConfig())) == ["stop_reason", "latency"]

    def test_the_regression_wire_appears_only_once_a_baseline_exists(self) -> None:
        # An invented limit would gate a card on a number nobody chose, so an unrecorded
        # card gets no wire rather than a generous one.
        free = builtin_properties(BuiltinConfig(token_baseline=100))
        assert ids(free) == ["stop_reason", "latency", "token_baseline"]

    def test_stop_reason_is_the_property_the_wire_text_compiles_to(self) -> None:
        # Not merely equivalent: the same object, so the evaluator, the report and the
        # dedup rule have one kind of thing to handle rather than two.
        assert by_id(builtin_properties(BuiltinConfig()), "stop_reason") == compile_wire(
            "stop_reason: not truncated"
        )

    def test_latency_is_the_property_the_wire_text_compiles_to(self) -> None:
        free = builtin_properties(BuiltinConfig(latency_budget_s=120.0))
        assert by_id(free, "latency") == compile_wire("latency: under 120s")

    def test_the_budget_reaches_the_bound(self) -> None:
        rule = by_id(builtin_properties(BuiltinConfig(latency_budget_s=30.0)), "latency").rule
        assert isinstance(rule, Bound)
        assert (rule.measure, rule.limit) == (Measure.AGENT_DURATION_S, 30.0)

    def test_every_free_wire_is_gate_tier_and_carries_no_weight(self) -> None:
        free = builtin_properties(BuiltinConfig(token_baseline=100))
        assert {(p.tier, p.weight) for p in free} == {(Tier.GATE, 0)}

    def test_a_budget_the_wire_grammar_could_not_read_back_still_works(self) -> None:
        # `f"under {0.00001:g}s"` is `under 1e-05s`, which `compile_wire` rejects. The
        # properties are built directly for exactly this reason.
        rule = by_id(builtin_properties(BuiltinConfig(latency_budget_s=0.00001)), "latency").rule
        assert rule.limit == 0.00001


class TestTheRegressionLimit:
    def test_the_limit_is_one_token_past_the_allowance(self) -> None:
        # 10% over 100 is 110, which is not *more than* the tolerance, so 110 passes and
        # 111 does not. The bound is strictly-under, hence 111 rather than 110.
        assert BuiltinConfig(token_baseline=100, tolerance=0.1).token_limit == 111.0

    def test_a_fractional_allowance_is_floored_to_a_whole_token(self) -> None:
        # 10% over 95 is 104.5, and no run costs half a token.
        assert BuiltinConfig(token_baseline=95, tolerance=0.1).token_limit == 105.0

    def test_the_float_cannot_let_a_run_at_the_allowance_read_as_under_it(self) -> None:
        # `100 * (1 + 0.1)` is 110.00000000000001. Used as the limit directly it admits a
        # run of 110 while the arithmetic says the allowance is 110 exactly.
        assert 100 * (1 + 0.1) != 110.0

    def test_no_baseline_is_no_limit(self) -> None:
        assert BuiltinConfig().token_limit is None

    def test_the_shipped_tolerance_is_ten_percent(self) -> None:
        assert (DEFAULT_TOLERANCE, DEFAULT_LATENCY_BUDGET_S) == (0.10, 120.0)

    def _verdict(self, tokens: int):
        free = builtin_properties(BuiltinConfig(token_baseline=100, tolerance=0.1))
        one = trace(
            span("root", Operation.INVOKE_AGENT, parent=None, duration=1.0),
            span("chat-0", Operation.CHAT, **{GenAI.USAGE_OUTPUT_TOKENS: tokens}),
        )
        return evaluate(by_id(free, "token_baseline"), one)

    def test_a_run_inside_the_tolerance_passes(self) -> None:
        assert self._verdict(109).passed is True

    def test_a_run_at_exactly_the_tolerance_has_not_exceeded_it(self) -> None:
        assert self._verdict(110).passed is True

    def test_one_token_past_the_allowance_fails(self) -> None:
        assert self._verdict(111).passed is False

    def test_the_detail_names_both_numbers(self) -> None:
        assert self._verdict(200).detail == "200, under 111"

    def test_a_trace_reporting_no_usage_fails_closed_and_says_which_attribute(self) -> None:
        free = builtin_properties(BuiltinConfig(token_baseline=100))
        silent = trace(
            span("root", Operation.INVOKE_AGENT, parent=None, duration=1.0),
            span("chat-0", Operation.CHAT),
        )
        verdict = evaluate(by_id(free, "token_baseline"), silent)
        # Documented rather than discovered in a user's repo: once a baseline exists, an
        # emitter that stops reporting usage reds the card, and the detail says why.
        assert verdict.passed is False
        assert GenAI.USAGE_OUTPUT_TOKENS in verdict.detail


class TestTheConfigRefusesNonsense:
    @pytest.mark.parametrize("budget", [0.0, -5.0])
    def test_a_budget_nothing_could_pass_is_refused(self, budget: float) -> None:
        with pytest.raises(ValidationError, match=r"latency budget must be positive"):
            BuiltinConfig(latency_budget_s=budget)

    def test_a_negative_tolerance_is_refused(self) -> None:
        with pytest.raises(ValidationError, match=r"tolerance must not be negative"):
            BuiltinConfig(tolerance=-0.1)

    def test_a_baseline_of_zero_is_refused(self) -> None:
        # It would bound every later run at zero and never come back.
        with pytest.raises(ValidationError, match=r"token baseline must be positive"):
            BuiltinConfig(token_baseline=0)


class TestOverriding:
    """No new card syntax: a card takes a built-in back by authoring the same subject."""

    def _authored(self, text: str) -> list[Property]:
        return compile_wires(parse_text(f"# Scenario: x\nThe agent answers.\n\nwire:\n{text}"))

    def test_an_authored_latency_wire_replaces_the_built_in(self) -> None:
        merged = merge_wires(
            self._authored("  - latency: under 30s\n"), builtin_properties(BuiltinConfig())
        )
        assert ids(merged) == ["latency", "stop_reason"]
        assert by_id(merged, "latency").rule.limit == 30.0

    def test_the_built_in_never_appears_twice(self) -> None:
        merged = merge_wires(
            self._authored("  - latency: under 30s\n  - stop_reason: not truncated\n"),
            builtin_properties(BuiltinConfig()),
        )
        assert ids(merged) == ["latency", "stop_reason"]

    def test_a_card_may_demote_a_built_in_to_credit(self) -> None:
        # Falls out of dedup-on-id, and is the only way to turn a free gate off. The card
        # author asked for it in a reviewed PR, so they get it — and docs say so.
        card = parse_text(
            "# Scenario: x\nThe agent answers.\n\ncredit:\n"
            "  - wire: stop_reason: not truncated: 1\n"
        )
        merged = merge_wires(compile_wires(card), builtin_properties(BuiltinConfig()))
        assert ids(merged) == ["stop_reason", "latency"]
        assert by_id(merged, "stop_reason").tier is Tier.CREDIT

    def test_an_absolute_token_cap_does_not_displace_the_regression(self) -> None:
        # Different ids because they are different assertions: a ceiling, and a comparison
        # against what this card used to cost.
        card = parse_text(
            "# Scenario: x\nThe agent answers.\n\ncredit:\n  - wire: response_tokens under 400: 1\n"
        )
        merged = merge_wires(
            compile_wires(card), builtin_properties(BuiltinConfig(token_baseline=100))
        )
        assert ids(merged) == ["response_tokens", "stop_reason", "latency", "token_baseline"]

    def test_authored_wires_keep_their_order_and_the_built_ins_come_last(self) -> None:
        # The report prints them in this order, so the card's own wires stay on top.
        merged = merge_wires(
            self._authored("  - modify_reservation: never\n  - web_search: at_most 2\n"),
            builtin_properties(BuiltinConfig()),
        )
        assert ids(merged) == [
            "never:modify_reservation",
            "at_most:web_search",
            "stop_reason",
            "latency",
        ]

    def test_a_card_with_no_wires_at_all_gets_every_built_in(self) -> None:
        merged = merge_wires([], builtin_properties(BuiltinConfig(token_baseline=100)))
        assert ids(merged) == ["stop_reason", "latency", "token_baseline"]

    def test_the_merge_returns_a_new_list(self) -> None:
        authored: list[Property] = []
        merge_wires(authored, builtin_properties(BuiltinConfig()))
        assert authored == []


class TestBuiltInsAreNotPinned:
    """`wires_hash` is computed from `compile_wires`, which must never see a built-in.

    A default moving in a specdeck release would otherwise read as drift on every card in
    every user repo, with a `--relock` hint for a card nobody edited.
    """

    def test_compile_wires_returns_the_authored_wires_and_nothing_else(self) -> None:
        card = parse_text("# Scenario: x\nThe agent answers.\n\nwire:\n  - a_tool: never\n")
        assert ids(compile_wires(card)) == ["never:a_tool"]

    def test_a_prose_only_card_still_compiles_to_nothing(self) -> None:
        card = parse_text("# Scenario: x\nThe agent answers.\n")
        assert compile_wires(card) == []
        # ...and is nonetheless evaluated with two free wires.
        assert len(merge_wires(compile_wires(card), builtin_properties(BuiltinConfig()))) == 2


class TestTheStopReasonGap:
    def test_there_is_no_looser_stop_reason_rule_to_author(self) -> None:
        # Recorded as a known gap: a card that genuinely cannot avoid truncation can only
        # demote the wire to credit, not relax it. Accepted for now, not solved.
        from specdeck.wires import WireError

        with pytest.raises(WireError, match=r"only stop_reason rule"):
            compile_wire("stop_reason: truncated_is_fine")

    def test_the_free_stop_reason_wire_forbids_exactly_max_tokens(self) -> None:
        rule = by_id(builtin_properties(BuiltinConfig()), "stop_reason").rule
        assert isinstance(rule, Never)
        assert rule.selector.finish_reason == "max_tokens"
        assert rule.selector.operation is Operation.CHAT
