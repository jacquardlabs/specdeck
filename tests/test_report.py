from datetime import date
from pathlib import Path

import pytest
from rich.console import Console

from specdeck.card import parse_text
from specdeck.cell import Cell, run_cell
from specdeck.ir import Verdict
from specdeck.rates import ModelRate, Rates
from specdeck.report import render
from specdeck.stats import RunMeasures
from specdeck.tier import Tier
from specdeck.waste import Finding, Kind, Level

from .test_cell import CARD, conversation, record, retries, run_stub

RATES = Rates(
    verified=date(2026, 8, 24),
    table={"anthropic": {"claude-sonnet-5": ModelRate(input=2.0, output=10.0)}},
)


@pytest.fixture
def card():
    return parse_text(CARD, path="cards/refund.md")


def cell_of(*results, **overrides) -> Cell:
    """A hand-built cell, for the lines that are about the report and not about a run."""
    fields: dict = {
        "card_path": "cards/escalation.md",
        "title": "escalate after repeated refusal",
        "runs": len(results),
        "threshold": 1,
        "passes": sum(r.passed for r in results),
        "credit_mean": None,
        "credit_total": 0,
        "judge_model": "claude-sonnet-5",
        "judge_calls": 0,
        "results": list(results),
    }
    return Cell(**(fields | overrides))


def priced(model: str, tokens: tuple[int | None, int | None] = (10, 10), **overrides):
    return run_stub(measured=RunMeasures(duration_s=1.0, usage={model: tokens}), **overrides)


def rendered(cell, *, rates: Rates | None = None) -> str:
    console = Console(record=True, width=100, force_terminal=False)
    render(cell, console, rates=rates)
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
        return cell_of(run_stub(passed=False, wires=wires), threshold=1)

    def test_an_id_past_the_old_fixed_column_keeps_its_separator(self) -> None:
        # 45 characters against a hard-coded 34 printed `...human_agentsk=3 reached`.
        text = rendered(self._cell([self.LONG, "latency"]))
        assert f"{self.LONG}  k=3 reached" in text

    def test_short_ids_stay_aligned_with_each_other(self) -> None:
        text = rendered(self._cell(["latency", "never:cancel_reservation"]))
        columns = [line.index("k=3 reached") for line in text.splitlines() if "k=3" in line]
        assert len(set(columns)) == 1


class TestTheHeadlineStaysFirst:
    def test_the_secondary_figures_sit_below_the_two_numbers(self, tmp_path: Path, card) -> None:
        traces = [conversation()]
        record(tmp_path, card, traces, {"prose": True, "tone_remains_professional": True})
        text = rendered(run_cell(card, traces, cassettes=tmp_path, n=1, k=1), rates=RATES)
        order = [text.index(label) for label in ("gate", "credit", "variance", "latency", "cost")]
        assert order == sorted(order)


class TestVariance:
    def test_one_passing_run_is_not_a_spread(self, tmp_path: Path, card) -> None:
        traces = [conversation()]
        record(tmp_path, card, traces, {"prose": True, "tone_remains_professional": True})
        text = rendered(run_cell(card, traces, cassettes=tmp_path, n=1, k=1))
        assert "variance n/a — 1 passing run" in text
        assert "sd 0" not in text

    def test_no_passing_run_says_so_rather_than_reporting_zero(self, tmp_path: Path, card) -> None:
        text = rendered(
            run_cell(card, [conversation(forbidden=True)], cassettes=tmp_path, n=1, k=1)
        )
        assert "variance n/a — 0 passing runs" in text

    def test_it_reports_the_range_and_the_deviation_of_the_credit_earned(self) -> None:
        text = rendered(
            cell_of(run_stub(credit_earned=2), run_stub(credit_earned=4), credit_mean=3.0)
        )
        assert "credit 2-4, sd 1.00 over 2 passing runs" in text

    def test_a_mixed_gate_says_so_beside_the_spread(self) -> None:
        text = rendered(
            cell_of(
                run_stub(credit_earned=2),
                run_stub(credit_earned=4),
                run_stub(passed=False),
                credit_mean=3.0,
            )
        )
        assert "gate mixed, 2 pass / 1 fail" in text

    def test_a_gate_that_never_failed_does_not_say_mixed(self) -> None:
        text = rendered(cell_of(run_stub(credit_earned=2), run_stub(credit_earned=4)))
        assert "gate mixed" not in text

    def test_it_never_reaches_for_a_verdict_of_its_own(self) -> None:
        # UNSTABLE and the quarantine lane are measurement.md's variance-attribution
        # section, a later phase; introducing one here would change the exit-code contract.
        text = rendered(cell_of(run_stub(credit_earned=2), run_stub(passed=False)))
        assert "UNSTABLE" not in text and "quarantine" not in text


class TestLatency:
    def test_it_names_both_percentiles_and_the_sample_count(self) -> None:
        runs = [run_stub(measured=RunMeasures(duration_s=float(s))) for s in (1, 2, 3, 4, 5)]
        text = rendered(cell_of(*runs))
        assert "p50 3s, p95 4.8s over 5 runs" in text


class TestCost:
    def test_it_is_labeled_an_estimate_and_never_reads_as_billing(self) -> None:
        text = rendered(cell_of(priced("claude-sonnet-5")), rates=RATES)
        assert "~$" in text and "estimate" in text
        for billing in ("billed", "charged", "spent", "invoice"):
            assert billing not in text

    def test_it_says_whose_tokens_it_priced(self) -> None:
        # specdeck's own judge and simulator spend is not in the figure, and the line has
        # to say so rather than leaving the reader to assume the run's whole cost.
        text = rendered(cell_of(priced("claude-sonnet-5")), rates=RATES)
        assert "agent tokens only" in text

    def test_no_rate_table_is_n_a_and_not_a_zero(self) -> None:
        text = rendered(cell_of(priced("claude-sonnet-5")))
        assert "cost     n/a — no rate table" in text
        assert "$0" not in text

    def test_a_trace_that_reported_no_usage_is_n_a_naming_the_attribute(self) -> None:
        text = rendered(cell_of(priced("claude-sonnet-5", (None, None))), rates=RATES)
        assert "gen_ai.usage" in text
        assert "$0" not in text

    def test_a_model_the_table_does_not_price_is_named(self) -> None:
        text = rendered(cell_of(priced("gpt-9")), rates=RATES)
        assert "no rate for gpt-9" in text
        assert "$" not in text

    def test_one_priced_model_beside_one_silent_one_says_both(self) -> None:
        run = run_stub(
            measured=RunMeasures(
                duration_s=1.0,
                usage={"claude-sonnet-5": (10, 10), "claude-haiku-4-5": (10, None)},
            )
        )
        text = rendered(cell_of(run), rates=RATES)
        assert "~$" in text
        assert "no gen_ai.usage from claude-haiku-4-5" in text

    def test_a_model_id_carrying_markup_renders_literally(self) -> None:
        # The id came out of a user-supplied trace file. As markup it would raise
        # MarkupError and discard the whole report, after every judge call was paid for.
        text = rendered(cell_of(priced("vendor/[bold]-1")), rates=RATES)
        assert "[bold]" in text


class TestWasteBlock:
    def _finding(self, summary: str = "Edit(a.py) failed 2x", **overrides) -> Finding:
        fields: dict = {
            "kind": Kind.RETRY_LOOP,
            "severity": Level.MEDIUM,
            "confidence": Level.HIGH,
            "first_span": 3,
            "last_span": 5,
            "summary": summary,
            "waste_tokens": 120,
        }
        return Finding(**(fields | overrides))

    def test_a_passing_cell_still_prints_its_waste(self, tmp_path: Path, card) -> None:
        traces = [retries(conversation())]
        record(tmp_path, card, traces, {"prose": True, "tone_remains_professional": True})
        text = rendered(run_cell(card, traces, cassettes=tmp_path, n=1, k=1))
        assert "PASS" in text
        assert "waste" in text
        assert "estimated" in text

    def test_a_clean_cell_prints_no_waste_block(self, tmp_path: Path, card) -> None:
        traces = [conversation()]
        record(tmp_path, card, traces, {"prose": True, "tone_remains_professional": True})
        assert "waste" not in rendered(run_cell(card, traces, cassettes=tmp_path, n=1, k=1))

    def test_identical_findings_collapse_to_one_line_with_a_count(self) -> None:
        runs = [run_stub(waste=[self._finding()]) for _ in range(5)]
        text = rendered(cell_of(*runs))
        assert text.count("Edit(a.py) failed 2x") == 1
        assert "in 5 of 5 runs" in text

    def test_the_worst_severity_is_listed_first(self) -> None:
        run = run_stub(
            waste=[
                self._finding("a medium one"),
                self._finding("a high one", severity=Level.HIGH, first_span=9),
            ]
        )
        text = rendered(cell_of(run))
        assert text.index("a high one") < text.index("a medium one")

    def test_a_summary_carrying_markup_renders_literally(self) -> None:
        # A similarity key can be a shell command; `Bash(ls [a-z])` as markup is a crash.
        text = rendered(cell_of(run_stub(waste=[self._finding("Bash(ls [a-z]) failed 2x")])))
        assert "[a-z]" in text

    def test_a_finding_that_could_not_size_itself_says_so(self) -> None:
        text = rendered(cell_of(run_stub(waste=[self._finding(waste_tokens=None)])))
        assert "not reported by the trace" in text
        assert "~0 tokens" not in text
