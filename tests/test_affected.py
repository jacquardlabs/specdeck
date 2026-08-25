"""`--affected-by`: which cards a diff touches, and the two failures that look alike.

The tests that matter most here are the asymmetry pair — a malformed diff raises and a
diff that matched nothing does not — and the narrowing proof. All five committed cards name
the same policy, so a policy edit selects five of five and demonstrates nothing; only a
fixture, trace or card-file edit shows that the selector narrows at all.

No provider call happens anywhere in this feature: `parse_diff` is text to data and
`select` is data to data, so nothing here fakes an agent or replays a cassette. The CLI
cases run the committed deck, which replays its own recordings offline.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

from specdeck.affected import DiffError, Inputs, parse_diff, select
from specdeck.cli import app

runner = CliRunner()
CARDS = Path(__file__).resolve().parent.parent / "cards"
ROOT = Path("/repo")


def _diff(*bodies: str) -> str:
    return "".join(bodies)


def _modified(path: str) -> str:
    return (
        f"diff --git a/{path} b/{path}\n"
        "index 1111111..2222222 100644\n"
        f"--- a/{path}\n"
        f"+++ b/{path}\n"
        "@@ -1,2 +1,2 @@\n"
        "-old\n"
        "+new\n"
    )


def _deleted(path: str) -> str:
    return (
        f"diff --git a/{path} b/{path}\n"
        "deleted file mode 100644\n"
        "index 2bdf67a..0000000\n"
        f"--- a/{path}\n"
        "+++ /dev/null\n"
        "@@ -1 +0,0 @@\n"
        "-gone\n"
    )


class TestParsingADiff:
    """Anchored on `diff --git`, and every stanza that begins must yield a path."""

    def test_two_files_yield_two_changes_against_the_given_root(self) -> None:
        changes = parse_diff(_diff(_modified("a.txt"), _modified("sub/b.txt")), root=ROOT)
        assert [one.path for one in changes] == [ROOT / "a.txt", ROOT / "sub/b.txt"]
        assert {one.status for one in changes} == {"modified"}

    def test_a_deletion_names_the_file_that_was_removed(self) -> None:
        (change,) = parse_diff(_deleted("cards/policy/airline.md"), root=ROOT)
        assert change.status == "deleted"
        assert change.path == ROOT / "cards/policy/airline.md"
        assert change.previous is None

    def test_an_addition_reads_its_path_off_the_new_side(self) -> None:
        body = (
            "diff --git a/added.txt b/added.txt\n"
            "new file mode 100644\n"
            "--- /dev/null\n"
            "+++ b/added.txt\n"
            "@@ -0,0 +1 @@\n"
            "+new\n"
        )
        (change,) = parse_diff(body, root=ROOT)
        assert (change.status, change.path) == ("added", ROOT / "added.txt")

    def test_a_rename_carries_both_sides(self) -> None:
        body = (
            "diff --git a/a.txt b/renamed.txt\n"
            "similarity index 100%\n"
            "rename from a.txt\n"
            "rename to renamed.txt\n"
        )
        (change,) = parse_diff(body, root=ROOT)
        assert change.status == "renamed"
        assert change.path == ROOT / "renamed.txt"
        assert change.previous == ROOT / "a.txt"
        assert change.paths == (ROOT / "renamed.txt", ROOT / "a.txt")
        assert change.label == "a.txt -> renamed.txt"

    def test_a_binary_stanza_is_a_change_rather_than_a_refusal(self) -> None:
        # It has no `---`/`+++` pair at all, so the path comes off the header line.
        body = (
            "diff --git a/d.bin b/d.bin\n"
            "index 366fd40..e570710 100644\n"
            "Binary files a/d.bin and b/d.bin differ\n"
        )
        (change,) = parse_diff(body, root=ROOT)
        assert (change.status, change.path) == ("modified", ROOT / "d.bin")

    def test_a_path_with_a_space_keeps_it_and_drops_gits_trailing_tab(self) -> None:
        # git appends a tab to the `---`/`+++` lines when the path contains a space.
        body = (
            "diff --git a/sp ace.txt b/sp ace.txt\n"
            "index f719efd..2312e0b 100644\n"
            "--- a/sp ace.txt\t\n"
            "+++ b/sp ace.txt\t\n"
            "@@ -1 +1,2 @@\n"
            " two\n"
            "+more\n"
        )
        (change,) = parse_diff(body, root=ROOT)
        assert change.path == ROOT / "sp ace.txt"

    def test_a_mode_only_stanza_reads_its_path_off_the_header(self) -> None:
        body = "diff --git a/sp ace.txt b/sp ace.txt\nold mode 100644\nnew mode 100755\n"
        (change,) = parse_diff(body, root=ROOT)
        assert change.path == ROOT / "sp ace.txt"

    def test_no_prefix_paths_are_read_as_they_stand(self) -> None:
        body = (
            "diff --git cards/x.md cards/x.md\n"
            "--- cards/x.md\n"
            "+++ cards/x.md\n"
            "@@ -1 +1 @@\n"
            "-a\n"
            "+b\n"
        )
        (change,) = parse_diff(body, root=ROOT)
        assert change.path == ROOT / "cards/x.md"

    def test_a_header_inside_a_hunk_body_is_body_not_a_header(self) -> None:
        # A diff of a diff. Every line of a hunk body carries a prefix character, so a
        # `diff --git` at column zero can only be a real stanza — but `--- a/x` is what a
        # deleted line of content `-- a/x` looks like, which is why `---`/`+++` are read
        # only before a stanza's first `@@`.
        body = (
            "diff --git a/example.patch b/example.patch\n"
            "--- a/example.patch\n"
            "+++ b/example.patch\n"
            "@@ -1,3 +1,3 @@\n"
            "+diff --git a/decoy.md b/decoy.md\n"
            "+--- a/decoy.md\n"
            "++++ b/decoy.md\n"
        )
        assert [one.path for one in parse_diff(body, root=ROOT)] == [ROOT / "example.patch"]

    def test_a_summary_is_refused_rather_than_read_as_nothing_changed(self) -> None:
        # The regression that matters: `git diff --stat` piped in would otherwise select no
        # card, run nothing and report green forever.
        summary = " cards/x.md | 2 +-\n 1 file changed, 1 insertion(+), 1 deletion(-)\n"
        with pytest.raises(DiffError) as caught:
            parse_diff(summary, root=ROOT)
        assert "--stat" in str(caught.value)

    def test_a_list_of_names_is_refused_too(self) -> None:
        with pytest.raises(DiffError):
            parse_diff("cards/x.md\ncards/y.md\n", root=ROOT)

    def test_whitespace_only_input_is_an_empty_diff_not_a_broken_one(self) -> None:
        assert parse_diff("", root=ROOT) == []
        assert parse_diff("   \n\n\t\n", root=ROOT) == []

    def test_a_quoted_path_is_refused_by_name(self) -> None:
        # Guessing at the octal escapes and getting one byte wrong would produce a path
        # that matches no card, which under this feature is a green run.
        body = 'diff --git "a/caf\\303\\251.md" "b/caf\\303\\251.md"\nnew file mode 100644\n'
        with pytest.raises(DiffError) as caught:
            parse_diff(body, root=ROOT)
        assert "core.quotePath" in str(caught.value)

    def test_a_stanza_with_no_readable_path_refuses_the_whole_diff(self) -> None:
        # "Contributed nothing" and "no card reads it" are indistinguishable downstream.
        with pytest.raises(DiffError) as caught:
            parse_diff("diff --git nonsense\n", root=ROOT)
        assert "diff --git nonsense" in str(caught.value)


def _inputs(name: str, **edges: object) -> Inputs:
    return Inputs(card=ROOT / "cards" / name, **edges)  # type: ignore[arg-type]


DECK = [
    _inputs(
        "one.md",
        policy=ROOT / "cards/policy/airline.md",
        fixture=ROOT / "cards/fixtures/one.json",
        traces=[ROOT / "cards/traces/one.otlp.json"],
    ),
    _inputs(
        "two.md",
        policy=ROOT / "cards/policy/airline.md",
        fixture=ROOT / "cards/fixtures/two.json",
        traces=[ROOT / "cards/traces/two.otlp.json"],
    ),
]
LOCK = ROOT / "cards/spec.lock.toml"


def _select(*bodies: str, vocabulary: Path | None = None, deck: list[Inputs] | None = None):
    return select(
        DECK if deck is None else deck,
        parse_diff(_diff(*bodies), root=ROOT),
        lock_path=LOCK,
        vocabulary_path=vocabulary,
    )


class TestSelectingCards:
    """File-level and nothing more: the card, its policy, its fixture, its traces."""

    def test_a_fixture_edit_selects_exactly_the_one_card_that_reads_it(self) -> None:
        selection = _select(_modified("cards/fixtures/two.json"))
        assert selection.cards == [ROOT / "cards/two.md"]
        assert selection.total == 2
        assert selection.reasons[str(ROOT / "cards/two.md")] == [
            "fixture cards/fixtures/two.json modified"
        ]

    def test_a_trace_edit_selects_the_card_that_declares_it(self) -> None:
        assert _select(_modified("cards/traces/one.otlp.json")).cards == [ROOT / "cards/one.md"]

    def test_a_card_file_edit_selects_that_card(self) -> None:
        selection = _select(_modified("cards/one.md"))
        assert selection.cards == [ROOT / "cards/one.md"]
        assert selection.reasons[str(ROOT / "cards/one.md")] == ["card cards/one.md modified"]

    def test_a_shared_policy_selects_every_card_naming_it(self) -> None:
        selection = _select(_modified("cards/policy/airline.md"))
        assert len(selection.cards) == 2
        # Not `everything`: every card matched on its own edge, which is a different fact
        # from a deck-wide input having changed.
        assert selection.everything == ""

    def test_the_lockfile_selects_the_whole_deck_and_says_so(self) -> None:
        selection = _select(_modified("cards/spec.lock.toml"))
        assert len(selection.cards) == 2
        assert "spec.lock.toml" in selection.everything
        assert selection.reasons[str(ROOT / "cards/one.md")] == [selection.everything]

    def test_the_vocabulary_selects_the_whole_deck_when_one_was_given(self) -> None:
        vocabulary = ROOT / "cards/vocabulary.txt"
        selection = _select(_modified("cards/vocabulary.txt"), vocabulary=vocabulary)
        assert len(selection.cards) == 2
        assert "vocabulary.txt" in selection.everything

    def test_without_a_vocabulary_flag_there_is_no_vocabulary_edge(self) -> None:
        assert _select(_modified("cards/vocabulary.txt")).cards == []

    def test_a_diff_touching_no_card_input_selects_nothing(self) -> None:
        selection = _select(_modified("src/agent.py"))
        assert selection.cards == []
        assert selection.everything == ""
        assert selection.total == 2

    def test_a_deleted_policy_still_selects_the_cards_that_read_it(self) -> None:
        # Deleting a policy out from under a card is not a reason to stop checking it.
        selection = _select(_deleted("cards/policy/airline.md"))
        assert len(selection.cards) == 2
        assert selection.reasons[str(ROOT / "cards/one.md")] == [
            "policy cards/policy/airline.md deleted"
        ]

    def test_a_renamed_fixture_selects_the_card_that_still_names_the_old_path(self) -> None:
        body = (
            "diff --git a/cards/fixtures/one.json b/cards/fixtures/moved.json\n"
            "similarity index 100%\n"
            "rename from cards/fixtures/one.json\n"
            "rename to cards/fixtures/moved.json\n"
        )
        selection = _select(body)
        assert selection.cards == [ROOT / "cards/one.md"]
        assert "->" in selection.reasons[str(ROOT / "cards/one.md")][0]

    def test_a_card_with_several_touched_inputs_names_every_one_of_them(self) -> None:
        selection = _select(_modified("cards/one.md"), _modified("cards/fixtures/one.json"))
        assert selection.reasons[str(ROOT / "cards/one.md")] == [
            "card cards/one.md modified",
            "fixture cards/fixtures/one.json modified",
        ]

    def test_every_selected_card_carries_a_reason(self) -> None:
        # A selection with no stated evidence is one nobody can check.
        for body in (_modified("cards/one.md"), _modified("cards/spec.lock.toml")):
            selection = _select(body)
            assert selection.cards
            assert all(selection.reasons[str(card)] for card in selection.cards)

    def test_a_card_that_could_not_be_read_is_selected_by_any_diff(self) -> None:
        broken = Inputs(card=ROOT / "cards/broken.md", unreadable="line 1: no heading")
        selection = _select(_modified("src/agent.py"), deck=[*DECK, broken])
        assert selection.cards == [ROOT / "cards/broken.md"]
        assert "could not be read" in selection.reasons[str(ROOT / "cards/broken.md")][0]

    def test_an_empty_diff_selects_nothing_without_raising(self) -> None:
        assert select(DECK, [], lock_path=LOCK).cards == []

    def test_both_sides_resolve_so_a_symlinked_root_still_matches(self, tmp_path: Path) -> None:
        # A path that fails to match is indistinguishable from a file no card reads, so an
        # unresolved side would be reported as a diff that touched nothing.
        real = tmp_path / "real"
        (real / "cards").mkdir(parents=True)
        link = tmp_path / "link"
        link.symlink_to(real)
        card = real / "cards" / "one.md"
        card.write_text("# Scenario: x\n\nThe agent answers.\n")
        selection = select(
            [Inputs(card=card)],
            parse_diff(_modified("cards/one.md"), root=link),
            lock_path=real / "cards" / "spec.lock.toml",
        )
        assert selection.cards == [card]


def _deck_copy(tmp_path: Path) -> Path:
    shutil.copytree(CARDS, tmp_path / "cards")
    return tmp_path


def _run(root: Path, diff: str, *extra: str):
    return runner.invoke(
        app,
        ["run", str(root / "cards"), "--affected-by", "-", "--diff-root", str(root), *extra],
        input=diff,
    )


class TestTheCommand:
    """`git diff origin/main... | specdeck run cards/ --affected-by -`."""

    def test_a_fixture_edit_runs_one_card_of_five(self, tmp_path: Path) -> None:
        root = _deck_copy(tmp_path)
        result = _run(root, _modified("cards/fixtures/delay-compensation-budget.json"))
        assert result.exit_code == 0, result.stdout
        unwrapped = " ".join(result.stdout.split())
        assert "selected 1 of 5 cards" in unwrapped
        assert "1 card, 1 passed" in unwrapped
        assert "basic-economy-return-change" not in unwrapped

    def test_the_evidence_for_the_selection_is_printed(self, tmp_path: Path) -> None:
        root = _deck_copy(tmp_path)
        result = _run(root, _modified("cards/traces/booking-with-certificates.otlp.json"))
        unwrapped = " ".join(result.stdout.split())
        assert "trace" in unwrapped and "booking-with-certificates.otlp.json modified" in unwrapped

    def test_a_diff_that_matched_nothing_runs_nothing_and_exits_zero(self, tmp_path: Path) -> None:
        root = _deck_copy(tmp_path)
        result = _run(root, _modified("src/agent.py"))
        assert result.exit_code == 0, result.stdout
        unwrapped = " ".join(result.stdout.split())
        assert "selected 0 of 5 cards" in unwrapped
        assert "nothing to run" in unwrapped
        # No deck table: "0 cards, 0 passed" where a verdict goes reads as a result.
        assert "0 passed" not in unwrapped
        assert "PASS" not in unwrapped

    def test_a_malformed_diff_exits_two_rather_than_selecting_nothing(self, tmp_path: Path) -> None:
        # The asymmetry the feature is honest by. Conflate this with the case above and a
        # broken invocation runs the full suite of nothing, forever, looking like it works.
        root = _deck_copy(tmp_path)
        result = _run(root, " cards/x.md | 2 +-\n 1 file changed\n")
        assert result.exit_code == 2, result.stdout
        assert "diff --git" in " ".join(result.stdout.split())

    def test_the_lockfile_selects_the_whole_deck(self, tmp_path: Path) -> None:
        root = _deck_copy(tmp_path)
        result = _run(root, _modified("cards/spec.lock.toml"))
        assert result.exit_code == 0, result.stdout
        unwrapped = " ".join(result.stdout.split())
        assert "selected 5 of 5 cards" in unwrapped and "5 cards, 5 passed" in unwrapped

    def test_a_diff_read_from_a_file_reads_the_same_as_one_from_stdin(self, tmp_path: Path) -> None:
        root = _deck_copy(tmp_path)
        patch = tmp_path / "pr.diff"
        patch.write_text(_modified("cards/fixtures/delay-compensation-budget.json"))
        result = runner.invoke(
            app,
            [
                "run",
                str(root / "cards"),
                "--affected-by",
                str(patch),
                "--diff-root",
                str(root),
            ],
        )
        assert result.exit_code == 0, result.stdout
        assert "selected 1 of 5 cards" in " ".join(result.stdout.split())

    def test_a_diff_file_that_does_not_exist_is_a_user_error(self, tmp_path: Path) -> None:
        root = _deck_copy(tmp_path)
        result = runner.invoke(
            app, ["run", str(root / "cards"), "--affected-by", str(tmp_path / "absent.diff")]
        )
        assert result.exit_code == 2, result.stdout
        assert "absent.diff" in result.stdout

    def test_one_card_is_not_a_deck_to_select_from(self, tmp_path: Path) -> None:
        root = _deck_copy(tmp_path)
        result = runner.invoke(
            app,
            [
                "run",
                str(root / "cards" / "basic-economy-return-change.md"),
                "--affected-by",
                "-",
            ],
            input=_modified("cards/one.md"),
        )
        assert result.exit_code == 2, result.stdout
        assert "--affected-by" in " ".join(result.stdout.split())

    def test_a_diff_root_with_no_diff_to_root_is_refused(self, tmp_path: Path) -> None:
        root = _deck_copy(tmp_path)
        result = runner.invoke(app, ["run", str(root / "cards"), "--diff-root", str(root)])
        assert result.exit_code == 2, result.stdout
        assert "--diff-root" in " ".join(result.stdout.split())

    def test_an_empty_directory_is_still_a_user_error(self, tmp_path: Path) -> None:
        # Discovery runs before the selection filters it: an empty directory is a user
        # error whatever the diff says, and a diff selecting none of five is an answer.
        empty = tmp_path / "deck"
        empty.mkdir()
        result = runner.invoke(
            app,
            ["run", str(empty), "--affected-by", "-", "--diff-root", str(tmp_path)],
            input=_modified("src/agent.py"),
        )
        assert result.exit_code == 2, result.stdout
        assert "no cards under" in result.stdout

    def test_a_card_that_does_not_parse_is_selected_by_a_diff_that_matched_nothing(
        self, tmp_path: Path
    ) -> None:
        # A card that cannot be read cannot be excluded, so the deck still reports it.
        root = _deck_copy(tmp_path)
        (root / "cards" / "broken.md").write_text("no heading here\n")
        result = _run(root, _modified("src/agent.py"))
        assert result.exit_code == 2, result.stdout
        unwrapped = " ".join(result.stdout.split())
        assert "selected 1 of 6 cards" in unwrapped
        assert "could not be read" in unwrapped
