"""`specdeck run --agent`: the loop reached from the command line, offline.

The agent is scripted and every model call replays a cassette, so this runs with no key.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from specdeck.card import CardError, parse
from specdeck.cli import _adapter, _resolve, app
from specdeck.judge import Cassette, criteria_of
from specdeck.judge import build_prompt as judge_prompt
from specdeck.lockfile import Lockfile
from specdeck.loop import run_agent
from specdeck.trace import SEMCONV

from .fake_agent import BareAgent, FakeAgent, refuses
from .fake_graph import FakeCompiled, refund_graph
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
    Cassette(cassettes, slug=card.slug).write(
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
        # Read as a lockfile, not as text: `model = "claude-sonnet-5"` also appears under
        # [judge], so a substring check passes on a simulator pin that was never written.
        assert Lockfile.load(workspace / "spec.lock.toml").simulator_model == MODEL

    def test_an_unpinned_simulator_refuses_to_run(self, workspace: Path) -> None:
        # An unpinned judge is not a test, and the simulator shifts pass rates the same way.
        result = run(workspace, "--relock")
        assert result.exit_code == 2
        assert "simulator" in result.stdout
        # And the hint has to name the flag that actually moves the pin: --relock alone is
        # what this run already did, so advising it again loops the reader (#76). Rich
        # soft-wraps at 80 columns under CliRunner, so unwrap before asserting the pair.
        assert "--relock --simulator-model" in " ".join(result.stdout.split())

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


class TestAdapterForms:
    """A class, a factory, and an instance all resolve — and a class is instantiated.

    `runtime_checkable` protocols check attribute presence, and a class carries `run` as
    an unbound function, so an uninstantiated class passes the guard and then dies at the
    first turn with `run() missing 1 required positional argument`. README documents the
    class form, which is how this was found.
    """

    def test_a_class_is_instantiated(self) -> None:
        adapter = _adapter("tests.fake_agent:BareAgent")
        assert isinstance(adapter, BareAgent)
        assert not isinstance(adapter, type)

    def test_a_resolved_class_can_actually_take_a_turn(self) -> None:
        adapter = _adapter("tests.fake_agent:BareAgent")
        assert asyncio.run(adapter.run([], [], {}))

    def test_a_factory_is_called(self) -> None:
        assert isinstance(_adapter("tests.fake_agent:refusing_agent"), FakeAgent)

    def test_an_instance_is_taken_as_it_stands(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import tests.fake_agent as module

        instance = FakeAgent(refuses())
        monkeypatch.setattr(module, "ready_agent", instance, raising=False)
        assert _adapter("tests.fake_agent:ready_agent") is instance

    def test_something_with_no_run_is_refused(self) -> None:
        with pytest.raises(CardError, match=r"run\(messages"):
            _adapter("tests.fake_agent:refuses")


class TestRelockKeepsWhatItWasNotGiven:
    def test_a_trace_mode_relock_does_not_clear_the_simulator_pin(self, workspace: Path) -> None:
        # Otherwise every later --agent run reads as StaleLock, from a relock that never
        # mentioned the simulator.
        run(workspace, "--relock", "--simulator-model", MODEL)
        recorded = (
            Path(__file__).resolve().parent.parent
            / "cards"
            / "traces"
            / "basic-economy-return-change.otlp.json"
        )
        result = runner.invoke(
            app,
            ["run", str(workspace / "refused.md"), "--trace", str(recorded), "--relock"],
        )
        # The run itself has no judge cassette for this pairing and stops at exit 2; the
        # lock is written before that, which is the state under test.
        assert result.exit_code in (1, 2)
        assert Lockfile.load(workspace / "spec.lock.toml").simulator_model == MODEL


def flat(text: str) -> str:
    """Console output with its wrapping undone. Rich wraps at the terminal width, so an
    assertion on a phrase is otherwise an assertion about where the line broke."""
    return " ".join(text.split())


class TestTheAgentDefinitionFlag:
    """`specdeck lint --agent-def`: zero tokens, no network, one user module imported."""

    def _deck(self, tmp_path: Path) -> Path:
        (tmp_path / "a.md").write_text(
            "# Scenario: a\nThe agent answers.\n\nwire:\n  - tools: at_most 3\n"
        )
        return tmp_path

    def test_the_depth_line_names_the_tier_and_the_counts(self, tmp_path: Path) -> None:
        result = runner.invoke(
            app,
            ["lint", str(self._deck(tmp_path)), "--agent-def", "tests.fake_graph:refund_graph"],
        )
        assert result.exit_code == 0
        assert "topology depth: 2 tools, 3 edges, 1 cycle, 1 HITL point" in flat(result.stdout)
        assert "via langgraph" in flat(result.stdout)

    def test_without_the_flag_the_line_says_it_was_not_introspected(self, tmp_path: Path) -> None:
        result = runner.invoke(app, ["lint", str(self._deck(tmp_path))])
        assert result.exit_code == 0
        assert "not introspected" in flat(result.stdout)

    def test_a_reference_that_does_not_import_is_a_user_error(self, tmp_path: Path) -> None:
        result = runner.invoke(
            app, ["lint", str(self._deck(tmp_path)), "--agent-def", "nope.nothing:here"]
        )
        assert result.exit_code == 2
        assert "nope.nothing:here" in flat(result.stdout)

    def test_a_reference_that_is_not_module_attribute_is_a_user_error(self, tmp_path: Path) -> None:
        result = runner.invoke(app, ["lint", str(self._deck(tmp_path)), "--agent-def", "bare"])
        assert result.exit_code == 2
        assert "--agent-def" in flat(result.stdout)

    def test_an_object_nothing_can_read_is_blindness_to_report_not_a_user_error(
        self, tmp_path: Path
    ) -> None:
        # We could not read it; that is a depth to state, not a mistake the user made.
        result = runner.invoke(
            app, ["lint", str(self._deck(tmp_path)), "--agent-def", "tests.fake_agent:BareAgent"]
        )
        assert result.exit_code == 0
        assert "via none — none depth" in flat(result.stdout)

    def test_an_unbounded_cycle_reds_the_lint(self, tmp_path: Path) -> None:
        (tmp_path / "a.md").write_text("# Scenario: a\nThe agent answers.\n")
        result = runner.invoke(
            app, ["lint", str(tmp_path), "--agent-def", "tests.fake_graph:refund_graph"]
        )
        assert result.exit_code == 1
        assert "unbounded-cycle" in flat(result.stdout)


class TestResolvingAReferenceNeverRunsIt:
    """`_resolve` is what `--agent-def` points at arbitrary user objects with."""

    def test_a_factory_is_still_called(self) -> None:
        assert isinstance(
            _resolve("tests.fake_graph:refund_graph", flag="--agent-def"), FakeCompiled
        )

    def test_an_object_that_is_no_adapter_is_taken_as_it_stands_never_called(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The regression guard for the old `or not isinstance(found, AgentAdapter)`.

        A compiled graph satisfies no protocol of ours, so under the old disjunct
        `--agent-def` would have *invoked* the user's agent just to look at it.
        """
        import tests.fake_graph as module

        compiled = refund_graph()
        monkeypatch.setattr(module, "ready_graph", compiled, raising=False)
        assert _resolve("tests.fake_graph:ready_graph", flag="--agent-def") is compiled

    def test_the_flag_name_reaches_the_message(self) -> None:
        with pytest.raises(CardError, match="--agent-def"):
            _resolve("nope.nothing:here", flag="--agent-def")
