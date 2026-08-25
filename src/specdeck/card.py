"""The card parser: four blocks out of a markdown file.

Spec: docs/card-format.md. A heading, a `context` mapping, a free-text prose block, and
two lists — `wire` and `credit`. No Gherkin, no step definitions: a second language to
learn would land the SME back in code.

The parser reads structure and nothing else. Wire rule text is handed to the wires engine
as written, and whether a card's *content* is sensible — a card with no prose, a wire that
names an unknown tool — belongs to lint, which owns severity and never style-polices the
SME zone.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel

BLOCKS = ("context", "wire", "credit")
CONTEXT_KEYS = ("fixture", "policy", "simulator", "traces")


class CardError(Exception):
    """The card is not shaped like a card. Always names the file and the line."""


class Weighted(BaseModel):
    """A credit entry: SME-owned text, SME-owned weight."""

    text: str
    weight: int


class CardContext(BaseModel):
    """What the run is set up with. Every field is optional — a prose-only card runs."""

    fixture: str = ""
    policy: str = ""
    simulator: str = ""
    #: One glob, not a list: the context block is a flat `key: value` mapping, and a list
    #: buys parser surgery for a case a glob covers.
    traces: str = ""


class Card(BaseModel):
    """One scenario, parsed. The zones stay separate all the way through: `prose` is the
    SME's and is graded by the judge, `wires` is the developer's and is compiled to the
    property IR. Paths in `context` are resolved against the card, never the cwd."""

    path: str
    title: str
    context: CardContext
    prose: str
    wires: list[str]
    credit_wires: list[Weighted]
    credit_criteria: list[Weighted]

    @property
    def slug(self) -> str:
        """The card's stem, which names its recordings. `<card>` for parsed text with no
        file behind it, so an in-memory card still keys somewhere rather than crashing."""
        return Path(self.path).stem or "card"

    @property
    def policy_path(self) -> Path | None:
        """The policy document, resolved against the card that names it."""
        return self._contained(self.context.policy, "policy")

    @property
    def fixture_path(self) -> Path | None:
        """The fixture data, resolved against the card that names it."""
        return self._contained(self.context.fixture, "fixture")

    @property
    def trace_paths(self) -> list[Path]:
        """Every recording the card's `traces:` glob matches, sorted, resolved against it.

        Total: a glob that matches nothing returns `[]` rather than raising, because the
        two callers want different answers to that one fact — lint reports it as a
        `dead-path` finding and the runner refuses to start — and neither is the parser's
        to pick.

        Containment is checked per match rather than on the pattern, because
        `Path('cards').glob('../*.md')` really does escape.
        """
        if not self.context.traces:
            return []
        root = Path(self.path).parent.resolve()
        try:
            matches = sorted(root.glob(self.context.traces))
        except (NotImplementedError, ValueError) as error:
            # `Path.glob` rejects an absolute pattern, and which of the two it raises has
            # moved across the versions the project supports. Neither is a `USER_ERROR`,
            # so uncaught, a pattern the user typed would exit 3, "specdeck itself broke".
            raise CardError(f"{self.path}: traces {self.context.traces!r}: {error}") from None
        return [self._contain(match.resolve(), self.context.traces, "traces") for match in matches]

    def _contained(self, value: str, key: str) -> Path | None:
        """Resolve a card-declared path, refusing anything outside the card's directory.

        Cards are contributed and reviewed in PRs, so the value is untrusted input. The
        policy file's bytes reach the judge prompt and, under `--live`, a third-party API:
        an unconstrained `../../../.ssh/id_rsa` would be read and sent.
        """
        if not value:
            return None
        root = Path(self.path).parent.resolve()
        return self._contain((root / value).resolve(), value, key)

    def _contain(self, resolved: Path, value: str, key: str) -> Path:
        """The containment rule itself, so the single-path and glob forms share one."""
        root = Path(self.path).parent.resolve()
        if resolved != root and root not in resolved.parents:
            raise CardError(f"{self.path}: {key} {value!r} resolves outside the card's directory")
        return resolved


def parse(path: Path | str) -> Card:
    """Read a card from disk. A path that does not resolve is a `CardError`, not an
    `OSError`: the CLI routes its own error types to exit 2, and a mistyped path is a user
    error rather than a crash."""
    try:
        text = Path(path).read_text()
    except OSError as error:
        raise CardError(f"cannot read the card at {path}: {error.strerror or error}") from None
    return parse_text(text, path=str(path))


def parse_text(text: str, path: str = "<card>") -> Card:
    """Parse a card's four blocks: heading, `context`, prose, `wire`, and `credit`.

    The prose block is everything outside the keyed blocks, and is the SME's zone verbatim
    — it becomes the judge prompt and is hashed into the lockfile, so nothing here
    normalises it. `credit` splits into `credit_wires` and `credit_criteria` because the
    two are evaluated by different machinery: a quoted string is graded by the judge, a
    `wire:` entry is compiled to the property IR and checked against the trace.
    """
    lines = text.splitlines()
    if not lines or not lines[0].startswith("# "):
        raise CardError(f"{path}: a card starts with a `# ` heading")

    prose: list[str] = []
    blocks: dict[str, list[tuple[str, int]]] = {}
    current: str | None = None

    for number, line in enumerate(lines[1:], start=2):
        keyword = line.strip().rstrip(":")
        if line == line.lstrip() and keyword in BLOCKS and line.strip().endswith(":"):
            if keyword in blocks:
                raise CardError(f"{path}:{number}: `{keyword}:` appears twice")
            blocks[keyword], current = [], keyword
            continue
        if line.strip() and line == line.lstrip():
            if current is not None and line.startswith("- "):
                # Silently reclassifying this as prose unenforces every wire on the card
                # and reports the run as passing, so it is an error rather than a guess.
                raise CardError(
                    f"{path}:{number}: `{current}:` entries are indented under it; "
                    f"this one starts at column zero, which ends the block — {line.strip()!r}"
                )
            current = None  # a keyed block ends at the first unindented line
        if current is None:
            prose.append(line)
        elif line.strip():
            blocks[current].append((line.strip(), number))

    context = _context(blocks.get("context", []), path)
    credit_criteria, credit_wires = _credit(blocks.get("credit", []), path)
    return Card(
        path=path,
        title=lines[0].removeprefix("# ").strip(),
        context=context,
        prose="\n".join(prose).strip(),
        wires=[_item(entry, number, path) for entry, number in blocks.get("wire", [])],
        credit_wires=credit_wires,
        credit_criteria=credit_criteria,
    )


def _item(entry: str, number: int, path: str) -> str:
    if not entry.startswith("- "):
        raise CardError(f"{path}:{number}: list entries start with `- `, got {entry!r}")
    return entry.removeprefix("- ").strip()


def _context(entries: list[tuple[str, int]], path: str) -> CardContext:
    values: dict[str, str] = {}
    for entry, number in entries:
        key, separator, value = entry.partition(":")
        if not separator:
            raise CardError(f"{path}:{number}: context entries are `key: value`, got {entry!r}")
        key = key.strip()
        if key not in CONTEXT_KEYS:
            raise CardError(
                f"{path}:{number}: unknown context key {key!r}; "
                f"expected one of {', '.join(CONTEXT_KEYS)}"
            )
        values[key] = value.strip().strip('"')
    return CardContext(**values)


def _credit(entries: list[tuple[str, int]], path: str) -> tuple[list[Weighted], list[Weighted]]:
    criteria: list[Weighted] = []
    wires: list[Weighted] = []
    for entry, number in entries:
        body = _item(entry, number, path)
        text, separator, weight = body.rpartition(":")
        if not separator:
            raise CardError(f"{path}:{number}: a credit entry ends in `: <weight>`, got {body!r}")
        try:
            parsed = int(weight)
        except ValueError:
            raise CardError(
                f"{path}:{number}: credit weight must be a whole number, got {weight.strip()!r}"
            ) from None
        if parsed <= 0:
            raise CardError(f"{path}:{number}: credit weight must be positive, got {parsed}")
        text = text.strip()
        if text.startswith("wire:"):
            wires.append(Weighted(text=text.removeprefix("wire:").strip(), weight=parsed))
        else:
            criteria.append(Weighted(text=text.strip('"'), weight=parsed))
    return criteria, wires
