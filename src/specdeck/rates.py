"""What a run cost, estimated from a rate table shipped beside the code.

Estimates, never billing. No live pricing API (DECISIONS.md, 2026-08-15): a figure this
runner prints is derived from `rates.toml` and is labeled as an estimate wherever it
appears, carrying the date the table was last checked against the vendor's own page.

A model the table does not price yields no number at all — not a substituted mid-range
default, and not $0.00. cctx can fall back to a plausible rate because it prices sessions
that already happened, where a rough figure beats nothing; specdeck reports a live run,
where a fabricated rate would put an invented number in front of a user as if it had been
measured. That is the same rule `Trace.reports_output_tokens` follows: "used none" and
"did not say" are different answers and are not summed away.

`Estimate.label` is the only sanctioned way to render a figure from here. The raw `usd`
float is reachable, but every label path says "estimate", so the honesty rule lives at the
boundary rather than at each call site.
"""

from __future__ import annotations

import tomllib
from datetime import date
from importlib import resources
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from specdeck.provider import split_model

RATES_FILE = "rates.toml"


class RateError(Exception):
    """The rate table could not be read as specified."""


class ModelRate(BaseModel):
    """USD per million tokens.

    No cache-read or cache-write multipliers: the semconv carries only
    `gen_ai.usage.input_tokens` and `gen_ai.usage.output_tokens`, so a cache rate would
    have nothing to apply to. A prompt-cached run is therefore over-estimated.
    """

    model_config = ConfigDict(frozen=True)

    input: float = Field(ge=0)
    output: float = Field(ge=0)


class Estimate(BaseModel):
    """A cost figure and how much of the run it actually covers.

    `priced` and `unpriced` are kept rather than collapsed into the dollar amount, because
    a partial estimate has to read as partial: three chat spans priced and one unknown
    model is a different statement from four priced.
    """

    model_config = ConfigDict(frozen=True)

    usd: float = 0.0
    priced: int = 0
    unpriced: tuple[str, ...] = ()
    verified: date

    @classmethod
    def nothing(cls, verified: date) -> Estimate:
        """The zero element, so a fold over per-span estimates has a start value."""
        return cls(verified=verified)

    @property
    def complete(self) -> bool:
        return not self.unpriced

    @property
    def label(self) -> str:
        """The figure, as it is allowed to appear. Never a bare float, never $0.00 for n/a."""
        as_of = f"rates as of {self.verified.isoformat()}"
        if not self.priced:
            missing = (
                f"no rate for {', '.join(self.unpriced)}" if self.unpriced else "nothing priced"
            )
            return f"n/a — {missing} ({as_of})"
        if self.unpriced:
            return (
                f"~${self.usd:.4f} estimate, partial — no rate for "
                f"{', '.join(self.unpriced)} ({as_of})"
            )
        return f"~${self.usd:.4f} estimate ({as_of})"

    def __add__(self, other: Estimate) -> Estimate:
        if not isinstance(other, Estimate):
            return NotImplemented
        if self.verified != other.verified:
            raise RateError(
                f"estimates from tables verified {self.verified} and {other.verified} do not "
                "sum — one figure cannot carry two dates"
            )
        return Estimate(
            usd=self.usd + other.usd,
            priced=self.priced + other.priced,
            unpriced=tuple(sorted(set(self.unpriced) | set(other.unpriced))),
            verified=self.verified,
        )


class Rates(BaseModel):
    """Provider -> model-id prefix -> rate, plus the date the whole table was checked."""

    verified: date
    table: dict[str, dict[str, ModelRate]]

    def rate_for(self, model: str, *, provider: str | None = None) -> ModelRate | None:
        """The rate for a model id, or None. Longest prefix wins within the provider.

        The provider comes from the model string by default, not from the trace:
        `gen_ai.provider.name` is written "unknown" on every chat span our own loop
        produces, so keying on it would leave every trace this runner generates unpriced.
        `provider=` is the override for a raw OTLP span that names a real one.
        """
        named, name = split_model(model)
        entries = self.table.get(provider or named)
        if not entries:
            return None
        # Real traces carry dated ids while the table keys families.
        match = max((prefix for prefix in entries if name.startswith(prefix)), key=len, default="")
        return entries[match] if match else None

    def estimate(
        self,
        model: str,
        *,
        input_tokens: int,
        output_tokens: int,
        provider: str | None = None,
    ) -> Estimate:
        rate = self.rate_for(model, provider=provider)
        if rate is None:
            return Estimate(unpriced=(model,), verified=self.verified)
        usd = (input_tokens * rate.input + output_tokens * rate.output) / 1_000_000
        return Estimate(usd=usd, priced=1, verified=self.verified)

    def merged(self, other: Rates) -> Rates:
        """`other` wins per model, and brings its own date: it describes the table in use."""
        table = {provider: dict(entries) for provider, entries in self.table.items()}
        for provider, entries in other.table.items():
            table[provider] = table.get(provider, {}) | entries
        return Rates(verified=other.verified, table=table)

    @classmethod
    def builtin(cls) -> Rates:
        text = resources.files("specdeck").joinpath(RATES_FILE).read_text(encoding="utf-8")
        return cls.from_toml(text, source=f"the built-in {RATES_FILE}")

    @classmethod
    def from_toml(cls, text: str, *, source: str) -> Rates:
        try:
            data = tomllib.loads(text)
        except tomllib.TOMLDecodeError as error:
            raise RateError(f"{source}: {error}") from None
        if "verified" not in data:
            raise RateError(
                f"{source}: no `verified` date. Every figure is printed with it, so a table "
                "without one makes its own label a lie."
            )
        entries = data.get("rates")
        if not isinstance(entries, dict):
            raise RateError(f"{source}: no [rates] table")
        table = {
            provider: {
                model: _rate(entry, source=source, provider=provider, model=model)
                for model, entry in models.items()
            }
            for provider, models in entries.items()
        }
        try:
            return cls(verified=data["verified"], table=table)
        except ValidationError as error:
            raise RateError(f"{source}: verified is not a date — {_why(error)}") from None


def load_rates(path: Path | None, *, beside: Path) -> Rates:
    """The built-in table, with a user's own merged over it if there is one.

    Named explicitly or found beside the card, the same shape `run` already uses for the
    lockfile and the cassettes: where you stand must not change which table is found.
    """
    builtin = Rates.builtin()
    found = path if path is not None else beside / RATES_FILE
    if not found.exists():
        if path is not None:
            raise RateError(f"no rate table at {path}")
        return builtin
    return builtin.merged(Rates.from_toml(found.read_text(), source=str(found)))


def _rate(entry: object, *, source: str, provider: str, model: str) -> ModelRate:
    try:
        return ModelRate(**entry)  # type: ignore[arg-type]
    except (ValidationError, TypeError) as error:
        raise RateError(f"{source}: [rates.{provider}] {model} — {_why(error)}") from None


def _why(error: Exception) -> str:
    """One sentence out of a pydantic failure. A raw dump is not an error message."""
    if isinstance(error, ValidationError):
        first = error.errors()[0]
        field = ".".join(str(part) for part in first["loc"])
        return f"{field}: {first['msg']}" if field else str(first["msg"])
    return str(error)
