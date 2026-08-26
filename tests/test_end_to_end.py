"""The tracer's closing condition: a clean clone runs the card green, with no API key.

If this test needs a network call or an environment variable to pass, the tracer has not
delivered what #48 said it would.
"""

from __future__ import annotations

import shutil
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
from typer.testing import CliRunner

from specdeck.cli import EXIT_CODES, app
from specdeck.lockfile import lock_key

CARDS = Path(__file__).resolve().parent.parent / "cards"
CARD = CARDS / "over-threshold-second-approval.md"
TRACE = CARDS / "traces" / "over-threshold-second-approval.1.otlp.json"

runner = CliRunner()


def invoke(*args: str):
    return runner.invoke(app, ["run", *args])


def demo(*extra: str):
    return invoke(
        str(CARD),
        "--trace",
        str(TRACE),
        "--runs",
        "1",
        "--pass-threshold",
        "1",
        "--cassettes",
        str(CARDS / "cassettes"),
        *extra,
    )


@pytest.fixture(autouse=True)
def _no_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)


class TestTheCardRunsGreen:
    def test_the_cell_passes(self) -> None:
        result = demo()
        assert result.exit_code == 0, result.stdout

    def test_it_reports_two_numbers_never_blended(self) -> None:
        stdout = demo().stdout
        assert "gate" in stdout and "1/1 runs" in stdout
        assert "credit" in stdout and "4/4" in stdout

    def test_the_judge_is_replayed_not_called(self) -> None:
        assert "replayed" in demo().stdout

    def test_it_prints_the_three_secondary_figures(self) -> None:
        stdout = " ".join(demo().stdout.split())
        # 24.9773s is the captured root span's own duration, the same number the `latency`
        # wire prints one block further down. Captured from a live run by --save-trace, so
        # it is what the agent actually took rather than a figure an author chose.
        assert "latency p50 24.9773s, p95 24.9773s over 1 run" in stdout
        assert "variance n/a — 1 passing run" in stdout

    def test_the_dollar_figure_is_an_estimate_and_says_whose_tokens(self) -> None:
        # Off the captured trace at the built-in table's rate. No key, no network: the
        # figure is arithmetic.
        stdout = " ".join(demo().stdout.split())
        assert "cost ~$0.0418 estimate (rates as of" in stdout
        assert "agent tokens only" in stdout
        # Scoped to the cost line, not the whole report. The deck's domain is accounts
        # payable, so "invoice" is an ordinary word here — asserting over all of stdout
        # tested the vocabulary of the fixture rather than the honesty of the figure.
        cost_line = next(line for line in demo().stdout.splitlines() if "cost" in line)
        for billing in ("billed", "charged", "invoiced", "due"):
            assert billing not in cost_line

    def test_a_clean_run_prints_no_waste_block(self) -> None:
        assert "waste" not in demo().stdout

    def test_every_wire_holds(self) -> None:
        stdout = demo().stdout
        for wire in ("never:pay_invoice", "at_most:request_second_approval"):
            assert wire in stdout
        assert "FAIL" not in stdout


class TestTheLockIsEnforced:
    def test_an_edited_card_refuses_to_run(self, tmp_path: Path) -> None:
        edited = _copy_cards(tmp_path)
        card = edited / CARD.name
        card.write_text(card.read_text().replace("requests a second approval", "requests a countersignature"))
        result = _run_copy(edited, card)
        assert result.exit_code == 2
        assert "--relock" in result.stdout

    def test_a_missing_lockfile_refuses_to_run(self, tmp_path: Path) -> None:
        edited = _copy_cards(tmp_path)
        (edited / "spec.lock.toml").unlink()
        result = _run_copy(edited, edited / CARD.name)
        assert result.exit_code == 2
        assert "--relock" in result.stdout


class TestTheRateTableIsFoundBesideTheCard:
    """Which table priced a run must not depend on where the runner was invoked from."""

    OVERRIDE = (
        "verified = 2026-08-20\n"
        "[rates.anthropic]\n"
        '"claude-sonnet-5" = { input = 100.0, output = 100.0 }\n'
    )

    def test_a_rates_file_beside_the_card_prices_the_run(self, tmp_path: Path) -> None:
        cards = _copy_cards(tmp_path)
        (cards / "rates.toml").write_text(self.OVERRIDE)
        # 17,651 input + 647 output tokens at $100/M is $1.8298, against $0.0418 built-in.
        assert "~$1.8298 estimate" in " ".join(_run_copy(cards, cards / CARD.name).stdout.split())

    def test_the_merged_table_cannot_claim_the_newer_date(self, tmp_path: Path) -> None:
        cards = _copy_cards(tmp_path)
        (cards / "rates.toml").write_text(self.OVERRIDE)
        assert "rates as of 2026-08-20" in _run_copy(cards, cards / CARD.name).stdout

    def test_a_named_table_that_does_not_exist_exits_two(self, tmp_path: Path) -> None:
        result = demo("--rates", str(tmp_path / "absent.toml"))
        assert result.exit_code == 2, result.stdout
        assert "absent.toml" in result.stdout

    #: The likely typo: measurement.md tells users to drop a table beside the card, and
    #: `verified` is the one key nothing about a rate row reminds them to write.
    BROKEN = '[rates.anthropic]\n"claude-sonnet-5" = { input = 1.0, output = 1.0 }\n'

    def test_a_broken_table_exits_two_not_three(self, tmp_path: Path) -> None:
        broken = tmp_path / "rates.toml"
        broken.write_text(self.BROKEN)
        assert demo("--rates", str(broken)).exit_code == 2

    def test_a_broken_table_beside_the_card_does_not_abort_the_eval(self, tmp_path: Path) -> None:
        # An unrequested optional file must not stop a card that has nothing wrong with
        # it. Exit 2 is documented as "the run could not start"; the run started fine.
        cards = _copy_cards(tmp_path)
        (cards / "rates.toml").write_text(self.BROKEN)
        result = _run_copy(cards, cards / CARD.name)
        assert result.exit_code == 0, result.stdout
        text = " ".join(result.stdout.split())
        assert "no `verified` date" in text
        assert "rates.toml" in text
        # Priced from the built-in table, which carries its own date. Never silently.
        assert "~$0.0418 estimate" in text


class TestTheTraceIsOtlp:
    def test_the_committed_trace_is_a_raw_otlp_export(self) -> None:
        # The locked trace decision says an agent already emitting OTel needs no adapter.
        # The demo fixture is that export, not a specdeck-shaped file.
        assert '"resourceSpans"' in TRACE.read_text()


def _copy_cards(tmp_path: Path) -> Path:
    destination = tmp_path / "cards"
    shutil.copytree(CARDS, destination)
    return destination


def _run_copy(cards: Path, card: Path):
    return invoke(
        str(card),
        "--trace",
        str(cards / "traces" / "over-threshold-second-approval.1.otlp.json"),
        "--runs",
        "1",
        "--pass-threshold",
        "1",
        "--cassettes",
        str(cards / "cassettes"),
    )


class TestFailingCellExitsOne:
    def test_a_failed_gate_wire_exits_one_not_zero(self, tmp_path: Path) -> None:
        # A failing card that exits 0 would pass CI silently.
        cards = _copy_cards(tmp_path)
        trace_path = cards / "traces" / "over-threshold-second-approval.1.otlp.json"
        broken = trace_path.read_text().replace(
            "get_purchase_order", "pay_invoice"
        )
        trace_path.write_text(broken)
        result = _run_copy(cards, cards / CARD.name)
        assert result.exit_code == 1, result.stdout
        assert "FAIL" in result.stdout

    def test_the_three_exit_codes_are_distinct(self, tmp_path: Path) -> None:
        assert demo().exit_code == 0
        cards = _copy_cards(tmp_path)
        (cards / "spec.lock.toml").unlink()
        assert _run_copy(cards, cards / CARD.name).exit_code == 2


class TestRelock:
    def test_relock_writes_the_card_key_semconv_and_judge(self, tmp_path: Path) -> None:
        cards = _copy_cards(tmp_path)
        (cards / "spec.lock.toml").unlink()
        result = invoke(
            str(cards / CARD.name),
            "--trace",
            str(cards / "traces" / "over-threshold-second-approval.1.otlp.json"),
            "--runs",
            "1",
            "--pass-threshold",
            "1",
            "--cassettes",
            str(cards / "cassettes"),
            "--relock",
        )
        assert result.exit_code == 0, result.stdout
        written = (cards / "spec.lock.toml").read_text()
        assert f'[cards."{CARD.name}"]' in written
        assert "semantic-conventions-genai" in written
        assert 'model = "claude-sonnet-5"' in written

    def test_relock_does_not_invent_a_simulator_pin(self, tmp_path: Path) -> None:
        cards = _copy_cards(tmp_path)
        (cards / "spec.lock.toml").unlink()
        invoke(
            str(cards / CARD.name),
            "--trace",
            str(cards / "traces" / "over-threshold-second-approval.1.otlp.json"),
            "--runs",
            "1",
            "--pass-threshold",
            "1",
            "--cassettes",
            str(cards / "cassettes"),
            "--relock",
        )
        written = (cards / "spec.lock.toml").read_text()
        simulator = written.split("[simulator]")[1].split("[")[0]
        assert 'model = ""' in simulator

    def test_relock_adopts_the_supplied_judge_model(self, tmp_path: Path) -> None:
        cards = _copy_cards(tmp_path)
        result = invoke(
            str(cards / CARD.name),
            "--trace",
            str(cards / "traces" / "over-threshold-second-approval.1.otlp.json"),
            "--runs",
            "1",
            "--pass-threshold",
            "1",
            "--cassettes",
            str(cards / "cassettes"),
            "--judge-model",
            "claude-opus-5",
            "--relock",
        )
        assert 'model = "claude-opus-5"' in (cards / "spec.lock.toml").read_text()
        # The judge is now pinned to a model with no cassette, so the run cannot replay.
        assert result.exit_code == 2

    def test_a_judge_model_disagreeing_with_the_lock_is_refused(self, tmp_path: Path) -> None:
        cards = _copy_cards(tmp_path)
        result = invoke(
            str(cards / CARD.name),
            "--trace",
            str(cards / "traces" / "over-threshold-second-approval.1.otlp.json"),
            "--runs",
            "1",
            "--pass-threshold",
            "1",
            "--cassettes",
            str(cards / "cassettes"),
            "--judge-model",
            "claude-opus-5",
        )
        assert result.exit_code == 2
        assert "disagrees with the pinned" in result.stdout


class TestCassetteDefault:
    def test_cassettes_resolve_beside_the_card_with_no_flag(self, tmp_path: Path) -> None:
        # The README command carries no --cassettes; it has to work from anywhere.
        cards = _copy_cards(tmp_path)
        result = invoke(
            str(cards / CARD.name),
            "--trace",
            str(cards / "traces" / "over-threshold-second-approval.1.otlp.json"),
            "--runs",
            "1",
            "--pass-threshold",
            "1",
        )
        assert result.exit_code == 0, result.stdout


class TestExitCodesAreDistinct:
    """0 pass, 1 a failed cell, 2 a user error, 3 a crash. A caller that reads only the
    code routes on them, so a broken lockfile must not look like an eval regression (#56).
    """

    def test_a_malformed_lockfile_is_not_a_failed_cell(self, tmp_path: Path) -> None:
        broken = tmp_path / "spec.lock.toml"
        broken.write_text("<<<<<<< HEAD\nbroken = [\n")
        result = invoke(
            str(CARD),
            "--trace",
            str(TRACE),
            "--lock",
            str(broken),
            "--runs",
            "1",
            "--pass-threshold",
            "1",
        )
        assert result.exit_code == 3
        assert "internal error" in result.stdout

    def test_a_card_that_does_not_exist_is_a_user_error(self, tmp_path: Path) -> None:
        # A mistyped path used to escape the funnel as an OSError and exit 1 — the same
        # code as a card that honestly failed.
        result = invoke(str(tmp_path / "nope.md"), "--trace", str(TRACE))
        assert result.exit_code == 2
        assert "cannot read the card" in result.stdout


class TestRunCountDefaults:
    def test_one_trace_needs_no_flags(self) -> None:
        # The README demo had to carry --runs 1 --pass-threshold 1 to work at all, which
        # is evidence the default fitted no shipped path. N=5 is untouched as a statistic;
        # what changed is guessing it when the invocation already states the count.
        result = invoke(str(CARD), "--trace", str(TRACE))
        assert result.exit_code == 0, result.stdout
        assert "1/1 runs" in result.stdout

    def test_the_threshold_never_exceeds_the_cell(self) -> None:
        result = invoke(str(CARD), "--trace", str(TRACE), "--trace", str(TRACE))
        assert result.exit_code == 0, result.stdout
        assert "2/2 runs" in result.stdout

    def test_an_explicit_runs_still_wins(self) -> None:
        result = invoke(str(CARD), "--trace", str(TRACE), "--runs", "2")
        assert result.exit_code == 2
        assert "2 runs but 1 trace" in result.stdout


class TestTheLatencyBudgetFlag:
    def test_a_budget_the_run_cannot_meet_fails_the_cell(self, tmp_path: Path) -> None:
        # The demo card authors `latency: under 120s`, so the flag has to reach a card
        # that authors none. Proven on a prose-only copy with the same trace.
        cards = _copy_cards(tmp_path)
        prose = cards / "prose-only.md"
        prose.write_text("# Scenario: x\nThe agent answers.\n")
        result = invoke(
            str(prose),
            "--trace",
            str(cards / "traces" / "over-threshold-second-approval.1.otlp.json"),
            "--relock",
            "--latency-budget",
            "0.001",
        )
        assert result.exit_code == 1, result.stdout
        assert "latency" in result.stdout

    def test_the_budget_never_overrides_a_card_that_authored_one(self) -> None:
        # The card says 120s and the run took 3.87s. A one-millisecond default must not
        # touch it — an authored wire always wins.
        assert demo("--latency-budget", "0.001").exit_code == 0

    @pytest.mark.parametrize("budget", ["0", "-5"])
    def test_a_budget_nothing_could_pass_is_a_user_error_not_a_crash(self, budget: str) -> None:
        # A pydantic ValidationError would exit 3, "specdeck itself broke", for a number
        # the user typed. Exit 2 is what a caller routes on (#56).
        result = demo("--latency-budget", budget)
        assert result.exit_code == 2, result.stdout
        assert "--latency-budget" in result.stdout


class TestTheBaseline:
    def _prose_card(self, cards: Path) -> Path:
        # The demo card is fine, but a prose-only copy keeps the assertions about the free
        # wires rather than about the card's own.
        path = cards / "prose-only.md"
        path.write_text("# Scenario: x\nThe agent answers.\n")
        return path

    def _run(self, cards: Path, card: Path, *extra: str):
        return invoke(
            str(card),
            "--trace",
            str(cards / "traces" / "over-threshold-second-approval.1.otlp.json"),
            *extra,
        )

    def test_update_baseline_writes_the_file_beside_the_card_and_continues(
        self, tmp_path: Path
    ) -> None:
        cards = _copy_cards(tmp_path)
        result = self._run(cards, cards / CARD.name, "--update-baseline")
        assert result.exit_code == 0, result.stdout
        written = (cards / "spec.baseline.toml").read_text()
        # 95 output tokens on the recorded trace, the same number the cost line prices.
        assert f'[cards."{CARD.name}"."default"]' in written
        assert "output_tokens = 647" in written

    def test_a_recorded_baseline_becomes_a_wire_on_the_next_run(self, tmp_path: Path) -> None:
        cards = _copy_cards(tmp_path)
        self._run(cards, cards / CARD.name, "--update-baseline")
        stdout = " ".join(self._run(cards, cards / CARD.name).stdout.split())
        assert "token_baseline 647, under 712" in stdout

    def test_recording_a_baseline_does_not_fail_the_card_that_set_it(self, tmp_path: Path) -> None:
        cards = _copy_cards(tmp_path)
        assert self._run(cards, cards / CARD.name, "--update-baseline").exit_code == 0
        assert self._run(cards, cards / CARD.name).exit_code == 0

    def test_a_run_past_the_tolerance_exits_one_as_a_failed_gate(self, tmp_path: Path) -> None:
        # Not a new exit code: the regression is a gate wire, so it fails like any other.
        cards = _copy_cards(tmp_path)
        (cards / "spec.baseline.toml").write_text(
            f'[cards."{CARD.name}"."default"]\noutput_tokens = 10\n'
        )
        result = self._run(cards, cards / CARD.name)
        assert result.exit_code == 1, result.stdout
        assert "token_baseline" in result.stdout

    def test_no_baseline_file_is_not_an_error_and_gates_nothing(self, tmp_path: Path) -> None:
        # A first install must run green. The free regression wire simply is not produced.
        cards = _copy_cards(tmp_path)
        assert not (cards / "spec.baseline.toml").exists()
        result = self._run(cards, cards / CARD.name)
        assert result.exit_code == 0, result.stdout
        assert "token_baseline" not in result.stdout

    def test_a_malformed_baseline_exits_two_not_three(self, tmp_path: Path) -> None:
        cards = _copy_cards(tmp_path)
        (cards / "spec.baseline.toml").write_text("<<<<<<< HEAD\nbroken = [\n")
        result = self._run(cards, cards / CARD.name)
        assert result.exit_code == 2, result.stdout
        assert "internal error" not in result.stdout

    def test_a_named_baseline_is_read_from_where_it_was_named(self, tmp_path: Path) -> None:
        # Not beside the card: the file the user named, keyed relative to itself.
        cards = _copy_cards(tmp_path)
        elsewhere = tmp_path / "ci" / "base.toml"
        elsewhere.parent.mkdir()
        key = lock_key(cards / CARD.name, elsewhere)
        elsewhere.write_text(f'[cards."{key}"."default"]\noutput_tokens = 10\n')
        result = self._run(cards, cards / CARD.name, "--baseline", str(elsewhere))
        # 95 tokens against a baseline of 10 is a regression, and a regression is a gate.
        assert result.exit_code == 1, result.stdout
        assert "token_baseline" in result.stdout

    def test_a_named_baseline_is_the_only_one_consulted(self, tmp_path: Path) -> None:
        # A file beside the card must not quietly win over the one the user named.
        cards = _copy_cards(tmp_path)
        (cards / "spec.baseline.toml").write_text(
            f'[cards."{CARD.name}"."default"]\noutput_tokens = 10\n'
        )
        empty = tmp_path / "empty.toml"
        empty.write_text("")
        assert self._run(cards, cards / CARD.name, "--baseline", str(empty)).exit_code == 0

    def test_a_failing_run_records_no_baseline(self, tmp_path: Path) -> None:
        """#102: a run whose gate failed does not get to say what normal costs.

        This reverses the earlier behaviour, which recorded and warned. A warning is
        fail-open on a gate-tier wire: the bar a broken run quietly raises is the bar the
        next run is measured against. The wire is still evaluated in-cell against the
        fresh median, so nothing about gating moves — only what reaches disk.
        """
        cards = _copy_cards(tmp_path)
        trace_path = cards / "traces" / "over-threshold-second-approval.1.otlp.json"
        trace_path.write_text(
            trace_path.read_text().replace("get_purchase_order", "pay_invoice")
        )
        result = self._run(cards, cards / CARD.name, "--update-baseline")
        assert result.exit_code == 1, result.stdout
        text = " ".join(result.stdout.split())
        assert "the baseline was not recorded" in text
        assert not (cards / "spec.baseline.toml").exists(), "a failing run wrote a baseline"

    def test_a_failing_run_leaves_a_committed_baseline_alone(self, tmp_path: Path) -> None:
        """The half that matters in a repo that already has one: it must not move."""
        cards = _copy_cards(tmp_path)
        self._run(cards, cards / CARD.name, "--update-baseline")
        committed = (cards / "spec.baseline.toml").read_text()

        trace_path = cards / "traces" / "over-threshold-second-approval.1.otlp.json"
        trace_path.write_text(
            trace_path.read_text().replace("get_purchase_order", "pay_invoice")
        )
        result = self._run(cards, cards / CARD.name, "--update-baseline")
        assert result.exit_code == 1, result.stdout
        assert (cards / "spec.baseline.toml").read_text() == committed

    def test_a_baseline_set_by_a_passing_run_says_nothing(self, tmp_path: Path) -> None:
        cards = _copy_cards(tmp_path)
        result = self._run(cards, cards / CARD.name, "--update-baseline")
        assert result.exit_code == 0
        assert "whose gate failed" not in result.stdout

    def test_a_trace_with_no_usage_refuses_and_writes_nothing(self, tmp_path: Path) -> None:
        # A recorded baseline of 0 would make every later run pass forever.
        cards = _copy_cards(tmp_path)
        trace_path = cards / "traces" / "over-threshold-second-approval.1.otlp.json"
        trace_path.write_text(
            trace_path.read_text().replace("gen_ai.usage.output_tokens", "gen_ai.usage.ignored")
        )
        result = self._run(cards, cards / CARD.name, "--update-baseline")
        assert result.exit_code == 2, result.stdout
        assert "gen_ai.usage.output_tokens" in result.stdout
        assert not (cards / "spec.baseline.toml").exists()

    def test_a_trace_that_spent_nothing_refuses_and_writes_nothing(self, tmp_path: Path) -> None:
        # Not the same fact as reporting no usage: this trace reported the attribute and
        # it summed to 0. Recorded, it would exit 3 on every later run of the card.
        cards = _copy_cards(tmp_path)
        trace_path = cards / "traces" / "over-threshold-second-approval.1.otlp.json"
        trace_path.write_text(
            trace_path.read_text()
            .replace('"intValue": "8"', '"intValue": "0"')
            .replace('"intValue": "87"', '"intValue": "0"')
        )
        result = self._run(cards, cards / CARD.name, "--update-baseline")
        assert result.exit_code == 2, result.stdout
        assert "totalling 0" in " ".join(result.stdout.split())
        assert not (cards / "spec.baseline.toml").exists()

    def test_a_hand_edited_zero_exits_two_not_three(self, tmp_path: Path) -> None:
        # The reader refuses what the writer can no longer produce, so a file already in
        # someone's repo reports itself rather than reading as specdeck breaking.
        cards = _copy_cards(tmp_path)
        (cards / "spec.baseline.toml").write_text(
            f'[cards."{CARD.name}"."default"]\noutput_tokens = 0\n'
        )
        result = self._run(cards, cards / CARD.name)
        assert result.exit_code == 2, result.stdout
        assert "internal error" not in result.stdout

    @pytest.mark.parametrize(
        "text",
        [
            '[cards."over-threshold-second-approval.md"]\noutput_tokens = 647\n',
            "cards = 5\n",
            '[cards]\n"over-threshold-second-approval.md" = 5\n',
        ],
    )
    def test_a_wrong_shaped_baseline_exits_two_not_three(self, tmp_path: Path, text: str) -> None:
        # Valid TOML, wrong structure — the natural hand-edit. A caller routing on the
        # exit code must not read a user's typo as "specdeck itself broke".
        cards = _copy_cards(tmp_path)
        (cards / "spec.baseline.toml").write_text(text)
        result = self._run(cards, cards / CARD.name)
        assert result.exit_code == 2, result.stdout
        assert "internal error" not in result.stdout

    def test_a_run_that_never_started_leaves_a_committed_baseline_alone(
        self, tmp_path: Path
    ) -> None:
        # The file is written only once the cell has run. Written before, a refusal down
        # in `run_cell` would have overwritten a committed number from a cell that never
        # ran — and, exiting before the report, without even the note that says so.
        cards = _copy_cards(tmp_path)
        committed = cards / "spec.baseline.toml"
        before = f'[cards."{CARD.name}"."default"]\noutput_tokens = 647\n'
        committed.write_text(before)
        result = self._run(cards, cards / CARD.name, "--runs", "5", "--update-baseline")
        assert result.exit_code == 2, result.stdout
        assert committed.read_text() == before

    def test_a_baseline_path_that_cannot_be_written_exits_two(self, tmp_path: Path) -> None:
        # A path the user named is part of the invocation, the rule --junit-xml and
        # --rates already follow. Exit 3 would report a typo as an internal defect.
        cards = _copy_cards(tmp_path)
        result = self._run(
            cards,
            cards / CARD.name,
            "--baseline",
            str(tmp_path / "absent" / "base.toml"),
            "--update-baseline",
        )
        assert result.exit_code == 2, result.stdout
        assert "cannot write the baseline" in result.stdout
        # The cell had already run and its report had already printed: the file is the
        # only thing lost.
        assert "gate" in result.stdout

    def test_a_spread_wider_than_the_tolerance_fails_the_run_that_recorded_it(
        self, tmp_path: Path
    ) -> None:
        # measurement.md says this out loud rather than leaving it to be discovered: the
        # fresh median gates the same run, so at n=3 with k=3 a single run more than 10%
        # above the median fails the invocation that recorded it. 95, 95, 123 -> median
        # 95, bound 105, run 3 over it.
        cards = _copy_cards(tmp_path)
        traces = cards / "traces"
        cheap = traces / "over-threshold-second-approval.1.otlp.json"
        dear = traces / "dear.otlp.json"
        dear.write_text(cheap.read_text().replace('"intValue": "87"', '"intValue": "115"'))
        result = invoke(
            str(cards / CARD.name),
            "--trace",
            str(cheap),
            "--trace",
            str(cheap),
            "--trace",
            str(dear),
            "--cassettes",
            str(cards / "cassettes"),
            "--update-baseline",
        )
        assert result.exit_code == 1, result.stdout
        text = " ".join(result.stdout.split())
        assert "token_baseline 123, under 105" in text
        assert "2/3 runs" in text
        # The wire is still folded in and still fires — that is what the two assertions
        # above prove. What #102 changed is only what reaches disk: this invocation
        # failed, so it records nothing.
        assert not (cards / "spec.baseline.toml").exists()
        assert "the baseline was not recorded" in text


class TestJUnitOutput:
    def test_a_passing_run_writes_a_parseable_suite(self, tmp_path: Path) -> None:
        report = tmp_path / "r.xml"
        assert demo("--junit-xml", str(report)).exit_code == 0
        root = ET.fromstring(report.read_text())
        assert root.tag == "testsuites"
        suite = root.find("testsuite")
        assert suite.get("name") == str(CARD)
        assert suite.get("failures") == "0"

    def test_a_failing_run_still_writes_the_report(self, tmp_path: Path) -> None:
        # The whole point in CI: the red build is the one that needs the file.
        cards = _copy_cards(tmp_path)
        trace_path = cards / "traces" / "over-threshold-second-approval.1.otlp.json"
        trace_path.write_text(
            trace_path.read_text().replace("get_purchase_order", "pay_invoice")
        )
        report = tmp_path / "r.xml"
        result = invoke(
            str(cards / CARD.name),
            "--trace",
            str(trace_path),
            "--cassettes",
            str(cards / "cassettes"),
            "--junit-xml",
            str(report),
        )
        assert result.exit_code == 1, result.stdout
        root = ET.fromstring(report.read_text())
        assert root.get("failures") == "1"
        failure = root.find(".//failure")
        assert "never:pay_invoice" in failure.text
        assert "the cell needs 1 of 1 and got 0" in failure.get("message")

    def test_a_path_that_cannot_be_written_exits_two(self, tmp_path: Path) -> None:
        # A named file is part of the invocation, the rule --rates already follows. CI
        # silently receiving no report is worse than a loud refusal.
        result = demo("--junit-xml", str(tmp_path / "absent" / "r.xml"))
        assert result.exit_code == 2, result.stdout
        assert "cannot write the JUnit report" in result.stdout

    def test_the_bytes_match_the_encoding_the_document_declares(self, tmp_path: Path) -> None:
        # The declaration says utf-8 in band, so the file has to be utf-8 whatever the
        # host's locale is. Left to `write_text`'s default, a latin-1 host raises on the
        # em dash every summary carries (exit 1, a card that honestly failed) and a cp1252
        # host raises nothing at all — CI just receives a document that will not parse.
        # Asserted on the bytes rather than by forcing a locale, which is not something a
        # test can change for an already-running interpreter.
        report = tmp_path / "r.xml"
        assert demo("--junit-xml", str(report)).exit_code == 0
        raw = report.read_bytes()
        assert b"encoding='utf-8'" in raw
        assert "—".encode() in raw
        assert ET.fromstring(raw.decode()).find(".//system-out") is not None

    def test_no_flag_writes_no_file(self, tmp_path: Path) -> None:
        cards = _copy_cards(tmp_path)
        before = sorted(p.name for p in cards.iterdir())
        _run_copy(cards, cards / CARD.name)
        assert sorted(p.name for p in cards.iterdir()) == before

    def test_a_run_that_never_produced_a_cell_writes_no_report(self, tmp_path: Path) -> None:
        # An empty green suite for a run that never started would be a lie.
        cards = _copy_cards(tmp_path)
        (cards / "spec.lock.toml").unlink()
        report = tmp_path / "r.xml"
        result = invoke(
            str(cards / CARD.name),
            "--trace",
            str(cards / "traces" / "over-threshold-second-approval.1.otlp.json"),
            "--cassettes",
            str(cards / "cassettes"),
            "--junit-xml",
            str(report),
        )
        assert result.exit_code == 2
        assert not report.exists()


class TestTheExitCodeRegistry:
    def test_it_names_every_code_the_runner_issues(self) -> None:
        # Written down in one place so a later command extends it rather than colliding
        # with it. 4 is the matrix budget abort (#15), reserved by wave 3 and now issued.
        assert sorted(EXIT_CODES) == [0, 1, 2, 3, 4]

    def test_the_readme_paragraph_names_every_code_the_registry_holds(self) -> None:
        # The paragraph, not the file: `0` appears in a README all over the place, so a
        # bare containment check stays green through an edit that deletes the sentence.
        readme = (Path(__file__).resolve().parent.parent / "README.md").read_text()
        paragraph = next(
            block for block in readme.split("\n\n") if block.startswith("Exit codes are")
        )
        flat = " ".join(paragraph.split())
        for code in EXIT_CODES:
            assert f"`{code}`" in flat, flat
        assert "specdeck itself broke" in flat


class TestTheCardDeclaresItsOwnTraces:
    """The card-to-trace binding lives in the card, not in the shell history of whoever
    invoked the runner."""

    def test_a_card_needs_no_flag_at_all(self) -> None:
        result = invoke(str(CARD))
        assert result.exit_code == 0, result.stdout
        assert "1/1 runs" in result.stdout

    def test_trace_still_overrides_the_declaration(self, tmp_path: Path) -> None:
        # Proof the declaration did not quietly win: the override points at a trace the
        # card fails against, and the run has to fail.
        cards = _copy_cards(tmp_path)
        broken = tmp_path / "broken.otlp.json"
        broken.write_text(
            (CARDS / "traces" / "over-threshold-second-approval.1.otlp.json")
            .read_text()
            .replace("get_purchase_order", "pay_invoice")
        )
        result = invoke(
            str(cards / CARD.name),
            "--trace",
            str(broken),
            "--cassettes",
            str(cards / "cassettes"),
        )
        assert result.exit_code == 1, result.stdout

    def test_a_card_declaring_nothing_and_given_nothing_is_a_user_error(
        self, tmp_path: Path
    ) -> None:
        cards = _copy_cards(tmp_path)
        card = cards / CARD.name
        card.write_text(
            card.read_text().replace("  traces: traces/over-threshold-second-approval.otlp.json\n", "")
        )
        result = invoke(str(card), "--relock")
        assert result.exit_code == 2
        assert "no traces to run" in " ".join(result.stdout.split())

    def test_a_glob_that_matches_nothing_refuses_rather_than_running_empty(
        self, tmp_path: Path
    ) -> None:
        # A card evaluating zero traces passes every wire it has and reports green.
        cards = _copy_cards(tmp_path)
        card = cards / CARD.name
        card.write_text(
            card.read_text().replace(
                "traces: traces/over-threshold-second-approval.otlp.json",
                "traces: traces/absent-*.json",
            )
        )
        result = invoke(str(card))
        assert result.exit_code == 2
        unwrapped = " ".join(result.stdout.split())
        assert "matches no file" in unwrapped and "absent-*.json" in unwrapped


class TestTheWholeDeck:
    """`specdeck run cards/` — rows, not a third axis."""

    def test_the_committed_deck_runs_green_offline(self) -> None:
        result = invoke(str(CARDS))
        assert result.exit_code == 0, result.stdout
        assert "5 cards, 5 passed" in " ".join(result.stdout.split())

    def test_it_names_every_card_it_ran(self) -> None:
        stdout = invoke(str(CARDS)).stdout
        for path in sorted(CARDS.glob("*.md")):
            assert path.stem in stdout

    def test_one_failing_card_exits_one_and_the_other_four_still_report(
        self, tmp_path: Path
    ) -> None:
        # A deck that aborts on the first failure hides four results that were free.
        cards = _copy_cards(tmp_path)
        trace_path = cards / "traces" / "over-threshold-second-approval.1.otlp.json"
        trace_path.write_text(
            trace_path.read_text().replace("get_purchase_order", "pay_invoice")
        )
        result = invoke(str(cards))
        assert result.exit_code == 1, result.stdout
        unwrapped = " ".join(result.stdout.split())
        assert "5 cards, 4 passed" in unwrapped
        assert "over-threshold-second-approval" in unwrapped

    def test_one_card_that_cannot_start_exits_two_and_the_rest_still_run(
        self, tmp_path: Path
    ) -> None:
        # 2 outranks 1: a deck missing a card has not answered the question asked, and a
        # CI reading 1 would call that an eval regression.
        cards = _copy_cards(tmp_path)
        card = cards / CARD.name
        card.write_text(card.read_text().replace("requests a second approval", "requests a countersignature"))
        result = invoke(str(cards))
        assert result.exit_code == 2, result.stdout
        unwrapped = " ".join(result.stdout.split())
        assert "4 cards, 4 passed, 1 could not run" in unwrapped

    def test_a_card_that_does_not_parse_is_one_error_among_the_results(
        self, tmp_path: Path
    ) -> None:
        cards = _copy_cards(tmp_path)
        (cards / "not-a-card.md").write_text("no heading here\n")
        result = invoke(str(cards))
        assert result.exit_code == 2, result.stdout
        assert "4 cards, 4 passed, 1 could not run" in " ".join(result.stdout.split())

    def test_an_empty_directory_is_a_user_error_never_a_green_deck(self, tmp_path: Path) -> None:
        # "All zero of them passed" is the empty report a deck exists to make impossible.
        empty = tmp_path / "deck"
        empty.mkdir()
        result = invoke(str(empty))
        assert result.exit_code == 2
        assert "no cards under" in result.stdout

    def _nested(self, tmp_path: Path) -> Path:
        """The deck with one card moved into a subdirectory, everything it needs beside it.

        The lockfile stays at the deck root, and its entry is re-keyed the way `lock_key`
        writes one for a card in a subdirectory (#61).
        """
        cards = _copy_cards(tmp_path)
        nested = cards / "sub"
        (nested / "traces").mkdir(parents=True)
        (nested / "policy").mkdir()
        (nested / "fixtures").mkdir()
        (cards / CARD.name).rename(nested / CARD.name)
        for source, name in (
            (cards / "traces", TRACE.name),
            (cards / "policy", "airline.md"),
            (cards / "fixtures", "data.json"),
        ):
            shutil.copy(source / name, nested / source.name / name)
        lock = cards / "spec.lock.toml"
        lock.write_text(
            lock.read_text().replace(f'[cards."{CARD.name}"]', f'[cards."sub/{CARD.name}"]')
        )
        return cards

    def test_the_lockfile_resolves_from_the_deck_root(self, tmp_path: Path) -> None:
        # Green only if the nested card verified against the ROOT lockfile under its
        # `sub/<card>.md` key. Resolved from the card's own parent instead, there is no
        # lockfile beside it and the deck exits 2.
        cards = self._nested(tmp_path)
        result = invoke(str(cards), "--cassettes", str(cards / "cassettes"))
        assert result.exit_code == 0, result.stdout
        assert "5 cards, 5 passed" in " ".join(result.stdout.split())

    def test_the_baseline_resolves_from_the_deck_root_too(self, tmp_path: Path) -> None:
        # Resolved from the nested card's own parent instead, there is no baseline beside
        # it, the regression wire is never built, and the deck reports green over a
        # regression the same card fails when it is run on its own.
        cards = self._nested(tmp_path)
        (cards / "spec.baseline.toml").write_text(
            f'[cards."sub/{CARD.name}".default]\noutput_tokens = 1\n'
        )
        result = invoke(str(cards), "--cassettes", str(cards / "cassettes"))
        assert result.exit_code == 1, result.stdout
        unwrapped = " ".join(result.stdout.split())
        assert "5 cards, 4 passed" in unwrapped
        assert "token_baseline" in unwrapped

    def test_a_nested_card_keyed_under_its_bare_name_is_stale(self, tmp_path: Path) -> None:
        # The other side of the same key: the bare name is not what the runner writes for
        # a card in a subdirectory, so the deck must not accept it.
        cards = self._nested(tmp_path)
        lock = cards / "spec.lock.toml"
        lock.write_text(
            lock.read_text().replace(f'[cards."sub/{CARD.name}"]', f'[cards."{CARD.name}"]')
        )
        result = invoke(str(cards), "--cassettes", str(cards / "cassettes"))
        assert result.exit_code == 2, result.stdout
        assert f"sub/{CARD.name}" in " ".join(result.stdout.split())

    @pytest.mark.parametrize(
        "flag,extra",
        [
            ("--relock", []),
            ("--trace", [str(TRACE)]),
            ("--agent", ["tests.fake_agent:FakeAgent"]),
            ("--matrix", ["matrix.toml"]),
            ("--junit-xml", ["out.xml"]),
            ("--update-baseline", []),
        ],
    )
    def test_a_one_card_flag_is_refused_with_a_reason(self, flag: str, extra: list[str]) -> None:
        result = invoke(str(CARDS), flag, *extra)
        assert result.exit_code == 2, result.stdout
        unwrapped = " ".join(result.stdout.split())
        # The reason, not a bare refusal: every one of these has an obvious next question,
        # and a caller who reads why knows whether to loop over the cards themselves.
        assert f"{flag} takes one card, not a directory —" in unwrapped

    def test_a_latency_budget_nothing_could_meet_is_a_user_error_not_a_crash(self) -> None:
        # `BuiltinConfig` validates the number, and a `ValidationError` is not a
        # USER_ERROR — unchecked here, a flag the user typed scores as specdeck breaking,
        # after the whole deck has already been read.
        result = invoke(str(CARDS), "--latency-budget", "0")
        assert result.exit_code == 2, result.stdout
        assert "internal error" not in result.stdout
        assert "--latency-budget takes a positive number" in " ".join(result.stdout.split())

    def test_a_named_rate_table_that_does_not_exist_is_refused_before_anything_runs(
        self, tmp_path: Path
    ) -> None:
        # A deck under --live would otherwise make every judge call and then exit 2 on a
        # mistyped path. Checked here rather than timed: no card's verdict is printed.
        result = invoke(str(CARDS), "--rates", str(tmp_path / "absent.toml"))
        assert result.exit_code == 2, result.stdout
        assert "PASS" not in result.stdout and "FAIL" not in result.stdout

    def test_a_glob_matching_only_a_directory_is_one_error_among_five(self, tmp_path: Path) -> None:
        # `IsADirectoryError` is not a `USER_ERROR`, so a directory reaching `load_trace`
        # escapes the per-card catch: exit 3, "specdeck itself broke", and four healthy
        # cards produce no result at all.
        cards = _copy_cards(tmp_path)
        (cards / "traces" / "archive").mkdir()
        card = cards / CARD.name
        card.write_text(
            card.read_text().replace(
                "traces: traces/over-threshold-second-approval.otlp.json", "traces: traces/arch*"
            )
        )
        result = invoke(str(cards))
        assert result.exit_code == 2, result.stdout
        unwrapped = " ".join(result.stdout.split())
        assert "internal error" not in unwrapped
        assert "4 cards, 4 passed, 1 could not run" in unwrapped

    def test_a_simulator_model_disagreeing_with_the_pin_is_refused_over_a_deck_too(self) -> None:
        # `--judge-model`'s sibling in the lock, and the one live in replay mode. A deck
        # that silently accepts a flag a card rejects is a green run that verified nothing
        # about the pin.
        result = invoke(str(CARDS), "--simulator-model", "gpt-4o")
        assert result.exit_code == 2, result.stdout
        unwrapped = " ".join(result.stdout.split())
        assert "--simulator-model gpt-4o disagrees with the pinned" in unwrapped

    def test_a_cap_with_no_matrix_to_cap_is_refused_over_a_deck_too(self) -> None:
        # `--matrix` is refused with a directory, so a cap here can never cap anything.
        # Silently accepting a flag the single-card path rejects is the drift to avoid.
        result = invoke(str(CARDS), "--budget-usd", "5.00")
        assert result.exit_code == 2, result.stdout
        assert "--budget-usd applies to --matrix" in " ".join(result.stdout.split())
