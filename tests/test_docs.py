"""The example cards in the docs are executable, and are checked as such.

The README's card is the first thing anyone reads, and it shipped for months with a wire
that does not compile — `writer<->reviewer: escalate_to_hitl after 5 non_agreement`, an
agent-pair scope the palette never had. Nothing caught it because no test ever fed the
documentation to the parser.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from specdeck.card import parse_text
from specdeck.wires import compile_wires

ROOT = Path(__file__).resolve().parent.parent
DOCS = ["README.md", "docs/card-format.md"]


def example(doc: str) -> str:
    """The first fenced ```markdown block, which is where each doc shows a card."""
    match = re.search(r"```markdown\n(.*?)```", (ROOT / doc).read_text(), re.S)
    assert match, f"{doc}: no fenced markdown card to check"
    return match.group(1)


@pytest.mark.parametrize("doc", DOCS)
class TestTheDocumentedCard:
    def test_it_parses(self, doc: str) -> None:
        card = parse_text(example(doc), path=doc)
        assert card.prose, doc

    def test_every_wire_compiles(self, doc: str) -> None:
        # Not just "the card parses": an unrecognised rule survives parsing and dies at
        # compile, which is exactly how the broken example went unnoticed.
        card = parse_text(example(doc), path=doc)
        assert len(compile_wires(card)) == len(card.wires) + len(card.credit_wires), doc

    def test_it_shows_both_zones(self, doc: str) -> None:
        card = parse_text(example(doc), path=doc)
        assert card.wires and card.credit_criteria, doc


def test_the_two_docs_show_the_same_card() -> None:
    # card-format.md is the full spec the README links to. Two examples that drift are
    # worse than one, because the reader cannot tell which is current.
    assert example("README.md").strip() == example("docs/card-format.md").strip()
