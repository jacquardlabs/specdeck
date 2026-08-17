import pytest

from specdeck.card import parse_text
from specdeck.ir import AtMost, Bound, Measure, Never, Operation, Tier
from specdeck.wires import WireError, compile_wire, compile_wires, gates_pass

CARD = """\
# Scenario: refund request on basic economy
context:
  simulator: "frustrated customer"

The agent refuses and explains.

wire:
  - modify_reservation: never
  - web_search: at_most 2
  - latency: under 120s
  - stop_reason: not truncated

credit:
  - "tone remains professional": 2
  - wire: response_tokens under 400: 1
"""


class TestToolWires:
    def test_never_compiles_to_a_tool_selector(self) -> None:
        prop = compile_wire("modify_reservation: never")
        assert isinstance(prop.rule, Never)
        assert prop.rule.selector.operation is Operation.EXECUTE_TOOL
        assert prop.rule.selector.tool == "modify_reservation"

    def test_at_most_carries_the_budget(self) -> None:
        prop = compile_wire("web_search: at_most 2")
        assert isinstance(prop.rule, AtMost)
        assert prop.rule.n == 2
        assert prop.rule.selector.tool == "web_search"

    def test_ids_are_stable_and_name_the_subject(self) -> None:
        assert compile_wire("modify_reservation: never").id == "never:modify_reservation"
        assert compile_wire("web_search: at_most 2").id == "at_most:web_search"


class TestBounds:
    def test_latency_bounds_the_agent_span(self) -> None:
        rule = compile_wire("latency: under 120s").rule
        assert isinstance(rule, Bound)
        assert (rule.measure, rule.limit) == (Measure.AGENT_DURATION_S, 120.0)

    def test_the_seconds_suffix_is_optional(self) -> None:
        assert compile_wire("latency: under 90").rule.limit == 90.0

    def test_response_tokens_bounds_the_trace_total(self) -> None:
        rule = compile_wire("response_tokens under 400").rule
        assert isinstance(rule, Bound)
        assert (rule.measure, rule.limit) == (Measure.TOTAL_OUTPUT_TOKENS, 400.0)


class TestStopReason:
    def test_not_truncated_compiles_to_never_max_tokens(self) -> None:
        rule = compile_wire("stop_reason: not truncated")
        assert isinstance(rule.rule, Never)
        assert rule.rule.selector.finish_reason == "max_tokens"
        assert rule.rule.selector.operation is Operation.CHAT


class TestDeferred:
    @pytest.mark.parametrize(
        "text",
        [
            "writer<->reviewer: escalate_to_hitl after 5 non_agreement",
            "transfer_to_human_agents: after 3 non_agreement",
        ],
    )
    def test_after_k_then_y_names_the_issue_it_waits_on(self, text: str) -> None:
        with pytest.raises(WireError, match=r"#47"):
            compile_wire(text)

    def test_eventually_says_it_is_deferred(self) -> None:
        with pytest.raises(WireError, match=r"not in the tracer"):
            compile_wire("send_confirmation: eventually")

    def test_an_unrecognised_rule_names_the_wire(self) -> None:
        with pytest.raises(WireError, match=r"wibble"):
            compile_wire("some_tool: wibble 3")

    def test_a_non_numeric_budget_is_rejected(self) -> None:
        with pytest.raises(WireError, match=r"whole number"):
            compile_wire("web_search: at_most lots")


class TestCompileCard:
    def test_gate_wires_come_from_the_wire_block(self) -> None:
        props = compile_wires(parse_text(CARD))
        gate_ids = [p.id for p in props if p.tier is Tier.GATE]
        assert gate_ids == [
            "never:modify_reservation",
            "at_most:web_search",
            "latency",
            "stop_reason",
        ]

    def test_credit_wires_carry_their_tier_and_weight(self) -> None:
        credit = [p for p in compile_wires(parse_text(CARD)) if p.tier is Tier.CREDIT]
        assert [(p.id, p.weight) for p in credit] == [("response_tokens", 1)]

    def test_a_card_with_no_wires_compiles_to_nothing(self) -> None:
        assert compile_wires(parse_text("# Scenario: x\nThe agent answers.\n")) == []

    def test_the_error_names_the_card_and_the_wire(self) -> None:
        card = parse_text("# Scenario: x\np\nwire:\n  - t: wibble\n", path="cards/x.md")
        with pytest.raises(WireError, match=r"cards/x.md"):
            compile_wires(card)


class TestGatesPass:
    def test_true_when_every_gate_verdict_passed(self) -> None:
        from specdeck.ir import Verdict

        verdicts = [
            Verdict(id="a", tier=Tier.GATE, weight=0, passed=True, detail=""),
            Verdict(id="b", tier=Tier.CREDIT, weight=1, passed=False, detail=""),
        ]
        assert gates_pass(verdicts) is True

    def test_false_when_any_gate_verdict_failed(self) -> None:
        from specdeck.ir import Verdict

        verdicts = [Verdict(id="a", tier=Tier.GATE, weight=0, passed=False, detail="")]
        assert gates_pass(verdicts) is False

    def test_a_card_with_no_gate_wires_passes_its_wires(self) -> None:
        assert gates_pass([]) is True
