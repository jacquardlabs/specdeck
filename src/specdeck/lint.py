"""`specdeck lint` — zero tokens, no network, runs in pre-commit and CI.

Two groups, by the data each needs. **Static** rules read the card and the lockfile:
structure, dead fixture and policy paths, lockfile freshness, contradictory wires, and
credit weight validity. **Vocabulary-fed** rules need an introspected tool vocabulary, and
say so when they do not have one.

Two rules govern the rest:

*Never style-police the SME zone.* No rule here has an opinion about how the SME writes.
There is exactly one rule that reads inside the prose block, `card-mechanics`, and it is
not a style rule: prose describing the card's own pass/fail machinery reproducibly makes
the judge return commentary instead of verdicts, so the run fails to grade at all. See
DECISIONS.md, 2026-08-24. Everything else about wording is theirs.

*A check that silently degrades is worse than one that reports its own blindness.* A rule
without the data it needs emits a SKIPPED finding naming what was missing, rather than
passing quietly and letting a clean report mean two different things.
"""

from __future__ import annotations

import re
from collections import Counter
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel

from .card import Card, CardError, parse
from .ir import AfterKThen, AtMost, Bound, Measure, Never, Property
from .judge import criteria_of, rubric_text
from .lockfile import Lockfile, StaleLock
from .wires import WireError, compile_wires

CARD_GLOB = "*.md"


class Severity(StrEnum):
    """ERROR fails the run. WARNING is machine-verifiable but not definitively wrong.
    SUGGESTION is about prose content and never blocks. SKIPPED is a rule reporting that
    it lacked the data to run at all."""

    ERROR = "error"
    WARNING = "warning"
    SUGGESTION = "suggestion"
    SKIPPED = "skipped"


class Finding(BaseModel):
    rule: str
    severity: Severity
    card: str
    message: str
    line: int | None = None


class Result(BaseModel):
    findings: list[Finding]

    @property
    def errors(self) -> int:
        return sum(1 for f in self.findings if f.severity is Severity.ERROR)

    @property
    def ok(self) -> bool:
        return self.errors == 0

    def counts(self) -> Counter[str]:
        return Counter(f.severity.value for f in self.findings)


class Vocabulary(BaseModel):
    """What a card is allowed to name. Introspected in the end; declared for now.

    Tools and markers are open sets the project declares. Measures are closed — they are
    the palette's own, so lint checks them against `Measure` and needs nothing declared.
    """

    tools: set[str] = set()
    markers: set[str] = set()


def lint_paths(
    paths: list[Path], *, lock: Lockfile | None = None, vocabulary: Vocabulary | None = None
) -> Result:
    cards = _cards(paths)
    findings: list[Finding] = []
    for card_path in cards:
        findings += lint_card(card_path, lock=lock, vocabulary=vocabulary)
    findings += _cassettes(cards)
    return Result(findings=findings)


def lint_card(
    path: Path | str, *, lock: Lockfile | None = None, vocabulary: Vocabulary | None = None
) -> list[Finding]:
    path = Path(path)
    name = str(path)
    try:
        card = parse(path)
    except CardError as error:
        # One finding, not a traceback: a card that does not parse is a lint result.
        return [Finding(rule="parse", severity=Severity.ERROR, card=name, message=str(error))]

    findings = _structure(card, name)
    findings += _card_mechanics(card, name)
    findings += _dead_paths(card, name)
    properties, wire_findings = _wires(card, name)
    findings += wire_findings
    findings += _vocabulary(properties, name, vocabulary)
    findings += _lockfile(card, name, lock)
    return findings


# -- rules --------------------------------------------------------------------


def _structure(card: Card, name: str) -> list[Finding]:
    """Zone structure. The presence of a zone, never its content."""
    if card.prose:
        return []
    # A wires-only card is not obviously wrong — the dev zone is legitimately theirs — but
    # the prose block is what the judge grades and what the lockfile hashes, so a card
    # without one has no SME zone and no gate criterion. Warning, not error, until the
    # format says which it is.
    return [
        Finding(
            rule="empty-prose",
            severity=Severity.WARNING,
            card=name,
            message=(
                "no prose block: nothing for the judge to grade and nothing to hash into "
                "the lockfile. A prose-only card is legal; a wires-only card may not be."
            ),
        )
    ]


#: Prose that talks about the card's own grading machinery rather than the behaviour being
#: graded. Not a style list: each of these was observed making a judge slide into
#: commentary, returning a reply with no verdict for the criterion it was asked about.
#: Kept narrow on purpose — the risk of a rule that reads the SME's zone is that it grows.
_MECHANICS = (
    (re.compile(r"\bthis card\b", re.I), "refers to the card itself"),
    (re.compile(r"\b(?:do not|don't|never)\s+fail\b", re.I), "instructs the judge how to grade"),
    (re.compile(r"\bfails?\s+only\s+if\b", re.I), "states a pass/fail condition"),
    (re.compile(r"\b(?:should|must)\s+(?:pass|fail)\b", re.I), "states a pass/fail verdict"),
    (re.compile(r"\bmark(?:ed)?\s+(?:as\s+)?(?:pass|fail)\w*\b", re.I), "asks for a verdict"),
    (re.compile(r"\b(?:gate|credit)\s+(?:tier|check|wire)\b", re.I), "names specdeck's own tiers"),
)

#: Three or more consecutive capitalised words. ALL-CAPS emphasis showed up in every
#: reproduction alongside the phrases above, and on its own it is the one signal here that
#: could plausibly be ordinary prose — an airline card may legitimately say IAH or JFK —
#: so the run has to be long enough not to fire on a pair of airport codes.
_SHOUTING = re.compile(r"(?:\b[A-Z]{2,}\b[^\w\n]+){2,}\b[A-Z]{2,}\b")


def _card_mechanics(card: Card, name: str) -> list[Finding]:
    """Prose about the card's machinery, which measurably breaks grading.

    The only rule that reads inside the SME's block, and it earns that by not being about
    style: rewriting such a criterion as plain declarative expected behaviour fixed the
    grading on the first sample, where two live judge calls had failed before it. It warns
    rather than errors — it is a strong signal, not a certainty, and the SME's words are
    still theirs to keep.
    """
    hits = [why for pattern, why in _MECHANICS if pattern.search(card.prose)]
    if _SHOUTING.search(card.prose):
        hits.append("shouts in capitals")
    if not hits:
        return []
    return [
        Finding(
            rule="card-mechanics",
            severity=Severity.WARNING,
            card=name,
            message=(
                f"prose {', and '.join(hits)}. Language about how the card is scored makes "
                "the judge answer with commentary instead of a verdict, and an ungraded "
                "criterion fails closed. Describe the behaviour expected of the agent."
            ),
        )
    ]


def _dead_paths(card: Card, name: str) -> list[Finding]:
    findings = []
    for key, value, resolved in (
        ("policy", card.context.policy, card.policy_path),
        ("fixture", card.context.fixture, card.fixture_path),
    ):
        if resolved is not None and not resolved.exists():
            findings.append(
                Finding(
                    rule="dead-path",
                    severity=Severity.ERROR,
                    card=name,
                    message=f"{key} {value!r} does not exist (looked in {resolved.parent})",
                )
            )
    return findings


def _wires(card: Card, name: str) -> tuple[list[Property], list[Finding]]:
    try:
        properties = compile_wires(card)
    except WireError as error:
        return [], [
            Finding(rule="wire-syntax", severity=Severity.ERROR, card=name, message=str(error))
        ]
    return properties, _consistency(properties, name)


def _consistency(properties: list[Property], name: str) -> list[Finding]:
    """Wires that cannot both be meant, and wires that say the same thing twice."""
    findings: list[Finding] = []
    by_tool: dict[str, list[Property]] = {}
    by_measure: dict[str, list[Property]] = {}
    for prop in properties:
        rule = prop.rule
        if isinstance(rule, Never | AtMost) and rule.selector.tool:
            by_tool.setdefault(rule.selector.tool, []).append(prop)
        elif isinstance(rule, Bound):
            by_measure.setdefault(rule.measure.value, []).append(prop)

    for tool, props in by_tool.items():
        nevers = [p for p in props if isinstance(p.rule, Never)]
        budgets = [p for p in props if isinstance(p.rule, AtMost)]
        if nevers and any(p.rule.n > 0 for p in budgets):
            findings.append(
                Finding(
                    rule="contradictory-wires",
                    severity=Severity.ERROR,
                    card=name,
                    message=(
                        f"{tool}: `never` and `at_most` with a budget above zero cannot both hold"
                    ),
                )
            )
        elif nevers and budgets:
            findings.append(
                Finding(
                    rule="redundant-wires",
                    severity=Severity.WARNING,
                    card=name,
                    message=f"{tool}: `at_most 0` says what `never` already says",
                )
            )
        if len({p.rule.n for p in budgets}) < len(budgets):
            findings.append(
                Finding(
                    rule="redundant-wires",
                    severity=Severity.WARNING,
                    card=name,
                    message=f"{tool}: the same call budget is stated more than once",
                )
            )

    for measure, props in by_measure.items():
        if len(props) > 1:
            limits = ", ".join(f"{p.rule.limit:g}" for p in props)
            findings.append(
                Finding(
                    rule="contradictory-wires",
                    severity=Severity.ERROR,
                    card=name,
                    message=f"{measure}: bounded more than once ({limits}); only the "
                    "tightest can be meant",
                )
            )
    return findings


def _vocabulary(
    properties: list[Property], name: str, vocabulary: Vocabulary | None
) -> list[Finding]:
    """Wires may only name things that exist: tools, markers, and measures."""
    findings = _measures(properties, name)
    if vocabulary is None:
        return findings + [
            Finding(
                rule=rule,
                severity=Severity.SKIPPED,
                card=name,
                message=(
                    f"no {noun} vocabulary supplied, so wires naming a {noun} that does "
                    "not exist cannot be caught here; pass --vocabulary"
                ),
            )
            for rule, noun in (("unknown-tool", "tool"), ("unknown-marker", "marker"))
        ]
    findings += [
        Finding(
            rule="unknown-tool",
            severity=Severity.ERROR,
            card=name,
            message=f"wire names tool {tool!r}, which is not in the vocabulary",
        )
        for tool in _named_tools(properties)
        if tool not in vocabulary.tools
    ]
    findings += [
        Finding(
            rule="unknown-marker",
            severity=Severity.ERROR,
            card=name,
            message=f"wire triggers on marker {marker!r}, which is not in the vocabulary",
        )
        for marker in _named_markers(properties)
        if marker not in vocabulary.markers
    ]
    return findings


def _named_tools(properties: list[Property]) -> list[str]:
    tools: set[str] = set()
    for prop in properties:
        rule = prop.rule
        if isinstance(rule, Never | AtMost) and rule.selector.tool:
            tools.add(rule.selector.tool)
        elif isinstance(rule, AfterKThen) and rule.then.tool:
            tools.add(rule.then.tool)
    return sorted(tools)


def _named_markers(properties: list[Property]) -> list[str]:
    return sorted(
        {
            p.rule.trigger.marker
            for p in properties
            if isinstance(p.rule, AfterKThen) and p.rule.trigger.marker
        }
    )


def _measures(properties: list[Property], name: str) -> list[Finding]:
    """Measures are the palette's own closed set, so nothing needs declaring."""
    known = {m.value for m in Measure}
    return [
        Finding(
            rule="unknown-measure",
            severity=Severity.ERROR,
            card=name,
            message=f"wire bounds {p.rule.measure!r}, which is not a known measure "
            f"({', '.join(sorted(known))})",
        )
        for p in properties
        if isinstance(p.rule, Bound) and p.rule.measure.value not in known
    ]


def _lockfile(card: Card, name: str, lock: Lockfile | None) -> list[Finding]:
    if lock is None:
        return [
            Finding(
                rule="stale-lock",
                severity=Severity.SKIPPED,
                card=name,
                message="no lockfile supplied, so freshness was not checked",
            )
        ]
    try:
        lock.verify(
            Path(name).name, rubric=rubric_text(criteria_of(card)), simulator=card.context.simulator
        )
    except StaleLock as error:
        return [Finding(rule="stale-lock", severity=Severity.ERROR, card=name, message=str(error))]
    return []


CASSETTE_DIR = "cassettes"
_KINDS = ("judge-", "simulator-")


def _cassettes(cards: list[Path]) -> list[Finding]:
    """Recordings in `cassettes/` that no card owns.

    Every prose edit re-keys the prompt and strands the recording it was keyed on, silently
    — three failed pinning iterations on one card leave three dead files, and at thirty
    cards under iteration the directory is a landfill nothing collects (#69).

    Nothing is deleted here. CLAUDE.md makes cassettes the substrate for the Phase-3
    mutation runner, so an orphan is a fixture with a second job rather than garbage, and
    a linter that removes one is a linter that removes evidence.
    """
    slugs = {path.stem for path in cards}
    findings: list[Finding] = []
    seen_directory = False
    for directory in sorted({path.parent / CASSETTE_DIR for path in cards}):
        if not directory.is_dir():
            continue
        seen_directory = True
        for recording in sorted(directory.glob("*.json")):
            slug, _, rest = recording.name.partition(".")
            name = str(recording)
            if not rest.startswith(_KINDS):
                findings.append(
                    Finding(
                        rule="orphan-cassette",
                        severity=Severity.WARNING,
                        card=name,
                        message=(
                            "cassette names no card. Recordings are "
                            "`<card>.judge-<hash>.json`; a bare hash cannot be traced back "
                            "to what replays it. Re-record, or rename it after its card."
                        ),
                    )
                )
            elif slug not in slugs:
                findings.append(
                    Finding(
                        rule="orphan-cassette",
                        severity=Severity.WARNING,
                        card=name,
                        message=(
                            f"cassette is owned by {slug!r}, which is not a card here. "
                            "Either the card was renamed or removed, or this recording "
                            "outlived the prompt it was keyed on."
                        ),
                    )
                )
    if seen_directory:
        findings.append(
            Finding(
                rule="orphan-cassette",
                severity=Severity.SKIPPED,
                card=CASSETTE_DIR,
                message=(
                    "a cassette whose card still exists but whose prompt has moved cannot "
                    "be detected without the trace that produced it. Traces are declared "
                    "per card in #70; until then this rule sees ownership, not staleness."
                ),
            )
        )
    return findings


def _cards(paths: list[Path]) -> list[Path]:
    """Cards under the given paths.

    A named path is always linted — if you point at a file, you meant it.

    Walking a directory is where this gets interesting: policy documents are markdown too
    and live beside the cards that name them, so a naive walk lints `policy/airline.md` as
    a card and reports a parse error about a file nobody claimed was one. Sniffing the
    heading would fix that and introduce a worse bug — a real card whose heading is broken
    would be silently skipped, which is exactly the case lint exists to catch.

    So: everything is a card except what a card points at. One pass to collect the
    policies and fixtures the parseable cards reference, then lint the rest.
    """
    named = [p for p in paths if not p.is_dir()]
    walked = sorted({p for path in paths if path.is_dir() for p in path.rglob(CARD_GLOB)})
    return named + [p for p in walked if p.resolve() not in _referenced(walked)]


def _referenced(candidates: list[Path]) -> set[Path]:
    """Policy and fixture paths named by cards that parse."""
    referenced: set[Path] = set()
    for path in candidates:
        try:
            card = parse(path)
            referenced.update(p for p in (card.policy_path, card.fixture_path) if p)
        except CardError:
            continue  # it does not parse, so it names nothing — and lint will say so
    return referenced
