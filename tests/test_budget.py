"""The hard cap, driven directly. No network, no card, no cassettes.

The three fail-closed rules each have their own test, because each is a different way of
arriving at the same failure: charging zero for a run nobody can price.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime

import pytest

from specdeck.budget import Budget, BudgetError, BudgetStop
from specdeck.matrix import Column
from specdeck.rates import ModelRate, Rates
from specdeck.trace import UNKNOWN_MODEL, UNKNOWN_PROVIDER, GenAI, Span, Trace

#: $1 in, $1 out per million, so a million tokens is exactly a dollar and every figure in
#: this file can be read without arithmetic.
RATES = Rates(
    verified=date(2026, 8, 24),
    table={"anthropic": {"priced": ModelRate(input=1.0, output=1.0)}},
)

WHEN = datetime(2026, 8, 24, tzinfo=UTC)


def budget(cap: float | None = 1.0) -> Budget:
    return Budget(cap_usd=cap, rates=RATES)


def column(name: str, model: str) -> Column:
    return Column(name=name, provider=name, prompt="", model=model, config={})


def trace(*chats: dict) -> Trace:
    """A trace of one root and one chat span per dict, each carrying what it declares."""
    root = Span(
        span_id="0" * 16,
        parent_span_id=None,
        name="invoke_agent a",
        start_time=WHEN,
        end_time=WHEN,
        attributes={GenAI.OPERATION_NAME: "invoke_agent", GenAI.AGENT_NAME: "a"},
    )
    spans = [root]
    for index, chat in enumerate(chats):
        attributes: dict = {
            GenAI.OPERATION_NAME: "chat",
            # What `loop._Builder._chat` writes for a trace specdeck produced itself.
            GenAI.PROVIDER_NAME: UNKNOWN_PROVIDER,
            GenAI.REQUEST_MODEL: chat.get("model", "priced"),
        }
        if chat.get("input_tokens") is not None:
            attributes[GenAI.USAGE_INPUT_TOKENS] = chat["input_tokens"]
        if chat.get("output_tokens") is not None:
            attributes[GenAI.USAGE_OUTPUT_TOKENS] = chat["output_tokens"]
        spans.append(
            Span(
                span_id=f"c{index:015d}",
                parent_span_id=root.span_id,
                name="chat",
                start_time=WHEN,
                end_time=WHEN,
                attributes=attributes,
            )
        )
    return Trace(semconv="x", spans=spans)


class TestCharging:
    def test_a_charge_prices_through_the_rate_table(self) -> None:
        one = budget()
        one.charge("priced", input_tokens=500_000, output_tokens=500_000)
        assert one.spent.usd == pytest.approx(1.0)

    def test_charges_accumulate_across_concurrent_tasks_without_loss(self) -> None:
        # asyncio, one thread, no await between reading the total and writing it back —
        # which is exactly why `Budget` holds no lock. If that ever stops being true, this
        # is the test that says so.
        one = budget(cap=None)

        async def hundred() -> None:
            async def charge() -> None:
                one.charge("priced", input_tokens=1_000, output_tokens=0)

            await asyncio.gather(*(charge() for _ in range(100)))

        asyncio.run(hundred())
        assert one.spent.usd == pytest.approx(0.1)
        assert one.spent.priced == 100

    def test_an_unmetered_call_is_counted_not_charged_as_zero(self) -> None:
        one = budget()
        one.charge("priced", input_tokens=None, output_tokens=None)
        assert one.spent.usd == 0.0
        assert one.unmetered == {"priced": 1}

    def test_one_reported_half_is_still_charged(self) -> None:
        # A model that reported input and stayed silent on output did spend the input.
        one = budget()
        one.charge("priced", input_tokens=1_000_000, output_tokens=None)
        assert one.spent.usd == pytest.approx(1.0)

    def test_without_a_cap_an_unpriced_model_is_recorded_as_unpriced(self) -> None:
        one = budget(cap=None)
        one.charge("mystery-9", input_tokens=10, output_tokens=10)
        assert one.spent.unpriced == ("mystery-9",)
        assert "n/a" in one.spent.label


class TestTheCap:
    def test_check_passes_below_the_cap(self) -> None:
        one = budget()
        one.charge("priced", input_tokens=100, output_tokens=100)
        one.check("more work")

    def test_check_refuses_once_the_cap_is_reached(self) -> None:
        one = budget()
        one.charge("priced", input_tokens=1_000_000, output_tokens=0)
        with pytest.raises(BudgetStop, match=r"budget cap is reached"):
            one.check("a judge call")

    def test_the_call_that_trips_the_cap_is_still_charged_and_still_returns(self) -> None:
        # In-flight work finishes so its cassette is recorded: `judge` writes only after
        # the reply parses, and cancelling mid-flight discards a paid-for fixture.
        one = budget()
        one.charge("priced", input_tokens=2_000_000, output_tokens=0)
        assert one.spent.usd == pytest.approx(2.0)
        assert one.stopped

    def test_the_stop_message_carries_the_spend_as_an_estimate(self) -> None:
        one = budget()
        one.charge("priced", input_tokens=1_000_000, output_tokens=0)
        with pytest.raises(BudgetStop, match=r"estimate"):
            one.check("a judge call")

    def test_with_no_cap_nothing_is_ever_refused(self) -> None:
        one = budget(cap=None)
        one.charge("priced", input_tokens=10_000_000, output_tokens=10_000_000)
        one.check("more work")
        assert not one.stopped

    @pytest.mark.parametrize("cap", [0.0, -1.0])
    def test_a_cap_that_is_not_positive_is_refused(self, cap: float) -> None:
        with pytest.raises(BudgetError, match=r"positive number of dollars"):
            Budget(cap_usd=cap, rates=RATES)


class TestPreflight:
    def test_a_column_whose_model_has_no_rate_refuses_to_start(self) -> None:
        # Fail-closed rule 1. The whole matrix, not the column alone: there is no honest
        # exit code for "ran three of four because the rate table was incomplete".
        with pytest.raises(BudgetError, match=r"no rate for opus \(claude-opus-9\)"):
            budget().preflight([column("sonnet", "priced"), column("opus", "claude-opus-9")])

    def test_the_refusal_names_where_to_fix_it(self) -> None:
        with pytest.raises(BudgetError, match=r"rates.toml"):
            budget().preflight([column("opus", "claude-opus-9")])

    def test_a_priced_column_starts(self) -> None:
        budget().preflight([column("sonnet", "priced")])

    def test_an_unpriced_judge_refuses_the_matrix_by_name(self) -> None:
        # specdeck's own spend is the half the cap can genuinely prevent, and an unpriced
        # model is charged $0.00 forever — so a judge the table cannot price would leave
        # the cap unable to trip on the one axis it actually governs.
        with pytest.raises(BudgetError, match=r"no rate for judge \(my-finetune-v3\)"):
            budget().preflight([column("sonnet", "priced")], judge_model="my-finetune-v3")

    def test_an_unpriced_simulator_refuses_the_matrix_by_name(self) -> None:
        with pytest.raises(BudgetError, match=r"no rate for simulator \(my-finetune-v3\)"):
            budget().preflight([column("sonnet", "priced")], simulator_model="my-finetune-v3")

    def test_an_unpinned_simulator_is_not_an_unpriced_one(self) -> None:
        # `""` means no simulator has been pinned yet, which the lockfile refuses further
        # down with the flag that fixes it. "no rate for simulator ()" would bury that.
        budget().preflight([column("sonnet", "priced")], judge_model="priced")

    def test_without_a_cap_an_unpriced_column_is_not_refused(self) -> None:
        # Nothing is being enforced, so nothing has to be priceable. The report will say
        # the estimate is partial, which is the honest outcome rather than a refusal.
        budget(cap=None).preflight([column("opus", "claude-opus-9")])


class TestChargingATrace:
    def test_a_traces_reported_usage_is_charged_per_model(self) -> None:
        one = budget(cap=None)
        one.charge_trace(trace({"input_tokens": 500_000, "output_tokens": 500_000}), adapter="A")
        assert one.spent.usd == pytest.approx(1.0)

    def test_an_unknown_model_aborts_naming_the_adapter(self) -> None:
        # Fail-closed rule 2: `loop` writes "unknown" for an adapter that reported no
        # model, and "unknown" has no rate. Charging it zero is the silent spend.
        one = budget()
        with pytest.raises(BudgetStop, match=r"TheAdapter reported no model"):
            one.charge_trace(
                trace({"model": UNKNOWN_MODEL, "input_tokens": 5, "output_tokens": 5}),
                adapter="TheAdapter",
            )
        assert one.spent.usd == 0.0

    def test_a_trace_reporting_no_output_tokens_aborts_rather_than_charging_zero(self) -> None:
        # Fail-closed rule 3, on `Trace.reports_output_tokens` itself.
        with pytest.raises(BudgetStop, match=r"TheAdapter reported no gen_ai.usage.output"):
            budget().charge_trace(
                trace({"input_tokens": 500, "output_tokens": None}), adapter="TheAdapter"
            )

    def test_a_trace_reporting_no_input_tokens_aborts_too(self) -> None:
        # Half a run's cost is not a cost, and the other half is not the whole.
        with pytest.raises(BudgetStop, match=r"no gen_ai.usage.input_tokens"):
            budget().charge_trace(
                trace({"input_tokens": None, "output_tokens": 500}), adapter="TheAdapter"
            )

    def test_a_model_the_table_does_not_price_aborts_under_a_cap(self) -> None:
        # The declared column model priced at pre-flight; the adapter called another one.
        with pytest.raises(BudgetStop, match=r"which the rate table does not price"):
            budget().charge_trace(
                trace({"model": "mystery-9", "input_tokens": 5, "output_tokens": 5}),
                adapter="TheAdapter",
            )

    def test_a_span_that_reported_nothing_is_counted_even_beside_one_that_did(self) -> None:
        # An adapter that attaches usage to the final message of a turn and to nothing
        # else is a shape, not a bug — `tests/fake_agent.refuses` has it. `usage_by_model`
        # folds the silent spans into the reporting one, so without a per-span count two
        # of these three model calls would be charged zero invisibly.
        one = budget(cap=None)
        one.charge_trace(trace({}, {}, {"input_tokens": 1000, "output_tokens": 1000}), adapter="P")
        assert one.spent.usd == pytest.approx(0.002)
        assert one.unmetered == {"priced": 2}

    def test_under_a_cap_a_partly_metered_trace_is_charged_and_the_gap_is_named(self) -> None:
        # None of the three rules fires: the trace does report output tokens, both halves
        # are present once folded, and the model is priced. Refusing it would refuse the
        # shape a real adapter has, so it is charged for what it reported and the silent
        # spans are named — the cap trips on the floor rather than on nothing.
        one = budget()
        one.charge_trace(trace({}, {"input_tokens": 1000, "output_tokens": 1000}), adapter="P")
        assert one.unmetered == {"priced": 1}

    def test_without_a_cap_none_of_the_three_rules_fires(self) -> None:
        one = budget(cap=None)
        one.charge_trace(trace({"model": UNKNOWN_MODEL, "output_tokens": None}), adapter="A")
        one.charge_trace(trace({"input_tokens": 100, "output_tokens": 100}), adapter="A")
        assert one.spent.usd == pytest.approx(0.0002)
        assert one.unmetered == {UNKNOWN_MODEL: 1}
