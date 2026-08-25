"""The ported classifiers, case for case against cctx's own tests.

Each case names the cctx test it comes from. Where the assertion had to move — cctx counts
turns, specdeck counts span ordinals, and one tool call is two turns and one span — the
comment says what the number became and why.

NOT PORTED: cctx's test_compaction_resets_stale_count. It exercises the guard that skips a
candidate when a compaction event follows it, and the trace schema has three operations,
no system span and no compaction concept, so the set that guard tests over is provably
empty here. The guard is deleted in waste.py rather than left as unreachable code.
"""

from __future__ import annotations

import json

from specdeck.trace import ERROR_TYPE, GenAI, Operation, SpanEvent, Trace
from specdeck.waste import (
    N_STALE,
    STALE_HIGH_THRESHOLD,
    T_SIZE,
    Kind,
    Level,
    _estimate_tokens,
    classify,
)

from .test_trace import span, trace

# 160 reps x 10 words x 1.3 = 2080 estimated tokens, over cctx's T_SIZE of 2000.
LARGE = ("The search results show many TODO items across the codebase. " * 160).strip()
LARGE_3GRAM = "search results show"


def tool(
    span_id: str,
    name: str,
    arguments: dict,
    result: str | None,
    *,
    offset: float,
    failed: bool = False,
) -> object:
    attributes: dict[str, object] = {
        GenAI.TOOL_NAME: name,
        GenAI.TOOL_CALL_ARGUMENTS: json.dumps(arguments),
    }
    if result is not None:
        attributes[GenAI.TOOL_CALL_RESULT] = result
    if failed:
        attributes[ERROR_TYPE] = "tool_error"
    return span(span_id, Operation.EXECUTE_TOOL, offset=offset, **attributes)


def chat(
    span_id: str,
    *,
    offset: float,
    text: str = "",
    input_tokens: int | None = None,
    output_tokens: int | None = None,
) -> object:
    attributes: dict[str, object] = {}
    if input_tokens is not None:
        attributes[GenAI.USAGE_INPUT_TOKENS] = input_tokens
    if output_tokens is not None:
        attributes[GenAI.USAGE_OUTPUT_TOKENS] = output_tokens
    one = span(span_id, Operation.CHAT, offset=offset, **attributes)
    one.events.append(
        SpanEvent(
            name="details",
            attributes={GenAI.OUTPUT_MESSAGES: [{"role": "assistant", "content": text}]},
        )
    )
    return one


def root(*, duration: float = 60.0) -> object:
    return span("root", Operation.INVOKE_AGENT, parent=None, duration=duration)


def attempts(*calls: tuple[str, dict, str | None, bool], tokens: int | None = None) -> Trace:
    """One chat span per tool call, so ordinals read root, chat, tool, chat, tool…"""
    spans: list = [root()]
    for index, (name, arguments, result, failed) in enumerate(calls):
        spans.append(
            chat(f"c{index}", offset=1.0 + 2 * index, input_tokens=tokens, output_tokens=tokens)
        )
        spans.append(
            tool(f"t{index}", name, arguments, result, offset=2.0 + 2 * index, failed=failed)
        )
    return trace(*spans)


def edits(n_fails: int, **kwargs) -> Trace:
    """cctx's _edit_trace_with_retry: Edit(src/foo.py) fails n times with no fix between."""
    return attempts(
        *[("Edit", {"file_path": "src/foo.py"}, "Error: file not found", True)] * n_fails, **kwargs
    )


class TestRetryLoop:
    def test_no_retry_on_a_single_call(self) -> None:
        """Ports test_no_retry_on_single_call."""
        assert classify(edits(1)) == []

    def test_two_identical_failures_are_a_loop(self) -> None:
        """Ports test_detects_retry_loop_two_failures."""
        findings = classify(edits(2))
        assert len(findings) == 1
        finding = findings[0]
        assert finding.kind is Kind.RETRY_LOOP
        assert finding.confidence is Level.HIGH
        assert finding.severity is Level.MEDIUM
        # cctx asserts turn 4, the assistant turn of the second failing call. Here the
        # call is one span: root, chat, tool, chat, tool — the second failure is span 5.
        assert finding.first_span == 5
        assert finding.evidence["loop_length"] == 2

    def test_four_failures_raise_the_severity(self) -> None:
        """Ports test_severity_high_at_four_failures."""
        assert classify(edits(4))[0].severity is Level.HIGH

    def test_an_intervening_success_is_not_a_loop(self) -> None:
        """Ports test_no_retry_when_success_intervenes."""
        one = attempts(
            ("Edit", {"file_path": "a.py"}, "Error: oops", True),
            ("Edit", {"file_path": "a.py"}, "ok", False),
            ("Edit", {"file_path": "a.py"}, "Error: oops", True),
        )
        assert classify(one) == []

    def test_bash_calls_are_keyed_on_their_command(self) -> None:
        """Ports test_bash_key_uses_command."""
        one = attempts(*[("Bash", {"command": "ls -la"}, "Error: permission denied", True)] * 2)
        findings = classify(one)
        assert len(findings) == 1
        assert findings[0].kind is Kind.RETRY_LOOP

    def test_different_keys_are_not_a_loop(self) -> None:
        """Ports test_different_keys_not_a_loop."""
        one = attempts(
            ("Edit", {"file_path": "a.py"}, "Error: oops", True),
            ("Edit", {"file_path": "b.py"}, "Error: oops", True),
        )
        assert classify(one) == []

    def test_a_trace_with_no_tool_call_finds_nothing(self) -> None:
        """Ports test_empty_trace_returns_empty — a Trace needs at least one span."""
        assert classify(trace(root())) == []

    def test_the_summary_names_the_tool_and_the_span_range(self) -> None:
        """Ports test_summary_mentions_tool_and_turns."""
        summary = classify(edits(2))[0].summary
        assert "Edit" in summary
        assert "fail" in summary.lower()

    def test_an_error_prefix_is_a_failure_without_the_attribute(self) -> None:
        """Ports test_error_detected_by_error_prefix."""
        one = attempts(*[("Bash", {"command": "ls"}, "Error: permission denied", False)] * 2)
        assert classify(one)[0].kind is Kind.RETRY_LOOP

    def test_a_result_merely_containing_error_is_not_a_failure(self) -> None:
        """Ports test_substring_error_not_detected_without_flag — the exact-prefix rule."""
        one = attempts(*[("Bash", {"command": "ls"}, "Warning: encountered error: 42", False)] * 2)
        assert classify(one) == []

    def test_the_attribute_marks_a_failure_the_text_does_not(self) -> None:
        # specdeck-only: `error.type` is what `ToolResult.is_error` became. Nothing in
        # this repo writes it yet, so it is the half of `_failed` no real trace exercises.
        one = attempts(*[("Bash", {"command": "ls"}, "no such file", True)] * 2)
        assert classify(one)[0].kind is Kind.RETRY_LOOP

    def test_a_tool_span_with_no_recorded_result_is_not_read(self) -> None:
        # specdeck-only: cctx drops a tool_use whose tool_result never arrived, and a span
        # carrying no `gen_ai.tool.call.result` is the same unpaired call. Marked failed,
        # so the span is dropped for want of a result rather than for reading as a success.
        one = attempts(*[("Bash", {"command": "ls"}, None, True)] * 2)
        assert classify(one) == []

    def test_arguments_that_are_not_json_read_as_empty(self) -> None:
        # specdeck-only: the attribute is a string out of a user-supplied trace file.
        spans = [
            root(),
            span(
                "t0",
                Operation.EXECUTE_TOOL,
                offset=1.0,
                **{
                    GenAI.TOOL_NAME: "modify_reservation",
                    GenAI.TOOL_CALL_ARGUMENTS: "not json at all",
                    GenAI.TOOL_CALL_RESULT: "Error: nope",
                },
            ),
            span(
                "t1",
                Operation.EXECUTE_TOOL,
                offset=2.0,
                **{
                    GenAI.TOOL_NAME: "modify_reservation",
                    GenAI.TOOL_CALL_ARGUMENTS: "not json at all",
                    GenAI.TOOL_CALL_RESULT: "Error: nope",
                },
            ),
        ]
        # Both calls key on the same empty argument set, so they are the same call.
        assert classify(trace(*spans))[0].kind is Kind.RETRY_LOOP


class TestRetryWaste:
    def test_it_is_none_when_no_chat_span_reported_usage(self) -> None:
        """Ports test_cost_usd_is_none: the classifier never invents the size."""
        assert classify(edits(2))[0].waste_tokens is None

    def test_it_is_the_issuing_requests_own_tokens(self) -> None:
        # The repeat attempt is span 5; the chat that issued it is span 4, at 30 in and
        # 30 out. The first attempt was legitimate and is not counted.
        assert classify(edits(2, tokens=30))[0].waste_tokens == 60

    def test_four_failures_charge_three_repeats(self) -> None:
        assert classify(edits(4, tokens=30))[0].waste_tokens == 180

    def test_two_calls_from_one_request_charge_that_request_once(self) -> None:
        # specdeck-only: cctx's waste_turns hold the issuing assistant turn, and it dedupes
        # them before pricing, so a turn issuing two parallel tool calls is one request.
        # Here the ordinals are tool spans — `loop._Builder.record` emits this shape — so
        # the same dedup has to land on the chat they resolve to.
        one = trace(
            root(),
            chat("c0", offset=1.0, input_tokens=1000, output_tokens=100),
            tool("t0", "Edit", {"file_path": "a.py"}, "Error: nope", offset=2.0, failed=True),
            tool("t1", "Edit", {"file_path": "a.py"}, "Error: nope", offset=2.5, failed=True),
            chat("c1", offset=3.0, input_tokens=2000, output_tokens=200),
            tool("t2", "Edit", {"file_path": "a.py"}, "Error: nope", offset=4.0, failed=True),
            tool("t3", "Edit", {"file_path": "a.py"}, "Error: nope", offset=4.5, failed=True),
        )
        finding = classify(one)[0]
        assert finding.evidence["waste_spans"] == [4, 6, 7]
        # c0 once at 1,100 and c1 once at 2,200 — not c1 twice for its two tool spans.
        assert finding.waste_tokens == 3300


class TestStaleContext:
    def silent(self, n_silent: int = 6, content: str = LARGE) -> Trace:
        """cctx's _stale_trace: a large result, then n silent chat-and-tool pairs."""
        spans: list = [
            root(duration=float(4 + 2 * n_silent)),
            chat("c0", offset=1.0),
            tool("t0", "Bash", {"command": "grep -r TODO ."}, content, offset=2.0),
        ]
        for index in range(n_silent):
            spans.append(chat(f"cs{index}", offset=3.0 + 2 * index, text="unrelated work here"))
            spans.append(
                tool(
                    f"ts{index}", "Read", {"file_path": "other.py"}, "some", offset=4.0 + 2 * index
                )
            )
        return trace(*spans)

    def trailing(self, n_after: int, content: str = LARGE) -> Trace:
        """A large result, then `n_after` silent spans alternating chat and tool.

        `silent` moves in pairs, so it can only land on an even `spans_stale`. The N_STALE
        boundary is odd, and nothing tests a threshold it cannot sit on.
        """
        spans: list = [
            root(duration=float(4 + n_after)),
            chat("c0", offset=1.0),
            tool("t0", "Bash", {"command": "grep -r TODO ."}, content, offset=2.0),
        ]
        for index in range(n_after):
            offset = 3.0 + index
            spans.append(
                chat(f"s{index}", offset=offset, text="unrelated work here")
                if index % 2 == 0
                else tool(f"s{index}", "Read", {"file_path": "other.py"}, "some", offset=offset)
            )
        return trace(*spans)

    def test_a_trace_with_no_tool_result_finds_nothing(self) -> None:
        """Ports test_empty_trace_returns_empty."""
        assert classify(trace(root())) == []

    def test_a_small_result_is_not_a_candidate(self) -> None:
        """Ports test_small_result_ignored."""
        one = self.silent(content="file1.py file2.py")
        assert classify(one) == []

    def test_a_referenced_result_is_not_stale(self) -> None:
        """Ports test_large_result_stays_referenced_no_finding."""
        spans = [
            root(),
            chat("c0", offset=1.0),
            tool("t0", "Bash", {"command": "grep -r TODO ."}, LARGE, offset=2.0),
            chat("c1", offset=3.0, text=f"Based on {LARGE_3GRAM}, I see several items."),
            chat("c2", offset=4.0, text=f"Continuing with {LARGE_3GRAM} analysis."),
        ]
        assert classify(trace(*spans)) == []

    def test_it_detects_a_large_unreferenced_result(self) -> None:
        """Ports test_detects_stale_large_result."""
        findings = [f for f in classify(self.silent()) if f.kind is Kind.STALE_CONTEXT]
        assert len(findings) == 1
        assert findings[0].evidence["total_token_turns"] > 0
        assert len(findings[0].evidence["stale_items"]) == 1

    def test_it_becomes_stale_n_spans_after_the_last_reference(self) -> None:
        """Ports test_first_turn_is_after_n_stale."""
        finding = classify(self.silent())[0]
        item = finding.evidence["stale_items"][0]
        assert finding.first_span == item["last_referenced_span"] + N_STALE

    def test_below_the_threshold_it_is_medium(self) -> None:
        """Ports test_confidence_medium_below_500k_token_turns."""
        finding = classify(self.silent())[0]
        assert finding.evidence["total_token_turns"] < STALE_HIGH_THRESHOLD
        assert finding.confidence is Level.MEDIUM
        assert finding.severity is Level.MEDIUM

    def test_above_the_threshold_it_is_high(self) -> None:
        """Ports test_confidence_high_above_500k_token_turns.

        Reachable only from a synthetic trace: `loop.py` caps a conversation at 12 turns,
        so no cell specdeck runs today can accumulate 500,000 token-turns.
        """
        finding = classify(self.silent(n_silent=200, content=(LARGE * 2)[:12000]))[0]
        assert finding.evidence["total_token_turns"] > STALE_HIGH_THRESHOLD
        assert finding.confidence is Level.HIGH
        assert finding.severity is Level.HIGH

    def test_exactly_n_stale_spans_later_is_not_yet_stale(self) -> None:
        # specdeck-only: cctx's boundary is `spans_stale <= N_STALE`, and every other
        # fixture here sits well past it, so nothing else would notice it moving.
        assert classify(self.trailing(N_STALE)) == []

    def test_one_span_further_on_is(self) -> None:
        assert classify(self.trailing(N_STALE + 1))[0].kind is Kind.STALE_CONTEXT

    def test_a_result_estimating_to_exactly_t_size_is_a_candidate(self) -> None:
        # specdeck-only: cctx's boundary is `>= T_SIZE`, and LARGE sits 80 tokens past it.
        # 1539 words x 1.3 truncates to exactly 2000; one word fewer falls short.
        assert _estimate_tokens(" ".join(["tok"] * 1539)) == T_SIZE
        assert _estimate_tokens(" ".join(["tok"] * 1538)) == T_SIZE - 1
        assert classify(self.silent(content=" ".join(["tok"] * 1539))) != []
        assert classify(self.silent(content=" ".join(["tok"] * 1538))) == []

    def test_exactly_the_high_threshold_is_not_yet_high(self) -> None:
        # specdeck-only: cctx's boundary is `> STALE_HIGH_THRESHOLD`. 2404 words estimate
        # to 3,125 tokens, carried by 160 silent requests — 500,000 token-turns on the nose.
        finding = classify(self.silent(n_silent=160, content=" ".join(["tok"] * 2404)))[0]
        assert finding.evidence["total_token_turns"] == STALE_HIGH_THRESHOLD
        assert finding.severity is Level.MEDIUM

    def test_only_the_requests_that_re_sent_it_are_billed(self) -> None:
        # specdeck-only: `billed_stale` is the correction cctx documents as keeping a
        # tool-heavy stretch from reading ~2x its real cost. Six silent chat-and-tool
        # pairs follow the result, so six requests carried it — not twelve spans.
        item = classify(self.silent())[0].evidence["stale_items"][0]
        assert item["spans_stale"] == 12
        assert item["token_turns"] == item["content_tokens"] * 6
        assert item["token_turns"] == 12_480

    def test_the_waste_is_the_token_turns_it_counted(self) -> None:
        """Ports test_cost_usd_is_none_from_classifier — the token half is not None here."""
        finding = classify(self.silent())[0]
        assert finding.waste_tokens == finding.evidence["total_token_turns"]
        # The figure itself, not just that two fields agree: it is what the report prints.
        assert finding.waste_tokens == 12_480

    def test_the_size_is_estimated_because_a_span_carries_no_count(self) -> None:
        # cctx reads `ToolResult.token_count` when it has one; a span never does, so the
        # estimate branch is the only one taken.
        assert _estimate_tokens("one two three four") == int(4 * 1.3)
        item = classify(self.silent())[0].evidence["stale_items"][0]
        assert item["content_tokens"] == _estimate_tokens(LARGE)


class TestBothClassifiers:
    def test_findings_come_back_earliest_first(self) -> None:
        spans = [
            root(),
            chat("c0", offset=1.0),
            tool("t0", "Bash", {"command": "grep -r TODO ."}, LARGE, offset=2.0),
        ]
        for index in range(6):
            spans.append(chat(f"cs{index}", offset=3.0 + 2 * index, text="unrelated"))
            spans.append(
                tool(
                    f"ts{index}",
                    "Edit",
                    {"file_path": "a.py"},
                    "Error: nope",
                    offset=4.0 + 2 * index,
                    failed=True,
                )
            )
        findings = classify(trace(*spans))
        assert {f.kind for f in findings} == {Kind.RETRY_LOOP, Kind.STALE_CONTEXT}
        assert [f.first_span for f in findings] == sorted(f.first_span for f in findings)
