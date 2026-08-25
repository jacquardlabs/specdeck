"""`spec.baseline.toml` — what this card cost the last time anyone recorded it.

One number per card per cell: the output tokens a run of it produced. The built-in
`token_baseline` wire bounds a run at that number plus a tolerance, so a card that starts
costing materially more fails rather than passing quietly at four times the price.

The number recorded is `Trace.total_output_tokens` and not the per-model `usage_by_model`
table, because `Bound(Measure.TOTAL_OUTPUT_TOKENS)` is what the gate compares against and
the two have to read the same number. A baseline the bound never reads cannot fire.

Its own file, not a key in `spec.lock.toml`: the lockfile's contract is refuse-and-exit-2
on any drift, and a measured token count moves on every real run, so ordinary variance
would become a hard wall carrying a `--relock` hint that fixes nothing.

Keyed card x cell, with the cell key held at `"default"` until the provider x prompt matrix
fills the slot. A baseline keyed by card alone is wrong the day that matrix lands, and a
file users have already committed is the expensive place to learn it.

TOML is written by hand, like the lockfile's, and for the same reason: the structure is
small and fixed, and the dependency budget is four packages.
"""

from __future__ import annotations

import statistics
import tomllib
from collections.abc import Callable
from pathlib import Path

from pydantic import BaseModel, Field

from .lockfile import quote
from .trace import GenAI, Trace

BASELINE_NAME = "spec.baseline.toml"
UPDATE_HINT = "run with --update-baseline to record what this card costs now"

#: The provider x prompt column this baseline belongs to. One value until the matrix lands.
DEFAULT_CELL = "default"


class BaselineError(Exception):
    """The baseline cannot be read or cannot honestly be recorded."""


class CellBaseline(BaseModel):
    """What one cell of one card cost.

    A model rather than a bare int, so a second recorded quantity is a new key here rather
    than a migration of a file users have already committed.
    """

    #: Positive, because `BuiltinConfig` refuses a baseline of 0 or less. Without the bound
    #: here the writer could produce a file the reader then rejects as an internal error on
    #: every later run, and a hand-edited `output_tokens = 0` would do the same — this turns
    #: both into the exit 2 a user-owned file deserves.
    output_tokens: int = Field(gt=0)


class Baseline(BaseModel):
    cards: dict[str, dict[str, CellBaseline]] = Field(default_factory=dict)

    def get(self, card_key: str, cell: str = DEFAULT_CELL) -> int | None:
        """This card's recorded token cost, or None when nothing recorded it.

        None and not 0: an unrecorded card gets no regression wire at all, and a limit of
        zero would fail every run of it forever.
        """
        entry = self.cards.get(card_key, {}).get(cell)
        return None if entry is None else entry.output_tokens

    def record(self, card_key: str, output_tokens: int, *, cell: str = DEFAULT_CELL) -> Baseline:
        """A copy with this card's cell refreshed. Other cards are untouched.

        `output_tokens` must be positive; `observed` is the only caller and refuses before
        it returns anything else.
        """
        cells = self.cards.get(card_key, {}) | {cell: CellBaseline(output_tokens=output_tokens)}
        return self.model_copy(update={"cards": self.cards | {card_key: cells}}, deep=True)

    # -- persistence -------------------------------------------------------------

    @classmethod
    def load(cls, path: Path | str) -> Baseline:
        """The recorded baselines, or an empty one when the file does not exist.

        Missing is not an error, unlike the lockfile: a repo that has never recorded a
        baseline must still run, it simply gets no free regression wire.
        """
        path = Path(path)
        if not path.exists():
            return cls()
        try:
            return cls.from_toml(path.read_text())
        except (tomllib.TOMLDecodeError, ValueError, OSError) as error:
            raise BaselineError(f"cannot read the baseline at {path}: {error}") from None

    def save(self, path: Path | str) -> None:
        Path(path).write_text(self.to_toml())

    @classmethod
    def from_toml(cls, text: str) -> Baseline:
        # Validated by pydantic against the declared shape rather than walked by hand. A
        # hand-written comprehension raises `TypeError` or `AttributeError` on a file whose
        # tables are nested one level wrong — the natural hand-edit — and those are not
        # `ValueError`, so `load` let them out as exit 3 for a file the user owns.
        return cls.model_validate(tomllib.loads(text))

    def to_toml(self) -> str:
        lines = [f"# Written by specdeck. {UPDATE_HINT.capitalize()}; do not hand-edit."]
        for card in sorted(self.cards):
            for cell in sorted(self.cards[card]):
                lines += [
                    "",
                    f"[cards.{quote(card)}.{quote(cell)}]",
                    f"output_tokens = {self.cards[card][cell].output_tokens}",
                ]
        return "\n".join(lines) + "\n"


def observed(traces: list[Trace]) -> int:
    """What to record for these runs: the median output-token count, low half on a tie.

    A median rather than a mean, which one spike moves, or a max, which ratchets upward
    forever and never comes back down. `median_low` rather than the interpolating median,
    because over an even number of runs the plain median averages two of them into a token
    count no run produced — and the point of the statistic is that it is a number something
    actually cost.

    A trace that reported no usage refuses instead of contributing a 0, and so does one
    whose chat spans reported 0 between them. A baseline of 0 is not a bound the runner can
    hold — `BuiltinConfig` rejects it — and one averaged down by a silent trace would gate
    a card on the instrumentation rather than on the cost. The two are kept apart because
    they are different facts: one run said nothing, the other said nothing was spent.
    """
    if not traces:
        raise BaselineError("no runs to record a baseline from")
    if silent := _runs_where(traces, lambda trace: not trace.reports_output_tokens):
        raise BaselineError(
            f"run {silent} reports no {GenAI.USAGE_OUTPUT_TOKENS}, so there is no token cost "
            "to record — instrument the agent's usage before recording a baseline"
        )
    if empty := _runs_where(traces, lambda trace: trace.total_output_tokens == 0):
        raise BaselineError(
            f"run {empty} reports {GenAI.USAGE_OUTPUT_TOKENS} totalling 0, and a baseline of "
            "0 bounds every later run at nothing — record from runs that cost something"
        )
    return statistics.median_low(trace.total_output_tokens for trace in traces)


def _runs_where(traces: list[Trace], predicate: Callable[[Trace], bool]) -> str:
    """The 1-based run numbers matching, listed for a message, or "" when none do.

    Numbered from 1 because that is how the report and the JUnit rows count runs, and a
    refusal naming run 0 sends the reader to a run nothing else calls by that name.
    """
    return ", ".join(str(index) for index, trace in enumerate(traces, start=1) if predicate(trace))
