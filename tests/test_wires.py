import pytest

from specdeck.card import parse_text
from specdeck.ir import (
    AfterKThen,
    AtMost,
    Bound,
    Measure,
    Never,
    NeverRequested,
    Operation,
    Tier,
)
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

    def test_never_executed_is_the_same_property_as_never_not_merely_an_equivalent_one(
        self,
    ) -> None:
        # Identity, not equivalence: `wires_text` hashes the dump, so anything less would
        # move a committed `wires_hash` the moment a card adopted the long spelling.
        short = compile_wire("modify_reservation: never")
        long = compile_wire("modify_reservation: never_executed")
        assert short.model_dump(mode="json") == long.model_dump(mode="json")

    def test_never_requested_is_its_own_pattern_and_its_own_id(self) -> None:
        prop = compile_wire("pay_invoice: never_requested")
        assert prop.id == "never_requested:pay_invoice"
        assert isinstance(prop.rule, NeverRequested)
        assert prop.rule.selector.tool == "pay_invoice"

    def test_never_requested_selects_no_operation(self) -> None:
        # A request shows on a `chat` span as readily as on an `execute_tool` one, so
        # pinning the operation would make the wire blind to the case it exists for.
        assert compile_wire("pay_invoice: never_requested").rule.selector.operation is None

    def test_the_new_spellings_did_not_widen_the_grammar(self) -> None:
        with pytest.raises(WireError, match=r"never_asked"):
            compile_wire("pay_invoice: never_asked")

    def test_a_card_may_state_both_and_gets_two_distinct_properties(self) -> None:
        card = parse_text(
            "# Both\n\nThe agent refuses.\n\nwire:\n"
            "  - pay_invoice: never_executed\n"
            "  - send_certificate: never_requested\n"
        )
        assert [p.id for p in compile_wires(card)] == [
            "never:pay_invoice",
            "never_requested:send_certificate",
        ]


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


class TestAfterKThenY:
    def test_compiles_to_a_marker_trigger_and_a_tool_follow_up(self) -> None:
        prop = compile_wire("transfer_to_human_agents: after 3 non_agreement")
        assert isinstance(prop.rule, AfterKThen)
        assert prop.rule.k == 3
        assert prop.rule.trigger.marker == "non_agreement"
        assert prop.rule.then.tool == "transfer_to_human_agents"

    def test_the_id_names_k_the_marker_and_the_subject(self) -> None:
        prop = compile_wire("transfer_to_human_agents: after 3 non_agreement")
        assert prop.id == "after_3_non_agreement:transfer_to_human_agents"

    def test_k_must_be_at_least_one(self) -> None:
        with pytest.raises(ValueError, match=r"at least 1"):
            compile_wire("escalate: after 0 non_agreement")


class TestDeferred:
    def test_eventually_says_it_is_not_implemented(self) -> None:
        with pytest.raises(WireError, match=r"not implemented"):
            compile_wire("send_confirmation: eventually")

    def test_an_unrecognised_rule_names_the_wire(self) -> None:
        with pytest.raises(WireError, match=r"wibble"):
            compile_wire("some_tool: wibble 3")

    def test_a_non_numeric_budget_is_rejected(self) -> None:
        with pytest.raises(WireError, match=r"expected a number"):
            compile_wire("web_search: at_most lots")

    def test_a_fractional_call_budget_is_rejected(self) -> None:
        with pytest.raises(WireError, match=r"whole number"):
            compile_wire("web_search: at_most 2.5")

    def test_a_seconds_suffix_on_a_token_bound_is_rejected(self) -> None:
        # `response_tokens under 400s` used to compile to a 400-token bound.
        with pytest.raises(WireError, match=r"expected a number"):
            compile_wire("response_tokens under 400s")

    def test_latency_still_takes_the_seconds_suffix(self) -> None:
        assert compile_wire("latency: under 120s").rule.limit == 120.0


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


class TestBuiltInsNeverReachTheCompiler:
    """`compile_wires` feeds `wires_hash`. A built-in in here stales every user's lock.

    The merge lives in `cell.run_cell_async`; these two assertions are what stop a later
    refactor from unifying the pinned derivation with the evaluated one.
    """

    def test_the_module_card_compiles_to_exactly_what_it_authored(self) -> None:
        card = parse_text(CARD)
        props = compile_wires(card)
        assert len(props) == len(card.wires) + len(card.credit_wires)

    def test_a_card_that_authors_no_latency_wire_is_given_none_here(self) -> None:
        card = parse_text("# Scenario: x\nThe agent answers.\n\nwire:\n  - a_tool: never\n")
        assert [p.id for p in compile_wires(card)] == ["never:a_tool"]
