"""The committed card suite: every card, evaluated end to end from its cassette.

No network and no key — replay is the default mode, so the whole suite runs offline. The
CLI-surface assertions (flags, exit codes, relock) stay in tests/test_end_to_end.py; this
module owns the properties every card must hold.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from specdeck.baseline import observed
from specdeck.builtin import BuiltinConfig, builtin_properties, merge_wires
from specdeck.card import parse
from specdeck.cell import run_cell
from specdeck.cli import _vocabulary, app
from specdeck.ir import AfterKThen, AtMost, Bound, Never, NeverRequested
from specdeck.judge import criteria_of, rubric_text
from specdeck.lint import Severity, lint_paths
from specdeck.lockfile import Lockfile, fingerprint
from specdeck.trace import Operation
from specdeck.traceio import load_trace
from specdeck.wires import compile_wires, wires_text

ROOT = Path(__file__).resolve().parent.parent
CARDS = ROOT / "cards"
SEMCONV = "semantic-conventions-genai@1.38.0"

CARD_PATHS = sorted(CARDS.glob("*.md"))
#: Every card's trace is generated and named for the card. There is no exception: card 1
#: kept a hand-written `run-01.otlp.json` until #63, and one trace outside the generator
#: is one trace that rots when the schema moves.
GENERATED = CARD_PATHS


def ids(paths: list[Path]) -> list[str]:
    return [p.stem for p in paths]


class TestSuiteShape:
    def test_six_cards_ship(self) -> None:
        assert len(CARD_PATHS) == 6, ids(CARD_PATHS)

    def test_every_card_parses(self) -> None:
        for path in CARD_PATHS:
            assert parse(path).prose, path.name

    def test_every_card_lints_clean_against_lock_and_vocabulary(self) -> None:
        result = lint_paths(
            [CARDS],
            lock=Lockfile.load(CARDS / "spec.lock.toml"),
            vocabulary=_vocabulary(CARDS / "vocabulary.txt"),
        )
        assert [f for f in result.findings if f.severity is not Severity.SKIPPED] == []

    def test_the_deck_lints_clean_with_the_vocabulary_and_an_agent_definition_together(
        self,
    ) -> None:
        """The two ERROR rules have to be satisfiable at the same time.

        `unbounded-cycle` and `unknown-tool` shipped mutually unsatisfiable because
        nothing ran them together: the cycle rule intersected graph *node* names against
        wire *tool* names, so the only wire that cleared it was one `unknown-tool` then
        rejected. Both flags, on the committed deck, through the CLI. See DECISIONS.md,
        2026-08-25.
        """
        result = CliRunner().invoke(
            app,
            [
                "lint",
                str(CARDS),
                "--vocabulary",
                str(CARDS / "vocabulary.txt"),
                "--agent-def",
                "tests.fake_graph:refund_graph",
            ],
        )
        assert result.exit_code == 0, result.stdout

    def test_every_card_declares_the_traces_it_is_evaluated_against(self) -> None:
        # The binding lives in the card, so `specdeck run cards/` needs no shell history.
        for path in CARD_PATHS:
            assert parse(path).context.traces, path.name

    def test_every_declaration_resolves_to_at_least_one_recording(self) -> None:
        for path in CARD_PATHS:
            assert parse(path).trace_paths, path.name

    def test_traces_reach_no_hash_so_the_migration_moved_no_lock(self) -> None:
        """Only `context.simulator` reaches a pin, and this is what holds that.

        If a later change pins traces, the five committed cards each need a --relock and
        this test is where that lands rather than in a surprised reviewer.
        """
        lock = Lockfile.load(CARDS / "spec.lock.toml")
        for path in CARD_PATHS:
            card = parse(path)
            stripped = card.model_copy(
                update={"context": card.context.model_copy(update={"traces": ""})}
            )
            lock.verify(
                path.name,
                rubric=rubric_text(criteria_of(stripped)),
                wires=wires_text(compile_wires(stripped)),
                simulator=stripped.context.simulator,
            )

    def test_the_whole_deck_runs_green_from_its_own_declarations(self) -> None:
        result = CliRunner().invoke(app, ["run", str(CARDS)])
        assert result.exit_code == 0, result.stdout

    def test_every_card_is_locked_under_its_own_name(self) -> None:
        lock = Lockfile.load(CARDS / "spec.lock.toml")
        assert sorted(lock.cards) == sorted(p.name for p in CARD_PATHS)

    def test_the_lock_matches_every_card_as_written(self) -> None:
        lock = Lockfile.load(CARDS / "spec.lock.toml")
        for path in CARD_PATHS:
            card = parse(path)
            lock.verify(
                path.name,
                rubric=rubric_text(criteria_of(card)),
                wires=wires_text(compile_wires(card)),
                simulator=card.context.simulator,
            )


class TestCoverage:
    """The suite exists to exercise the palette, not to be five of the same card."""

    def _rules(self) -> list:
        return [p.rule for path in CARD_PATHS for p in compile_wires(parse(path))]

    def test_every_shipped_pattern_appears_somewhere(self) -> None:
        """#92: equality, not a subset.

        As a subset check this passed while `NeverRequested` was absent from every card,
        which is how a shipped pattern went unexercised by the deck for a whole milestone.
        Equality makes the next added pattern fail here until a card carries it.
        """
        kinds = {type(rule) for rule in self._rules()}
        assert kinds == {Never, NeverRequested, AtMost, Bound, AfterKThen}

    def test_exactly_one_card_carries_the_escalation_wire(self) -> None:
        carrying = [
            path.stem
            for path in CARD_PATHS
            if any(isinstance(p.rule, AfterKThen) for p in compile_wires(parse(path)))
        ]
        assert carrying == ["escalation-after-repeated-refusal"]

    @pytest.mark.parametrize("path", CARD_PATHS, ids=ids(CARD_PATHS))
    def test_each_card_carries_at_least_one_gate_wire(self, path: Path) -> None:
        assert compile_wires(parse(path)), path.name

    def test_the_suite_is_not_all_refusals(self) -> None:
        # A suite of five refusals proves the agent can say no, not that it can work.
        booking = parse(CARDS / "booking-with-certificates.md")
        assert "completes the booking" in booking.prose


class TestFixturesAndPolicy:
    @pytest.mark.parametrize("path", CARD_PATHS, ids=ids(CARD_PATHS))
    def test_the_named_fixture_and_policy_exist_and_parse(self, path: Path) -> None:
        card = parse(path)
        assert card.policy_path and card.policy_path.exists()
        assert card.fixture_path and card.fixture_path.exists()
        json.loads(card.fixture_path.read_text())

    def test_no_fixture_carries_a_users_reservation_list(self) -> None:
        # The upstream user records list every reservation that user holds; a card's
        # fixture should carry only what its own scenario touches.
        for path in CARD_PATHS:
            fixture = json.loads(parse(path).fixture_path.read_text())
            for user in fixture.get("users", {}).values():
                assert "reservations" not in user, path.name


class TestTraces:
    @pytest.mark.parametrize("path", GENERATED, ids=ids(GENERATED))
    def test_each_card_has_a_trace_named_after_it(self, path: Path) -> None:
        assert (CARDS / "traces" / f"{path.stem}.otlp.json").exists()

    @pytest.mark.parametrize("path", GENERATED, ids=ids(GENERATED))
    def test_every_trace_is_a_raw_otlp_export(self, path: Path) -> None:
        # The locked trace decision says an agent already emitting OTel needs no adapter.
        # Fixtures in specdeck's own format would leave that claim untested.
        text = (CARDS / "traces" / f"{path.stem}.otlp.json").read_text()
        assert '"resourceSpans"' in text

    @pytest.mark.parametrize("path", GENERATED, ids=ids(GENERATED))
    def test_every_trace_declares_the_pinned_semconv(self, path: Path) -> None:
        # `--relock` rewrites the global semconv pin from whichever trace it was handed,
        # so one trace disagreeing flips the pin under every other card. See #56.
        assert load_trace(CARDS / "traces" / f"{path.stem}.otlp.json").semconv == SEMCONV

    def test_the_lockfile_pins_that_same_semconv(self) -> None:
        assert Lockfile.load(CARDS / "spec.lock.toml").semconv == SEMCONV

    @pytest.mark.parametrize("path", GENERATED, ids=ids(GENERATED))
    def test_every_trace_reports_usage_so_token_bounds_can_fire(self, path: Path) -> None:
        # A token bound over a trace with no usage fails closed; a trace that forgot to
        # emit it would red-light a card for the wrong reason.
        assert load_trace(CARDS / "traces" / f"{path.stem}.otlp.json").reports_output_tokens

    def test_the_escalation_trace_actually_escalates_after_three_markers(self) -> None:
        trace = load_trace(CARDS / "traces" / "escalation-after-repeated-refusal.otlp.json")
        assert sum(1 for s in trace.ordered if s.marker == "non_agreement") == 3
        tools = [s.attributes["gen_ai.tool.name"] for s in trace.of(Operation.EXECUTE_TOOL)]
        assert tools[-1] == "transfer_to_human_agents"


class TestGeneratorIsAuthoritative:
    def test_the_committed_traces_regenerate_byte_for_byte(self) -> None:
        # Without this the traces rot: a hand-edit or a schema change is invisible.
        result = subprocess.run(
            [sys.executable, str(ROOT / "tools" / "make_traces.py"), "--check"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stdout + result.stderr

    def test_a_trace_quotes_its_fixture_rather_than_a_copy_of_it(self) -> None:
        # make_traces.fixture() reads the committed file, so the two cannot drift.
        trace = load_trace(CARDS / "traces" / "basic-economy-cancellation-refused.otlp.json")
        lookup = trace.of(Operation.EXECUTE_TOOL)[0]
        recorded = json.loads(lookup.attributes["gen_ai.tool.call.result"])
        fixture = json.loads(
            (CARDS / "fixtures" / "basic-economy-cancellation-refused.json").read_text()
        )
        assert recorded == fixture["reservations"]["SI5UKW"]


class TestRubricPinning:
    @pytest.mark.parametrize("path", CARD_PATHS, ids=ids(CARD_PATHS))
    def test_editing_the_prose_would_invalidate_the_lock(self, path: Path) -> None:
        card = parse(path)
        lock = Lockfile.load(CARDS / "spec.lock.toml")
        entry = lock.cards[path.name]
        edited = criteria_of(card)
        edited[0].text += " And it always apologises."
        assert entry.rubric_hash != fingerprint(rubric_text(edited))


class TestEveryCardRuns:
    """Each card, gate and credit, from its recorded cassette. No key, no network."""

    @pytest.mark.parametrize("path", CARD_PATHS, ids=ids(CARD_PATHS))
    def test_the_card_passes_against_its_own_trace(
        self, path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        card = parse(path)
        trace = load_trace(CARDS / "traces" / f"{path.stem}.otlp.json")
        cell = run_cell(card, [trace], cassettes=CARDS / "cassettes", n=1, k=1)
        assert cell.passed, path.name
        assert cell.results[0].judged.replayed
        assert cell.credit_mean == cell.credit_total, path.name

    @pytest.mark.parametrize("path", CARD_PATHS, ids=ids(CARD_PATHS))
    def test_every_cassette_names_the_card_that_owns_it(self, path: Path) -> None:
        # A hash alone is unattributable: finding which cassette a card replays otherwise
        # means moving them all aside and re-running to see which one goes missing (#69).
        owned = list((CARDS / "cassettes").glob(f"{path.stem}.judge-*.json"))
        assert len(owned) == 1, [p.name for p in owned]

    def test_no_cassette_is_orphaned(self) -> None:
        # Every prose edit re-keys the prompt and strands the old recording.
        assert len(list((CARDS / "cassettes").glob("*.json"))) == len(CARD_PATHS)


class TestTheFreeWiresChangeNothingHere:
    """Every committed card authors both a latency wire and `stop_reason`, so both
    built-ins dedup away and the suite's verdicts are what they were before #17.

    Four of the five author `latency: under 180s` and one `under 120s`; what matters is
    that each authors *a* latency wire, not which limit it chose.
    """

    @pytest.mark.parametrize("path", CARD_PATHS, ids=ids(CARD_PATHS))
    def test_every_card_authors_the_two_wires_it_would_otherwise_be_given(self, path: Path) -> None:
        authored = {p.id for p in compile_wires(parse(path))}
        assert {"latency", "stop_reason"} <= authored, path.name

    @pytest.mark.parametrize("path", CARD_PATHS, ids=ids(CARD_PATHS))
    def test_no_free_wire_is_evaluated_twice(self, path: Path) -> None:
        card = parse(path)
        merged = merge_wires(compile_wires(card), builtin_properties(BuiltinConfig()))
        assert [p.id for p in merged] == [p.id for p in compile_wires(card)], path.name

    @pytest.mark.parametrize("path", CARD_PATHS, ids=ids(CARD_PATHS))
    def test_the_card_still_passes_against_a_baseline_of_its_own_cost(
        self, path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Recording what a card cost and then running it must not fail it. This is the
        # `--update-baseline` round trip, without the CLI.
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        card = parse(path)
        trace = load_trace(CARDS / "traces" / f"{path.stem}.otlp.json")
        cell = run_cell(
            card,
            [trace],
            cassettes=CARDS / "cassettes",
            n=1,
            k=1,
            builtin=BuiltinConfig(token_baseline=observed([trace])),
        )
        assert cell.passed, path.name
        assert "token_baseline" in [w.id for w in cell.results[0].wires]

    def test_the_wire_hashes_are_untouched_by_the_free_wires(self) -> None:
        # The load-bearing one. `compile_wires` feeds `wires_hash`, so a built-in leaking
        # into it would stale every lock in every user repo on a specdeck upgrade with no
        # card change. If this fails, the merge landed in the compiler.
        lock = Lockfile.load(CARDS / "spec.lock.toml")
        for path in CARD_PATHS:
            card = parse(path)
            assert lock.cards[path.name].wires_hash == fingerprint(
                wires_text(compile_wires(card))
            ), path.name
