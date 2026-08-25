"""Which cards a diff touches.

**Selection is file-level and nothing more.** A card is selected when the diff touches the
card file itself, the policy it names, the fixture it names, or any recording its `traces:`
glob resolves to; a diff touching the lockfile or the vocabulary selects every card,
because those two pin what "correct" means for the whole deck rather than for one card.
There is no clause layer here — see the module's follow-up issue for the two independent
blockers, one of which is that intersecting the *old* side of a hunk needs the base blob
that a unified diff does not carry. Hunk ranges are therefore not parsed at all: a range
nothing reads is a range that drifts.

Two failures that look alike and must never be conflated:

* a **malformed** diff — non-empty input that yields no change — raises `DiffError`. A
  parser that read `git diff --stat` output as "nothing changed" would select nothing, run
  nothing, and report green forever, and the feature would be silently inert while looking
  like it works.
* an **unmatched** diff — valid, non-empty, touching nothing any card reads — selects no
  cards. That is an answer, not a failure, and the caller says so out loud.

Because an unmatched diff runs nothing, every path comparison here is safety-critical: a
path that fails to match is indistinguishable from a file no card reads. So both sides are
resolved once, at the boundary — `parse_diff` resolves against the root it is given and
the caller hands in already-resolved card inputs — and never at the comparison site.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

#: What a selected card was selected by. Read by the report, so the reader is told which
#: file put the card in the run rather than being handed a bare list.
Kind = Literal["card", "policy", "fixture", "trace"]

Status = Literal["added", "modified", "deleted", "renamed"]

_DEV_NULL = "/dev/null"


class DiffError(Exception):
    """The input is not a unified diff this can read. A user error, never a silent zero."""


class Change(BaseModel):
    """One file's worth of a unified diff.

    `path` is where the file is now — for a deletion, where it was. `previous` is set only
    when a rename moved it, and both sides are matched: a fixture renamed out from under a
    card selects that card, because the card is now broken and a run is how that is found.
    """

    status: Status
    path: Path
    previous: Path | None = None
    #: The paths as the diff wrote them, repo-relative. Echoed back in the report, so a
    #: reader sees the line their own `git diff` produced rather than an absolute path.
    name: str
    previous_name: str = ""

    @property
    def label(self) -> str:
        return f"{self.previous_name} -> {self.name}" if self.previous_name else self.name

    @property
    def paths(self) -> tuple[Path, ...]:
        return (self.path,) if self.previous is None else (self.path, self.previous)


class Inputs(BaseModel):
    """Every file one card reads, resolved.

    Built at the boundary rather than here: reading a card can fail, and a card that cannot
    be read cannot be excluded — `unreadable` carries why and selects it unconditionally.
    Its run then reports the same error the deck would have reported anyway, where a
    silent exclusion would let a broken card sit unread for as long as nobody touched it.
    """

    #: The card as discovery found it, unresolved — this is what `select` hands back, so
    #: the runner and its report keep the path the user typed.
    card: Path
    policy: Path | None = None
    fixture: Path | None = None
    traces: list[Path] = Field(default_factory=list)
    unreadable: str = ""


class Selection(BaseModel):
    """The cards a diff selected, and the evidence for each one."""

    cards: list[Path]
    #: Card path -> the evidence lines that selected it. Never empty for a selected card:
    #: a selection with no stated reason is one nobody can check.
    reasons: dict[str, list[str]] = Field(default_factory=dict)
    total: int
    #: Non-empty when a deck-wide input was touched, naming it. Distinct from "every card
    #: happened to match", which is a different fact about the same count.
    everything: str = ""


def parse_diff(text: str, *, root: Path) -> list[Change]:
    """A unified diff from `git diff`, one `Change` per file.

    Anchored on `diff --git`, and every stanza that begins must yield a path or the whole
    input is refused. Anchoring there is what makes the parse unambiguous: a deleted line
    whose content is `-- x` renders as `--- x`, so `---`/`+++` are read only *before* a
    stanza's first `@@`, and everything from there to the next stanza is hunk body.

    Whitespace-only input is a legitimately empty diff and returns `[]`. Non-empty input
    that yields nothing raises, because that is a broken invocation and not an answer.
    """
    changes: list[Change] = []
    stanza: _Stanza | None = None
    body = False
    for line in text.splitlines():
        if line.startswith("diff --git "):
            if stanza is not None:
                changes.append(stanza.change(root))
            stanza = _Stanza(header=line[len("diff --git ") :])
            body = False
        elif stanza is None or body:
            continue
        elif line.startswith("@@"):
            body = True
        else:
            stanza.read(line)
    if stanza is not None:
        changes.append(stanza.change(root))
    if not changes and text.strip():
        raise DiffError(
            "no `diff --git` stanza — --affected-by takes a unified diff from `git diff`, "
            "not a summary (`--stat`) or a list of names (`--name-only`)"
        )
    return changes


def select(
    inputs: Sequence[Inputs],
    changes: Sequence[Change],
    *,
    lock_path: Path,
    vocabulary_path: Path | None = None,
) -> Selection:
    """The cards `changes` touch, in the order they were discovered.

    The only place the card-to-file edges live. Everything a card reads is declared in its
    `context` block, so this walks that block and nothing else — an edit to the agent's own
    source is not a card input and selects nothing, which is the answer and not an
    oversight.
    """
    touched = {path: change for change in changes for path in change.paths}
    everything = _everything(touched, lock_path=lock_path, vocabulary_path=vocabulary_path)
    cards: list[Path] = []
    reasons: dict[str, list[str]] = {}
    for one in inputs:
        why = [everything] if everything else _why(one, touched)
        if not why:
            continue
        cards.append(one.card)
        reasons[str(one.card)] = why
    return Selection(cards=cards, reasons=reasons, total=len(inputs), everything=everything)


def _everything(
    touched: dict[Path, Change], *, lock_path: Path, vocabulary_path: Path | None
) -> str:
    """The deck-wide inputs, which select every card or none.

    The lockfile pins the judge model and hashes every card's rubric, and the vocabulary is
    the denominator every card's tools are checked against: an edit to either changes what
    a passing run means for cards the diff never mentions. `--vocabulary` is a *selection*
    input here even though a deck of recorded traces never reads it to run — it is what
    lint and coverage measure every card against, and narrowing past it would be the
    selector claiming an authority it does not have.
    """
    edges = [("it pins the judge and hashes every card's rubric", lock_path)]
    if vocabulary_path is not None:
        edges.append(("every card's tools are checked against it", vocabulary_path))
    for why, path in edges:
        change = touched.get(path.resolve())
        if change is not None:
            return f"{change.label} changed: {why}"
    return ""


def _why(one: Inputs, touched: dict[Path, Change]) -> list[str]:
    """The evidence lines for one card, or an empty list when nothing it reads changed."""
    if one.unreadable:
        # Selected without looking at its inputs, because they could not be read. The run
        # reports the same error, where a silent exclusion would hide it.
        return [f"the card could not be read: {one.unreadable}"]
    edges: list[tuple[Kind, Path]] = [("card", one.card.resolve())]
    edges += [("policy", one.policy)] if one.policy else []
    edges += [("fixture", one.fixture)] if one.fixture else []
    edges += [("trace", trace) for trace in one.traces]
    lines: list[str] = []
    for kind, path in edges:
        change = touched.get(path)
        if change is not None:
            lines.append(f"{kind} {change.label} {change.status}")
    return lines


class _Stanza:
    """One `diff --git` stanza, accumulated. Mutable on purpose: the parser is a fold over
    lines and the header, the rename lines and the `---`/`+++` pair each carry a piece.

    The path of a stanza that is not a rename comes off the header and from nowhere else —
    see `_from_header`. `---`/`+++` are read only for the `/dev/null` on one side of them,
    which is what says added or deleted for a patch that carries no `new file mode` line.
    """

    def __init__(self, header: str) -> None:
        self.header = header
        self.old = ""
        self.new = ""
        self.renamed = False
        self.status: Status = "modified"

    def read(self, line: str) -> None:
        if line.startswith("rename from "):
            self.old = _quotable(line[len("rename from ") :])
            self.renamed, self.status = True, "renamed"
        elif line.startswith("rename to "):
            self.new = _quotable(line[len("rename to ") :])
            self.renamed, self.status = True, "renamed"
        elif self.renamed:
            return
        elif line.startswith("new file mode") or _gone(line, "--- "):
            self.status = "added"
        elif line.startswith("deleted file mode") or _gone(line, "+++ "):
            self.status = "deleted"

    def change(self, root: Path) -> Change:
        name = self.new or self._from_header()
        previous = self.old if self.renamed and self.old and self.old != name else ""
        return Change(
            status=self.status,
            path=_join(root, name),
            previous=_join(root, previous) if previous else None,
            name=name,
            previous_name=previous,
        )

    def _from_header(self) -> str:
        """The path off the `diff --git` line, for every stanza that is not a rename.

        The header is the only line whose prefixes are self-describing, which is why the
        path is read here rather than off `---`/`+++`: git writes the *same* path on both
        sides of it, so `X/P Y/P` yields P whatever X and Y are — `a/`/`b/` by default,
        `c/`/`i/`/`w/` under `diff.mnemonicPrefix`, whatever `--src-prefix` was given — and
        `--no-prefix` yields P by writing it twice unchanged. Stripping an assumed `a/`
        instead would read a foreign prefix as part of the path, and a path that matches no
        card is, under this feature, an empty selection and a green run.

        A prefix of more than one component cannot be told from the path it prefixes, so it
        is refused by name rather than guessed at — the rule `_quotable` follows. A stanza
        that yields no path at all refuses the whole diff too: "contributed nothing" is
        indistinguishable from "no card reads it", and that also reads as green.
        """
        rest = _quotable(self.header)
        for index, char in enumerate(rest):
            if char != " ":
                continue
            old, new = rest[:index], rest[index + 1 :]
            if old == new:
                return old
            _, old_sep, old_tail = old.partition("/")
            _, new_sep, new_tail = new.partition("/")
            if old_sep and new_sep and old_tail and old_tail == new_tail:
                return old_tail
        # A copy, or a rename whose `rename to` line the patch dropped: two different paths,
        # readable only under the default prefixes.
        head, sep, tail = rest.rpartition(" b/")
        if sep and head.startswith("a/"):
            return tail
        raise DiffError(
            f"could not read a path out of `diff --git {self.header}` — a `--src-prefix` "
            "of more than one path component cannot be told from the path it prefixes"
        )


def _gone(line: str, marker: str) -> bool:
    """Whether `line` is the `--- `/`+++ ` side that git wrote `/dev/null` on. The tab is
    what git appends to these two lines when the path contains a space."""
    return line.startswith(marker) and line[len(marker) :].split("\t")[0] == _DEV_NULL


def _quotable(value: str) -> str:
    """Refuse a C-quoted path rather than guessing at its escapes.

    `core.quotePath` renders a non-ASCII path as `"caf\\303\\251.md"`, and a decoder that
    got one byte wrong would produce a path that matches no card — which under this feature
    is an empty selection and a green run. Named and refused instead.
    """
    if value.startswith('"'):
        raise DiffError(
            f"{value} is a quoted path — re-run the diff with `-c core.quotePath=false`, "
            "because a path this cannot read is a card it would silently not select"
        )
    return value


def _join(root: Path, name: str) -> Path:
    """A repo-relative diff path against the root it is relative to, resolved once here.

    Resolved at the boundary and never at the comparison site: card inputs arrive resolved
    (`Card._contained`), and one unresolved side — a symlinked checkout, a worktree — would
    match nothing and be reported as a diff that touched no card.
    """
    return (root / name).resolve()
