"""The provider x prompt matrix, declared in its own TOML file.

Not in the card. `CONTEXT_KEYS` is a closed set, `variants:` is reserved for Phase 4, and
a card naming a developer's model roster breaks the two-owned-zones rule the card format
is built on: the SME owns the prose and the assertions, the developer owns the runner's
invocation. A roster of providers is the second zone, so it lives in a file the developer
passes on the command line.

Two axes, each a list of named entries, and the columns are their cartesian product. An
entry carries a `config` table specdeck never reads and hands straight to `adapter.run` —
that seam already exists (`run_agent(config=...)`), and everything a column varies passes
through it. The one key specdeck does read is `model`, and only to price the column before
it starts: under a cap, a column whose model has no rate cannot be governed.

Pure. One file read, no asyncio, no rich, no cost — `matrix_run.py` is the shell.
"""

from __future__ import annotations

import tomllib
from itertools import product
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from .baseline import DEFAULT_CELL

#: The axes, in the order a column name reads them: `<provider>/<prompt>`.
PROVIDERS = "provider"
PROMPTS = "prompt"


class MatrixError(Exception):
    """The matrix file cannot be read as specified."""


class Axis(BaseModel):
    """One entry on one axis — a provider, or a prompt variant."""

    model_config = ConfigDict(frozen=True)

    name: str
    #: The model the column is expected to call, needed only to price it. A prompt variant
    #: may leave it unset and inherit the provider's; a provider may not, because then no
    #: entry on either axis names one and the column has no rate to be held to.
    model: str | None = None
    config: dict = Field(default_factory=dict)


class Column(BaseModel):
    """One cell of the matrix: one card, one provider, one prompt variant."""

    model_config = ConfigDict(frozen=True)

    name: str
    provider: str
    prompt: str
    model: str
    config: dict


class Matrix(BaseModel):
    """What a matrix file declares."""

    model_config = ConfigDict(frozen=True)

    providers: tuple[Axis, ...] = ()
    prompts: tuple[Axis, ...] = ()
    #: The hard cap, in USD, or None for a matrix that only reports what it spent.
    budget_usd: float | None = None


def load_matrix(path: Path | str) -> Matrix:
    """Read a matrix file. Every failure is a `MatrixError` naming the file and the key.

    `tomllib` raises `TOMLDecodeError`, and pydantic raises `ValidationError`; neither is
    in `USER_ERRORS`, so a typo in a file the user wrote would surface as exit 3,
    "specdeck itself broke". Translated here, at the boundary, rather than at the CLI.
    """
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        raise MatrixError(f"cannot read the matrix at {path}: {error}") from None
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as error:
        raise MatrixError(f"{path}: {error}") from None

    providers = _axis(data, PROVIDERS, path=path, needs_model=True)
    prompts = _axis(data, PROMPTS, path=path, needs_model=False)
    if not providers and not prompts:
        raise MatrixError(
            f"{path}: no [[{PROVIDERS}]] and no [[{PROMPTS}]] — a matrix with neither axis "
            "has no columns to run"
        )
    if not providers and any(entry.model is None for entry in prompts):
        raise MatrixError(
            f"{path}: with no [[{PROVIDERS}]] every [[{PROMPTS}]] needs its own `model`, "
            "because nothing else names what the column calls"
        )
    return Matrix(providers=providers, prompts=prompts, budget_usd=_budget(data, path=path))


def columns(matrix: Matrix) -> list[Column]:
    """The cartesian product, in declared order, config merged prompt over provider.

    An empty axis degenerates to the other one alone rather than to zero columns: a file
    declaring three providers and no prompt variant asks for three columns, not for
    nothing. The absent axis contributes an empty name, so the column is named for the
    axis that exists.
    """
    left = matrix.providers or (Axis(name=""),)
    right = matrix.prompts or (Axis(name=""),)
    return [
        Column(
            name="/".join(part for part in (provider.name, prompt.name) if part),
            provider=provider.name,
            prompt=prompt.name,
            # The prompt variant is the more specific axis, so its model wins. Neither
            # source dict is touched: `|` builds a new one, and an adapter that mutates
            # what it was handed must not reach the next column's config through it.
            model=str(prompt.model or provider.model),
            config=dict(provider.config) | dict(prompt.config),
        )
        for provider, prompt in product(left, right)
    ]


def _axis(data: dict, key: str, *, path: Path, needs_model: bool) -> tuple[Axis, ...]:
    entries = data.get(key, [])
    if not isinstance(entries, list):
        raise MatrixError(f"{path}: [[{key}]] is {type(entries).__name__}, not a list of entries")
    axis = tuple(
        _entry(entry, key=key, path=path, index=index, needs_model=needs_model)
        for index, entry in enumerate(entries, start=1)
    )
    names = [entry.name for entry in axis]
    if duplicates := sorted({name for name in names if names.count(name) > 1}):
        # Column names key the report grid and the baseline's cell slot, so two entries
        # sharing one would overwrite each other in both.
        raise MatrixError(f"{path}: two [[{key}]] entries named {', '.join(duplicates)}")
    return axis


def _entry(entry: object, *, key: str, path: Path, index: int, needs_model: bool) -> Axis:
    where = f"{path}: [[{key}]] #{index}"
    if not isinstance(entry, dict):
        raise MatrixError(f"{where} is {type(entry).__name__}, not a table")
    name = entry.get("name")
    if not isinstance(name, str) or not name.strip():
        raise MatrixError(f"{where} has no `name`")
    if "/" in name:
        # The column name joins the two axes with a slash, so a name carrying one makes
        # `provider/prompt` unreadable in the grid and ambiguous as a baseline key.
        raise MatrixError(f"{where}: `name` {name!r} cannot contain '/'")
    model = entry.get("model")
    if model is not None and not isinstance(model, str):
        raise MatrixError(f"{where}: `model` is {type(model).__name__}, not a string")
    if needs_model and not model:
        raise MatrixError(
            f"{where} ({name}) has no `model` — it is what prices the column before it "
            "starts, and a column under a cap that cannot be priced cannot be governed"
        )
    config = entry.get("config", {})
    if not isinstance(config, dict):
        raise MatrixError(f"{where}: `config` is {type(config).__name__}, not a table")
    # `config` is handed to the adapter untouched and nothing here looks inside it. A key
    # naming a file specdeck cannot see is the adapter's business, not this parser's.
    return Axis(name=name, model=model, config=config)


def _budget(data: dict, *, path: Path) -> float | None:
    section = data.get("budget", {})
    if not isinstance(section, dict):
        raise MatrixError(f"{path}: [budget] is {type(section).__name__}, not a table")
    usd = section.get("usd")
    if usd is None:
        return None
    if not isinstance(usd, int | float) or isinstance(usd, bool) or usd <= 0:
        raise MatrixError(f"{path}: [budget] usd must be a positive number, got {usd!r}")
    return float(usd)


def cell_key(column: Column | None) -> str:
    """The baseline's cell slot for a column. One derivation, the way `lock_key` is one.

    The column's own name, so two providers of one card keep separate token baselines —
    a matrix keyed by card alone would gate a cheap provider at an expensive one's cost.
    The single-cell path has no column and keeps the `"default"` slot wave 3 held open, so
    a `spec.baseline.toml` already committed needs no migration.
    """
    return column.name if column is not None else DEFAULT_CELL
