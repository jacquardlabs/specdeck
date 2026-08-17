import pytest

from specdeck.ir import (
    AfterKThen,
    AtMost,
    Bound,
    Measure,
    Never,
    Property,
    Scope,
    Selector,
    Tier,
    evaluate,
    evaluate_all,
)
from specdeck.trace import GenAI, Operation

from .test_trace import span, trace


@pytest.fixture
def refusal_trace():
    """Looks up the reservation, refuses. Touches no forbidden tool."""
    return trace(
        span("root", Operation.INVOKE_AGENT, parent=None, duration=12.0),
        span("chat-0", Operation.CHAT, offset=0.0, **{GenAI.USAGE_OUTPUT_TOKENS: 40}),
        span("tool-0", Operation.EXECUTE_TOOL, offset=1.0),
        span("chat-1", Operation.CHAT, offset=2.0, **{GenAI.USAGE_OUTPUT_TOKENS: 110}),
    )


def gate(rule, id: str = "w") -> Property:
    return Property(id=id, rule=rule)


class TestNever:
    def test_passes_when_the_forbidden_tool_is_absent(self, refusal_trace) -> None:
        rule = Never(selector=Selector(operation=Operation.EXECUTE_TOOL, tool="update_reservation"))
        assert evaluate(gate(rule), refusal_trace).passed

    def test_fails_and_counts_the_matches(self, refusal_trace) -> None:
        rule = Never(
            selector=Selector(operation=Operation.EXECUTE_TOOL, tool="get_reservation_details")
        )
        verdict = evaluate(gate(rule), refusal_trace)
        assert not verdict.passed
        assert "1" in verdict.detail

    def test_selects_on_a_finish_reason(self) -> None:
        truncated = trace(
            span("root", Operation.INVOKE_AGENT, parent=None),
            span("chat-0", Operation.CHAT, **{GenAI.RESPONSE_FINISH_REASONS: ["max_tokens"]}),
        )
        rule = Never(selector=Selector(operation=Operation.CHAT, finish_reason="max_tokens"))
        assert not evaluate(gate(rule), truncated).passed

    def test_a_finish_reason_selector_ignores_other_reasons(self, refusal_trace) -> None:
        rule = Never(selector=Selector(operation=Operation.CHAT, finish_reason="max_tokens"))
        assert evaluate(gate(rule), refusal_trace).passed


class TestAtMost:
    @pytest.mark.parametrize(("budget", "expected"), [(0, False), (1, True), (2, True)])
    def test_compares_the_count_against_the_budget(
        self, refusal_trace, budget: int, expected: bool
    ) -> None:
        rule = AtMost(
            n=budget,
            selector=Selector(operation=Operation.EXECUTE_TOOL, tool="get_reservation_details"),
        )
        assert evaluate(gate(rule), refusal_trace).passed is expected

    def test_detail_reports_the_count_and_the_budget(self, refusal_trace) -> None:
        rule = AtMost(n=3, selector=Selector(operation=Operation.EXECUTE_TOOL))
        assert evaluate(gate(rule), refusal_trace).detail == "1 call, budget 3"

    def test_a_negative_budget_is_rejected(self) -> None:
        with pytest.raises(ValueError, match=r"at_most"):
            AtMost(n=-1, selector=Selector(operation=Operation.EXECUTE_TOOL))


class TestBound:
    def test_agent_duration_against_the_limit(self, refusal_trace) -> None:
        assert evaluate(
            gate(Bound(measure=Measure.AGENT_DURATION_S, limit=120)), refusal_trace
        ).passed
        assert not evaluate(
            gate(Bound(measure=Measure.AGENT_DURATION_S, limit=5)), refusal_trace
        ).passed

    def test_total_output_tokens_against_the_limit(self, refusal_trace) -> None:
        rule = Bound(measure=Measure.TOTAL_OUTPUT_TOKENS, limit=400)
        assert evaluate(gate(rule), refusal_trace).passed
        assert not evaluate(
            gate(Bound(measure=Measure.TOTAL_OUTPUT_TOKENS, limit=100)), refusal_trace
        ).passed

    def test_the_limit_is_exclusive_because_the_card_says_under(self, refusal_trace) -> None:
        # The trace totals exactly 150; `under 150` is not satisfied by 150.
        assert not evaluate(
            gate(Bound(measure=Measure.TOTAL_OUTPUT_TOKENS, limit=150)), refusal_trace
        ).passed
        assert evaluate(
            gate(Bound(measure=Measure.TOTAL_OUTPUT_TOKENS, limit=151)), refusal_trace
        ).passed

    def test_detail_reports_the_measured_value(self, refusal_trace) -> None:
        verdict = evaluate(
            gate(Bound(measure=Measure.TOTAL_OUTPUT_TOKENS, limit=100)), refusal_trace
        )
        assert "150" in verdict.detail and "100" in verdict.detail


class TestProperty:
    def test_defaults_to_gate_tier_with_no_weight(self) -> None:
        assert gate(Never(selector=Selector())).tier is Tier.GATE

    def test_a_credit_property_carries_its_weight_into_the_verdict(self, refusal_trace) -> None:
        prop = Property(
            id="tokens",
            tier=Tier.CREDIT,
            weight=2,
            rule=Bound(measure=Measure.TOTAL_OUTPUT_TOKENS, limit=400),
        )
        verdict = evaluate(prop, refusal_trace)
        assert (verdict.tier, verdict.weight, verdict.passed) == (Tier.CREDIT, 2, True)

    def test_a_gate_property_may_not_carry_a_weight(self) -> None:
        with pytest.raises(ValueError, match=r"weight"):
            Property(id="w", tier=Tier.GATE, weight=2, rule=Never(selector=Selector()))

    def test_a_credit_property_needs_a_positive_weight(self) -> None:
        with pytest.raises(ValueError, match=r"weight"):
            Property(id="w", tier=Tier.CREDIT, weight=0, rule=Never(selector=Selector()))

    def test_evaluate_all_preserves_order_and_ids(self, refusal_trace) -> None:
        props = [
            gate(Never(selector=Selector(tool="nope")), id="a"),
            gate(AtMost(n=9, selector=Selector(operation=Operation.CHAT)), id="b"),
        ]
        assert [v.id for v in evaluate_all(props, refusal_trace)] == ["a", "b"]


class TestScope:
    def test_globally_is_the_default_and_sees_every_span(self, refusal_trace) -> None:
        prop = gate(AtMost(n=0, selector=Selector(operation=Operation.CHAT)))
        assert prop.scope == Scope()
        assert not evaluate(prop, refusal_trace).passed


class TestRoundTrip:
    def test_a_property_survives_serialisation(self) -> None:
        prop = Property(
            id="never_update",
            rule=Never(selector=Selector(operation=Operation.EXECUTE_TOOL, tool="update")),
        )
        assert Property.model_validate(prop.model_dump()) == prop

    def test_the_rule_union_is_discriminated_by_pattern(self) -> None:
        restored = Property.model_validate(
            {"id": "budget", "rule": {"pattern": "at_most", "n": 2, "selector": {"tool": "search"}}}
        )
        assert isinstance(restored.rule, AtMost)


class TestAfterKThen:
    def _trace(self, markers: int, escalates: bool):
        from specdeck.trace import Specdeck

        spans = [span("root", Operation.INVOKE_AGENT, parent=None, duration=20.0)]
        for i in range(markers):
            spans.append(
                span(
                    f"chat-{i}",
                    Operation.CHAT,
                    offset=float(i),
                    **{Specdeck.MARKER: "pushback"},
                )
            )
        if escalates:
            escalation = span("tool-esc", Operation.EXECUTE_TOOL, offset=float(markers) + 1)
            escalation.attributes[GenAI.TOOL_NAME] = "escalate"
            spans.append(escalation)
        return trace(*spans)

    def _rule(self):
        return AfterKThen(
            k=3,
            trigger=Selector(marker="pushback"),
            then=Selector(operation=Operation.EXECUTE_TOOL, tool="escalate"),
        )

    def test_is_vacuously_true_below_k(self) -> None:
        verdict = evaluate(gate(self._rule()), self._trace(markers=2, escalates=False))
        assert verdict.passed
        assert "under k=3" in verdict.detail

    def test_fails_when_k_is_reached_and_nothing_follows(self) -> None:
        verdict = evaluate(gate(self._rule()), self._trace(markers=3, escalates=False))
        assert not verdict.passed
        assert "0 follow-ups" in verdict.detail

    def test_passes_when_the_follow_up_occurs_after_the_kth_trigger(self) -> None:
        assert evaluate(gate(self._rule()), self._trace(markers=3, escalates=True)).passed

    def test_a_follow_up_before_the_kth_trigger_does_not_count(self) -> None:
        from specdeck.trace import Specdeck

        early = span("tool-esc", Operation.EXECUTE_TOOL, offset=0.0)
        early.attributes[GenAI.TOOL_NAME] = "escalate"
        spans = [span("root", Operation.INVOKE_AGENT, parent=None, duration=20.0), early]
        spans += [
            span(f"chat-{i}", Operation.CHAT, offset=float(i) + 1, **{Specdeck.MARKER: "pushback"})
            for i in range(3)
        ]
        assert not evaluate(gate(self._rule()), trace(*spans)).passed

    def test_it_round_trips_through_serialisation(self) -> None:
        prop = gate(self._rule())
        assert Property.model_validate(prop.model_dump()) == prop
