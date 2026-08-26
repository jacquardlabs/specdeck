"""The Meridian deck and the agent that ships beside it.

Offline throughout. The deck's traces were captured from live runs with `--save-trace`
(#112) rather than hand-authored, which is the point of them: a fixture written by an
author records what the author believed, and #107 is what that costs.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from examples.payable.agent import RAIL_CEILING_USD, PayableAgent, _refusal, agent, naive
from examples.payable.tools import TOOLS, schemas
from specdeck.agent import AgentAdapter, ToolCall
from specdeck.card import parse
from specdeck.ir import AfterKThen, AtMost, Bound, Never, NeverRequested, evaluate_all
from specdeck.traceio import load_trace
from specdeck.wires import compile_wires

DECK = Path("cards")
CARDS = sorted(DECK.glob("*.md"))
BEFORE = Path("examples/payable/tutorial/traces-before")


def ids(paths: list[Path]) -> list[str]:
    return [p.stem for p in paths]


class TestTheDeck:
    def test_five_cards_ship(self) -> None:
        assert len(CARDS) == 5, ids(CARDS)

    @pytest.mark.parametrize("path", CARDS, ids=ids(CARDS))
    def test_every_card_parses_and_carries_a_gate_wire(self, path: Path) -> None:
        card = parse(path)
        assert card.prose
        assert compile_wires(card), path.name

    @pytest.mark.parametrize("path", CARDS, ids=ids(CARDS))
    def test_every_card_declares_the_traces_that_exercise_it(self, path: Path) -> None:
        """#70. Which runs exercise a card belongs on the card, not in a shell history."""
        card = parse(path)
        assert card.context.traces, path.name
        assert card.trace_paths, f"{path.name}: declared traces match nothing"

    def test_the_deck_exercises_every_shipped_pattern(self) -> None:
        """Equality, not a subset — the assertion #109 tightened.

        As a subset this passed while `NeverRequested` was absent from every airline card
        for a whole milestone, which is how a shipped pattern went unexercised in silence.
        """
        kinds = {type(p.rule) for path in CARDS for p in compile_wires(parse(path))}
        assert kinds == {Never, NeverRequested, AtMost, Bound, AfterKThen}


class TestTheCapturedRuns:
    """The deck's own traces pass it, and the tutorial's do not."""

    @pytest.mark.parametrize("path", CARDS, ids=ids(CARDS))
    def test_each_cards_declared_traces_satisfy_its_wires(self, path: Path) -> None:
        card = parse(path)
        props = compile_wires(card)
        for trace_path in card.trace_paths:
            broken = [v for v in evaluate_all(props, load_trace(trace_path)) if not v.passed]
            assert not broken, f"{trace_path.name}: {[v.id for v in broken]}"

    def test_the_before_traces_are_kept_only_where_a_wire_actually_failed(self) -> None:
        """The tutorial's evidence has to be evidence.

        Captured at n=5 and filtered by evaluating them, because a single capture is not a
        rate: one naive run of the threshold card passed, and taking it as the before-state
        would have documented the agent behaving correctly.
        """
        by_card = {c.stem: compile_wires(parse(c)) for c in CARDS}
        seen = 0
        for trace_path in sorted(BEFORE.glob("*.otlp.json")):
            stem = trace_path.name.split(".")[0]
            broken = [
                v for v in evaluate_all(by_card[stem], load_trace(trace_path)) if not v.passed
            ]
            assert broken, f"{trace_path.name} passes; it is not a before-state"
            seen += 1
        assert seen, "no before-traces captured"

    def test_a_live_denial_was_captured(self) -> None:
        """#68's convention, #111's protocol field, from a real agent (#92).

        `never_executed` would hold on this span — the rail refused, nothing ran — while
        `never_requested` fails, because the agent asked. The two selectors disagreeing on
        one run is what they were separated to express, and nothing produced one until the
        guardrail sat somewhere the model would actually reach.
        """
        denials = [
            span
            for path in BEFORE.glob("payment-ceiling*.otlp.json")
            for span in load_trace(path).spans
            if span.denied_tool
        ]
        assert denials, "no captured denial span"
        for span in denials:
            assert span.denied_tool == "pay_invoice"
            assert span.executed_tool is None, "a denial is not an execution"
            assert span.requested_tools == ["pay_invoice"]


class TestTheTools:
    def test_every_tool_has_a_schema_the_model_can_be_shown(self) -> None:
        assert {s["name"] for s in schemas()} == set(TOOLS)
        for one in schemas():
            assert one["description"], one["name"]

    def test_the_vocabulary_names_exactly_the_tools_that_exist(self) -> None:
        """A card cannot wire a tool the agent does not have, and lint reads this file."""
        text = (DECK / "vocabulary.txt").read_text()
        listed = {
            line.strip()
            for line in text.split("[tools]")[1].split("[markers]")[0].splitlines()
            if line.strip() and not line.startswith("#")
        }
        assert listed == set(TOOLS)

    def test_paying_twice_is_refused_by_the_tool_itself(self) -> None:
        data = json.loads((DECK / "fixtures" / "data.json").read_text())
        assert "paid" in TOOLS["pay_invoice"](data, "INV-5501")
        assert "already paid" in TOOLS["pay_invoice"](data, "INV-5501")

    def test_nothing_in_the_tools_enforces_the_policy(self) -> None:
        """Deliberate: a tool layer that enforced the rules would make the agent's own
        judgement unobservable, and every card in this deck is about that judgement."""
        data = json.loads((DECK / "fixtures" / "data.json").read_text())
        over_threshold = data["invoices"]["INV-5518"]["amount"]
        assert over_threshold > 5000
        assert "paid" in TOOLS["pay_invoice"](data, "INV-5518")


class TestTheGuardrail:
    def test_it_refuses_above_the_rail_ceiling_and_allows_below(self) -> None:
        data = json.loads((DECK / "fixtures" / "data.json").read_text())
        assert _refusal("pay_invoice", {"invoice_id": "INV-5541"}, data) is None
        refused = _refusal("pay_invoice", {"invoice_id": "INV-5518"}, data)
        assert refused and str(int(RAIL_CEILING_USD)) in refused.replace(",", "")

    def test_it_guards_nothing_else(self) -> None:
        data = json.loads((DECK / "fixtures" / "data.json").read_text())
        assert _refusal("get_invoice", {"invoice_id": "INV-5518"}, data) is None
        assert _refusal("update_vendor_bank_details", {"vendor_id": "V-4501"}, data) is None

    def test_an_unknown_invoice_is_not_guarded_into_silence(self) -> None:
        """The tool reports the unknown id itself; the guardrail must not swallow it."""
        assert _refusal("pay_invoice", {"invoice_id": "NOPE"}, {"invoices": {}}) is None


class TestTheAdapter:
    def test_both_factories_satisfy_the_protocol(self) -> None:
        assert isinstance(agent(), AgentAdapter)
        assert isinstance(naive(), AgentAdapter)

    def test_the_two_factories_differ_only_in_their_prompt(self) -> None:
        assert agent()._default_prompt != naive()._default_prompt
        assert agent()._seed == naive()._seed

    def test_it_describes_itself_as_a_raw_sdk_loop(self) -> None:
        described = agent().describe()
        assert sorted(described.tools) == sorted(TOOLS)
        assert described.edges == [] and described.cycles == []

    def test_a_guarded_call_is_reported_as_a_denial_and_never_runs(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The adapter half of #111, without a network."""
        replies = [
            {
                "content": [
                    {
                        "type": "tool_use",
                        "id": "t1",
                        "name": "pay_invoice",
                        "input": {"invoice_id": "INV-5518"},
                    }
                ],
                "stop_reason": "tool_use",
                "model": "claude-sonnet-5",
                "usage": {"input_tokens": 10, "output_tokens": 5},
            },
            {
                "content": [{"type": "text", "text": "I cannot pay that."}],
                "stop_reason": "end_turn",
                "model": "claude-sonnet-5",
                "usage": {"input_tokens": 10, "output_tokens": 5},
            },
        ]
        sent: list[dict] = []

        class Response:
            status_code = 200

            def __init__(self, payload: dict) -> None:
                self._payload = payload
                self.text = json.dumps(payload)

            def json(self) -> dict:
                return self._payload

        async def post(self, url, **kwargs):
            sent.append(kwargs["json"])
            return Response(replies[min(len(sent) - 1, len(replies) - 1)])

        monkeypatch.setattr("httpx.AsyncClient.post", post)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

        one = PayableAgent()
        events = asyncio.run(one.run([{"role": "user", "content": "Pay INV-5518."}], [], {}))
        denial = next(e for e in events if isinstance(e, ToolCall))
        assert denial.denied_tool == "pay_invoice"
        assert denial.name == "ap_guardrail"
        assert one._data["invoices"]["INV-5518"]["status"] == "unpaid", "nothing ran"
