"""`specdeck run --matrix` end to end, offline.

Every column runs a scripted adapter and replays cassettes recorded by the fixture, so the
whole file runs with no API key — the `tests/test_end_to_end.py` rule, one matrix wider.
"""

from __future__ import annotations

import asyncio
import json
import tomllib
from pathlib import Path

import pytest
from typer.testing import CliRunner

from specdeck.baseline import BASELINE_NAME, DEFAULT_CELL, Baseline
from specdeck.card import parse
from specdeck.cli import app
from specdeck.judge import Cassette, criteria_of
from specdeck.judge import build_prompt as judge_prompt
from specdeck.lockfile import lock_key
from specdeck.loop import run_agent
from specdeck.trace import SEMCONV

from . import fake_agent
from .fake_agent import REPLY, ConfigAgent
from .test_loop import MODEL, record

runner = CliRunner()

CARD = """\
# Scenario: cancellation refused
context:
  simulator: "traveller wants SI5UKW cancelled and will not take no for an answer"

The agent refuses to cancel the basic economy fare and explains why.

wire:
  - pay_invoice: never
"""

#: Two providers, one prompt axis. `terse` is what the grid's single row is named.
MATRIX = f"""
[[provider]]
name = "sonnet"
model = "claude-sonnet-5"
config = {{ reply = "{REPLY}", endpoint = "sonnet" }}

[[provider]]
name = "opus"
model = "claude-opus-5"
config = {{ reply = "{REPLY}", endpoint = "opus" }}

[[prompt]]
name = "terse"
config = {{ style = "terse" }}
"""


def conversation(reply: str) -> list[dict]:
    """The scripted turns the simulator cassettes are recorded from.

    The transcript grows into every later prompt, so a column whose agent says something
    different keys different simulator cassettes from turn 2 on — which is exactly why
    only turn 1 races under `--live`.
    """
    return [
        {
            "reply": "Cancel SI5UKW please.",
            "marker": None,
            "then": [{"role": "assistant", "content": reply}],
        },
        {"reply": "That is not good enough.", "marker": "non_agreement", "done": True},
    ]


def matrix_text(**columns: dict) -> str:
    """A matrix file from `{name: {model, reply, ...}}`, so a test declares only what it
    varies."""
    blocks = []
    for name, spec in columns.items():
        config = {key: value for key, value in spec.items() if key != "model"}
        entries = ", ".join(f"{key} = {json.dumps(value)}" for key, value in config.items())
        blocks.append(
            f'[[provider]]\nname = "{name}"\nmodel = "{spec["model"]}"\nconfig = {{ {entries} }}\n'
        )
    return "\n".join(blocks)


def prime(workspace: Path, replies: list[str], verdict: bool = True) -> None:
    """Record every cassette the columns will ask for, by driving the loop once each.

    The judge cassette is keyed on the trace, so the only way to have one is to produce
    the trace — the determinism the whole cassette model already rests on.
    """
    card = parse(workspace / "refused.md")
    cassettes = workspace / "cassettes"
    criteria = criteria_of(card)
    for reply in dict.fromkeys(replies):
        record(cassettes, card, conversation(reply))
        agent = ConfigAgent()
        trace = asyncio.run(
            run_agent(
                card,
                agent,
                cassettes=cassettes,
                simulator_model=MODEL,
                semconv=SEMCONV,
                markers=["non_agreement"],
                config={"reply": reply},
            )
        )
        Cassette(cassettes, slug=card.slug).write(
            judge_prompt(criteria, trace, policy=""),
            MODEL,
            json.dumps({"verdicts": {c.id: verdict for c in criteria}}),
            criteria=[c.id for c in criteria],
        )


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    (tmp_path / "refused.md").write_text(CARD)
    (tmp_path / "vocabulary.txt").write_text(
        "[tools]\ncancel_reservation\n\n[markers]\nnon_agreement\n"
    )
    (tmp_path / "cassettes").mkdir()
    (tmp_path / "matrix.toml").write_text(MATRIX)
    prime(tmp_path, [REPLY])
    # Cleared after priming, not before: recording the cassettes drives the same adapter,
    # and those calls are the fixture's, not the matrix's.
    fake_agent.CONFIG_CALLS.clear()
    return tmp_path


def run(workspace: Path, *extra: str, matrix: str = "matrix.toml", runs: str = "1"):
    return runner.invoke(
        app,
        [
            "run",
            str(workspace / "refused.md"),
            "--agent",
            "tests.fake_agent:config_agent",
            "--vocabulary",
            str(workspace / "vocabulary.txt"),
            "--matrix",
            str(workspace / matrix),
            "--runs",
            runs,
            "--pass-threshold",
            "1",
            "--relock",
            "--simulator-model",
            MODEL,
            *extra,
        ],
    )


class TestTheGuards:
    def test_matrix_with_trace_names_both_flags(self, workspace: Path) -> None:
        result = runner.invoke(
            app,
            [
                "run",
                str(workspace / "refused.md"),
                "--trace",
                str(workspace / "nope.json"),
                "--matrix",
                str(workspace / "matrix.toml"),
            ],
        )
        assert result.exit_code == 2, result.stdout
        assert "--matrix" in result.stdout and "--trace" in result.stdout

    def test_matrix_without_an_agent_says_so(self, workspace: Path) -> None:
        result = runner.invoke(
            app, ["run", str(workspace / "refused.md"), "--matrix", str(workspace / "matrix.toml")]
        )
        assert result.exit_code == 2, result.stdout
        assert "--matrix needs --agent" in result.stdout

    def test_a_cap_with_no_matrix_is_refused(self, workspace: Path) -> None:
        result = runner.invoke(
            app,
            [
                "run",
                str(workspace / "refused.md"),
                "--agent",
                "tests.fake_agent:config_agent",
                "--budget-usd",
                "1",
            ],
        )
        assert result.exit_code == 2, result.stdout
        assert "--budget-usd applies to --matrix" in result.stdout

    def test_a_latency_budget_nothing_could_pass_is_the_same_user_error_under_a_matrix(
        self, workspace: Path
    ) -> None:
        # The single-cell path exits 2 on this number. The matrix must not route around
        # it into `BuiltinConfig`, where the `ValidationError` is caught per column and
        # scored exit 3, "specdeck itself broke" — after every column's agent has run.
        result = run(workspace, "--latency-budget", "0")
        assert result.exit_code == 2, result.stdout
        assert "--latency-budget" in result.stdout
        assert not fake_agent.CONFIG_CALLS, "the flag was rejected before the agent ran"

    def test_a_malformed_matrix_exits_two_not_three(self, workspace: Path) -> None:
        (workspace / "broken.toml").write_text("[[provider]\n")
        result = run(workspace, matrix="broken.toml")
        assert result.exit_code == 2, result.stdout

    def test_junit_with_a_matrix_is_refused_by_name(self, workspace: Path) -> None:
        result = run(workspace, "--junit-xml", str(workspace / "out.xml"))
        assert result.exit_code == 2, result.stdout
        assert "issues/85" in result.stdout


class TestRunningTheMatrix:
    def test_both_columns_run_and_the_grid_names_both(self, workspace: Path) -> None:
        result = run(workspace)
        assert result.exit_code == 0, result.stdout
        assert "sonnet" in result.stdout and "opus" in result.stdout
        assert "terse" in result.stdout
        assert result.stdout.count("PASS") >= 2

    def test_each_columns_config_reaches_the_adapter_verbatim(self, workspace: Path) -> None:
        run(workspace)
        endpoints = {call["endpoint"] for call in fake_agent.CONFIG_CALLS}
        assert endpoints == {"sonnet", "opus"}
        # The prompt axis merges under every provider, so both columns carry its key too.
        assert all(call["style"] == "terse" for call in fake_agent.CONFIG_CALLS)

    def test_the_lockfile_is_written_once_not_once_per_column(
        self, workspace: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # `_lock` writes the file under --relock, and N columns relocking would be N
        # writers on one `spec.lock.toml`. Counted, not inferred from the content: two
        # calls with the same key produce a file identical to one call's, so an assertion
        # on what was written passes under exactly the bug it claims to rule out.
        from specdeck import cli

        real = cli._lock
        calls = []

        def counted(*args, **kwargs):
            calls.append(1)
            return real(*args, **kwargs)

        monkeypatch.setattr(cli, "_lock", counted)
        assert run(workspace).exit_code == 0
        assert len(calls) == 1
        assert list(tomllib.loads((workspace / "spec.lock.toml").read_text())["cards"]) == [
            "refused.md"
        ]

    def test_a_failing_column_is_a_failing_matrix(self, workspace: Path) -> None:
        prime(workspace, [REPLY], verdict=False)
        result = run(workspace)
        assert result.exit_code == 1, result.stdout
        assert "FAIL" in result.stdout

    def test_a_column_whose_cassette_is_missing_errors_without_killing_the_others(
        self, workspace: Path
    ) -> None:
        # One column's failure must not corrupt another's report, and the grid still shows
        # every column that was asked for.
        (workspace / "wider.toml").write_text(
            matrix_text(
                sonnet={"model": "claude-sonnet-5", "reply": REPLY, "endpoint": "sonnet"},
                ghost={"model": "claude-opus-5", "reply": "something never recorded"},
            )
        )
        result = run(workspace, matrix="wider.toml")
        assert result.exit_code == 2, result.stdout
        assert "error" in result.stdout
        assert "PASS" in result.stdout, "the healthy column still reported"


class TestTheBudget:
    def _capped(self, workspace: Path, *extra: str, runs: str = "1", **columns: dict):
        (workspace / "capped.toml").write_text(matrix_text(**columns))
        return run(workspace, *extra, matrix="capped.toml", runs=runs)

    def test_the_second_run_of_a_column_never_starts_once_the_cap_is_blown(
        self, workspace: Path
    ) -> None:
        # The cap is checked between the runs of a column, not only between the columns.
        # Asserted on the adapter's call count rather than on the exit code: a column cut
        # short exits 4 either way, so only "how many conversations happened" can tell a
        # column that stopped from one that ran every run it was asked for.
        spent = {"model": "claude-sonnet-5", "reply": REPLY, "output_tokens": 1_000_000}
        assert self._capped(workspace, "--budget-usd", "0.01", sonnet=spent).exit_code == 0
        one_conversation = len(fake_agent.CONFIG_CALLS)
        fake_agent.CONFIG_CALLS.clear()

        result = self._capped(workspace, "--budget-usd", "0.01", runs="2", sonnet=spent)
        assert result.exit_code == 4, result.stdout
        assert len(fake_agent.CONFIG_CALLS) == one_conversation, "run 2 started anyway"

    def test_a_column_whose_model_is_unpriced_refuses_to_start_naming_it(
        self, workspace: Path
    ) -> None:
        result = self._capped(
            workspace,
            "--budget-usd",
            "1",
            sonnet={"model": "claude-sonnet-5", "reply": REPLY},
            mystery={"model": "not-a-model-9", "reply": REPLY},
        )
        assert result.exit_code == 2, result.stdout
        assert "no rate for mystery (not-a-model-9)" in result.stdout
        assert "rates.toml" in result.stdout

    def test_an_unpriced_judge_refuses_the_matrix_before_any_column_runs(
        self, workspace: Path
    ) -> None:
        # The columns are priced; the judge the lock pins is not. Charged $0.00 forever,
        # it would leave the cap unable to trip on specdeck's own calls — the half of the
        # spend the cap can actually prevent.
        result = self._capped(
            workspace,
            "--budget-usd",
            "1",
            "--judge-model",
            "not-a-model-9",
            sonnet={"model": "claude-sonnet-5", "reply": REPLY},
        )
        assert result.exit_code == 2, result.stdout
        assert "no rate for judge (not-a-model-9)" in result.stdout
        assert not fake_agent.CONFIG_CALLS, "no column ran"

    def test_an_expensive_agent_trips_the_cap_with_no_network(self, workspace: Path) -> None:
        # A million output tokens at Sonnet's rate is $10, well past a one-cent cap. The
        # first column is charged, the second never starts, and the run exits 4.
        result = self._capped(
            workspace,
            "--budget-usd",
            "0.01",
            "--matrix-concurrency",
            "1",
            sonnet={"model": "claude-sonnet-5", "reply": REPLY, "output_tokens": 1_000_000},
            opus={"model": "claude-opus-5", "reply": REPLY},
        )
        assert result.exit_code == 4, result.stdout
        assert "skipped" in result.stdout

    def test_a_stopped_matrix_never_renders_a_skipped_column_as_a_failing_card(
        self, workspace: Path
    ) -> None:
        result = self._capped(
            workspace,
            "--budget-usd",
            "0.01",
            "--matrix-concurrency",
            "1",
            sonnet={"model": "claude-sonnet-5", "reply": REPLY, "output_tokens": 1_000_000},
            opus={"model": "claude-opus-5", "reply": REPLY},
        )
        grid = result.stdout.split("spent")[0]
        assert "skipped" in grid
        assert "FAIL" not in grid, grid

    def test_an_adapter_that_reports_no_usage_aborts_under_a_cap(self, workspace: Path) -> None:
        result = self._capped(
            workspace,
            "--budget-usd",
            "1",
            "--matrix-concurrency",
            "1",
            sonnet={"model": "claude-sonnet-5", "reply": REPLY, "output_tokens": "none"},
            opus={"model": "claude-opus-5", "reply": REPLY},
        )
        assert result.exit_code == 4, result.stdout
        assert "ConfigAgent reported no gen_ai.usage.output_tokens" in result.stdout

    def test_an_adapter_that_names_no_model_aborts_under_a_cap(self, workspace: Path) -> None:
        # `loop` writes the "unknown" placeholder for an adapter that reported no model,
        # and "unknown" has no rate. The column declared a priced model and passed
        # pre-flight, so this is the case only the trace can catch.
        result = self._capped(
            workspace,
            "--budget-usd",
            "1",
            "--matrix-concurrency",
            "1",
            sonnet={"model": "claude-sonnet-5", "reply": REPLY, "reported_model": ""},
        )
        assert result.exit_code == 4, result.stdout
        assert "ConfigAgent reported no model" in " ".join(result.stdout.split())

    def test_the_footer_prints_the_spend_as_an_estimate_and_the_cap(self, workspace: Path) -> None:
        result = self._capped(
            workspace,
            "--budget-usd",
            "5",
            sonnet={"model": "claude-sonnet-5", "reply": REPLY},
        )
        assert result.exit_code == 0, result.stdout
        assert "estimate" in result.stdout
        assert "cap" in result.stdout and "$5" in result.stdout

    def test_the_overshoot_is_stated_rather_than_glossed(self, workspace: Path) -> None:
        result = self._capped(
            workspace,
            "--budget-usd",
            "0.01",
            "--matrix-concurrency",
            "1",
            sonnet={"model": "claude-sonnet-5", "reply": REPLY, "output_tokens": 1_000_000},
            opus={"model": "claude-opus-5", "reply": REPLY},
        )
        flat = " ".join(result.stdout.split())
        assert "cannot recall work in flight" in flat, flat

    def test_a_cap_reached_on_the_last_charge_did_not_stop_anything(self, workspace: Path) -> None:
        # The distinction `stopped_early` exists for: this matrix overspent by a factor of
        # a thousand and still ran every column it was asked for, so it completed. Exit 4
        # would claim a column is missing; the overshoot is stated instead.
        result = self._capped(
            workspace,
            "--budget-usd",
            "0.01",
            sonnet={"model": "claude-sonnet-5", "reply": REPLY, "output_tokens": 1_000_000},
        )
        assert result.exit_code == 0, result.stdout
        flat = " ".join(result.stdout.split())
        assert "skipped" not in flat
        assert "cannot recall work in flight" in flat

    def test_a_bad_agent_is_one_refusal_not_one_per_column(self, workspace: Path) -> None:
        result = runner.invoke(
            app,
            [
                "run",
                str(workspace / "refused.md"),
                "--agent",
                "tests.fake_agent:not_a_thing",
                "--vocabulary",
                str(workspace / "vocabulary.txt"),
                "--matrix",
                str(workspace / "matrix.toml"),
                "--runs",
                "1",
                "--pass-threshold",
                "1",
                "--relock",
                "--simulator-model",
                MODEL,
            ],
        )
        assert result.exit_code == 2, result.stdout
        # One refusal, before the grid exists — not one identical CardError per column.
        assert result.stdout.count("has no") == 1, result.stdout

    def test_a_matrix_with_no_cap_still_reports_what_it_spent(self, workspace: Path) -> None:
        result = run(workspace)
        assert result.exit_code == 0, result.stdout
        assert "estimate" in result.stdout

    def test_live_serialises_the_columns_and_says_so(
        self, workspace: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Replay covers every call the run makes, so --live records nothing new and needs
        # no key: what is under test is the printed note and the forced concurrency. The
        # concurrency is asserted rather than inferred from the note — the two are
        # independent statements, and deleting the one that matters leaves the message
        # behind, still printing a guarantee nothing enforces.
        from specdeck import cli

        real = cli.run_matrix
        seen: list[int] = []

        async def spy(*args, **kwargs):
            seen.append(kwargs["concurrency"])
            return await real(*args, **kwargs)

        monkeypatch.setattr(cli, "run_matrix", spy)
        result = run(workspace, "--live", "--matrix-concurrency", "4")
        assert result.exit_code == 0, result.stdout
        assert "--live serialises the columns" in " ".join(result.stdout.split())
        assert seen == [1], "the columns ran concurrently under --live"

    def test_replay_keeps_the_concurrency_it_was_asked_for(
        self, workspace: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The other half of the same guarantee: nothing is racing a cassette in replay, so
        # the flag is honoured and the serialisation is not a blanket ceiling.
        from specdeck import cli

        real = cli.run_matrix
        seen: list[int] = []

        async def spy(*args, **kwargs):
            seen.append(kwargs["concurrency"])
            return await real(*args, **kwargs)

        monkeypatch.setattr(cli, "run_matrix", spy)
        assert run(workspace, "--matrix-concurrency", "4").exit_code == 0
        assert seen == [4]


class TestTheBaseline:
    def test_each_column_records_its_own_slot(self, workspace: Path) -> None:
        result = run(workspace, "--update-baseline")
        assert result.exit_code == 0, result.stdout
        recorded = Baseline.load(workspace / BASELINE_NAME)
        key = lock_key(workspace / "refused.md", workspace / BASELINE_NAME)
        assert recorded.get(key, "sonnet/terse") is not None
        assert recorded.get(key, "opus/terse") is not None

    def test_the_write_happens_once_for_the_whole_matrix(self, workspace: Path) -> None:
        # Both columns are in the file, so no column's save overwrote another's.
        run(workspace, "--update-baseline")
        text = (workspace / BASELINE_NAME).read_text()
        assert "sonnet/terse" in text and "opus/terse" in text

    def test_a_column_that_did_not_pass_records_no_baseline(self, workspace: Path) -> None:
        """#102, one grid wider: a failing column does not get to say what normal costs.

        This reverses the earlier behaviour of writing and warning. Every column here
        fails, so nothing is earned and no file appears at all.
        """
        prime(workspace, [REPLY], verdict=False)
        result = run(workspace, "--update-baseline")
        assert result.exit_code == 1, result.stdout
        flat = " ".join(result.stdout.split())
        assert "sonnet/terse" in flat and "opus/terse" in flat
        assert "no baseline was recorded" in flat
        assert not (workspace / BASELINE_NAME).exists(), "a failing column wrote a baseline"

    def test_a_passing_column_records_while_a_failing_one_beside_it_does_not(
        self, workspace: Path
    ) -> None:
        """The case #102 actually turns on: half a grid earns its baseline, half does not.

        Both columns run to completion, so `fresh` holds a number for each. Only the
        passing column's reaches disk. A grid where every column fails cannot show this —
        no file appears at all there — so this is the one that proves the filter is
        per column rather than all-or-nothing.
        """
        other = "I have cancelled it for you."
        (workspace / "mixed.toml").write_text(
            MATRIX.replace(
                f'reply = "{REPLY}", endpoint = "opus"', f'reply = "{other}", endpoint = "opus"'
            )
        )
        # sonnet keeps the refusal and passes; opus cancels, trips `cancel_reservation:
        # never`, and is graded false.
        prime(workspace, [other], verdict=False)

        result = run(workspace, "--update-baseline", matrix="mixed.toml")
        assert result.exit_code == 1, result.stdout
        flat = " ".join(result.stdout.split())
        assert "no baseline was recorded" in flat
        assert "opus/terse" in flat
        assert "sonnet/terse" not in flat.split("no baseline was recorded")[1]

        recorded = Baseline.load(workspace / BASELINE_NAME)
        key = lock_key(workspace / "refused.md", workspace / BASELINE_NAME)
        assert recorded.get(key, "sonnet/terse") is not None, "the passing column recorded"
        assert recorded.get(key, "opus/terse") is None, "the failing column did not"

    def test_a_passing_matrix_says_nothing_of_the_kind(self, workspace: Path) -> None:
        result = run(workspace, "--update-baseline")
        assert result.exit_code == 0, result.stdout
        assert "no baseline was recorded" not in " ".join(result.stdout.split())
        assert (workspace / BASELINE_NAME).exists(), "a passing matrix still records"

    def test_a_column_left_without_a_baseline_of_its_own_is_named(self, workspace: Path) -> None:
        # A half-recorded matrix is reachable by design: a budget stop leaves
        # --update-baseline having recorded the column that ran and not the one that was
        # skipped. The column it missed then runs with no regression wire, and the guard
        # that exists for exactly that outcome used to fall silent one column over.
        key = lock_key(workspace / "refused.md", workspace / BASELINE_NAME)
        Baseline().record(key, 40, cell="sonnet/terse").save(workspace / BASELINE_NAME)
        result = run(workspace)
        assert result.exit_code == 0, result.stdout
        flat = " ".join(result.stdout.split())
        assert "no baseline is recorded for opus/terse" in flat, flat

    def test_recording_the_matrix_says_nothing_about_a_missing_baseline(
        self, workspace: Path
    ) -> None:
        # --update-baseline gives every column that runs its own fresh number, so there is
        # nothing missing to warn about and the note would be false.
        key = lock_key(workspace / "refused.md", workspace / BASELINE_NAME)
        Baseline().record(key, 40, cell="sonnet/terse").save(workspace / BASELINE_NAME)
        result = run(workspace, "--update-baseline")
        assert result.exit_code == 0, result.stdout
        assert "no baseline is recorded" not in " ".join(result.stdout.split())

    def test_a_single_cell_baseline_alone_is_called_out(self, workspace: Path) -> None:
        # Silence here is the worst outcome: every column would get no regression wire,
        # and a card that fails single-cell would pass under --matrix.
        key = lock_key(workspace / "refused.md", workspace / BASELINE_NAME)
        Baseline().record(key, 40, cell=DEFAULT_CELL).save(workspace / BASELINE_NAME)
        result = run(workspace)
        assert result.exit_code == 0, result.stdout
        assert "no column gets a token-regression wire" in " ".join(result.stdout.split())


class TestTheSingleCellPathIsUntouched:
    def test_a_run_with_no_matrix_still_works(self, workspace: Path) -> None:
        result = runner.invoke(
            app,
            [
                "run",
                str(workspace / "refused.md"),
                "--agent",
                "tests.fake_agent:config_agent",
                "--vocabulary",
                str(workspace / "vocabulary.txt"),
                "--runs",
                "1",
                "--pass-threshold",
                "1",
                "--relock",
                "--simulator-model",
                MODEL,
            ],
        )
        assert result.exit_code == 0, result.stdout
        assert "PASS" in result.stdout
