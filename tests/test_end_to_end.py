"""The tracer's closing condition: a clean clone runs the card green, with no API key.

If this test needs a network call or an environment variable to pass, the tracer has not
delivered what #48 said it would.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

from specdeck.cli import app

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

    def test_a_broken_table_exits_two_not_three(self, tmp_path: Path) -> None:
        broken = tmp_path / "rates.toml"
        broken.write_text('[rates.anthropic]\n"claude-sonnet-5" = { input = 1.0, output = 1.0 }\n')
        assert demo("--rates", str(broken)).exit_code == 2


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
