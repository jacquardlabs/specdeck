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
"""

import pytest


@pytest.fixture(autouse=True)
def _fixed_console_width(monkeypatch: pytest.MonkeyPatch) -> None:
    """Render at a fixed wide console. A test wanting a narrow one sets `COLUMNS` itself."""
    monkeypatch.setenv("COLUMNS", "200")
