"""Coverage denominators: how much of the system the deck actually touches.

Three tables, printed separately and **never blended into one percentage** — policy,
vocabulary and path are different questions with different denominators, and a single
figure over them would mean nothing. Each carries a `blind` sentence naming what it could
not see, rather than reporting 0% or 0 of 0: lint's rule that a check reporting its own
blindness beats one that silently degrades, applied to the report.

**Nothing here produces a severity and nothing here may import one.** `Severity` and
`Finding` live in `lint`, and a coverage figure that acquires a severity acquires an exit
code — DECISIONS.md, 2026-08-15: coverage percentages never gate CI. `specdeck coverage`
exits 0 on any computed result, whatever the numbers say. The one carve-out, the per-feature
definition obligations, is binary and non-gameable and lives in `lint.py` behind
`specdeck lint`, where it can honestly gate.

What ships of the policy denominator is the **clause inventory**, not the clauses x cards
matrix docs/measurement.md describes. There is no clause-to-card predicate: a card's
`context` names a *document*, and all five committed cards name the same one, so the only
attribution available today is document-level. Rendered as a matrix that would put an
identical mark in every cell of a document's rows and read as per-clause attribution —
precisely the silent degradation the blindness rule exists to prevent. See DECISIONS.md,
2026-08-25.
"""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

from pydantic import BaseModel, Field

from .card import Card
from .judge import slug
from .lint import Vocabulary
from .trace import GenAI, Operation, Trace
from .wires import WireError, compile_wires, named_tools

#: A clause starts at a list marker in column zero. `-`, `*`, `+`, `1.` and `1)`.
_MARKER = re.compile(r"^([-*+]|\d+[.)])\s+(?P<body>.+)$")
_HEADING = re.compile(r"^#{1,6}\s+(?P<title>.*)$")

#: The section id for clauses that appear before any heading. `judge.slug` answers
#: `"criterion"` for empty input, which is its own fallback and not ours.
PREAMBLE = "preamble"


class CoverageError(Exception):
    """A coverage input is not usable. A user error, never a coverage figure."""


class Clause(BaseModel):
    """One obligation extracted from a policy document, quoted back verbatim."""

    id: str
    document: str
    section: str
    text: str
    line: int


class SectionCount(BaseModel):
    section: str
    clauses: int


class PolicyDocument(BaseModel):
    """One policy document, its clause inventory, and the cards that name it."""

    path: str
    clauses: list[Clause] = Field(default_factory=list)
    sections: list[SectionCount] = Field(default_factory=list)
    cards: list[str] = Field(default_factory=list)
    blind: str = ""


class ToolRow(BaseModel):
    tool: str
    wired_by: list[str] = Field(default_factory=list)
    exercised: bool = False


class VocabularyTable(BaseModel):
    rows: list[ToolRow] = Field(default_factory=list)
    blind: str = ""
    traces_seen: int = 0
    traces_blind: str = ""


class Coverage(BaseModel):
    """The three tables, each optional.

    `None` and an empty table are different facts and the renderer treats them so: `None`
    means this invocation did not ask the question, an empty table means it asked and
    found nothing. A run of one card asks only the path question — policy and vocabulary
    are suite-level denominators and one card cannot answer them.
    """

    policy: list[PolicyDocument] | None = None
    vocabulary: VocabularyTable | None = None


def extract_clauses(text: str, *, document: str = "") -> list[Clause]:
    """A policy document's obligations, by a structural rule over the markdown.

    Deterministic and zero-token, deliberately. A model call here would make the
    denominator move when the model moves, and a denominator that is not reproducible is
    not a denominator.

    The rule, stated so a policy author can predict it:

    1. A `#`..`######` heading opens a section. The section id is `judge.slug` of the
       heading; before any heading it is `preamble`. Two headings that slugify alike get
       `_2`, `_3`, so no two clauses share an id.
    2. A **clause starts** at a list marker in column zero — `-`, `*`, `+`, `1.` or `1)`.
    3. It **continues** over every following indented line, and over a blank line whose
       next non-blank line is indented. So a nested bullet qualifies its parent rather
       than becoming a clause of its own.
    4. It **ends** at the next column-zero list marker, at a heading, at a column-zero
       non-list line, or at end of file.
    5. Nothing else is a clause. Headings are not, and column-zero paragraphs are not:
       "The current time is..." sets a scene, it does not oblige the agent.
    6. The text is the matched lines with the marker stripped from the first, otherwise
       verbatim — it is quoted back to a human, so nothing normalises it.
    7. The id is `<section>/<n>`, `n` 1-based within the section. Underscores inside the
       slug, a slash before the index: `preamble/1`, `modify_flight/2`.

    A document written as paragraphs yields nothing, and the caller sets `blind` rather
    than reporting it 0% covered.
    """
    clauses: list[Clause] = []
    section, section_id = "", PREAMBLE
    used: Counter[str] = Counter({PREAMBLE: 1})
    per_section: Counter[str] = Counter()
    open_clause: tuple[str, str, int, list[str]] | None = None
    held_blanks: list[str] = []

    def close() -> None:
        nonlocal open_clause
        if open_clause is None:
            return
        name, ident, line, body = open_clause
        per_section[ident] += 1
        clauses.append(
            Clause(
                id=f"{ident}/{per_section[ident]}",
                document=document,
                section=name,
                text="\n".join(body),
                line=line,
            )
        )
        open_clause = None

    for number, line in enumerate(text.splitlines(), start=1):
        if heading := _HEADING.match(line):
            close()
            held_blanks.clear()
            section = heading.group("title").strip()
            candidate = slug(section) if section else PREAMBLE
            used[candidate] += 1
            section_id = candidate if used[candidate] == 1 else f"{candidate}_{used[candidate]}"
            continue
        if not line.strip():
            if open_clause is not None:
                # Held, not committed: whether this blank ends the clause is decided by
                # what comes after it.
                held_blanks.append(line)
            continue
        if line[0].isspace():
            if open_clause is not None:
                open_clause[3].extend(held_blanks)
                open_clause[3].append(line)
            held_blanks.clear()
            continue
        held_blanks.clear()
        close()
        if marker := _MARKER.match(line):
            open_clause = (section, section_id, number, [marker.group("body")])
    close()
    return clauses


def policy_coverage(cards: list[Card]) -> list[PolicyDocument]:
    """One entry per policy document any card names, with the cards that name it.

    A document named by **zero cards** is the one genuinely deterministic signal here, and
    it is the caller's to supply: this function only sees documents cards point at. The
    command passes the deck's own directory so an unreferenced `.md` under `policy/` shows
    up — see `unnamed_policies`.

    A `policy:` that does not resolve raises rather than being counted blind: lint's
    `dead-path` rule already diagnoses that, and a coverage table quietly reporting a
    missing file as "no clauses" would hide it.
    """
    by_document: dict[Path, list[str]] = {}
    for card in cards:
        path = card.policy_path
        if path is None:
            continue
        if not path.exists():
            raise CoverageError(
                f"{card.path}: policy {card.context.policy!r} does not exist (looked in "
                f"{path.parent})"
            )
        by_document.setdefault(path, []).append(card.path)
    return [_document(path, sorted(cards_)) for path, cards_ in sorted(by_document.items())]


def unnamed_policies(documents: list[PolicyDocument], cards: list[Card]) -> list[PolicyDocument]:
    """Policy documents sitting beside the named ones that no card points at.

    The deterministic half of the policy question the clauses x cards matrix was going to
    answer: a document nobody names is uncovered by the whole suite, and saying so needs no
    per-clause predicate at all.

    Candidates are the other `.md` files in the directories the named documents live in,
    minus the cards themselves. Discovery cannot help here — `lint.cards_under` excludes
    what a card *points at*, so a policy document nobody points at looks exactly like a
    card to it, and `cards/policy/airline.md` parses as one. Siblings-of-a-named-policy is
    the narrowest rule that finds the real case without walking the whole deck.

    The cost, stated: a repo that keeps its policies in the same directory as its cards
    gets any other markdown file there reported as an unnamed policy. Report-only, so the
    failure mode is a line to ignore rather than a red build.
    """
    named = {Path(one.path).resolve() for one in documents}
    excluded = named | {Path(card.path).resolve() for card in cards}
    candidates = sorted(
        {
            found
            for directory in {Path(one.path).parent for one in documents}
            for found in directory.glob("*.md")
            if found.resolve() not in excluded
        }
    )
    return [_document(path, []) for path in candidates]


def _document(path: Path, cards: list[str]) -> PolicyDocument:
    try:
        text = path.read_text()
    except OSError as error:
        raise CoverageError(f"cannot read the policy document at {path}: {error}") from None
    clauses = extract_clauses(text, document=str(path))
    counts: dict[str, int] = {}
    for clause in clauses:
        counts[clause.section] = counts.get(clause.section, 0) + 1
    return PolicyDocument(
        path=str(path),
        clauses=clauses,
        sections=[SectionCount(section=name, clauses=n) for name, n in counts.items()],
        cards=cards,
        blind=(
            ""
            if clauses
            else "no column-zero markdown list items, so no clauses could be extracted — "
            "this document has no denominator, which is not the same as none of it "
            "being covered"
        ),
    )


def vocabulary_coverage(
    cards: list[Card], vocabulary: Vocabulary | None, traces: list[Trace]
) -> VocabularyTable:
    """Declared tools, against the wires that constrain them and the runs that ran them.

    The denominator is the declared vocabulary, never the set of tools the cards happen to
    name: a tool no card mentions is exactly the row this table exists to print. Without a
    vocabulary there is no denominator at all, so the table reports itself blind rather
    than reporting 0 of 0.

    Traces are pooled across the deck on purpose. "Tools with no wire and no exercising
    scenario" is a suite-level question, so it needs no card-to-trace binding and does not
    wait on #70 — at the cost that this table cannot say *which* card exercised a tool.

    "Exercised" means an `execute_tool` span was recorded. A tool the model asked for and
    the runtime refused does not count; #68 is the issue that re-keys that.
    """
    if vocabulary is None:
        return VocabularyTable(
            blind="no tool vocabulary supplied, so there is no denominator to report "
            "against; pass --vocabulary"
        )
    wired: dict[str, list[str]] = {}
    for card in cards:
        try:
            properties = compile_wires(card)
        except WireError:
            continue  # lint owns `wire-syntax`; a card that does not compile wires nothing
        for tool in named_tools(properties):
            wired.setdefault(tool, []).append(card.path)
    executed = {
        str(span.attributes.get(GenAI.TOOL_NAME))
        for trace in traces
        for span in trace.of(Operation.EXECUTE_TOOL)
    }
    return VocabularyTable(
        rows=[
            ToolRow(tool=tool, wired_by=sorted(wired.get(tool, [])), exercised=tool in executed)
            for tool in sorted(vocabulary.tools)
        ],
        traces_seen=len(traces),
        traces_blind=(
            ""
            if traces
            else "no traces supplied, so whether a tool was ever exercised was not "
            "checked; pass --trace"
        ),
    )


def collect(cards: list[Card], *, vocabulary: Vocabulary | None, traces: list[Trace]) -> Coverage:
    """Every table, in one call. The only entry point the CLI uses."""
    documents = policy_coverage(cards)
    documents += unnamed_policies(documents, cards)
    return Coverage(
        policy=sorted(documents, key=lambda one: one.path),
        vocabulary=vocabulary_coverage(cards, vocabulary, traces),
    )
