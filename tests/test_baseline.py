"""`spec.baseline.toml` — reading it, writing it, and what may honestly be recorded."""

from __future__ import annotations

from pathlib import Path

import pytest

from specdeck.baseline import (
    BASELINE_NAME,
    DEFAULT_CELL,
    Baseline,
    BaselineError,
    observed,
)
from specdeck.lockfile import lock_key
from specdeck.trace import GenAI, Operation

from .test_trace import span, trace


def run(tokens: int | None):
    """One trace reporting `tokens` output tokens, or reporting no usage at all."""
    chat = span("chat-0", Operation.CHAT)
    if tokens is not None:
        chat.attributes[GenAI.USAGE_OUTPUT_TOKENS] = tokens
    return trace(span("root", Operation.INVOKE_AGENT, parent=None, duration=1.0), chat)


class TestRoundTrip:
    def test_two_cards_and_two_cells_survive_the_file(self) -> None:
        written = (
            Baseline()
            .record("a.md", 100)
            .record("nested/b.md", 200)
            .record("nested/b.md", 300, cell="openai")
        )
        assert Baseline.from_toml(written.to_toml()) == written

    def test_the_file_is_stable_when_nothing_changed(self) -> None:
        # A baseline that rewrites itself differently every run is a diff nobody can read.
        one = Baseline().record("a.md", 100)
        assert one.to_toml() == Baseline.from_toml(one.to_toml()).to_toml()

    def test_a_card_path_with_dots_is_quoted(self) -> None:
        # Every key carries `.md`, so an unquoted key would nest three tables deep.
        assert '[cards."a.md"."default"]' in Baseline().record("a.md", 100).to_toml()

    def test_the_header_points_at_the_flag_that_writes_it(self) -> None:
        assert "--update-baseline" in Baseline().record("a.md", 100).to_toml()

    def test_the_file_is_named_beside_the_lockfile(self) -> None:
        assert BASELINE_NAME == "spec.baseline.toml"


class TestGet:
    def test_a_card_with_no_entry_reads_none_not_zero(self) -> None:
        # None means no regression wire at all; 0 would bound every run at zero forever.
        assert Baseline().get("a.md") is None

    def test_a_recorded_card_reads_back(self) -> None:
        assert Baseline().record("a.md", 100).get("a.md") == 100

    def test_a_cell_nobody_recorded_reads_none(self) -> None:
        assert Baseline().record("a.md", 100).get("a.md", cell="openai") is None

    def test_the_default_cell_is_the_one_the_matrix_will_fill(self) -> None:
        assert Baseline().record("a.md", 100).get("a.md", cell=DEFAULT_CELL) == 100


class TestRecord:
    def test_it_returns_a_copy_and_leaves_the_original_alone(self) -> None:
        original = Baseline().record("a.md", 100)
        original.record("a.md", 999)
        assert original.get("a.md") == 100

    def test_other_cards_are_untouched(self) -> None:
        two = Baseline().record("a.md", 100).record("b.md", 200)
        assert two.record("a.md", 300).get("b.md") == 200

    def test_another_cell_of_the_same_card_is_untouched(self) -> None:
        two = Baseline().record("a.md", 100).record("a.md", 200, cell="openai")
        assert two.record("a.md", 300).get("a.md", cell="openai") == 200


class TestLoad:
    def test_a_missing_file_is_an_empty_baseline_not_an_error(self, tmp_path: Path) -> None:
        # A repo that has never recorded one must still run; it just gets no free wire.
        assert Baseline.load(tmp_path / BASELINE_NAME) == Baseline()

    def test_a_file_that_is_not_toml_names_itself(self, tmp_path: Path) -> None:
        path = tmp_path / BASELINE_NAME
        path.write_text("<<<<<<< HEAD\nbroken = [\n")
        with pytest.raises(BaselineError, match=str(path)):
            Baseline.load(path)

    def test_an_entry_of_the_wrong_shape_is_a_baseline_error(self, tmp_path: Path) -> None:
        # Routed as a user error, not an internal one: a hand-edited file is the user's.
        path = tmp_path / BASELINE_NAME
        path.write_text('[cards."a.md"."default"]\noutput_tokens = "many"\n')
        with pytest.raises(BaselineError):
            Baseline.load(path)

    def test_what_was_saved_is_what_loads(self, tmp_path: Path) -> None:
        path = tmp_path / BASELINE_NAME
        Baseline().record("a.md", 100).save(path)
        assert Baseline.load(path).get("a.md") == 100


class TestTheStatistic:
    def test_it_is_the_median_not_the_mean(self) -> None:
        # A mean would be 176 here — moved by one spike into a number no run cost.
        assert observed([run(100), run(105), run(110), run(115), run(450)]) == 110

    def test_it_is_the_median_not_the_max(self) -> None:
        # A max ratchets upward on the worst run ever seen and never comes back down.
        assert observed([run(100), run(105), run(110), run(115), run(450)]) != 450

    def test_over_an_even_count_it_is_a_number_a_run_actually_cost(self) -> None:
        # The interpolating median would give 105.0, which no run produced. `median_low`
        # takes the lower of the two, because the point of the figure is that it happened.
        assert observed([run(100), run(110)]) == 100

    def test_one_run_is_its_own_baseline(self) -> None:
        assert observed([run(95)]) == 95


class TestWhatMayNotBeRecorded:
    def test_a_trace_reporting_no_usage_refuses_rather_than_recording_zero(self) -> None:
        with pytest.raises(BaselineError, match=GenAI.USAGE_OUTPUT_TOKENS):
            observed([run(100), run(None), run(120)])

    def test_the_refusal_names_which_run_was_silent(self) -> None:
        with pytest.raises(BaselineError, match=r"run 2"):
            observed([run(100), run(None), run(120)])

    def test_nothing_to_record_is_an_error_not_a_zero(self) -> None:
        with pytest.raises(BaselineError, match=r"no runs"):
            observed([])


class TestOneKeyDerivation:
    def test_the_baseline_keys_a_card_exactly_as_the_lockfile_does(self, tmp_path: Path) -> None:
        # One derivation across lock, lint and baseline: two files disagreeing about which
        # card is which is the #61 failure, one file later.
        card = tmp_path / "cards" / "a.md"
        card.parent.mkdir()
        card.write_text("# Scenario: x\n")
        key = lock_key(card, tmp_path / "cards" / BASELINE_NAME)
        assert key == "a.md"
        assert Baseline().record(key, 100).get(lock_key(card, tmp_path / "cards" / BASELINE_NAME))
