"""The loop: a simulated user and an agent taking turns, emitting a trace.

Offline throughout. The agent is scripted (tests/fake_agent.py) and the simulator replays
cassettes written by hand here, so nothing in this module needs a key.
"""

from __future__ import annotations

import asyncio
import json
from datetime import date
from pathlib import Path

import pytest

from specdeck.agent import AgentAdapter, Chat, Describable, ToolCall
from specdeck.budget import Budget, BudgetStop
from specdeck.card import parse_text
from specdeck.judge import Cassette
from specdeck.loop import DEFAULT_MAX_TURNS, LoopError, run_agent
from specdeck.rates import ModelRate, Rates
from specdeck.simulator import build_prompt
from specdeck.trace import GenAI, Operation, Specdeck

from .fake_agent import BareAgent, FakeAgent, refuses

SEMCONV = "semantic-conventions-genai@1.38.0"
MODEL = "claude-sonnet-5"
MARKERS = ["non_agreement"]

CARD = """\
# Scenario: cancellation refused
context:
  simulator: "traveller wants SI5UKW cancelled and will not take no for an answer"

The agent refuses and explains why.

wire:
  - cancel_reservation: never
"""


@pytest.fixture
def card():
    return parse_text(CARD, path="cards/refused.md")


def record(directory: Path, card, turns: list[dict], *, model: str = MODEL) -> None:
    """Write the simulator cassettes this conversation will ask for, in order.

    Each turn's prompt carries the transcript so far, so the keys can only be computed by
    replaying the same growth — which is also what proves the loop feeds the simulator the
    conversation rather than just the intent.
    """
    cassette = Cassette(directory, kind="simulator", slug=card.slug)
    messages: list[dict] = []
    for turn in turns:
        prompt = build_prompt(card.context.simulator, messages, MARKERS)
        cassette.write(prompt, model, json.dumps(turn))
        messages.append({"role": "user", "content": turn["reply"]})
        for message in turn.get("then", []):
            messages.append(message)


def conversation(agent_says: list[list[dict]]) -> list[list[dict]]:
    return agent_says


def drive(card, agent, cassettes: Path, **kwargs):
    return asyncio.run(
        run_agent(
            card,
            agent,
            cassettes=cassettes,
            simulator_model=MODEL,
            semconv=SEMCONV,
            markers=MARKERS,
            **kwargs,
        )
    )


AGENT_TURN_1 = [
    {"role": "assistant", "content": "Let me pull that up."},
    {"role": "tool", "content": '{"cabin": "basic_economy", "insurance": "no"}'},
    {
        "role": "assistant",
        "content": "I'm sorry, that fare cannot be cancelled and I cannot offer a credit.",
    },
]


class TestProtocol:
    def test_the_scripted_agent_satisfies_the_protocol(self) -> None:
        assert isinstance(FakeAgent([]), AgentAdapter)

    def test_describe_is_optional(self) -> None:
        # A raw-SDK loop has no graph to introspect, and requiring one would exclude it.
        assert isinstance(BareAgent(), AgentAdapter)
        assert not isinstance(BareAgent(), Describable)
        assert isinstance(FakeAgent([], tools=["x"]), Describable)


class TestOneConversation:
    def _run(self, card, tmp_path: Path, **kwargs):
        record(
            tmp_path,
            card,
            [
                {"reply": "Cancel SI5UKW please.", "marker": None, "then": AGENT_TURN_1},
                {"reply": "That is not good enough.", "marker": "non_agreement", "done": True},
            ],
        )
        agent = FakeAgent(refuses())
        return agent, drive(card, agent, tmp_path, **kwargs)

    def test_the_trace_is_the_agents_events_not_the_loops_view(self, card, tmp_path: Path) -> None:
        # The adapter reported a tool call; nothing in the loop could have observed it.
        _, trace = self._run(card, tmp_path)
        tools = [s.attributes[GenAI.TOOL_NAME] for s in trace.of(Operation.EXECUTE_TOOL)]
        assert tools == ["get_reservation_details"]

    def test_it_builds_a_valid_trace_the_rest_of_specdeck_reads(self, card, tmp_path: Path) -> None:
        _, trace = self._run(card, tmp_path)
        assert trace.semconv == SEMCONV
        assert trace.root.operation is Operation.INVOKE_AGENT
        # Three, not two: the agent answers the `done` turn as well (#108).
        assert len(trace.of(Operation.CHAT)) == 3
        assert "cannot be cancelled" in trace.final_response

    def test_the_agent_is_handed_the_conversation_so_far(self, card, tmp_path: Path) -> None:
        agent, _ = self._run(card, tmp_path)
        assert agent.calls[0]["messages"] == [{"role": "user", "content": "Cancel SI5UKW please."}]

    def test_a_done_turn_ends_the_run_before_the_cap(self, card, tmp_path: Path) -> None:
        """Two turns and then stop, against a cap of more (#108).

        The agent answers the `done` turn rather than never seeing it: a conversation ends
        on the agent's word, not the user's. Every deck's policy makes the agent ask before
        it writes, so the traveller's last turn is the "yes" that authorises the write —
        and breaking on `done` threw away exactly the turn that does the work.
        """
        agent, _ = self._run(card, tmp_path)
        assert len(agent.calls) == 2

    def test_tools_and_config_reach_the_adapter(self, card, tmp_path: Path) -> None:
        agent, _ = self._run(card, tmp_path, tools=["cancel_reservation"], config={"seed": 1})
        assert agent.calls[0]["tools"] == ["cancel_reservation"]
        assert agent.calls[0]["config"] == {"seed": 1}


class TestMarkers:
    def test_the_marker_lands_on_the_turn_the_user_is_answering(self, card, tmp_path: Path) -> None:
        record(
            tmp_path,
            card,
            [
                {"reply": "Cancel SI5UKW please.", "marker": None, "then": AGENT_TURN_1},
                {"reply": "No. Give me a voucher.", "marker": "non_agreement", "done": True},
            ],
        )
        trace = drive(card, FakeAgent(refuses()), tmp_path)
        marked = [s for s in trace.ordered if s.marker == "non_agreement"]
        assert len(marked) == 1
        assert "cannot be cancelled" in marked[0].output_messages[0]["content"]

    def test_a_marker_on_the_opening_turn_is_dropped_not_misattributed(
        self, card, tmp_path: Path
    ) -> None:
        # There is no agent turn to disagree with yet, and stamping it on the first one
        # would say the traveller rejected an answer they had not heard.
        record(
            tmp_path,
            card,
            [{"reply": "Cancel SI5UKW.", "marker": "non_agreement"}],
        )
        trace = drive(card, FakeAgent(refuses()), tmp_path, max_turns=1)
        assert [s for s in trace.ordered if s.marker] == []


class TestBounds:
    def test_the_turn_cap_stops_a_simulator_that_never_lets_go(self, card, tmp_path: Path) -> None:
        record(
            tmp_path,
            card,
            [
                {"reply": "Cancel SI5UKW please.", "marker": None, "then": AGENT_TURN_1},
                {"reply": "No.", "marker": "non_agreement", "then": AGENT_TURN_1[-1:]},
            ],
        )
        # Neither recorded turn sets `done`, so only the cap ends this run.
        agent = FakeAgent(refuses())
        trace = drive(card, agent, tmp_path, max_turns=2)
        assert len(agent.calls) == 2
        assert trace.of(Operation.CHAT)

    def test_zero_turns_is_refused(self, card, tmp_path: Path) -> None:
        with pytest.raises(LoopError, match=r"at least one turn"):
            drive(card, FakeAgent(refuses()), tmp_path, max_turns=0)

    def test_the_default_cap_is_stated_rather_than_unbounded(self) -> None:
        assert DEFAULT_MAX_TURNS > 0


class TestAdapterFailures:
    def test_an_adapter_returning_nothing_says_so(self, card, tmp_path: Path) -> None:
        record(tmp_path, card, [{"reply": "Cancel SI5UKW.", "marker": None}])
        with pytest.raises(LoopError, match=r"no events"):
            drive(card, FakeAgent([[]]), tmp_path, max_turns=1)

    def test_an_unknown_event_type_is_refused(self, card, tmp_path: Path) -> None:
        record(tmp_path, card, [{"reply": "Cancel SI5UKW.", "marker": None}])
        with pytest.raises(LoopError, match=r"not an AgentEvent"):
            drive(card, FakeAgent([["a bare string"]]), tmp_path, max_turns=1)


class TestUsage:
    def test_usage_is_absent_when_the_adapter_did_not_report_it(self, card, tmp_path: Path) -> None:
        # A zero written in for an adapter that stayed silent makes a token bound pass
        # forever, which is the distinction Trace.reports_output_tokens exists to keep.
        record(tmp_path, card, [{"reply": "Cancel SI5UKW.", "marker": None}])
        script = [[Chat(content="No.", model="fake-1")]]
        trace = drive(card, FakeAgent(script), tmp_path, max_turns=1)
        assert trace.reports_output_tokens is False

    def test_reported_usage_reaches_the_trace(self, card, tmp_path: Path) -> None:
        record(tmp_path, card, [{"reply": "Cancel SI5UKW.", "marker": None}])
        script = [[Chat(content="No.", model="fake-1", output_tokens=7)]]
        trace = drive(card, FakeAgent(script), tmp_path, max_turns=1)
        assert trace.total_output_tokens == 7


class TestSpanShape:
    def test_a_tool_span_hangs_off_the_chat_that_called_it(self, card, tmp_path: Path) -> None:
        record(tmp_path, card, [{"reply": "Cancel SI5UKW.", "marker": None}])
        script = [
            [
                Chat(content="Looking.", finish_reason="tool_calls", model="fake-1"),
                ToolCall(name="get_reservation_details", arguments={"id": "X"}, result="{}"),
            ]
        ]
        trace = drive(card, FakeAgent(script), tmp_path, max_turns=1)
        chat = trace.of(Operation.CHAT)[0]
        tool = trace.of(Operation.EXECUTE_TOOL)[0]
        assert tool.parent_span_id == chat.span_id
        assert json.loads(tool.attributes[GenAI.TOOL_CALL_ARGUMENTS]) == {"id": "X"}

    def test_spans_are_strictly_ordered(self, card, tmp_path: Path) -> None:
        # Two spans sharing a timestamp make after-K-then-Y read the wrong one as first.
        record(
            tmp_path,
            card,
            [
                {"reply": "Cancel SI5UKW.", "marker": None, "then": AGENT_TURN_1},
                {"reply": "Fine.", "marker": None, "done": True},
            ],
        )
        trace = drive(card, FakeAgent(refuses()), tmp_path)
        starts = [s.start_time for s in trace.ordered if s.parent_span_id is not None]
        assert starts == sorted(starts)
        assert len(set(starts)) == len(starts)


class TestMarkerVocabulary:
    def test_an_undeclared_marker_is_refused_rather_than_stamped(
        self, card, tmp_path: Path
    ) -> None:
        # A marker no wire selects on would read as a run where it never happened.
        record(tmp_path, card, [{"reply": "No.", "marker": "invented_marker"}])
        with pytest.raises(Exception, match=r"invented"):
            drive(card, FakeAgent(refuses()), tmp_path, max_turns=1)


class TestSpecdeckNamespace:
    def test_the_marker_uses_the_reserved_attribute(self, card, tmp_path: Path) -> None:
        record(
            tmp_path,
            card,
            [
                {"reply": "Cancel SI5UKW.", "marker": None, "then": AGENT_TURN_1},
                {"reply": "No.", "marker": "non_agreement", "done": True},
            ],
        )
        trace = drive(card, FakeAgent(refuses()), tmp_path)
        assert any(Specdeck.MARKER in s.attributes for s in trace.spans)


class TestOneCardUnderTheLoop:
    """The exit artifact for #9: a card graded on a trace the loop produced, offline."""

    CARD = """\
# Scenario: cancellation refused
context:
  simulator: "traveller wants SI5UKW cancelled and will not take no for an answer"

The agent refuses to cancel the basic economy fare and explains why.

wire:
  - cancel_reservation: never
  - get_reservation_details: at_most 2

credit:
  - "explains the fare rule rather than only refusing": 1
"""

    def test_the_card_evaluates_against_the_loops_own_trace(self, tmp_path: Path) -> None:
        from specdeck.cell import run_cell
        from specdeck.judge import Cassette, criteria_of
        from specdeck.judge import build_prompt as judge_prompt

        card = parse_text(self.CARD, path="cards/refused.md")
        record(
            tmp_path,
            card,
            [
                {"reply": "Cancel SI5UKW please.", "marker": None, "then": AGENT_TURN_1},
                {"reply": "That is not good enough.", "marker": "non_agreement", "done": True},
            ],
        )
        trace = drive(card, FakeAgent(refuses()), tmp_path)

        # The judge cassette is written against the trace the loop just produced, which is
        # only possible because the transcript is a function of the conversation and not
        # of the clock. A live run would record this instead.
        criteria = criteria_of(card)
        prompt = judge_prompt(criteria, trace, policy="")
        Cassette(tmp_path, slug=card.slug).write(
            prompt,
            MODEL,
            json.dumps({"verdicts": {c.id: True for c in criteria}}),
            criteria=[c.id for c in criteria],
        )

        cell = run_cell(card, [trace], cassettes=tmp_path, n=1, k=1, judge_model=MODEL)
        assert cell.passed
        assert cell.credit_mean == cell.credit_total
        assert cell.results[0].judged.replayed


class TestTheBudget:
    """What a run cost is read off the trace the adapter produced, never off the loop.

    Reactive by necessity: `adapter.run` has already spent the money by the time
    `Chat.input_tokens` is visible, so this charge can only stop the *next* run.
    """

    RATES = Rates(
        verified=date(2026, 8, 24),
        table={"anthropic": {"fake": ModelRate(input=1.0, output=1.0)}},
    )

    def _script(self, **kwargs) -> list[list]:
        return [[Chat(content="I'm sorry, no.", model="fake", **kwargs)]]

    def test_the_agents_own_tokens_are_charged_from_the_trace(self, tmp_path: Path, card) -> None:
        record(tmp_path, card, [{"reply": "Cancel it.", "done": True}])
        budget = Budget(cap_usd=None, rates=self.RATES)
        drive(card, FakeAgent(self._script()), tmp_path, budget=budget)
        # The conversation ends on the simulator's first turn, so the agent never ran.
        assert budget.spent.usd == 0.0

    def test_a_run_that_spoke_is_charged(self, tmp_path: Path, card) -> None:
        turns = [
            {"reply": "Cancel it.", "then": [{"role": "assistant", "content": "I'm sorry, no."}]},
            {"reply": "Fine.", "done": True},
        ]
        record(tmp_path, card, turns)
        budget = Budget(cap_usd=None, rates=self.RATES)
        drive(
            card,
            FakeAgent(self._script(input_tokens=500_000, output_tokens=500_000)),
            tmp_path,
            budget=budget,
        )
        # Two agent turns, not one: the agent answers the `done` turn too (#108), and
        # each is charged. A run that spoke twice costing the same as one that spoke once
        # would be the silent-zero the cap exists to prevent.
        assert budget.spent.usd == pytest.approx(2.0)

    def test_an_adapter_that_reports_no_usage_aborts_under_a_cap(
        self, tmp_path: Path, card
    ) -> None:
        turns = [
            {"reply": "Cancel it.", "then": [{"role": "assistant", "content": "I'm sorry, no."}]},
            {"reply": "Fine.", "done": True},
        ]
        record(tmp_path, card, turns)
        with pytest.raises(BudgetStop, match=r"FakeAgent reported no gen_ai.usage"):
            drive(
                card,
                FakeAgent(self._script()),
                tmp_path,
                budget=Budget(cap_usd=1.0, rates=self.RATES),
            )

    def test_with_no_cap_the_same_run_is_only_counted(self, tmp_path: Path, card) -> None:
        turns = [
            {"reply": "Cancel it.", "then": [{"role": "assistant", "content": "I'm sorry, no."}]},
            {"reply": "Fine.", "done": True},
        ]
        record(tmp_path, card, turns)
        budget = Budget(cap_usd=None, rates=self.RATES)
        drive(card, FakeAgent(self._script()), tmp_path, budget=budget)
        assert budget.unmetered == {"fake": 2}
