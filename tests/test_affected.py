"""`--affected-by`: which cards a diff touches, and the two failures that look alike.

The tests that matter most here are the asymmetry pair — a malformed diff raises and a
diff that matched nothing does not — and the narrowing proof. All five committed cards name
the same policy, so a policy edit selects six of six and demonstrates nothing; only a
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


def _prefixed(path: str, old: str, new: str) -> str:
    """The same modification a foreign prefix pair writes — `diff.mnemonicPrefix` writes
    `c/` and `i/`, `--src-prefix`/`--dst-prefix` write whatever they were given."""
    return (
        f"diff --git {old}{path} {new}{path}\n"
        "index 1111111..2222222 100644\n"
        f"--- {old}{path}\n"
        f"+++ {new}{path}\n"
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
        (change,) = parse_diff(_deleted("cards/policy/ap.md"), root=ROOT)
        assert change.status == "deleted"
        assert change.path == ROOT / "cards/policy/ap.md"
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

    def test_a_mnemonic_prefix_is_read_as_a_prefix_and_not_as_the_path(self) -> None:
        # `diff.mnemonicPrefix` is a documented git config, settable globally, and it writes
        # `c/` and `i/` where a plain diff writes `a/` and `b/`. Read as part of the path,
        # every one of these paths matches no card — an empty selection and a green run.
        (change,) = parse_diff(_prefixed("cards/one.md", "c/", "i/"), root=ROOT)
        assert change.path == ROOT / "cards/one.md"

    def test_a_custom_src_and_dst_prefix_are_read_the_same_way(self) -> None:
        (change,) = parse_diff(_prefixed("cards/one.md", "old/", "new/"), root=ROOT)
        assert change.path == ROOT / "cards/one.md"

    def test_an_addition_under_a_foreign_prefix_keeps_its_path_and_its_status(self) -> None:
        # `--- /dev/null` is the only thing the `---`/`+++` pair is read for; the path is
        # the header's, which carries the prefix on both sides even for a new file.
        body = (
            "diff --git c/added.txt i/added.txt\n"
            "new file mode 100644\n"
            "index 0000000..3e75765\n"
            "--- /dev/null\n"
            "+++ i/added.txt\n"
            "@@ -0,0 +1 @@\n"
            "+new\n"
        )
        (change,) = parse_diff(body, root=ROOT)
        assert (change.status, change.path) == ("added", ROOT / "added.txt")

    def test_the_stanzas_with_no_hunk_at_all_read_a_foreign_prefix_too(self) -> None:
        # A mode change and a binary file never write `---`/`+++`, so the header is all
        # there is either way and the prefix has to come off it.
        mode = "diff --git c/sp ace.txt i/sp ace.txt\nold mode 100644\nnew mode 100755\n"
        binary = (
            "diff --git c/d.bin i/d.bin\n"
            "index 366fd40..e570710 100644\n"
            "Binary files c/d.bin and i/d.bin differ\n"
        )
        assert [one.path for one in parse_diff(_diff(mode, binary), root=ROOT)] == [
            ROOT / "sp ace.txt",
            ROOT / "d.bin",
        ]

    def test_a_prefix_of_two_components_is_refused_rather_than_guessed_at(self) -> None:
        # `--src-prefix=foo/bar/` cannot be told from a path that starts `foo/bar/`, and a
        # wrong guess is a card silently not selected. Refused by name, like a quoted path.
        body = _prefixed("cards/one.md", "foo/bar/", "baz/qux/")
        with pytest.raises(DiffError) as caught:
            parse_diff(body, root=ROOT)
        assert "--src-prefix" in str(caught.value)

    def test_a_quoted_path_is_refused_in_a_rename_stanza_too(self) -> None:
        # Verbatim from `git mv a.txt café.txt; git diff --cached -M` under the default
        # `core.quotePath=true`. The header alone would not catch it — only its second path
        # is quoted — and the rename lines are what the path is read from.
        body = (
            'diff --git a/a.txt "b/caf\\303\\251.txt"\n'
            "similarity index 100%\n"
            "rename from a.txt\n"
            'rename to "caf\\303\\251.txt"\n'
        )
        with pytest.raises(DiffError) as caught:
            parse_diff(body, root=ROOT)
        assert "core.quotePath" in str(caught.value)

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
        policy=ROOT / "cards/policy/ap.md",
        fixture=ROOT / "cards/fixtures/one.json",
        traces=[ROOT / "cards/traces/one.otlp.json"],
    ),
    _inputs(
        "two.md",
        policy=ROOT / "cards/policy/ap.md",
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
        selection = _select(_modified("cards/policy/ap.md"))
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
        selection = _select(_deleted("cards/policy/ap.md"))
        assert len(selection.cards) == 2
        assert selection.reasons[str(ROOT / "cards/one.md")] == [
            "policy cards/policy/ap.md deleted"
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
        assert "cannot start" in selection.reasons[str(ROOT / "cards/broken.md")][0]

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
        result = _run(root, _modified("cards/fixtures/escalation-after-repeated-pressure.json"))
        assert result.exit_code == 0, result.stdout
        unwrapped = " ".join(result.stdout.split())
        assert "selected 1 of 5 cards" in unwrapped
        assert "1 card, 1 passed" in unwrapped
        assert "over-threshold-second-approval" not in unwrapped

    def test_the_same_edit_under_a_mnemonic_prefix_runs_the_same_card(self, tmp_path: Path) -> None:
        # The end the reviewer reproduced at: with `diff.mnemonicPrefix=true` set globally,
        # every path in the diff was read with an `i/` still on it, so the deck reported the
        # ratified "the diff touched no card" answer and exited 0 on a diff that touched one.
        root = _deck_copy(tmp_path)
        result = _run(root, _prefixed("cards/fixtures/escalation-after-repeated-pressure.json", "c/", "i/"))
        assert result.exit_code == 0, result.stdout
        assert "selected 1 of 5 cards" in " ".join(result.stdout.split())

    def test_the_evidence_for_the_selection_is_printed(self, tmp_path: Path) -> None:
        root = _deck_copy(tmp_path)
        result = _run(root, _modified("cards/traces/bank-details-in-invoice-note.1.otlp.json"))
        unwrapped = " ".join(result.stdout.split())
        assert "trace" in unwrapped and "bank-details-in-invoice-note.otlp.json modified" in unwrapped

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

    def test_an_empty_diff_is_refused_rather_than_run_as_a_green_deck(self, tmp_path: Path) -> None:
        # An empty pipe in CI is almost always a `git diff` whose ref did not resolve.
        root = _deck_copy(tmp_path)
        result = _run(root, "")
        assert result.exit_code == 2, result.stdout
        assert "empty diff" in " ".join(result.stdout.split())

    def test_the_lockfile_selects_the_whole_deck(self, tmp_path: Path) -> None:
        root = _deck_copy(tmp_path)
        result = _run(root, _modified("cards/spec.lock.toml"))
        assert result.exit_code == 0, result.stdout
        unwrapped = " ".join(result.stdout.split())
        assert "selected 5 of 5 cards" in unwrapped and "5 cards, 5 passed" in unwrapped

    def test_the_vocabulary_reaches_the_selector_when_the_flag_is_given(
        self, tmp_path: Path
    ) -> None:
        # The flag is inert over a deck of recorded traces and live only as a selection
        # edge, so a slip at either hop into `_deck` would leave every unit test green
        # while the flag did nothing at all.
        root = _deck_copy(tmp_path)
        result = _run(
            root,
            _modified("cards/vocabulary.txt"),
            "--vocabulary",
            str(root / "cards" / "vocabulary.txt"),
        )
        assert result.exit_code == 0, result.stdout
        unwrapped = " ".join(result.stdout.split())
        assert "selected 5 of 5 cards" in unwrapped and "vocabulary.txt" in unwrapped

    def test_without_the_flag_the_same_diff_selects_nothing(self, tmp_path: Path) -> None:
        root = _deck_copy(tmp_path)
        result = _run(root, _modified("cards/vocabulary.txt"))
        assert result.exit_code == 0, result.stdout
        assert "selected 0 of 5 cards" in " ".join(result.stdout.split())

    def test_a_diff_read_from_a_file_reads_the_same_as_one_from_stdin(self, tmp_path: Path) -> None:
        root = _deck_copy(tmp_path)
        patch = tmp_path / "pr.diff"
        patch.write_text(_modified("cards/fixtures/escalation-after-repeated-pressure.json"))
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
                str(root / "cards" / "over-threshold-second-approval.md"),
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
        # error whatever the diff says, and a diff selecting none of six is an answer.
        empty = tmp_path / "deck"
        empty.mkdir()
        result = runner.invoke(
            app,
            ["run", str(empty), "--affected-by", "-", "--diff-root", str(tmp_path)],
            input=_modified("src/agent.py"),
        )
        assert result.exit_code == 2, result.stdout
        assert "no cards under" in result.stdout

    def test_a_deleted_recording_selects_the_card_that_declared_it(self, tmp_path: Path) -> None:
        # The hole a unit test cannot see, because it builds `Inputs(traces=[...])` by hand:
        # a recording the diff removed is not on disk, so the card's glob resolved to
        # nothing and the card was dropped from a deck that then exited 0 — while the same
        # tree run without `--affected-by` exits 2. A card the deck cannot start cannot be
        # excluded, and the selector now asks the runner's own question to find out.
        root = _deck_copy(tmp_path)
        recording = "cards/traces/escalation-after-repeated-pressure.1.otlp.json"
        (root / recording).unlink()
        result = _run(root, _deleted(recording))
        assert result.exit_code == 2, result.stdout
        unwrapped = " ".join(result.stdout.split())
        assert "selected 1 of 5 cards" in unwrapped
        assert "cannot start" in unwrapped
        assert "matches no file" in unwrapped

    def test_a_card_declaring_no_traces_is_selected_by_a_diff_that_matched_nothing(
        self, tmp_path: Path
    ) -> None:
        # The same rule from the other side: `traces:` deleted from the card rather than the
        # recording deleted from the tree. The deck exits 2 on it either way, so a selection
        # that exits 0 is the selector hiding a card the deck could not run.
        root = _deck_copy(tmp_path)
        card = root / "cards" / "escalation-after-repeated-pressure.md"
        card.write_text(
            "".join(
                line for line in card.read_text().splitlines(keepends=True) if "traces:" not in line
            )
        )
        result = _run(root, _modified("src/agent.py"))
        assert result.exit_code == 2, result.stdout
        unwrapped = " ".join(result.stdout.split())
        assert "selected 1 of 5 cards" in unwrapped
        assert "no traces to run" in unwrapped

    def test_a_card_that_does_not_parse_is_selected_by_a_diff_that_matched_nothing(
        self, tmp_path: Path
    ) -> None:
        # A card that cannot be read cannot be excluded, so the deck still reports it.
        root = _deck_copy(tmp_path)
        (root / "cards" / "broken.md").write_text("no heading here\n")
        result = _run(root, _modified("src/agent.py"))
        assert result.exit_code == 2, result.stdout
        unwrapped = " ".join(result.stdout.split())
        assert "selected 1 of 5 cards" in unwrapped
        assert "cannot start" in unwrapped
