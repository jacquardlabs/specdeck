"""`spec.lock.toml` — what the run is pinned to.

Pins the judge model, the rubric hash per card, the simulator model and its prompt hash,
and the OTel GenAI semconv version. The runner refuses a stale lock without `--relock`.

An unpinned judge is not a test: the judge model string alone drifts silently when the
rubric text changes underneath it, and that is exactly the failure this file exists to
catch.

TOML is written by hand rather than by a library. The structure is small and fixed, and
the dependency budget is four packages.
"""

from __future__ import annotations

import hashlib
import tomllib
from pathlib import Path

from pydantic import BaseModel

LOCKFILE_NAME = "spec.lock.toml"
RELOCK_HINT = "run with --relock to record the current state"


class StaleLock(Exception):
    """The lock no longer describes what is about to run."""


def fingerprint(text: str) -> str:
    """Algorithm-tagged hash of a pinned text. Edge whitespace is not a change."""
    return f"sha256:{hashlib.sha256(text.strip().encode()).hexdigest()}"


class CardLock(BaseModel):
    rubric_hash: str
    simulator_hash: str


class Lockfile(BaseModel):
    semconv: str
    judge_model: str
    simulator_model: str
    cards: dict[str, CardLock]

    # -- verification ------------------------------------------------------------

    def verify(self, card_path: str, *, rubric: str, simulator: str) -> None:
        """Raise if the card has drifted from what was locked."""
        entry = self.cards.get(card_path)
        if entry is None:
            raise StaleLock(f"{card_path} is not in the lockfile — {RELOCK_HINT}")
        drift = [
            name
            for name, locked, current in (
                ("rubric", entry.rubric_hash, fingerprint(rubric)),
                ("simulator", entry.simulator_hash, fingerprint(simulator)),
            )
            if locked != current
        ]
        if drift:
            raise StaleLock(
                f"{card_path}: {' and '.join(drift)} changed since the lock — {RELOCK_HINT}"
            )

    def verify_semconv(self, semconv: str) -> None:
        if semconv != self.semconv:
            raise StaleLock(
                f"trace semconv {semconv} does not match the locked {self.semconv} — {RELOCK_HINT}"
            )

    def relock(self, card_path: str, *, rubric: str, simulator: str) -> Lockfile:
        """A copy with this card's hashes refreshed. Other cards are untouched."""
        entry = CardLock(rubric_hash=fingerprint(rubric), simulator_hash=fingerprint(simulator))
        return self.model_copy(update={"cards": self.cards | {card_path: entry}}, deep=True)

    # -- persistence -------------------------------------------------------------

    @classmethod
    def load(cls, path: Path | str) -> Lockfile:
        path = Path(path)
        if not path.exists():
            raise StaleLock(f"no lockfile at {path} — {RELOCK_HINT}")
        return cls.from_toml(path.read_text())

    def save(self, path: Path | str) -> None:
        Path(path).write_text(self.to_toml())

    @classmethod
    def from_toml(cls, text: str) -> Lockfile:
        data = tomllib.loads(text)
        return cls(
            semconv=data["semconv"],
            judge_model=data["judge"]["model"],
            simulator_model=data["simulator"]["model"],
            cards={path: CardLock(**entry) for path, entry in (data.get("cards") or {}).items()},
        )

    def to_toml(self) -> str:
        lines = [
            "# Written by specdeck. Refresh with --relock; do not hand-edit.",
            f"semconv = {_quote(self.semconv)}",
            "",
            "[judge]",
            f"model = {_quote(self.judge_model)}",
            "",
            "[simulator]",
            f"model = {_quote(self.simulator_model)}",
        ]
        for path in sorted(self.cards):
            entry = self.cards[path]
            lines += [
                "",
                f"[cards.{_quote(path)}]",
                f"rubric_hash = {_quote(entry.rubric_hash)}",
                f"simulator_hash = {_quote(entry.simulator_hash)}",
            ]
        return "\n".join(lines) + "\n"


def _quote(value: str) -> str:
    """A TOML basic string. Card paths carry dots, so keys are always quoted."""
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'
