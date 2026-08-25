"""The derived figures, on plain values.

`Trace.usage_by_model` is exercised here rather than in test_trace.py because it exists
for this module: it is the one place a token count is read off a trace, and #17's baseline
and #15's budget cap will read the same property.
"""

from __future__ import annotations

from datetime import date

import pytest

from specdeck.rates import ModelRate, Rates
from specdeck.stats import (
    RunMeasures,
    cost_estimate,
    credit_spread,
    latency,
    measure,
    percentile,
    total_usage,
    unreported,
)
from specdeck.trace import GenAI, Operation, reported_sum

from .test_trace import span, trace

RATES = Rates(
    verified=date(2026, 8, 24),
    table={
        "anthropic": {
            "claude-sonnet-5": ModelRate(input=2.0, output=10.0),
            "claude-haiku-4-5": ModelRate(input=1.0, output=5.0),
        },
        # The documented way a user adds a provider the built-in table does not carry.
        "openai": {"gpt-4o": ModelRate(input=2.5, output=10.0)},
    },
)


def chat(span_id: str, *, model: str = "claude-sonnet-5", provider: str | None = None, **usage):
    attributes: dict[str, object] = {GenAI.RESPONSE_MODEL: model, GenAI.REQUEST_MODEL: model}
    if provider is not None:
        attributes[GenAI.PROVIDER_NAME] = provider
    if "input_tokens" in usage:
        attributes[GenAI.USAGE_INPUT_TOKENS] = usage["input_tokens"]
    if "output_tokens" in usage:
        attributes[GenAI.USAGE_OUTPUT_TOKENS] = usage["output_tokens"]
    return span(span_id, Operation.CHAT, **attributes)


class TestPercentile:
    def test_it_interpolates_between_order_statistics(self) -> None:
        # Pins the method: nearest-rank would give 3 and 5, so a later swap is visible.
        assert percentile([1, 2, 3, 4, 5], 0.5) == 3.0
        assert percentile([1, 2, 3, 4, 5], 0.95) == pytest.approx(4.8)

    def test_one_value_is_its_own_percentile(self) -> None:
        assert percentile([2.5], 0.95) == 2.5

    def test_nothing_to_measure_is_an_error_not_a_zero(self) -> None:
        with pytest.raises(ValueError, match="no values"):
            percentile([], 0.5)

    def test_latency_carries_the_sample_count_it_rests_on(self) -> None:
        measured = latency([1.0, 2.0, 3.0, 4.0, 5.0])
        assert (measured.p50, measured.n) == (3.0, 5)


class TestMeasure:
    def test_duration_is_the_root_span_not_the_sum_of_the_chats(self) -> None:
        one = trace(
            span("root", Operation.INVOKE_AGENT, parent=None, duration=4.0),
            chat("c0"),
            chat("c1", model="claude-sonnet-5"),
        )
        # The two chat spans are a second each; the run took four.
        assert measure(one).duration_s == 4.0

    def test_usage_sums_across_the_chat_spans_of_one_model(self) -> None:
        one = trace(
            span("root", Operation.INVOKE_AGENT, parent=None),
            chat("c0", input_tokens=37, output_tokens=8),
            chat("c1", input_tokens=204, output_tokens=87),
        )
        assert measure(one).usage == {"claude-sonnet-5": (241, 95)}

    def test_two_models_are_two_entries(self) -> None:
        one = trace(
            span("root", Operation.INVOKE_AGENT, parent=None),
            chat("c0", input_tokens=10, output_tokens=1),
            chat("c1", model="claude-haiku-4-5", input_tokens=20, output_tokens=2),
        )
        assert measure(one).usage == {"claude-sonnet-5": (10, 1), "claude-haiku-4-5": (20, 2)}


class TestUsageByModel:
    def test_an_unreported_half_is_none_not_zero(self) -> None:
        one = trace(span("root", Operation.INVOKE_AGENT, parent=None), chat("c0", input_tokens=37))
        assert one.usage_by_model == {"claude-sonnet-5": (37, None)}

    def test_the_response_model_wins_over_the_requested_one(self) -> None:
        served = span(
            "c0",
            Operation.CHAT,
            **{
                GenAI.REQUEST_MODEL: "claude-sonnet-5",
                GenAI.RESPONSE_MODEL: "claude-sonnet-5-20260514",
                GenAI.USAGE_INPUT_TOKENS: 5,
                GenAI.USAGE_OUTPUT_TOKENS: 1,
            },
        )
        one = trace(span("root", Operation.INVOKE_AGENT, parent=None), served)
        assert one.usage_by_model == {"claude-sonnet-5-20260514": (5, 1)}

    def test_a_span_without_a_response_model_falls_back_to_the_request(self) -> None:
        one = trace(span("root", Operation.INVOKE_AGENT, parent=None), chat("c0", input_tokens=5))
        assert list(one.usage_by_model) == ["claude-sonnet-5"]

    def test_only_chat_spans_are_read(self) -> None:
        one = trace(
            span("root", Operation.INVOKE_AGENT, parent=None),
            span("t0", Operation.EXECUTE_TOOL, offset=1.0),
        )
        assert one.usage_by_model == {}

    def test_a_provider_the_default_does_not_serve_survives_into_the_key(self) -> None:
        # Without the prefix `gpt-4o` reads as Anthropic's, and the rate table's own
        # [rates.openai] section — the documented way to add a provider — never resolves.
        one = trace(
            span("root", Operation.INVOKE_AGENT, parent=None),
            chat("c0", provider="openai", model="gpt-4o", input_tokens=1_000_000, output_tokens=0),
        )
        assert one.usage_by_model == {"openai/gpt-4o": (1_000_000, 0)}
        estimate = cost_estimate(one.usage_by_model, RATES)
        assert estimate is not None
        assert estimate.usd == pytest.approx(2.5)

    def test_the_loops_placeholder_provider_leaves_the_id_bare(self) -> None:
        # `loop._chat` writes "unknown" whenever the adapter named no provider. Qualifying
        # on it would send every trace specdeck generates to a [rates.unknown] nobody has.
        one = trace(
            span("root", Operation.INVOKE_AGENT, parent=None),
            chat("c0", provider="unknown", input_tokens=1_000_000, output_tokens=0),
        )
        assert one.usage_by_model == {"claude-sonnet-5": (1_000_000, 0)}
        estimate = cost_estimate(one.usage_by_model, RATES)
        assert estimate is not None
        assert estimate.usd == pytest.approx(2.0)

    def test_an_id_that_already_names_its_provider_is_not_prefixed_twice(self) -> None:
        one = trace(
            span("root", Operation.INVOKE_AGENT, parent=None),
            chat("c0", provider="openai", model="openai/gpt-4o", input_tokens=1),
        )
        assert list(one.usage_by_model) == ["openai/gpt-4o"]

    def test_reported_sum_keeps_did_not_say_out_of_the_arithmetic(self) -> None:
        assert reported_sum(None, None) is None
        assert reported_sum(None, 0) == 0
        assert reported_sum(3, None, 4) == 7


class TestCreditSpread:
    def test_no_spread_below_two_values(self) -> None:
        assert credit_spread([]) is None
        assert credit_spread([3]) is None

    def test_an_identical_set_is_not_variance(self) -> None:
        spread = credit_spread([3, 3, 3])
        assert spread is not None
        assert (spread.low, spread.high, spread.sd) == (3, 3, 0.0)

    def test_it_reports_the_range_and_the_deviation(self) -> None:
        spread = credit_spread([1, 3, 3, 5])
        assert spread is not None
        assert (spread.low, spread.high, spread.n) == (1, 5, 4)
        assert spread.sd == pytest.approx(1.4142, abs=1e-4)


class TestCost:
    def test_a_trace_that_reported_nothing_is_not_a_dollar_figure(self) -> None:
        assert cost_estimate({"claude-sonnet-5": (None, None)}, RATES) is None
        assert cost_estimate({}, RATES) is None

    def test_a_model_reporting_only_one_half_is_not_charged_zero_for_the_other(self) -> None:
        assert cost_estimate({"claude-sonnet-5": (1_000_000, None)}, RATES) is None
        assert unreported({"claude-sonnet-5": (1_000_000, None)}) == ("claude-sonnet-5",)

    def test_input_and_output_are_priced_at_their_separate_rates(self) -> None:
        estimate = cost_estimate({"claude-sonnet-5": (1_000_000, 1_000_000)}, RATES)
        assert estimate is not None
        assert estimate.usd == pytest.approx(12.0)

    def test_two_models_fold_into_one_figure(self) -> None:
        usage = {"claude-sonnet-5": (1_000_000, 0), "claude-haiku-4-5": (1_000_000, 0)}
        estimate = cost_estimate(usage, RATES)
        assert estimate is not None
        assert (estimate.usd, estimate.priced) == (pytest.approx(3.0), 2)

    def test_an_unpriced_model_reads_as_partial_rather_than_dropping_out(self) -> None:
        usage = {"claude-sonnet-5": (1_000_000, 0), "gpt-9": (1_000_000, 0)}
        estimate = cost_estimate(usage, RATES)
        assert estimate is not None
        assert estimate.unpriced == ("gpt-9",)
        assert "partial" in estimate.label

    def test_the_fold_carries_the_tables_own_date_not_todays(self) -> None:
        estimate = cost_estimate({"claude-sonnet-5": (10, 10)}, RATES)
        assert estimate is not None
        assert "2026-08-24" in estimate.label


class TestTotalUsage:
    def test_it_sums_the_same_model_across_runs(self) -> None:
        runs = [
            RunMeasures(duration_s=1.0, usage={"claude-sonnet-5": (10, 1)}),
            RunMeasures(duration_s=1.0, usage={"claude-sonnet-5": (20, 2)}),
        ]
        assert total_usage(runs) == {"claude-sonnet-5": (30, 3)}

    def test_a_run_that_did_not_say_does_not_add_zero(self) -> None:
        runs = [
            RunMeasures(duration_s=1.0, usage={"claude-sonnet-5": (10, None)}),
            RunMeasures(duration_s=1.0, usage={"claude-sonnet-5": (20, None)}),
        ]
        assert total_usage(runs) == {"claude-sonnet-5": (30, None)}

    def test_a_run_that_measured_nothing_contributes_nothing(self) -> None:
        assert total_usage([RunMeasures.nothing()]) == {}
