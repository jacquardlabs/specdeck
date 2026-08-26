import json
from pathlib import Path

import pytest

from specdeck.trace import GenAI, Operation
from specdeck.traceio import TraceError, dump_trace, load_trace

SEMCONV = "semantic-conventions-genai@1.38.0"

CANONICAL = {
    "semconv": SEMCONV,
    "spans": [
        {
            "span_id": "root",
            "parent_span_id": None,
            "name": "invoke_agent airline-support",
            "start_time": "2026-08-16T12:00:00Z",
            "end_time": "2026-08-16T12:00:12Z",
            "attributes": {
                GenAI.OPERATION_NAME: "invoke_agent",
                GenAI.AGENT_NAME: "airline-support",
            },
        },
        {
            "span_id": "tool-0",
            "parent_span_id": "root",
            "name": "execute_tool get_reservation_details",
            "start_time": "2026-08-16T12:00:01Z",
            "end_time": "2026-08-16T12:00:02Z",
            "attributes": {
                GenAI.OPERATION_NAME: "execute_tool",
                GenAI.TOOL_NAME: "get_reservation_details",
            },
        },
    ],
}


def otlp_span(span_id: str, parent: str, name: str, attributes: list[dict]) -> dict:
    return {
        "traceId": "0af7651916cd43dd8448eb211c80319c",
        "spanId": span_id,
        "parentSpanId": parent,
        "name": name,
        "startTimeUnixNano": "1786968000000000000",
        "endTimeUnixNano": "1786968002500000000",
        "attributes": attributes,
    }


OTLP = {
    "resourceSpans": [
        {
            "scopeSpans": [
                {
                    "scope": {"name": "opentelemetry.instrumentation.genai", "version": SEMCONV},
                    "spans": [
                        otlp_span(
                            "1111",
                            "",
                            "invoke_agent airline-support",
                            [
                                {
                                    "key": GenAI.OPERATION_NAME,
                                    "value": {"stringValue": "invoke_agent"},
                                },
                                {"key": GenAI.AGENT_NAME, "value": {"stringValue": "support"}},
                            ],
                        ),
                        otlp_span(
                            "2222",
                            "1111",
                            "chat claude-sonnet-5",
                            [
                                {"key": GenAI.OPERATION_NAME, "value": {"stringValue": "chat"}},
                                {
                                    "key": GenAI.PROVIDER_NAME,
                                    "value": {"stringValue": "anthropic"},
                                },
                                {
                                    "key": GenAI.REQUEST_MODEL,
                                    "value": {"stringValue": "claude-sonnet-5"},
                                },
                                {
                                    "key": GenAI.USAGE_OUTPUT_TOKENS,
                                    "value": {"intValue": "142"},
                                },
                                {
                                    "key": GenAI.RESPONSE_FINISH_REASONS,
                                    "value": {"arrayValue": {"values": [{"stringValue": "stop"}]}},
                                },
                            ],
                        ),
                    ],
                }
            ]
        }
    ]
}


def write(tmp_path: Path, name: str, payload: dict) -> Path:
    path = tmp_path / name
    path.write_text(json.dumps(payload))
    return path


class TestCanonical:
    def test_reads_the_span_tree(self, tmp_path: Path) -> None:
        trace = load_trace(write(tmp_path, "run.json", CANONICAL))
        assert trace.root.span_id == "root"
        assert [s.attributes[GenAI.TOOL_NAME] for s in trace.of(Operation.EXECUTE_TOOL)] == [
            "get_reservation_details"
        ]

    def test_carries_the_semconv_version_for_the_lockfile(self, tmp_path: Path) -> None:
        assert load_trace(write(tmp_path, "run.json", CANONICAL)).semconv == SEMCONV

    def test_a_schema_violation_names_the_file_and_the_span(self, tmp_path: Path) -> None:
        broken = json.loads(json.dumps(CANONICAL))
        del broken["spans"][1]["attributes"][GenAI.TOOL_NAME]
        with pytest.raises(TraceError, match=r"run.json.*tool-0"):
            load_trace(write(tmp_path, "run.json", broken))


class TestOtlp:
    def test_an_otel_emitting_agent_needs_no_adapter(self, tmp_path: Path) -> None:
        trace = load_trace(write(tmp_path, "otlp.json", OTLP))
        assert trace.root.span_id == "1111"
        assert [s.operation for s in trace.ordered] == [Operation.INVOKE_AGENT, Operation.CHAT]

    def test_an_empty_parent_span_id_becomes_the_root(self, tmp_path: Path) -> None:
        assert load_trace(write(tmp_path, "otlp.json", OTLP)).root.parent_span_id is None

    def test_unix_nanos_become_timestamps(self, tmp_path: Path) -> None:
        assert load_trace(write(tmp_path, "otlp.json", OTLP)).root.duration_s == 2.5

    def test_int_values_decode_as_numbers_not_strings(self, tmp_path: Path) -> None:
        assert load_trace(write(tmp_path, "otlp.json", OTLP)).total_output_tokens == 142

    def test_array_values_decode_as_lists(self, tmp_path: Path) -> None:
        chat = load_trace(write(tmp_path, "otlp.json", OTLP)).of(Operation.CHAT)[0]
        assert chat.attributes[GenAI.RESPONSE_FINISH_REASONS] == ["stop"]

    def test_the_semconv_comes_from_the_instrumentation_scope(self, tmp_path: Path) -> None:
        assert load_trace(write(tmp_path, "otlp.json", OTLP)).semconv == SEMCONV

    def test_an_unversioned_scope_needs_the_semconv_supplied(self, tmp_path: Path) -> None:
        payload = json.loads(json.dumps(OTLP))
        del payload["resourceSpans"][0]["scopeSpans"][0]["scope"]["version"]
        path = write(tmp_path, "otlp.json", payload)
        with pytest.raises(TraceError, match=r"semconv"):
            load_trace(path)
        assert load_trace(path, semconv=SEMCONV).semconv == SEMCONV

    def test_span_events_carry_content_through(self, tmp_path: Path) -> None:
        payload = json.loads(json.dumps(OTLP))
        payload["resourceSpans"][0]["scopeSpans"][0]["spans"][1]["events"] = [
            {
                "name": "gen_ai.client.inference.operation.details",
                "attributes": [
                    {
                        "key": GenAI.OUTPUT_MESSAGES,
                        "value": {
                            "arrayValue": {
                                "values": [
                                    {
                                        "kvlistValue": {
                                            "values": [
                                                {
                                                    "key": "role",
                                                    "value": {"stringValue": "assistant"},
                                                },
                                                {"key": "content", "value": {"stringValue": "no"}},
                                            ]
                                        }
                                    }
                                ]
                            }
                        },
                    }
                ],
            }
        ]
        assert load_trace(write(tmp_path, "otlp.json", payload)).final_response == "no"


class TestErrors:
    def test_a_missing_file_names_the_path(self, tmp_path: Path) -> None:
        with pytest.raises(TraceError, match=r"absent.json"):
            load_trace(tmp_path / "absent.json")

    def test_malformed_json_names_the_file(self, tmp_path: Path) -> None:
        path = tmp_path / "run.json"
        path.write_text("{not json")
        with pytest.raises(TraceError, match=r"run.json"):
            load_trace(path)

    def test_an_unrecognised_shape_says_what_it_accepts(self, tmp_path: Path) -> None:
        with pytest.raises(TraceError, match=r"resourceSpans"):
            load_trace(write(tmp_path, "run.json", {"events": []}))


class TestDumpTrace:
    """#112. `load_trace(dump_trace(t))` is the property that makes a saved run a fixture."""

    def _trace(self):
        return load_trace(
            Path(
                "examples/payable/tutorial/traces-before/payment-ceiling-denied-at-the-rail.2.otlp.json"
            )
        )

    def _round_trip(self, trace, tmp_path: Path):
        out = tmp_path / "rt.otlp.json"
        out.write_text(json.dumps(dump_trace(trace), indent=2))
        return load_trace(out)

    def test_a_trace_survives_the_round_trip_intact(self, tmp_path: Path) -> None:
        one = self._trace()
        back = self._round_trip(one, tmp_path)
        assert back.semconv == one.semconv
        assert [s.span_id for s in back.spans] == [s.span_id for s in one.spans]
        assert [s.attributes for s in back.spans] == [s.attributes for s in one.spans]
        assert [s.start_time for s in back.spans] == [s.start_time for s in one.spans]

    def test_the_denial_convention_survives(self, tmp_path: Path) -> None:
        """A reserved attribute that did not round trip would make a saved denial an
        ordinary execution, which is the misreading the attribute exists to prevent."""
        back = self._round_trip(self._trace(), tmp_path)
        denied = [s for s in back.spans if s.denied_tool]
        assert [s.denied_tool for s in denied] == ["pay_invoice"]
        assert all(s.executed_tool is None for s in denied)

    def test_it_writes_otlp_rather_than_specdecks_own_shape(self, tmp_path: Path) -> None:
        """A file only this project can read would let "an agent already emitting OTel
        needs no adapter" go untested by the artefacts meant to demonstrate it."""
        raw = dump_trace(self._trace())
        assert "resourceSpans" in raw
        spans = raw["resourceSpans"][0]["scopeSpans"][0]["spans"]
        assert all("startTimeUnixNano" in s for s in spans)
        assert all(isinstance(a["value"], dict) for s in spans for a in s["attributes"])

    def test_the_root_has_no_parent_rather_than_an_empty_one(self, tmp_path: Path) -> None:
        """OTLP marks a root by absence; a parent id of "" is a different claim."""
        spans = dump_trace(self._trace())["resourceSpans"][0]["scopeSpans"][0]["spans"]
        roots = [s for s in spans if "parentSpanId" not in s]
        assert len(roots) == 1

    def test_a_bool_does_not_round_trip_as_an_integer(self, tmp_path: Path) -> None:
        """`bool` is a subclass of `int`, so the writer checks it first or True becomes 1."""
        one = self._trace()
        one.spans[0].attributes["specdeck.flag"] = True
        back = self._round_trip(one, tmp_path)
        assert back.spans[0].attributes["specdeck.flag"] is True

    def test_a_service_name_reaches_the_resource(self, tmp_path: Path) -> None:
        raw = dump_trace(self._trace(), service_name="meridian")
        attrs = raw["resourceSpans"][0]["resource"]["attributes"]
        assert {"key": "service.name", "value": {"stringValue": "meridian"}} in attrs
