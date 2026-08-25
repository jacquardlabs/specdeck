"""The provider seam (#60). Never touches the network."""

from __future__ import annotations

import asyncio
import json

import pytest

from specdeck.provider import (
    DEFAULT_PROVIDER,
    EmptyCompletion,
    ProviderError,
    complete,
    split_model,
)


def _reply(blocks: list[dict], status: int = 200):
    class Response:
        status_code = status
        text = json.dumps({"content": blocks})

        def json(self) -> dict:
            return {"content": blocks}

    return Response()


def _patch(monkeypatch, response, seen: dict | None = None) -> None:
    async def post(self, url, **kwargs):
        if seen is not None:
            seen.update({"url": url, **kwargs})
        return response

    monkeypatch.setattr("httpx.AsyncClient.post", post)


def call(**kwargs) -> str:
    return asyncio.run(complete("grade this", max_tokens=16, **kwargs))


class TestModelStrings:
    def test_a_bare_model_is_the_default_provider(self) -> None:
        # Load-bearing: spec.lock.toml pins `claude-sonnet-5` and every cassette keys on
        # the model string, so requiring a prefix would re-key every recording on disk.
        assert split_model("claude-sonnet-5") == (DEFAULT_PROVIDER, "claude-sonnet-5")

    def test_a_prefix_names_a_departure_from_the_default(self) -> None:
        assert split_model("openai/gpt-5") == ("openai", "gpt-5")

    def test_the_prefix_is_stripped_before_the_wire(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # The provider is specdeck's routing, not part of the model name the API sees.
        monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
        seen: dict = {}
        _patch(monkeypatch, _reply([{"type": "text", "text": "ok"}]), seen)
        call(model="anthropic/claude-sonnet-5")
        assert seen["json"]["model"] == "claude-sonnet-5"

    def test_an_unimplemented_provider_names_itself_and_the_issue(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
        with pytest.raises(ProviderError, match=r"openai"):
            call(model="openai/gpt-5")


class TestAnthropic:
    def test_the_text_block_is_selected_past_a_thinking_block(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
        blocks = [{"type": "thinking", "thinking": "hm"}, {"type": "text", "text": "answer"}]
        _patch(monkeypatch, _reply(blocks))
        assert call(model="claude-sonnet-5") == "answer"

    def test_no_temperature_is_sent(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # A per-provider quirk that already bit once: current models reject it outright.
        monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
        seen: dict = {}
        _patch(monkeypatch, _reply([{"type": "text", "text": "ok"}]), seen)
        call(model="claude-sonnet-5")
        assert "temperature" not in seen["json"]

    def test_a_missing_key_is_not_a_network_call(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        with pytest.raises(ProviderError, match=r"ANTHROPIC_API_KEY"):
            call(model="claude-sonnet-5")

    def test_a_non_200_carries_the_status(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
        _patch(monkeypatch, _reply([], status=429))
        with pytest.raises(ProviderError, match=r"429"):
            call(model="claude-sonnet-5")

    def test_a_reply_with_no_text_is_its_own_class(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # The one provider failure worth asking again about, which is what lets the judge
        # and the simulator resample it while raising on everything else.
        monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
        _patch(monkeypatch, _reply([{"type": "thinking", "thinking": "all of it"}]))
        with pytest.raises(EmptyCompletion):
            call(model="claude-sonnet-5")
        assert issubclass(EmptyCompletion, ProviderError)
