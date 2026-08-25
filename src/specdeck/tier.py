"""Tier: the one concept the card format and the property IR both need.

Every check a card makes carries a tier, whether it is a judge criterion over prose or a
wire over the trace. It lived in `ir.py`, which meant the judge and the report imported the
property IR in order to grade and print prose that never touches a wire.

That coupling has a deadline: DECISIONS.md commits the card format and the IR spec to a
separate repo (#42), so format stability is not held hostage to runner churn. `Tier` would
be pulled out of the IR under pressure at that point. It is cheaper to own its own module
now, when moving it is an import change.
"""

from __future__ import annotations

from enum import StrEnum


class Tier(StrEnum):
    """Gate defines pass and blocks. Credit is weighted, reported, never blocking."""

    GATE = "gate"
    CREDIT = "credit"
