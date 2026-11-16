from __future__ import annotations


from anvil_serving.benchmarking.swe_logs import (
    collect_server_log_metadata,
)


SELECTOR = {"service": "candidate", "tail": 3}
STARTED = "2026-10-16T12:00:00Z"
ENDED = "2026-10-16T12:01:00Z"


def test_collector_projects_known_ignis_jsonl_shape_without_body_or_attributes():
    result = collect_server_log_metadata(
        SELECTOR, STARTED, ENDED,
        operation=lambda *_args, **_kwargs: {
            "lines": [
                '{"timestamp":"2026-10-16T12:00:02+01:00","severity_text":"INFO",'
                '"event_name":"ignis.request.admitted","body":"private prompt",'
                '"attributes":{"request_id":42,"lane":3,"class":"private","error":"private"},'
                '"trace_id":"trace-private","span_id":"span-private","service.name":"candidate"}',
                '{"timestamp":"not-a-time","event_name":["unhashable"]}',
            ],
            "observed_runtime": {}, "bounds": {"tail_lines": 3, "truncation": "not_truncated"},
        },
    )
    assert result["events"] == [{
        "event_type": "admission", "engine_request_id": 42, "lane": 3,
        "timestamp": "2026-10-16T12:00:02+01:00", "timestamp_source": "engine_embedded",
    }]
    rendered = repr(result)
    assert "private prompt" not in rendered and "trace-private" not in rendered and "unhashable" not in rendered


def test_cancellation_acknowledgement_does_not_infer_lane_release():
    result = collect_server_log_metadata(
        SELECTOR, STARTED, ENDED,
        operation=lambda *_args, **_kwargs: {
            "lines": ['2026-10-16T12:00:02Z {"event":"cancel_acknowledged"}'],
            "observed_runtime": {}, "bounds": {"tail_lines": 3, "truncation": "not_truncated"},
        },
    )
    assert [event["event_type"] for event in result["events"]] == ["cancel_acknowledged"]
    assert all(event["event_type"] != "lane_release" for event in result["events"])
