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
    """What one cell of one card cost. A model rather than a bare int, so a second
    recorded quantity is a new key here instead of a file migration."""

    output_tokens: int


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
        """A copy with this card's cell refreshed. Other cards are untouched."""
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
        data = tomllib.loads(text)
        return cls(
            cards={
                card: {cell: CellBaseline(**entry) for cell, entry in cells.items()}
                for card, cells in (data.get("cards") or {}).items()
            }
        )

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

    A trace that reported no usage refuses instead of contributing a 0. A baseline of 0
    would bound every later run at 0, and one averaged down by a silent trace would gate a
    card on the instrumentation rather than on the cost.
    """
    if not traces:
        raise BaselineError("no runs to record a baseline from")
    silent = [
        index for index, trace in enumerate(traces, start=1) if not trace.reports_output_tokens
    ]
    if silent:
        listed = ", ".join(str(index) for index in silent)
        raise BaselineError(
            f"run {listed} reports no {GenAI.USAGE_OUTPUT_TOKENS}, so there is no token cost "
            "to record — instrument the agent's usage before recording a baseline"
        )
    return statistics.median_low(trace.total_output_tokens for trace in traces)
