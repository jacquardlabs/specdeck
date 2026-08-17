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
TRACE = CARDS / "traces" / "run-01.otlp.json"

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
        str(cards / "traces" / "run-01.otlp.json"),
        "--runs",
        "1",
        "--pass-threshold",
        "1",
        "--cassettes",
        str(cards / "cassettes"),
    )
