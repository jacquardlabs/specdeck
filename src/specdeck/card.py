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
CONTEXT_KEYS = ("fixture", "policy", "simulator")


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


class Card(BaseModel):
    path: str
    title: str
    context: CardContext
    prose: str
    wires: list[str]
    credit_wires: list[Weighted]
    credit_criteria: list[Weighted]

    @property
    def policy_path(self) -> Path | None:
        """The policy document, resolved against the card that names it."""
        if not self.context.policy:
            return None
        return (Path(self.path).parent / self.context.policy).resolve()

    @property
    def fixture_path(self) -> Path | None:
        if not self.context.fixture:
            return None
        return (Path(self.path).parent / self.context.fixture).resolve()


def parse(path: Path | str) -> Card:
    return parse_text(Path(path).read_text(), path=str(path))


def parse_text(text: str, path: str = "<card>") -> Card:
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
