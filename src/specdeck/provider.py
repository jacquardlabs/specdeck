"""specdeck's own outbound model call — the judge's and the simulator's, not the agent's.

One function, `complete`, behind which a provider lives. Not a plugin registry: there are
exactly two callers, and a registry would be more surface than the thing it wraps.

The four-dependency budget stands (CLAUDE.md). A provider is `httpx` plus a request shape,
never a vendor SDK — 82 transitive packages for two call sites is the trade the #2 spike
already refused.

A model string may name its provider (`openai/gpt-5`); a bare one is Anthropic. That is a
compatibility decision, not a default worth arguing about: `spec.lock.toml` pins
`claude-sonnet-5` and every cassette keys on `fingerprint(model + prompt)`, so requiring a
prefix would re-key every recording on disk and cost a live call each to record what is
already there. The prefix names a *departure* from the default.

Only Anthropic is implemented. #60 stays open for the second one, and the seam exists so
that issue is a new function rather than a rewrite of two call sites.
"""

from __future__ import annotations

import os

import httpx
from pydantic import BaseModel, ConfigDict

DEFAULT_PROVIDER = "anthropic"
DEFAULT_TIMEOUT_S = 180

ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"


class ProviderError(Exception):
    """The call could not be made, or came back as a failure. Not worth repeating."""


class EmptyCompletion(ProviderError):
    """The call succeeded and carried no text.

    Its own class because it is the one provider failure a caller should ask again about:
    a reasoning model that spent its budget on thinking returns a well-formed reply with
    no text block in it, and the next sample of the same prompt usually has one.

    It carries the usage the reply reported, because this call was made and billed like
    any other — and because it is the one the caller *repeats*. A thinking model that
    keeps returning no text bills every attempt, so a cap that skips this path reads zero
    while the money leaves. Either half is `None` when the reply did not report it, on the
    same "used none" / "did not say" rule `Completion` holds.
    """

    def __init__(
        self,
        message: str,
        *,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
    ) -> None:
        super().__init__(message)
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens


class Completion(BaseModel):
    """A reply and what it cost, as the provider reported it.

    The token counts are what makes a budget cap enforceable over specdeck's own spend at
    all: before this, every token the judge and the simulator burned was discarded at the
    wire, and a call nobody counted is indistinguishable from a call that was free.

    Either half is `None` when the reply did not carry it — never 0. That is the same rule
    `Trace.reports_output_tokens` holds: "used none" and "did not say" are different
    answers, and a cap that reads the second as the first is a cap that never trips.
    """

    model_config = ConfigDict(frozen=True)

    text: str
    input_tokens: int | None = None
    output_tokens: int | None = None


def split_model(model: str) -> tuple[str, str]:
    """`anthropic/claude-sonnet-5` -> `("anthropic", "claude-sonnet-5")`; bare is default."""
    provider, _, name = model.rpartition("/")
    return (provider or DEFAULT_PROVIDER), name


async def complete(
    prompt: str, *, model: str, max_tokens: int, timeout_s: int = DEFAULT_TIMEOUT_S
) -> Completion:
    """One turn, one reply back. No streaming, no tools — neither caller needs either."""
    provider, name = split_model(model)
    if provider != DEFAULT_PROVIDER:
        raise ProviderError(
            f"no provider {provider!r} — specdeck speaks {DEFAULT_PROVIDER} today, and a "
            "second one is #60. Pin a model without a prefix to use the default."
        )
    return await _anthropic(prompt, model=name, max_tokens=max_tokens, timeout_s=timeout_s)


async def _anthropic(prompt: str, *, model: str, max_tokens: int, timeout_s: int) -> Completion:
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise ProviderError("ANTHROPIC_API_KEY is not set, and --live needs it")
    async with httpx.AsyncClient(timeout=timeout_s) as client:
        response = await client.post(
            ANTHROPIC_URL,
            headers={
                "x-api-key": key,
                "anthropic-version": ANTHROPIC_VERSION,
                "content-type": "application/json",
            },
            # No temperature: current models reject it outright, and a shared abstraction
            # is exactly where a per-provider quirk gets papered over wrongly. What is
            # pinned is the model and the prompt, never a sampling setting.
            json={
                "model": model,
                "max_tokens": max_tokens,
                "messages": [{"role": "user", "content": prompt}],
            },
        )
    if response.status_code != httpx.codes.OK:
        raise ProviderError(f"call failed: {response.status_code} {response.text[:200]}")
    payload = response.json()
    blocks = payload["content"]
    # Absent rather than zero when the reply did not report usage: a caller that charges a
    # budget must be able to tell a call nobody counted from a call that cost nothing.
    # Read before the empty-reply check, not after: that reply was billed too.
    usage = payload.get("usage") or {}
    # Reasoning models lead with a thinking block, so select by type rather than by index.
    text = next((b["text"] for b in blocks if b.get("type") == "text"), None)
    if text is None:
        raise EmptyCompletion(
            "the reply carried no text block",
            input_tokens=_count(usage.get("input_tokens")),
            output_tokens=_count(usage.get("output_tokens")),
        )
    return Completion(
        text=str(text),
        input_tokens=_count(usage.get("input_tokens")),
        output_tokens=_count(usage.get("output_tokens")),
    )


def _count(value: object) -> int | None:
    """A reported token count, or None. A non-integer is treated as not reported."""
    return int(value) if isinstance(value, int) else None
