from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from specdeck.trace import GenAI, Operation, Span, SpanEvent, Trace

T0 = datetime(2026, 8, 16, 12, 0, 0, tzinfo=UTC)


def span(
    span_id: str,
    operation: Operation,
    *,
    parent: str | None = "root",
    offset: float = 0.0,
    duration: float = 1.0,
    **attributes: object,
) -> Span:
    base: dict[str, object] = {GenAI.OPERATION_NAME: operation.value}
    if operation is Operation.INVOKE_AGENT:
        base[GenAI.AGENT_NAME] = "airline-support"
    if operation is Operation.CHAT:
        base[GenAI.PROVIDER_NAME] = "anthropic"
        base[GenAI.REQUEST_MODEL] = "claude-sonnet-5"
    if operation is Operation.EXECUTE_TOOL:
        base[GenAI.TOOL_NAME] = "get_reservation_details"
    return Span(
        span_id=span_id,
        parent_span_id=parent,
        name=f"{operation.value} {span_id}",
        start_time=T0 + timedelta(seconds=offset),
        end_time=T0 + timedelta(seconds=offset + duration),
        attributes=base | attributes,
    )


def trace(*spans: Span) -> Trace:
    return Trace(semconv="semantic-conventions-genai@1.38.0", spans=list(spans))


@pytest.fixture
def three_span_trace() -> Trace:
    return trace(
        span("root", Operation.INVOKE_AGENT, parent=None, duration=4.0),
        span("chat-0", Operation.CHAT, offset=0.5, duration=1.0),
        span("tool-0", Operation.EXECUTE_TOOL, parent="chat-0", offset=1.5, duration=0.25),
    )


class TestSpan:
    def test_operation_reads_from_the_semconv_attribute(self) -> None:
        assert span("chat-0", Operation.CHAT).operation is Operation.CHAT

    def test_duration_comes_from_the_span_boundaries(self) -> None:
        assert span("tool-0", Operation.EXECUTE_TOOL, duration=0.25).duration_s == 0.25

    def test_an_unknown_operation_names_the_span(self) -> None:
        with pytest.raises(ValidationError, match="embedding-0.*gen_ai.operation.name"):
            Span(
                span_id="embedding-0",
                name="embeddings",
                start_time=T0,
                end_time=T0,
                attributes={GenAI.OPERATION_NAME: "embeddings"},
            )

    def test_a_missing_operation_names_the_span(self) -> None:
        with pytest.raises(ValidationError, match="mystery-0.*gen_ai.operation.name"):
            Span(span_id="mystery-0", name="?", start_time=T0, end_time=T0)

    @pytest.mark.parametrize(
        ("operation", "missing"),
        [
            (Operation.EXECUTE_TOOL, GenAI.TOOL_NAME),
            (Operation.INVOKE_AGENT, GenAI.AGENT_NAME),
            (Operation.CHAT, GenAI.REQUEST_MODEL),
        ],
    )
    def test_a_required_attribute_is_named_when_absent(
        self, operation: Operation, missing: str
    ) -> None:
        attributes = span("s", operation).attributes
        del attributes[missing]
        with pytest.raises(ValidationError, match=f"s.*{missing}"):
            Span(span_id="s", name="s", start_time=T0, end_time=T0, attributes=attributes)

    def test_a_span_cannot_end_before_it_starts(self) -> None:
        with pytest.raises(ValidationError, match="backwards-0"):
            Span(
                span_id="backwards-0",
                name="chat",
                start_time=T0 + timedelta(seconds=1),
                end_time=T0,
                attributes=span("x", Operation.CHAT).attributes,
            )


class TestTrace:
    def test_of_selects_by_operation(self, three_span_trace: Trace) -> None:
        assert [s.span_id for s in three_span_trace.of(Operation.CHAT)] == ["chat-0"]

    def test_of_returns_spans_in_start_order(self) -> None:
        t = trace(
            span("root", Operation.INVOKE_AGENT, parent=None, duration=9.0),
            span("chat-1", Operation.CHAT, offset=3.0),
            span("chat-0", Operation.CHAT, offset=1.0),
        )
        assert [s.span_id for s in t.of(Operation.CHAT)] == ["chat-0", "chat-1"]

    def test_root_is_the_parentless_span(self, three_span_trace: Trace) -> None:
        assert three_span_trace.root.span_id == "root"

    def test_total_output_tokens_sums_the_chat_spans(self) -> None:
        t = trace(
            span("root", Operation.INVOKE_AGENT, parent=None),
            span("chat-0", Operation.CHAT, **{GenAI.USAGE_OUTPUT_TOKENS: 120}),
            span("chat-1", Operation.CHAT, offset=2.0, **{GenAI.USAGE_OUTPUT_TOKENS: 80}),
            span("tool-0", Operation.EXECUTE_TOOL, offset=3.0),
        )
        assert t.total_output_tokens == 200

    def test_total_output_tokens_is_zero_when_no_span_reports_usage(
        self, three_span_trace: Trace
    ) -> None:
        assert three_span_trace.total_output_tokens == 0

    def test_a_trace_needs_exactly_one_root(self) -> None:
        with pytest.raises(ValidationError, match="exactly one root"):
            trace(
                span("root-a", Operation.INVOKE_AGENT, parent=None),
                span("root-b", Operation.INVOKE_AGENT, parent=None, offset=5.0),
            )

    def test_a_dangling_parent_is_rejected_by_span_id(self) -> None:
        with pytest.raises(ValidationError, match="orphan-0.*ghost"):
            trace(
                span("root", Operation.INVOKE_AGENT, parent=None),
                span("orphan-0", Operation.CHAT, parent="ghost"),
            )

    def test_duplicate_span_ids_are_rejected(self) -> None:
        with pytest.raises(ValidationError, match="chat-0"):
            trace(
                span("root", Operation.INVOKE_AGENT, parent=None),
                span("chat-0", Operation.CHAT),
                span("chat-0", Operation.CHAT, offset=2.0),
            )

    def test_an_empty_trace_is_rejected(self) -> None:
        with pytest.raises(ValidationError, match="at least one span"):
            trace()


class TestContent:
    def test_messages_live_in_span_events_not_attributes(self) -> None:
        chat = span("chat-0", Operation.CHAT)
        chat.events.append(
            SpanEvent(
                name="gen_ai.client.inference.operation.details",
                attributes={
                    GenAI.INPUT_MESSAGES: [{"role": "user", "content": "refund please"}],
                    GenAI.OUTPUT_MESSAGES: [{"role": "assistant", "content": "I cannot"}],
                },
            )
        )
        assert chat.output_messages == [{"role": "assistant", "content": "I cannot"}]
        assert chat.input_messages[0]["content"] == "refund please"

    def test_a_chat_span_with_no_content_event_reports_no_messages(self) -> None:
        assert span("chat-0", Operation.CHAT).output_messages == []

    def test_final_response_is_the_last_assistant_message_of_the_last_chat(self) -> None:
        early, late = span("chat-0", Operation.CHAT), span("chat-1", Operation.CHAT, offset=2.0)
        for s, text in ((early, "let me look"), (late, "I cannot change it")):
            s.events.append(
                SpanEvent(
                    name="gen_ai.client.inference.operation.details",
                    attributes={GenAI.OUTPUT_MESSAGES: [{"role": "assistant", "content": text}]},
                )
            )
        t = trace(span("root", Operation.INVOKE_AGENT, parent=None), late, early)
        assert t.final_response == "I cannot change it"
