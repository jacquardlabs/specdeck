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
CARD = CARDS / "basic-economy-return-change.md"
TRACE = CARDS / "traces" / "basic-economy-return-change.otlp.json"

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
        # 3.87s is the recorded root span's own duration, the same number the `latency`
        # wire prints one block further down.
        assert "latency p50 3.87s, p95 3.87s over 1 run" in stdout
        assert "variance n/a — 1 passing run" in stdout

    def test_the_dollar_figure_is_an_estimate_and_says_whose_tokens(self) -> None:
        # 241 input and 95 output tokens of claude-sonnet-5, off the recorded trace, at
        # the built-in table's rate. No key, no network: the figure is arithmetic.
        stdout = " ".join(demo().stdout.split())
        assert "cost ~$0.0014 estimate (rates as of" in stdout
        assert "agent tokens only" in stdout
        for billing in ("billed", "charged", "invoice"):
            assert billing not in stdout

    def test_a_clean_run_prints_no_waste_block(self) -> None:
        assert "waste" not in demo().stdout

    def test_every_wire_holds(self) -> None:
        stdout = demo().stdout
        for wire in ("never:update_reservation_flights", "at_most:search_direct_flight"):
            assert wire in stdout
        assert "FAIL" not in stdout


class TestTheLockIsEnforced:
    def test_an_edited_card_refuses_to_run(self, tmp_path: Path) -> None:
        edited = _copy_cards(tmp_path)
        card = edited / CARD.name
        card.write_text(card.read_text().replace("refuses to", "declines to"))
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
        # 241 input + 95 output tokens at $100/M is $0.0336, against $0.0014 built-in.
        assert "~$0.0336 estimate" in " ".join(_run_copy(cards, cards / CARD.name).stdout.split())

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
        assert "~$0.0014 estimate" in text


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
        str(cards / "traces" / "basic-economy-return-change.otlp.json"),
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
        trace_path = cards / "traces" / "basic-economy-return-change.otlp.json"
        broken = trace_path.read_text().replace(
            "get_reservation_details", "update_reservation_flights"
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
            str(cards / "traces" / "basic-economy-return-change.otlp.json"),
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
            str(cards / "traces" / "basic-economy-return-change.otlp.json"),
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
            str(cards / "traces" / "basic-economy-return-change.otlp.json"),
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
            str(cards / "traces" / "basic-economy-return-change.otlp.json"),
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
            str(cards / "traces" / "basic-economy-return-change.otlp.json"),
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
            str(cards / "traces" / "basic-economy-return-change.otlp.json"),
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
            str(cards / "traces" / "basic-economy-return-change.otlp.json"),
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
        assert "output_tokens = 95" in written

    def test_a_recorded_baseline_becomes_a_wire_on_the_next_run(self, tmp_path: Path) -> None:
        cards = _copy_cards(tmp_path)
        self._run(cards, cards / CARD.name, "--update-baseline")
        stdout = " ".join(self._run(cards, cards / CARD.name).stdout.split())
        assert "token_baseline 95, under 105" in stdout

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

    def test_a_trace_with_no_usage_refuses_and_writes_nothing(self, tmp_path: Path) -> None:
        # A recorded baseline of 0 would make every later run pass forever.
        cards = _copy_cards(tmp_path)
        trace_path = cards / "traces" / "basic-economy-return-change.otlp.json"
        trace_path.write_text(
            trace_path.read_text().replace("gen_ai.usage.output_tokens", "gen_ai.usage.ignored")
        )
        result = self._run(cards, cards / CARD.name, "--update-baseline")
        assert result.exit_code == 2, result.stdout
        assert "gen_ai.usage.output_tokens" in result.stdout
        assert not (cards / "spec.baseline.toml").exists()


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
        trace_path = cards / "traces" / "basic-economy-return-change.otlp.json"
        trace_path.write_text(
            trace_path.read_text().replace("get_reservation_details", "update_reservation_flights")
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
        assert "never:update_reservation_flights" in failure.text
        assert "the cell needs 1 of 1 and got 0" in failure.get("message")

    def test_a_path_that_cannot_be_written_exits_two(self, tmp_path: Path) -> None:
        # A named file is part of the invocation, the rule --rates already follows. CI
        # silently receiving no report is worse than a loud refusal.
        result = demo("--junit-xml", str(tmp_path / "absent" / "r.xml"))
        assert result.exit_code == 2, result.stdout
        assert "cannot write the JUnit report" in result.stdout

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
            str(cards / "traces" / "basic-economy-return-change.otlp.json"),
            "--cassettes",
            str(cards / "cassettes"),
            "--junit-xml",
            str(report),
        )
        assert result.exit_code == 2
        assert not report.exists()


class TestTheExitCodeRegistry:
    def test_it_names_every_code_the_runner_issues(self) -> None:
        # Written down in one place so a later wave extends it rather than colliding with
        # it. 4 is reserved for the matrix budget abort (#15) and is not issued yet.
        assert sorted(EXIT_CODES) == [0, 1, 2, 3]

    def test_the_registry_agrees_with_what_the_readme_documents(self) -> None:
        readme = (Path(__file__).resolve().parent.parent / "README.md").read_text()
        for code in EXIT_CODES:
            assert f"`{code}`" in readme
