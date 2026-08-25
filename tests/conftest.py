"""Pin the console width the whole suite renders at.

Rich reads `COLUMNS` even when it is not writing to a terminal, and falls back to 80 when
nothing sets it. That made the suite's output assertions depend on a number no test
declares — and, through it, on how long the checkout's own path happens to be. The same
two coverage tests passed on a developer's machine and failed in CI purely because
`/home/runner/work/specdeck/specdeck` is shorter than a local worktree path, so a folded
line broke inside `refunds.md` there and beside it here.

A wide fixed width makes every rendered table and every wrapped line the same on both.
Tests that care about narrow terminals set `COLUMNS` themselves; there is at least one,
and it is the guard that the soft-wrapped lines stay unbroken at 80 columns.

Colour is the same story with a different switch. Typer sets `FORCE_TERMINAL = True` when
`GITHUB_ACTIONS` is set (`typer/rich_utils.py`), so under CI it renders help panels in
colour that nothing renders locally — and the escape sequences land inside the option
names, so `"--affected-by" in stdout` is false against output that visibly contains it.
Turning it off makes the suite assert against the same bytes everywhere.
"""

import pytest
import typer.rich_utils


@pytest.fixture(autouse=True)
def _fixed_console_width(monkeypatch: pytest.MonkeyPatch) -> None:
    """Render at a fixed wide console. A test wanting a narrow one sets `COLUMNS` itself."""
    monkeypatch.setenv("COLUMNS", "200")


@pytest.fixture(autouse=True)
def _no_forced_colour(monkeypatch: pytest.MonkeyPatch) -> None:
    """Assert against plain text, whatever CI tells Typer about the terminal."""
    monkeypatch.setattr(typer.rich_utils, "FORCE_TERMINAL", False, raising=False)
