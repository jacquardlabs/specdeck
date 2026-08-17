from pathlib import Path

import pytest

from specdeck.lockfile import CardLock, Lockfile, StaleLock, fingerprint

PROSE = "The agent refuses the change and offers cancellation under travel insurance."
SIMULATOR = "frustrated traveller wants a later return flight"


def lock(**overrides) -> Lockfile:
    base = dict(
        semconv="semantic-conventions-genai@1.38.0",
        judge_model="claude-sonnet-5",
        simulator_model="claude-sonnet-5",
        cards={
            "cards/basic-economy.md": CardLock(
                rubric_hash=fingerprint(PROSE), simulator_hash=fingerprint(SIMULATOR)
            )
        },
    )
    return Lockfile(**(base | overrides))


class TestFingerprint:
    def test_is_stable_and_algorithm_tagged(self) -> None:
        assert fingerprint(PROSE) == fingerprint(PROSE)
        assert fingerprint(PROSE).startswith("sha256:")

    def test_any_edit_to_the_prose_changes_it(self) -> None:
        assert fingerprint(PROSE) != fingerprint(PROSE + " It never promises an exception.")

    def test_whitespace_at_the_edges_is_not_a_change(self) -> None:
        assert fingerprint(f"\n  {PROSE}  \n") == fingerprint(PROSE)


class TestVerify:
    def test_a_matching_card_passes(self) -> None:
        lock().verify("cards/basic-economy.md", rubric=PROSE, simulator=SIMULATOR)

    def test_an_edited_rubric_is_stale_and_says_so(self) -> None:
        with pytest.raises(StaleLock, match=r"rubric.*--relock"):
            lock().verify("cards/basic-economy.md", rubric=PROSE + " More.", simulator=SIMULATOR)

    def test_an_edited_simulator_prompt_is_stale(self) -> None:
        with pytest.raises(StaleLock, match=r"simulator"):
            lock().verify("cards/basic-economy.md", rubric=PROSE, simulator="calm traveller")

    def test_an_unlocked_card_is_stale(self) -> None:
        with pytest.raises(StaleLock, match=r"not in the lockfile"):
            lock().verify("cards/refund.md", rubric=PROSE, simulator=SIMULATOR)

    def test_the_error_names_every_drift_at_once(self) -> None:
        with pytest.raises(StaleLock) as excinfo:
            lock().verify("cards/basic-economy.md", rubric="new", simulator="new")
        assert "rubric" in str(excinfo.value) and "simulator" in str(excinfo.value)


class TestSemconv:
    def test_a_matching_semconv_passes(self) -> None:
        lock().verify_semconv("semantic-conventions-genai@1.38.0")

    def test_a_different_semconv_is_stale(self) -> None:
        with pytest.raises(StaleLock, match=r"semconv"):
            lock().verify_semconv("semantic-conventions-genai@1.39.0")


class TestToml:
    def test_round_trips_through_toml(self) -> None:
        assert Lockfile.from_toml(lock().to_toml()) == lock()

    def test_the_written_file_is_readable_and_ordered(self) -> None:
        text = lock().to_toml()
        assert text.index("[judge]") < text.index("[simulator]") < text.index("[cards.")
        assert 'model = "claude-sonnet-5"' in text

    def test_a_card_path_with_a_dot_survives_the_round_trip(self) -> None:
        original = lock(
            cards={
                "cards/v1.2/basic.md": CardLock(
                    rubric_hash=fingerprint(PROSE), simulator_hash=fingerprint(SIMULATOR)
                )
            }
        )
        assert Lockfile.from_toml(original.to_toml()) == original

    def test_load_and_save_use_the_file_system(self, tmp_path: Path) -> None:
        path = tmp_path / "spec.lock.toml"
        lock().save(path)
        assert Lockfile.load(path) == lock()

    def test_a_missing_lockfile_reports_relock(self, tmp_path: Path) -> None:
        with pytest.raises(StaleLock, match=r"--relock"):
            Lockfile.load(tmp_path / "absent.lock.toml")


class TestRelock:
    def test_relocking_records_the_current_hashes(self) -> None:
        stale = lock()
        fresh = stale.relock("cards/basic-economy.md", rubric="new prose", simulator=SIMULATOR)
        fresh.verify("cards/basic-economy.md", rubric="new prose", simulator=SIMULATOR)

    def test_relocking_one_card_leaves_the_others_alone(self) -> None:
        two = lock(
            cards=lock().cards
            | {"cards/refund.md": CardLock(rubric_hash="sha256:old", simulator_hash="sha256:old")}
        )
        fresh = two.relock("cards/refund.md", rubric="p", simulator="s")
        assert fresh.cards["cards/basic-economy.md"] == two.cards["cards/basic-economy.md"]

    def test_relocking_does_not_mutate_the_original(self) -> None:
        original = lock()
        original.relock("cards/basic-economy.md", rubric="new", simulator=SIMULATOR)
        original.verify("cards/basic-economy.md", rubric=PROSE, simulator=SIMULATOR)
