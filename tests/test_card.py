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
