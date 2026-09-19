import json

import pytest

from anvil_serving import benchmark as bm
from anvil_serving.benchmarking.requests import tool_argument_observations


@pytest.mark.parametrize("external", [False, True])
@pytest.mark.parametrize("raw", ['{"zip":98101}', '{"zip":'])
def test_failed_tool_retains_wire_arguments_without_passing(monkeypatch, tmp_path, external, raw):
    def response(*args, **kwargs):
        return {"latency_s": 0.1, "response": {"choices": [{
            "finish_reason": "tool_calls",
            "message": {"tool_calls": [{"type": "function", "function": {
                "name": "record_weather_zip", "arguments": raw,
            }}]},
        }]}}

    monkeypatch.setattr(bm, "post_chat", response)
    output = tmp_path / "evidence.json"
    args = ["--bakeoff", "--base-url", "http://127.0.0.1:39012/v1",
            "--model", "fixture", "--max-model-len", "8192",
            "--candidate-id", "fixture", "--config-id", "diagnostic",
            "--eval-repetitions", "3", "--evidence-out", str(output)]
    if external:
        suite = tmp_path / "suite.json"
        suite.write_text(json.dumps({"suite": "typed", "evals": [{
            "id": "zip", "prompt": "Call record_weather_zip with zip 98101.",
            "tools": [{"type": "function", "function": {
                "name": "record_weather_zip", "parameters": {
                    "type": "object", "properties": {"zip": {"type": "string"}},
                    "required": ["zip"],
                },
            }}],
            "expect_tool": {"name": "record_weather_zip", "required_args": {"zip": "98101"}},
        }]}), encoding="utf-8")
        args += ["--suite-file", str(suite)]
    else:
        args += ["--suite", "tool"]
    assert bm.main(args) == 1
    evidence = json.loads(output.read_text(encoding="utf-8"))
    check = evidence["suites"]["typed"]["checks"][0] if external else evidence["tool"]["checks"][0]
    assert check["pass_count"] == 0
    for attempt in check["attempts"]:
        observations = attempt["tool_call"]["observations"] if external else attempt["tool_argument_observations"]
        assert observations[0]["arguments_excerpt"] == raw
        assert observations[0]["arguments_wire_type"] == "str"
        assert not observations[0]["arguments_truncated"]


def test_tool_observation_capture_is_bounded_and_preserves_non_string_type():
    messages = [{"tool_calls": [{"function": {"name": "f", "arguments": "x" * 5000}}] * 20}]
    observations = tool_argument_observations(messages)
    assert len(observations) == 4
    assert all(len(item["arguments_excerpt"]) == 2048 for item in observations)
    assert all(item["arguments_truncated"] for item in observations)
    assert all(item["arguments_chars"] == 5000 for item in observations)
    item = tool_argument_observations([{"tool_calls": [{"function": {"arguments": {"zip": 98101}}}]}])[0]
    assert item["arguments_wire_type"] == "dict"
    assert item["arguments_excerpt"] is None
    assert item["arguments_chars"] is None


@pytest.mark.parametrize("raw", [
    '{"value":"line1\\r\\nline2"}',
    '{"value":"<tag>&</tag>; $(echo synthetic) C:\\\\fixture"}',
])
def test_wire_excerpt_preserves_escaping_without_interpretation(raw):
    item = tool_argument_observations([{"tool_calls": [{"function": {"arguments": raw}}]}])[0]
    assert item["arguments_excerpt"] == raw


def test_non_string_arguments_are_not_serialized():
    class NoSerialization(dict):
        def items(self):
            raise AssertionError("must not serialize malformed wire arguments")

        def __str__(self):
            raise AssertionError("must not stringify malformed tool name")

    value = NoSerialization({"large": "x" * 100_000})
    messages = [{"tool_calls": [None, {"function": None}, {"function": {
        "name": value, "arguments": value,
    }}]}]
    item = tool_argument_observations(messages)[0]
    assert item["arguments_excerpt"] is None
    assert item["arguments_wire_type"] == "NoSerialization"
    assert item["name"] is None


def test_malformed_entries_have_a_fixed_inspection_budget():
    class BoundedCalls(list):
        def __iter__(self):
            for _ in range(16):
                yield {}
            raise AssertionError("must stop after sixteen inspected calls")

    def messages():
        for _ in range(4):
            yield {"tool_calls": BoundedCalls()}
        raise AssertionError("must stop after four inspected messages")

    assert tool_argument_observations(messages()) == []
