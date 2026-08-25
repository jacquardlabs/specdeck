"""The example agent, exercised without a model or a network.

What is faked here is the provider reply, exactly as `tests/fake_agent.py` fakes the model
behind an agent: the tool-use loop, the database copying and the event mapping are the real
ones. Live behaviour is the migration report's job (#24), not the suite's.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from examples.airline.agent import MAX_STEPS, AirlineError, agent
from examples.airline.tools import TOOLS
from specdeck.agent import AgentAdapter, Chat, ToolCall

OPENING = [{"role": "user", "content": "Can you look at reservation SI5UKW?"}]


def _reply(blocks: list[dict], *, stop: str = "end_turn", usage: dict | None = None) -> dict:
    return {
        "content": blocks,
        "stop_reason": stop,
        "model": "claude-opus-5",
        "usage": usage if usage is not None else {"input_tokens": 10, "output_tokens": 5},
    }


def _patch(monkeypatch: pytest.MonkeyPatch, replies: list[dict]) -> list[dict]:
    """Serve `replies` in order, the last repeating, and record what was sent."""
    sent: list[dict] = []

    class Response:
        status_code = 200

        def __init__(self, payload: dict) -> None:
            self._payload = payload
            self.text = json.dumps(payload)

        def json(self) -> dict:
            return self._payload

    async def post(self, url, **kwargs):
        # Cycled, not clamped to the last: a second conversation on the same instance has
        # to see the same opening reply the first did, or the database test cannot tell a
        # re-run tool from a replayed one.
        sent.append(kwargs["json"])
        return Response(replies[(len(sent) - 1) % len(replies)])

    monkeypatch.setattr("httpx.AsyncClient.post", post)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    return sent


class TestTheProtocol:
    def test_the_factory_returns_something_the_runner_accepts(self) -> None:
        # `cli._adapter` calls a callable that is not itself an adapter, then rechecks.
        assert isinstance(agent(), AgentAdapter)

    def test_it_describes_itself_as_a_raw_sdk_loop(self) -> None:
        """Tools and no edges, so `lint --agent-def` reports the lower tier honestly."""
        described = agent().describe()
        assert sorted(described.tools) == sorted(TOOLS)
        assert described.edges == [] and described.cycles == []


class TestTheToolLoop:
    def test_a_reply_with_no_tool_call_is_one_chat_event(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch(monkeypatch, [_reply([{"type": "text", "text": "Happy to help."}])])
        events = asyncio.run(agent().run(OPENING, [], {}))
        assert len(events) == 1
        assert isinstance(events[0], Chat)
        assert events[0].content == "Happy to help."
        assert (events[0].input_tokens, events[0].output_tokens) == (10, 5)

    def test_a_tool_call_runs_the_real_tool_and_reports_its_result(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch(
            monkeypatch,
            [
                _reply(
                    [
                        {"type": "text", "text": "Let me look."},
                        {
                            "type": "tool_use",
                            "id": "toolu_1",
                            "name": "get_reservation_details",
                            "input": {"reservation_id": "SI5UKW"},
                        },
                    ],
                    stop="tool_use",
                ),
                _reply([{"type": "text", "text": "That is basic economy."}]),
            ],
        )
        events = asyncio.run(agent().run(OPENING, [], {}))
        call = next(e for e in events if isinstance(e, ToolCall))
        assert call.name == "get_reservation_details"
        assert call.call_id == "toolu_1"
        # The ported tool actually ran: this is the fixture's own reservation, not a stub.
        assert json.loads(call.result)["cabin"] == "basic_economy"

    def test_an_unknown_tool_comes_back_as_a_result_rather_than_a_crash(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An agent recovering from its own bad call is behaviour a card may grade."""
        _patch(
            monkeypatch,
            [
                _reply(
                    [{"type": "tool_use", "id": "t", "name": "teleport", "input": {}}],
                    stop="tool_use",
                ),
                _reply([{"type": "text", "text": "I cannot do that."}]),
            ],
        )
        events = asyncio.run(agent().run(OPENING, [], {}))
        assert "unknown tool teleport" in next(e for e in events if isinstance(e, ToolCall)).result

    def test_a_turn_that_never_yields_is_bounded(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The example that ships beside `lint --agent-def` does not contain an unbounded
        cycle, so the bound is held by a test rather than by hope."""
        _patch(
            monkeypatch,
            [
                _reply(
                    [
                        {
                            "type": "tool_use",
                            "id": "t",
                            "name": "get_reservation_details",
                            "input": {"reservation_id": "SI5UKW"},
                        }
                    ],
                    stop="tool_use",
                )
            ],
        )
        with pytest.raises(AirlineError, match=str(MAX_STEPS)):
            asyncio.run(agent().run(OPENING, [], {}))


class TestTheDatabase:
    def test_a_mutation_does_not_leak_into_the_next_conversation(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`cli._adapter` resolves `--agent` once, so one instance serves every run and
        every column. Without the per-conversation copy, run 2 would start from whatever
        run 1 cancelled, and a column would be graded against a world another mutated.
        """
        _patch(
            monkeypatch,
            [
                _reply(
                    [
                        {
                            "type": "tool_use",
                            "id": "t",
                            "name": "cancel_reservation",
                            "input": {"reservation_id": "SI5UKW"},
                        }
                    ],
                    stop="tool_use",
                ),
                _reply([{"type": "text", "text": "Cancelled."}]),
            ],
        )
        one = agent()
        first = asyncio.run(one.run(OPENING, [], {}))
        assert (
            json.loads(next(e for e in first if isinstance(e, ToolCall)).result)["status"]
            == "cancelled"
        )

        # A second conversation on the SAME instance starts from the seed again.
        second = asyncio.run(one.run(OPENING, [], {}))
        assert (
            json.loads(next(e for e in second if isinstance(e, ToolCall)).result)["status"]
            == "cancelled"
        ), "the tool ran again rather than replaying"
        assert one._seed["reservations"]["SI5UKW"].get("status") != "cancelled"


class TestWhatAColumnVaries:
    def test_the_model_and_the_prompt_come_from_config(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The two axes #24 compares, and the only things a column moves."""
        sent = _patch(monkeypatch, [_reply([{"type": "text", "text": "ok"}])])
        asyncio.run(
            agent().run(OPENING, [], {"model": "claude-opus-4-8", "system_prompt": "Be terse."})
        )
        assert sent[0]["model"] == "claude-opus-4-8"
        assert sent[0]["system"] == "Be terse."

    def test_the_default_prompt_is_the_committed_policy(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sent = _patch(monkeypatch, [_reply([{"type": "text", "text": "ok"}])])
        asyncio.run(agent().run(OPENING, [], {}))
        assert sent[0]["system"].startswith("# Airline Agent Policy")

    def test_a_prompt_path_resolves_against_the_example(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """So a matrix file can say `prompt = "prompts/trimmed.md"` from anywhere."""
        sent = _patch(monkeypatch, [_reply([{"type": "text", "text": "ok"}])])
        asyncio.run(agent().run(OPENING, [], {"prompt": "prompts/full.md"}))
        assert sent[0]["system"].startswith("# Airline Agent Policy")

    def test_a_prompt_that_does_not_exist_says_so(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch(monkeypatch, [_reply([{"type": "text", "text": "ok"}])])
        with pytest.raises(AirlineError, match="no prompt file"):
            asyncio.run(agent().run(OPENING, [], {"prompt": "prompts/absent.md"}))

    def test_the_cards_vocabulary_narrows_the_tools_offered(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A card can test an agent holding fewer tools than the domain defines."""
        sent = _patch(monkeypatch, [_reply([{"type": "text", "text": "ok"}])])
        asyncio.run(agent().run(OPENING, ["cancel_reservation"], {}))
        assert [t["name"] for t in sent[0]["tools"]] == ["cancel_reservation"]

    def test_naming_no_tools_offers_the_whole_domain(self, monkeypatch: pytest.MonkeyPatch) -> None:
        sent = _patch(monkeypatch, [_reply([{"type": "text", "text": "ok"}])])
        asyncio.run(agent().run(OPENING, [], {}))
        assert len(sent[0]["tools"]) == len(TOOLS)


class TestTheKey:
    def test_no_key_is_a_named_error_rather_than_a_traceback(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        with pytest.raises(AirlineError, match="ANTHROPIC_API_KEY"):
            asyncio.run(agent().run(OPENING, [], {}))


class TestTheData:
    def test_the_committed_database_answers_every_card_it_ships_beside(self) -> None:
        """Every reservation and user the six cards name resolves, so a live run does not
        fall off the edge of the data on its first lookup."""
        data = json.loads((Path("examples/airline/data.json")).read_text())
        named = set()
        for path in sorted(Path("cards/fixtures").glob("*.json")):
            fixture = json.loads(path.read_text())
            named |= set(fixture.get("reservations", {}))
        assert named and named <= set(data["reservations"])

    def test_the_searchable_universe_is_the_whole_upstream_one(self) -> None:
        """Flights are not sliced. A trimmed flight table would make a live agent's search
        return nothing and grade the data rather than the model."""
        data = json.loads((Path("examples/airline/data.json")).read_text())
        assert len(data["flights"]) == 300
