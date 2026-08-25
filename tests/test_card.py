from pathlib import Path

import pytest

from specdeck.card import CardError, parse, parse_text

FULL = """\
# Scenario: refund request on basic economy
context:
  fixture: airline_seed.json
  policy: airline.md
  simulator: "frustrated customer wants a refund on flight F1234"

The agent refuses the change, clearly explains the basic economy
restriction, and proposes cancel-and-rebook as an alternative.
It never promises an exception.

wire:
  - modify_reservation: never
  - web_search: at_most 2
  - latency: under 120s

credit:
  - "tone remains apologetic and professional": 2
  - wire: response_tokens under 400: 1
"""

PROSE_ONLY = """\
# Scenario: a card with nothing but prose
The agent answers the question and stops.
"""


class TestFourBlocks:
    def test_title_comes_from_the_heading(self) -> None:
        assert parse_text(FULL).title == "Scenario: refund request on basic economy"

    def test_context_is_a_mapping_with_quotes_stripped(self) -> None:
        context = parse_text(FULL).context
        assert context.fixture == "airline_seed.json"
        assert context.policy == "airline.md"
        assert context.simulator.startswith("frustrated customer")

    def test_prose_is_everything_between_the_keyed_blocks(self) -> None:
        prose = parse_text(FULL).prose
        assert prose.startswith("The agent refuses")
        assert prose.endswith("never promises an exception.")
        assert "fixture" not in prose and "web_search" not in prose

    def test_wires_are_raw_rule_text_for_the_engine_to_compile(self) -> None:
        assert parse_text(FULL).wires == [
            "modify_reservation: never",
            "web_search: at_most 2",
            "latency: under 120s",
        ]

    def test_credit_splits_criteria_from_wires(self) -> None:
        card = parse_text(FULL)
        assert [(c.text, c.weight) for c in card.credit_criteria] == [
            ("tone remains apologetic and professional", 2)
        ]
        assert [(w.text, w.weight) for w in card.credit_wires] == [("response_tokens under 400", 1)]


class TestProseOnly:
    def test_a_prose_only_card_parses(self) -> None:
        card = parse_text(PROSE_ONLY)
        assert card.prose == "The agent answers the question and stops."
        assert card.wires == [] and card.credit_criteria == [] and card.credit_wires == []

    def test_wires_are_never_a_prerequisite(self) -> None:
        assert parse_text(PROSE_ONLY).context.simulator == ""


class TestErrors:
    def test_a_card_without_a_heading_is_rejected(self) -> None:
        with pytest.raises(CardError, match=r"heading"):
            parse_text("context:\n  policy: p.md\n")

    def test_a_repeated_block_is_rejected(self) -> None:
        with pytest.raises(CardError, match=r"wire"):
            parse_text("# Scenario: x\nprose\nwire:\n  - a: never\nwire:\n  - b: never\n")

    def test_a_credit_entry_without_a_weight_is_rejected(self) -> None:
        with pytest.raises(CardError, match=r"weight"):
            parse_text('# Scenario: x\nprose\ncredit:\n  - "tone is warm"\n')

    def test_a_non_integer_weight_is_rejected(self) -> None:
        with pytest.raises(CardError, match=r"weight"):
            parse_text('# Scenario: x\nprose\ncredit:\n  - "tone is warm": high\n')

    def test_a_zero_weight_is_rejected(self) -> None:
        with pytest.raises(CardError, match=r"weight"):
            parse_text('# Scenario: x\nprose\ncredit:\n  - "tone is warm": 0\n')

    def test_an_unknown_context_key_names_itself(self) -> None:
        with pytest.raises(CardError, match=r"dataset"):
            parse_text("# Scenario: x\ncontext:\n  dataset: d.json\n\nprose\n")

    def test_the_error_carries_the_card_path(self, tmp_path: Path) -> None:
        card = tmp_path / "broken.md"
        card.write_text("no heading here\n")
        with pytest.raises(CardError, match=r"broken.md"):
            parse(card)


class TestFile:
    def test_parse_records_the_path_it_read(self, tmp_path: Path) -> None:
        card = tmp_path / "refund.md"
        card.write_text(FULL)
        assert parse(card).path == str(card)

    def test_context_paths_resolve_against_the_card(self, tmp_path: Path) -> None:
        (tmp_path / "airline.md").write_text("policy text")
        card = tmp_path / "refund.md"
        card.write_text(FULL)
        assert parse(card).policy_path == tmp_path / "airline.md"

    def test_a_card_with_no_policy_has_no_policy_path(self) -> None:
        assert parse_text(PROSE_ONLY).policy_path is None


class TestBlockIndentation:
    def test_a_flush_left_wire_entry_is_rejected_not_read_as_prose(self) -> None:
        # Silently reclassifying it unenforces every gate wire on the card.
        with pytest.raises(CardError, match=r"column zero"):
            parse_text("# Scenario: x\nprose\nwire:\n- update_reservation: never\n")

    def test_the_error_names_the_block_and_the_entry(self) -> None:
        with pytest.raises(CardError, match=r"wire.*update_reservation"):
            parse_text("# Scenario: x\nprose\nwire:\n- update_reservation: never\n")

    def test_a_flush_left_credit_entry_is_rejected_too(self) -> None:
        with pytest.raises(CardError, match=r"column zero"):
            parse_text('# Scenario: x\nprose\ncredit:\n- "tone is warm": 2\n')

    def test_a_markdown_list_inside_prose_is_still_prose(self) -> None:
        card = parse_text("# Scenario: x\nThe agent:\n- answers\n- stops\n")
        assert "- answers" in card.prose and card.wires == []


class TestPathContainment:
    def test_a_policy_escaping_the_card_directory_is_rejected(self, tmp_path: Path) -> None:
        card = tmp_path / "evil.md"
        card.write_text("# Scenario: x\ncontext:\n  policy: ../../../etc/passwd\n\nprose\n")
        with pytest.raises(CardError, match=r"outside the card's directory"):
            _ = parse(card).policy_path

    def test_an_absolute_policy_is_rejected(self, tmp_path: Path) -> None:
        card = tmp_path / "evil.md"
        card.write_text("# Scenario: x\ncontext:\n  policy: /etc/passwd\n\nprose\n")
        with pytest.raises(CardError, match=r"outside the card's directory"):
            _ = parse(card).policy_path

    def test_a_fixture_escaping_the_card_directory_is_rejected(self, tmp_path: Path) -> None:
        card = tmp_path / "evil.md"
        card.write_text("# Scenario: x\ncontext:\n  fixture: ../secrets.json\n\nprose\n")
        with pytest.raises(CardError, match=r"outside the card's directory"):
            _ = parse(card).fixture_path

    def test_a_subdirectory_policy_is_allowed(self, tmp_path: Path) -> None:
        (tmp_path / "policy").mkdir()
        (tmp_path / "policy" / "airline.md").write_text("text")
        card = tmp_path / "ok.md"
        card.write_text("# Scenario: x\ncontext:\n  policy: policy/airline.md\n\nprose\n")
        assert parse(card).policy_path == tmp_path / "policy" / "airline.md"


class TestDeclaredTraces:
    """`traces:` is one glob, resolved against the card's own directory."""

    def _card(self, tmp_path: Path, pattern: str, *names: str) -> Path:
        (tmp_path / "traces").mkdir(exist_ok=True)
        for name in names:
            (tmp_path / "traces" / name).write_text("{}")
        card = tmp_path / "refund.md"
        card.write_text(f"# Scenario: x\ncontext:\n  traces: {pattern}\n\nThe agent answers.\n")
        return card

    def test_the_pattern_is_kept_verbatim(self, tmp_path: Path) -> None:
        card = parse(self._card(tmp_path, "traces/*.otlp.json", "a.otlp.json"))
        assert card.context.traces == "traces/*.otlp.json"

    def test_every_match_resolves_sorted(self, tmp_path: Path) -> None:
        path = self._card(tmp_path, "traces/*.otlp.json", "b.otlp.json", "a.otlp.json")
        assert [p.name for p in parse(path).trace_paths] == ["a.otlp.json", "b.otlp.json"]

    def test_it_resolves_against_the_card_and_never_the_cwd(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Where you stand must not change which recordings a card is evaluated against —
        # the rule `policy:` and `fixture:` already follow.
        path = self._card(tmp_path, "traces/*.otlp.json", "a.otlp.json")
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        monkeypatch.chdir(elsewhere)
        assert parse(path).trace_paths == [tmp_path / "traces" / "a.otlp.json"]

    def test_a_pattern_with_no_magic_resolves_to_that_one_file(self, tmp_path: Path) -> None:
        path = self._card(tmp_path, "traces/a.otlp.json", "a.otlp.json", "b.otlp.json")
        assert [p.name for p in parse(path).trace_paths] == ["a.otlp.json"]

    def test_a_glob_matching_nothing_returns_nothing_rather_than_raising(
        self, tmp_path: Path
    ) -> None:
        # The emptiness decision belongs to the caller: lint reports a finding and the
        # runner refuses to start, and those are different answers to one fact.
        assert parse(self._card(tmp_path, "traces/*.otlp.json")).trace_paths == []

    def test_a_glob_escaping_the_card_directory_is_rejected(self, tmp_path: Path) -> None:
        # `Path('cards').glob('../*.md')` really does escape, so containment has to run
        # per match rather than on the pattern.
        (tmp_path / "outside.json").write_text("{}")
        inner = tmp_path / "deck"
        inner.mkdir()
        card = inner / "refund.md"
        card.write_text("# Scenario: x\ncontext:\n  traces: ../*.json\n\nThe agent answers.\n")
        with pytest.raises(CardError, match=r"outside the card's directory"):
            _ = parse(card).trace_paths

    def test_an_absolute_glob_is_a_card_error_not_a_bare_not_implemented(
        self, tmp_path: Path
    ) -> None:
        # `Path.glob` raises NotImplementedError for an absolute pattern, which is not a
        # USER_ERROR — uncaught, a pattern the user typed would exit 3, "specdeck broke".
        card = tmp_path / "evil.md"
        card.write_text("# Scenario: x\ncontext:\n  traces: /etc/*\n\nThe agent answers.\n")
        with pytest.raises(CardError, match=r"traces"):
            _ = parse(card).trace_paths

    def test_a_declared_but_blank_glob_is_read_as_declaring_none(self, tmp_path: Path) -> None:
        # It never reaches `Path.glob`, which rejects an empty pattern with a ValueError
        # the boundary would otherwise have to turn into a user error.
        card = tmp_path / "blank.md"
        card.write_text("# Scenario: x\ncontext:\n  traces:\n\nThe agent answers.\n")
        assert parse(card).trace_paths == []

    def test_a_card_declaring_none_still_parses_and_reads_none(self) -> None:
        assert parse_text(FULL).trace_paths == []

    def test_an_unknown_context_key_now_names_four_legal_ones(self) -> None:
        with pytest.raises(CardError, match=r"traces"):
            parse_text("# Scenario: x\ncontext:\n  dataset: d.json\n\nprose\n")
