"""`specdeck run --agent`: the loop reached from the command line, offline.

The agent is scripted and every model call replays a cassette, so this runs with no key.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from specdeck.card import parse
from specdeck.cli import app
from specdeck.judge import Cassette, criteria_of
from specdeck.judge import build_prompt as judge_prompt
from specdeck.loop import run_agent
from specdeck.trace import SEMCONV

from .fake_agent import FakeAgent, refuses
from .test_loop import AGENT_TURN_1, MODEL, record

runner = CliRunner()

CARD = """\
# Scenario: cancellation refused
context:
  simulator: "traveller wants SI5UKW cancelled and will not take no for an answer"

The agent refuses to cancel the basic economy fare and explains why.

wire:
  - cancel_reservation: never
"""

VOCABULARY = """\
[tools]
get_reservation_details
cancel_reservation

[markers]
non_agreement
"""

CONVERSATION = [
    {"reply": "Cancel SI5UKW please.", "marker": None, "then": AGENT_TURN_1},
    {"reply": "That is not good enough.", "marker": "non_agreement", "done": True},
]


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A card, its vocabulary, and every cassette the run will ask for."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    card_path = tmp_path / "refused.md"
    card_path.write_text(CARD)
    (tmp_path / "vocabulary.txt").write_text(VOCABULARY)
    cassettes = tmp_path / "cassettes"
    cassettes.mkdir()

    card = parse(card_path)
    record(cassettes, card, CONVERSATION)

    # The judge cassette is keyed on the trace, so the loop has to run once here to
    # produce it. The CLI then replays the same conversation and builds the same prompt,
    # which is the determinism the cassette model already depends on.
    import asyncio

    trace = asyncio.run(
        run_agent(
            card,
            FakeAgent(refuses()),
            cassettes=cassettes,
            simulator_model=MODEL,
            semconv=SEMCONV,
            markers=["non_agreement"],
        )
    )
    criteria = criteria_of(card)
    Cassette(cassettes).write(
        judge_prompt(criteria, trace, policy=""),
        MODEL,
        json.dumps({"verdicts": {c.id: True for c in criteria}}),
        criteria=[c.id for c in criteria],
    )
    return tmp_path


def run(workspace: Path, *extra: str):
    return runner.invoke(
        app,
        [
            "run",
            str(workspace / "refused.md"),
            "--agent",
            "tests.fake_agent:refusing_agent",
            "--vocabulary",
            str(workspace / "vocabulary.txt"),
            "--runs",
            "1",
            "--pass-threshold",
            "1",
            *extra,
        ],
    )


class TestRunningTheAgent:
    def test_a_card_runs_against_the_agent_with_no_key(self, workspace: Path) -> None:
        result = run(workspace, "--relock", "--simulator-model", MODEL)
        assert result.exit_code == 0, result.stdout
        assert "PASS" in result.stdout

    def test_the_simulator_is_pinned_like_the_judge(self, workspace: Path) -> None:
        run(workspace, "--relock", "--simulator-model", MODEL)
        lock = (workspace / "spec.lock.toml").read_text()
        assert f'model = "{MODEL}"' in lock
        assert "[simulator]" in lock

    def test_an_unpinned_simulator_refuses_to_run(self, workspace: Path) -> None:
        # An unpinned judge is not a test, and the simulator shifts pass rates the same way.
        result = run(workspace, "--relock")
        assert result.exit_code == 2
        assert "simulator" in result.stdout

    def test_a_disagreeing_simulator_pin_is_drift(self, workspace: Path) -> None:
        run(workspace, "--relock", "--simulator-model", MODEL)
        result = run(workspace, "--simulator-model", "some-other-model")
        assert result.exit_code == 2
        assert "disagrees" in result.stdout


class TestFlagPairing:
    def test_neither_trace_nor_agent_is_refused(self, workspace: Path) -> None:
        result = runner.invoke(app, ["run", str(workspace / "refused.md")])
        assert result.exit_code == 2
        assert "exactly one" in result.stdout

    def test_both_at_once_is_refused(self, workspace: Path) -> None:
        result = run(workspace, "--trace", "anything.json")
        assert result.exit_code == 2
        assert "exactly one" in result.stdout


class TestAdapterResolution:
    def test_a_reference_that_is_not_module_attribute_says_so(self, workspace: Path) -> None:
        result = runner.invoke(
            app,
            [
                "run",
                str(workspace / "refused.md"),
                "--agent",
                "tests.fake_agent",
                "--relock",
                "--simulator-model",
                MODEL,
            ],
        )
        assert result.exit_code == 2
        assert "module:attribute" in result.stdout

    def test_a_missing_module_names_itself(self, workspace: Path) -> None:
        result = runner.invoke(
            app,
            [
                "run",
                str(workspace / "refused.md"),
                "--agent",
                "nope.nothing:Agent",
                "--relock",
                "--simulator-model",
                MODEL,
            ],
        )
        assert result.exit_code == 2

    def test_something_that_is_not_an_adapter_is_refused_before_any_call(
        self, workspace: Path
    ) -> None:
        result = runner.invoke(
            app,
            [
                "run",
                str(workspace / "refused.md"),
                "--agent",
                "tests.fake_agent:refuses",
                "--relock",
                "--simulator-model",
                MODEL,
            ],
        )
        assert result.exit_code == 2
        assert "run(messages" in result.stdout
