from pathlib import Path

import pytest

from specdeck.lockfile import CardLock, Lockfile, StaleLock, fingerprint, lock_key

PROSE = "The agent refuses the change and offers cancellation under travel insurance."
SIMULATOR = "frustrated traveller wants a later return flight"
WIRES = '[{"id": "never:modify_reservation"}]'


def lock(**overrides) -> Lockfile:
    base = dict(
        semconv="semantic-conventions-genai@1.38.0",
        judge_model="claude-sonnet-5",
        simulator_model="claude-sonnet-5",
        cards={
            "cards/basic-economy.md": CardLock(
                rubric_hash=fingerprint(PROSE),
                wires_hash=fingerprint(WIRES),
                simulator_hash=fingerprint(SIMULATOR),
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
        lock().verify("cards/basic-economy.md", rubric=PROSE, simulator=SIMULATOR, wires=WIRES)

    def test_an_edited_rubric_is_stale_and_says_so(self) -> None:
        with pytest.raises(StaleLock, match=r"rubric.*--relock"):
            lock().verify(
                "cards/basic-economy.md", rubric=PROSE + " More.", simulator=SIMULATOR, wires=WIRES
            )

    def test_an_edited_simulator_prompt_is_stale(self) -> None:
        with pytest.raises(StaleLock, match=r"simulator"):
            lock().verify(
                "cards/basic-economy.md", rubric=PROSE, simulator="calm traveller", wires=WIRES
            )

    def test_an_unlocked_card_is_stale(self) -> None:
        with pytest.raises(StaleLock, match=r"not in the lockfile"):
            lock().verify("cards/refund.md", rubric=PROSE, simulator=SIMULATOR, wires=WIRES)

    def test_the_error_names_every_drift_at_once(self) -> None:
        with pytest.raises(StaleLock) as excinfo:
            lock().verify("cards/basic-economy.md", rubric="new", simulator="new", wires=WIRES)
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
                    rubric_hash=fingerprint(PROSE),
                    wires_hash=fingerprint(WIRES),
                    simulator_hash=fingerprint(SIMULATOR),
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
        fresh = stale.relock(
            "cards/basic-economy.md", rubric="new prose", simulator=SIMULATOR, wires=WIRES
        )
        fresh.verify("cards/basic-economy.md", rubric="new prose", simulator=SIMULATOR, wires=WIRES)

    def test_relocking_one_card_leaves_the_others_alone(self) -> None:
        two = lock(
            cards=lock().cards
            | {"cards/refund.md": CardLock(rubric_hash="sha256:old", simulator_hash="sha256:old")}
        )
        fresh = two.relock("cards/refund.md", rubric="p", simulator="s", wires=WIRES)
        assert fresh.cards["cards/basic-economy.md"] == two.cards["cards/basic-economy.md"]

    def test_relocking_does_not_mutate_the_original(self) -> None:
        original = lock()
        original.relock("cards/basic-economy.md", rubric="new", simulator=SIMULATOR, wires=WIRES)
        original.verify("cards/basic-economy.md", rubric=PROSE, simulator=SIMULATOR, wires=WIRES)


class TestWiresArePinned:
    """Wires are half of what a card asserts, and the deterministic half — the half a
    reviewer is least likely to re-read. See #62."""

    def test_an_edited_wire_is_drift(self) -> None:
        with pytest.raises(StaleLock, match=r"wires"):
            lock().verify(
                "cards/basic-economy.md",
                rubric=PROSE,
                simulator=SIMULATOR,
                wires='[{"id": "at_most:modify_reservation"}]',
            )

    def test_the_error_says_which_half_moved(self) -> None:
        # Naming "wires" rather than a single opaque hash is the whole reason this is a
        # separate field: the SME and the developer own different halves of the card.
        with pytest.raises(StaleLock, match=r"^(?!.*rubric).*wires"):
            lock().verify(
                "cards/basic-economy.md", rubric=PROSE, simulator=SIMULATOR, wires="different"
            )

    def test_both_halves_are_named_when_both_moved(self) -> None:
        with pytest.raises(StaleLock, match=r"rubric and wires"):
            lock().verify(
                "cards/basic-economy.md", rubric="new", simulator=SIMULATOR, wires="different"
            )

    def test_a_lockfile_written_before_wires_were_pinned_still_loads(self) -> None:
        # And then reads as drift on the first verify, which is the correct answer: those
        # cards were never pinned against their wires.
        old = Lockfile.from_toml(
            'semconv = "s"\n[judge]\nmodel = "m"\n[simulator]\nmodel = ""\n'
            '[cards."a.md"]\nrubric_hash = "h"\nsimulator_hash = "h"\n'
        )
        assert old.cards["a.md"].wires_hash == ""
        with pytest.raises(StaleLock, match=r"wires"):
            old.verify("a.md", rubric="x", simulator="y", wires="z")


class TestLockKey:
    def test_a_card_beside_the_lockfile_keys_on_its_filename(self, tmp_path: Path) -> None:
        assert lock_key(tmp_path / "refund.md", tmp_path / "spec.lock.toml") == "refund.md"

    def test_a_card_in_a_subdirectory_keeps_the_subdirectory(self, tmp_path: Path) -> None:
        # The divergence in #61: lint read a bare filename, so `airline/refund.md` verified
        # clean in the runner and reported "not in the lockfile" in lint, same commit.
        key = lock_key(tmp_path / "airline" / "refund.md", tmp_path / "spec.lock.toml")
        assert key == "airline/refund.md"

    def test_the_key_does_not_depend_on_how_the_path_was_typed(self, tmp_path: Path) -> None:
        absolute = lock_key(tmp_path / "refund.md", tmp_path / "spec.lock.toml")
        relative = lock_key(Path(f"{tmp_path}/./refund.md"), tmp_path / "spec.lock.toml")
        assert absolute == relative

    def test_it_is_forward_slashed(self, tmp_path: Path) -> None:
        # The lockfile is committed and read on every platform.
        assert "\\" not in lock_key(tmp_path / "a" / "b.md", tmp_path / "spec.lock.toml")
