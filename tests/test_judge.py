import json
from pathlib import Path

import pytest

from specdeck.card import parse_text
from specdeck.ir import Tier
from specdeck.judge import (
    Cassette,
    JudgeError,
    build_prompt,
    criteria_of,
    judge,
    parse_response,
    render_transcript,
)
from specdeck.trace import GenAI, Operation, SpanEvent

from .test_trace import span, trace

CARD = """\
# Scenario: refund request on basic economy
context:
  policy: airline.md
  simulator: "frustrated customer"

The agent refuses the change and explains the restriction.

credit:
  - "tone remains professional": 2
  - wire: response_tokens under 400: 1
"""


@pytest.fixture
def conversation():
    chat = span("chat-0", Operation.CHAT)
    chat.events.append(
        SpanEvent(
            name="gen_ai.client.inference.operation.details",
            attributes={
                GenAI.INPUT_MESSAGES: [{"role": "user", "content": "I want a refund"}],
                GenAI.OUTPUT_MESSAGES: [{"role": "assistant", "content": "I cannot refund that"}],
            },
        )
    )
    return trace(
        span("root", Operation.INVOKE_AGENT, parent=None, duration=5.0),
        chat,
        span(
            "tool-0",
            Operation.EXECUTE_TOOL,
            offset=1.0,
            **{GenAI.TOOL_CALL_RESULT: '{"cabin": "basic_economy"}'},
        ),
    )


@pytest.fixture
def criteria():
    return criteria_of(parse_text(CARD))


def record(tmp_path: Path, criteria, conversation, verdicts: dict, reasons: dict | None = None):
    """Write the cassette a live run would have written for these inputs."""
    prompt = build_prompt(criteria, conversation, policy="airline policy")
    body = json.dumps({"verdicts": verdicts, "reasons": reasons or {}})
    Cassette(tmp_path).write(prompt, model="claude-sonnet-5", response=body)


class TestCriteria:
    def test_the_prose_block_is_the_gate_criterion(self, criteria) -> None:
        assert criteria[0].id == "prose"
        assert criteria[0].tier is Tier.GATE
        assert criteria[0].text.startswith("The agent refuses")

    def test_credit_criteria_carry_their_weights(self, criteria) -> None:
        credit = [c for c in criteria if c.tier is Tier.CREDIT]
        assert [(c.text, c.weight) for c in credit] == [("tone remains professional", 2)]

    def test_credit_wires_are_not_judge_criteria(self, criteria) -> None:
        assert not any("response_tokens" in c.text for c in criteria)

    def test_ids_are_slugs_that_survive_a_report(self, criteria) -> None:
        assert criteria[1].id == "tone_remains_professional"


class TestPrompt:
    def test_the_prose_reaches_the_judge_verbatim(self, criteria, conversation) -> None:
        prompt = build_prompt(criteria, conversation, policy="p")
        assert "The agent refuses the change and explains the restriction." in prompt

    def test_the_policy_reaches_the_judge(self, criteria, conversation) -> None:
        assert "airline policy text" in build_prompt(
            criteria, conversation, policy="airline policy text"
        )

    def test_the_prompt_is_deterministic(self, criteria, conversation) -> None:
        assert build_prompt(criteria, conversation, policy="p") == build_prompt(
            criteria, conversation, policy="p"
        )


class TestTranscript:
    def test_renders_the_conversation_and_the_tool_calls(self, conversation) -> None:
        rendered = render_transcript(conversation)
        assert "I want a refund" in rendered
        assert "I cannot refund that" in rendered
        assert "get_reservation_details" in rendered
        assert "basic_economy" in rendered

    def test_spans_appear_in_time_order(self, conversation) -> None:
        rendered = render_transcript(conversation)
        assert rendered.index("I cannot refund") < rendered.index("get_reservation_details")


class TestParseResponse:
    def test_reads_binary_verdicts(self) -> None:
        parsed = parse_response('{"verdicts": {"prose": true}, "reasons": {"prose": "ok"}}')
        assert parsed == ({"prose": True}, {"prose": "ok"})

    def test_tolerates_prose_around_the_json(self) -> None:
        parsed, _ = parse_response('Here is my answer:\n{"verdicts": {"prose": false}}\nDone.')
        assert parsed == {"prose": False}

    def test_a_numeric_verdict_is_rejected(self) -> None:
        with pytest.raises(JudgeError, match="binary"):
            parse_response('{"verdicts": {"prose": 0.8}}')

    def test_a_response_with_no_json_is_rejected(self) -> None:
        with pytest.raises(JudgeError, match="JSON"):
            parse_response("I would rather not say.")


class TestReplay:
    def test_replays_a_recorded_cassette(self, tmp_path: Path, criteria, conversation) -> None:
        record(tmp_path, criteria, conversation, {"prose": True, "tone_remains_professional": True})
        result = judge(criteria, conversation, policy="airline policy", cassettes=tmp_path)
        assert result.replayed is True
        assert {v.id: v.passed for v in result.verdicts} == {
            "prose": True,
            "tone_remains_professional": True,
        }

    def test_verdicts_carry_tier_and_weight_for_the_report(
        self, tmp_path: Path, criteria, conversation
    ) -> None:
        record(tmp_path, criteria, conversation, {"prose": True, "tone_remains_professional": True})
        result = judge(criteria, conversation, policy="airline policy", cassettes=tmp_path)
        credit = [v for v in result.verdicts if v.tier is Tier.CREDIT]
        assert [(v.id, v.weight) for v in credit] == [("tone_remains_professional", 2)]

    def test_a_missing_cassette_says_how_to_record_one(
        self, tmp_path: Path, criteria, conversation
    ) -> None:
        with pytest.raises(JudgeError, match="--live"):
            judge(criteria, conversation, policy="airline policy", cassettes=tmp_path)

    def test_editing_the_prose_invalidates_the_cassette(
        self, tmp_path: Path, criteria, conversation
    ) -> None:
        record(tmp_path, criteria, conversation, {"prose": True})
        edited = criteria_of(parse_text(CARD.replace("refuses", "declines")))
        with pytest.raises(JudgeError, match="--live"):
            judge(edited, conversation, policy="airline policy", cassettes=tmp_path)

    def test_a_criterion_the_judge_skipped_fails_closed(
        self, tmp_path: Path, criteria, conversation
    ) -> None:
        record(tmp_path, criteria, conversation, {"prose": True})
        result = judge(criteria, conversation, policy="airline policy", cassettes=tmp_path)
        assert {v.id: v.passed for v in result.verdicts}["tone_remains_professional"] is False

    def test_the_result_reports_what_it_was_pinned_to(
        self, tmp_path: Path, criteria, conversation
    ) -> None:
        record(tmp_path, criteria, conversation, {"prose": True})
        result = judge(criteria, conversation, policy="airline policy", cassettes=tmp_path)
        assert result.model == "claude-sonnet-5"
        assert result.rubric_hash.startswith("sha256:")
