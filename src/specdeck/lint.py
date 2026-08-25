"""`specdeck lint` — zero tokens, no network, runs in pre-commit and CI.

Three groups, by the data each needs. **Static** rules read the card and the lockfile:
structure, dead fixture and policy paths, lockfile freshness, contradictory wires, and
credit weight validity. **Vocabulary-fed** rules need an introspected tool vocabulary, and
say so when they do not have one. **Definition-fed** rules need the agent definition,
introspected from `--agent-def`, and check two obligations over the whole deck rather than
over any one card: every cycle is bounded, and every binding a card could reference is
referenced by one.

The definition-fed group states the introspection depth it saw in every report, at every
depth including "not introspected at all". Depth varies by framework, so an obligation that
ran against half a graph and one that ran against all of it must not read the same.

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
from .introspect import STRUCTURAL, Depth, Introspection, bounding_tools
from .ir import AtMost, Bound, Measure, Never, NeverRequested, Property
from .judge import criteria_of, rubric_text
from .lockfile import LOCKFILE_NAME, Lockfile, StaleLock, lock_key
from .wires import WireError, compile_wires, named_markers, named_tools, wires_text

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
    """Every finding, plus what the definition-fed rules were able to see.

    The introspection is a field rather than a line of console text so that every
    consumer — the Rich renderer today, a JUnit document later — reads the depth without
    parsing prose. `errors`, `ok` and `counts` are untouched by it: how much was legible
    is never itself a violation.
    """

    findings: list[Finding]
    introspection: Introspection | None = None

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
    paths: list[Path],
    *,
    lock: Lockfile | None = None,
    lock_path: Path | None = None,
    vocabulary: Vocabulary | None = None,
    agent_def: Introspection | None = None,
) -> Result:
    cards = cards_under(paths)
    findings: list[Finding] = []
    for card_path in cards:
        findings += lint_card(card_path, lock=lock, lock_path=lock_path, vocabulary=vocabulary)
    # Both deck-level groups append after every card, and both under one `card=` key each:
    # `_render_lint` groups contiguously without sorting, so a key that reappears later
    # would open a second block for the same subject.
    findings += _cassettes(cards)
    findings += _agent_definition(cards, agent_def)
    return Result(findings=findings, introspection=agent_def)


def lint_card(
    path: Path | str,
    *,
    lock: Lockfile | None = None,
    lock_path: Path | None = None,
    vocabulary: Vocabulary | None = None,
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
    findings += _lockfile(card, path, name, lock, lock_path, properties)
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
        if isinstance(rule, Never | NeverRequested | AtMost) and rule.selector.tool:
            by_tool.setdefault(rule.selector.tool, []).append(prop)
        elif isinstance(rule, Bound):
            by_measure.setdefault(rule.measure.value, []).append(prop)

    for tool, props in by_tool.items():
        nevers = [p for p in props if isinstance(p.rule, Never)]
        budgets = [p for p in props if isinstance(p.rule, AtMost)]
        requested = [p for p in props if isinstance(p.rule, NeverRequested)]
        if requested and any(p.rule.n > 0 for p in budgets):
            findings.append(
                Finding(
                    rule="contradictory-wires",
                    severity=Severity.ERROR,
                    card=name,
                    message=(
                        f"{tool}: `never_requested` and `at_most` with a budget above zero "
                        "cannot both hold"
                    ),
                )
            )
        if requested and nevers:
            findings.append(
                Finding(
                    rule="redundant-wires",
                    severity=Severity.WARNING,
                    card=name,
                    message=(
                        f"{tool}: `never_requested` already forbids executing it, which is "
                        "all `never` says"
                    ),
                )
            )
        if len(nevers) > 1:
            # Two spellings of one wire compile to one id, so the merge and the report show
            # a single property and nothing else would ever say the card states it twice.
            findings.append(
                Finding(
                    rule="redundant-wires",
                    severity=Severity.WARNING,
                    card=name,
                    message=(
                        f"{tool}: `never` and `never_executed` are the same wire, stated "
                        f"{len(nevers)} times"
                    ),
                )
            )
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
        for tool in named_tools(properties)
        if tool not in vocabulary.tools
    ]
    findings += [
        Finding(
            rule="unknown-marker",
            severity=Severity.ERROR,
            card=name,
            message=f"wire triggers on marker {marker!r}, which is not in the vocabulary",
        )
        for marker in named_markers(properties)
        if marker not in vocabulary.markers
    ]
    return findings


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


def _lockfile(
    card: Card,
    path: Path,
    name: str,
    lock: Lockfile | None,
    lock_path: Path | None,
    properties: list[Property],
) -> list[Finding]:
    if lock is None:
        return [
            Finding(
                rule="stale-lock",
                severity=Severity.SKIPPED,
                card=name,
                message="no lockfile supplied, so freshness was not checked",
            )
        ]
    # The runner's own key derivation, not a bare filename. Guessing the lockfile's
    # location when none was given keeps the flat layout working, and a card in a
    # subdirectory then reads the same key the runner wrote (#61).
    key = lock_key(path, lock_path or path.parent / LOCKFILE_NAME)
    try:
        lock.verify(
            key,
            rubric=rubric_text(criteria_of(card)),
            wires=wires_text(properties),
            simulator=card.context.simulator,
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


#: The `card=` key deck-level definition-fed findings carry, on `CASSETTE_DIR`'s
#: precedent. One key, never the `--agent-def` reference: `_render_lint` groups
#: contiguously, so two keys would open two blocks for one subject.
AGENT_DEF = "agent definition"


def _agent_definition(cards: list[Path], introspection: Introspection | None) -> list[Finding]:
    """The two obligations docs/card-format.md states over an introspected definition.

    Deck-wide, not per card. "Referenced by at least one wire or card" is a claim about
    the suite: a tool wired on any card in the deck is wired, and a cycle one card bounds
    is bounded for the agent, because there is one agent behind all of them.

    Both obligations report themselves SKIPPED rather than passing quietly when the depth
    they need was not reached — including when no `--agent-def` was given at all, which is
    the ordinary case and still has to read differently from "checked and clean".
    """
    if introspection is None:
        return [
            Finding(
                rule=rule,
                severity=Severity.SKIPPED,
                card=AGENT_DEF,
                message=(
                    f"{what} without the agent definition; pass "
                    "--agent-def <module:attribute> to introspect it"
                ),
            )
            for rule, what in (
                ("unbounded-cycle", "cycles cannot be found"),
                (
                    "unreferenced-binding",
                    "tool bindings, hand-offs and HITL points cannot be listed",
                ),
            )
        ]
    properties = _deck_properties(cards)
    return _unbounded_cycles(introspection, properties) + _unreferenced_bindings(
        cards, introspection, properties
    )


def _unbounded_cycles(introspection: Introspection, properties: list[Property]) -> list[Finding]:
    """Every cycle has a bounded or escalation wire. **Error.**

    What satisfies it is a wire naming a tool the cycle can call — `never` or `at_most` on
    one of them, or an `after K` escalation whose follow-up tool is one of them — where
    "can call" is `introspect.bounding_tools`: the tools the cycle's own nodes bind, plus a
    node that is itself a tool. A wire naming the *node* does not, because it is not a
    check: wires match `execute_tool` spans by tool name, so `tools: at_most 3` compiles to
    a property no trace can satisfy. A trace-level bound does not count either —
    `latency: under 120s` terminates a run, not a loop, and the format's own example card
    carries one, so counting it would ship this ERROR unreachable on any deck that bounds
    latency. See DECISIONS.md, 2026-08-25.

    The gate is the cycle list rather than the depth: a description that declares its own
    cycles and no edges is below TOPOLOGY and still has the thing this rule checks.
    """
    description = introspection.description
    if not description.cycles:
        if introspection.depth is Depth.TOPOLOGY:
            return []
        return [
            Finding(
                rule="unbounded-cycle",
                severity=Severity.SKIPPED,
                card=AGENT_DEF,
                message=(
                    f"{introspection.source} read this definition at '{introspection.depth.value}' "
                    "depth, which carries no edges, so cycles could not be found"
                ),
            )
        ]
    bounded = set(named_tools(properties))
    findings = []
    for cycle in description.cycles:
        wireable = bounding_tools(description, cycle)
        if not wireable:
            # Every wire subject is a tool name, so a loop through routers and chat steps
            # alone is one no card can bound. Reported as blindness rather than as an
            # ERROR whose instruction nobody could follow.
            findings.append(
                Finding(
                    rule="unbounded-cycle",
                    severity=Severity.SKIPPED,
                    card=AGENT_DEF,
                    message=(
                        f"the cycle through {', '.join(cycle)} passes no node that binds a "
                        "tool, so no wire can name anything inside it and whether it is "
                        "bounded could not be checked"
                    ),
                )
            )
        elif not bounded & wireable:
            findings.append(
                Finding(
                    rule="unbounded-cycle",
                    severity=Severity.ERROR,
                    card=AGENT_DEF,
                    message=(
                        f"the cycle through {', '.join(cycle)} has no wire on any card in "
                        f"this deck. Bound a tool it calls — {', '.join(sorted(wireable))} "
                        "— with `<tool>: never` or `<tool>: at_most <n>`, or escalate out "
                        "of it with `<tool>: after <k> <marker>`. A wire naming the node "
                        "rather than the tool is not one: wires match tool names. Nor is a "
                        "trace-level bound such as `latency: under 120s`, which bounds a "
                        "run and not a loop."
                    ),
                )
            )
    return findings


def _unreferenced_bindings(
    cards: list[Path], introspection: Introspection, properties: list[Property]
) -> list[Finding]:
    """Every tool binding, hand-off edge and HITL point is referenced by a wire or a card.
    **Warning**, because a binding no card exercises is a gap in the suite rather than a
    defect in it.

    "Referenced by a card" is read as: the name appears as a whole word in a card's
    `context` values. Nothing here reads `card.prose` — `card-mechanics` is the one rule
    allowed inside the SME's block, and the recall would be near zero anyway, since an SME
    writes "the agent refuses the change" and never `cancel_reservation`.
    """
    depth = introspection.depth
    if depth is Depth.NONE:
        return [
            Finding(
                rule="unreferenced-binding",
                severity=Severity.SKIPPED,
                card=AGENT_DEF,
                message=(
                    f"{introspection.source} read nothing out of this definition, so there "
                    "are no bindings to check"
                ),
            )
        ]
    wired = set(named_tools(properties))
    context = "\n".join(
        value for card in _deck_cards(cards) for value in dict(card.context).values() if value
    )
    findings = [
        Finding(
            rule="unreferenced-binding",
            severity=Severity.WARNING,
            card=AGENT_DEF,
            message=(
                f"{kind} {name!r} is named by no wire and no card in this deck — no "
                "scenario here says what the agent should do with it"
            ),
        )
        for kind, name in _bindings(introspection)
        if name not in wired and not re.search(rf"\b{re.escape(name)}\b", context)
    ]
    if depth is Depth.TOOLS:
        findings.append(
            Finding(
                rule="unreferenced-binding",
                severity=Severity.SKIPPED,
                card=AGENT_DEF,
                message=(
                    f"{introspection.source} read this definition at 'tools' depth, so tool "
                    "bindings were checked but hand-off edges and HITL points were not visible"
                ),
            )
        )
    return findings


def _bindings(introspection: Introspection) -> list[tuple[str, str]]:
    """Everything a card could reference, each labelled by what it is.

    Deduped across kinds so a node that is both a tool and an edge endpoint is one
    finding: the reader has one thing to do about it, not two.
    """
    description = introspection.description
    found: list[tuple[str, str]] = [("tool binding", name) for name in description.tools]
    found += [("HITL point", name) for name in description.hitl_points]
    found += [
        ("hand-off edge to", target) for _, target in description.edges if target not in STRUCTURAL
    ]
    seen: set[str] = set()
    unique = []
    for kind, name in found:
        if name in seen:
            continue
        seen.add(name)
        unique.append((kind, name))
    return unique


def _deck_cards(paths: list[Path]) -> list[Card]:
    """Every card in the deck that parses. One that does not already has its own finding."""
    parsed = []
    for path in paths:
        try:
            parsed.append(parse(path))
        except CardError:
            continue
    return parsed


def _deck_properties(paths: list[Path]) -> list[Property]:
    """Every wire in the deck, compiled.

    Re-parsed rather than threaded out of `lint_card`. That function's signature is used
    directly by around thirty tests and by the CLI, and widening its return type to carry
    properties out would be a worse trade than reading five small markdown files twice.
    """
    properties: list[Property] = []
    for card in _deck_cards(paths):
        try:
            properties += compile_wires(card)
        except WireError:
            continue  # `wire-syntax` already said so, per card
    return properties


def cards_under(paths: list[Path]) -> list[Path]:
    """Cards under the given paths. **The one card-discovery rule in the project.**

    Public because `specdeck lint`, `specdeck coverage` and every later command over a
    deck have to agree about what a card is. Do not reimplement the walk: the rule below
    is subtle, and two commands disagreeing about whether `policy/airline.md` is a card is
    a discrepancy nobody would look for.

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
