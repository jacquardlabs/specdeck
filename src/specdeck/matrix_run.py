"""Running the matrix: columns in parallel, under one budget, reporting what each did.

CONCURRENCY IS ASYNCIO, NOT THREADS. The whole call path below the CLI is already
coroutines — `run_agent`, `cell.run_cell_async`, `judge`, `simulator.turn` — and the
provider is `httpx.AsyncClient`. The two synchronous entry points, `cell.run_cell` and
`cli._drive`, are thin `asyncio.run` wrappers, and `asyncio.run` does not nest, which is
why the matrix path goes past both of them to their async bodies. A thread pool would need
a second HTTP client, a second copy of the semaphore `cell.run_cell_async` already owns,
and a lock on the budget that asyncio makes unnecessary. #55 made this stack async in
Phase 1; this is the first caller that needed it to be.

LIVE COLUMNS ARE SERIALISED, and it is not a performance choice. The simulator's prompt is
built from the card's intent and the transcript alone, so turn 1 is byte-identical across
every column, and a cassette is keyed on prompt plus model — two live columns would race
the same file. Folding the column name into the cassette key would re-key recordings
CLAUDE.md designates Phase-3 mutation fixtures, which is a decision, not a detail. Replay
has no such race and runs at full concurrency.

A COLUMN'S FAILURE NEVER TOUCHES ANOTHER'S. Each column is awaited inside its own
try/except and becomes a `ColumnResult` either way, so a matrix that stopped part-way
reports what ran, what did not, and why — never a grid quietly showing fewer columns than
were asked for.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from enum import StrEnum

from pydantic import BaseModel, Field

from .budget import Budget, BudgetStop
from .cell import Cell
from .matrix import Column

#: Columns in flight at once. Two, not the cell's four: a column is itself a fan-out of N
#: runs, so the real ceiling is this times `--concurrency`, and the product is what a
#: provider's rate limiter sees.
DEFAULT_MATRIX_CONCURRENCY = 2


class Status(StrEnum):
    """What became of one column. Every column ends with exactly one of these."""

    PASSED = "passed"
    FAILED = "failed"
    #: Never started: the cap was already reached when this column's turn came.
    SKIPPED_BUDGET = "skipped"
    #: Started and could not finish under the cap — the cap tripped, or the run could not
    #: be priced at all, which under a cap is the same refusal.
    STOPPED_BUDGET = "stopped"
    #: The column itself raised. Whether that is exit 2 or exit 3 is `user_error`.
    ERRORED = "errored"


class ColumnResult(BaseModel):
    """One column and what it did. The cell is absent unless the column produced one."""

    column: Column
    status: Status
    cell: Cell | None = None
    #: Why, for anything but a plain pass or fail. Printed verbatim in the grid's notes.
    detail: str = ""
    #: Whether an `ERRORED` column raised something `cli.USER_ERRORS` covers. It decides
    #: exit 2 against exit 3, which is the distinction the exit-3 comment exists to hold.
    user_error: bool = False


class MatrixResult(BaseModel):
    """Every column, and what the whole matrix spent."""

    columns: list[ColumnResult]
    #: `Estimate.label` is the only sanctioned render path for the dollar figure.
    spent_label: str
    cap_usd: float | None = None
    #: Models whose calls reported no usage, and how many. Named rather than charged as
    #: zero, so the spend reads as the floor it is.
    unmetered: dict[str, int] = Field(default_factory=dict)
    #: True when the cap trailed off the end of the run — the cap was reached, but nothing
    #: was left to skip. A completed matrix, with an overshoot worth stating.
    overspent: bool = False

    @property
    def stopped_early(self) -> bool:
        """Whether the budget cost this matrix a column.

        Not `budget.stopped`: a cap reached on the very last charge stopped nothing, and a
        matrix that ran every column it was asked for did complete. Only a column that was
        skipped or cut short makes the answer unknown.
        """
        return any(
            result.status in (Status.SKIPPED_BUDGET, Status.STOPPED_BUDGET)
            for result in self.columns
        )

    @property
    def passed(self) -> bool:
        return all(result.status is Status.PASSED for result in self.columns)


async def run_matrix(
    columns: list[Column],
    run_column: Callable[[Column], Awaitable[Cell]],
    *,
    budget: Budget,
    concurrency: int = DEFAULT_MATRIX_CONCURRENCY,
    user_errors: tuple[type[BaseException], ...] = (),
) -> MatrixResult:
    """Run every column, in order, and report what each one did.

    `run_column` is the one thing this module does not own: the CLI supplies a coroutine
    that drives the agent and runs the cell for a column. Keeping it a parameter is what
    keeps the fan-out testable without a card, a lockfile or a cassette on disk.
    """
    limit = asyncio.Semaphore(max(1, concurrency))
    #: The stop that ended the matrix, if one did. A `BudgetStop` from a run that could
    #: not be priced leaves the cap untouched, so `check()` alone would let every later
    #: column carry on past the refusal — and "aborts the remaining columns" is the whole
    #: of fail-closed rules 2 and 3.
    halted: list[BudgetStop] = []

    async def one(column: Column) -> ColumnResult:
        async with limit:
            # Checked after acquiring, not before the task was created. Columns queued
            # behind the semaphore are exactly the ones the cap may have run out on while
            # they waited, and a check made before the wait would never fire for them.
            if halted:
                return ColumnResult(
                    column=column,
                    status=Status.SKIPPED_BUDGET,
                    detail=f"the matrix stopped before this column: {halted[0]}",
                )
            try:
                budget.check(f"column {column.name}")
            except BudgetStop as stop:
                return ColumnResult(column=column, status=Status.SKIPPED_BUDGET, detail=str(stop))
            try:
                cell = await run_column(column)
            except BudgetStop as stop:
                halted.append(stop)
                return ColumnResult(column=column, status=Status.STOPPED_BUDGET, detail=str(stop))
            except Exception as error:
                # Deliberately broad, and the type is kept rather than flattened: a column
                # that raised a user error is exit 2 and anything else is exit 3, which is
                # the distinction a caller reading only the code depends on.
                return ColumnResult(
                    column=column,
                    status=Status.ERRORED,
                    detail=f"{type(error).__name__}: {error}",
                    user_error=isinstance(error, user_errors),
                )
        return ColumnResult(
            column=column,
            status=Status.PASSED if cell.passed else Status.FAILED,
            cell=cell,
        )

    results = list(await asyncio.gather(*(one(column) for column in columns)))
    return MatrixResult(
        columns=results,
        spent_label=budget.spent.label,
        cap_usd=budget.cap_usd,
        unmetered=dict(budget.unmetered),
        overspent=budget.stopped,
    )
