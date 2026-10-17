from __future__ import annotations

import pytest

from anvil_serving.benchmarking.jobs import BenchmarkJobError
from anvil_serving.benchmarking.swe_logs import (
    collect_server_log_metadata,
    validate_server_log_selector,
)
from anvil_serving.service_runtime.contracts import ServiceError


SELECTOR = {"service": "candidate", "tail": 3}
STARTED = "2026-10-16T12:00:00Z"
ENDED = "2026-10-16T12:01:00Z"


def test_selector_requires_exact_managed_service_and_bounded_tail():
    assert validate_server_log_selector(SELECTOR) == SELECTOR
    assert validate_server_log_selector({"recipe_container": "candidate", "tail": 3}) == {
        "recipe_container": "candidate", "tail": 3,
    }
    for value in ({"service": "candidate"}, {"service": "candidate", "tail": 0}, {"service": "bad/id", "tail": 1}):
        with pytest.raises(BenchmarkJobError, match="server_log"):
            validate_server_log_selector(value)


def test_recipe_selector_uses_managed_recipe_collector_without_service_inference():
    captured = {}
    result = collect_server_log_metadata(
        {"recipe_container": "candidate", "tail": 3}, STARTED, ENDED,
        recipe_operation=lambda *args, **kwargs: captured.update(args=args, kwargs=kwargs) or {
            "lines": ['2026-10-16T12:00:02Z {"event":"admission"}'],
            "observed_runtime": {"container_id": "a" * 64},
            "bounds": {"tail_lines": 3, "capture_byte_limit": 1024, "truncation": "unknown"},
        },
    )
    assert captured == {
        "args": ("candidate",),
        "kwargs": {"tail": 3, "since": STARTED, "until": ENDED, "timestamps": True},
    }
    assert result["selector"] == {"recipe_container": "candidate", "tail": 3}


def test_collector_keeps_only_allowed_events_timestamps_request_ids_and_status():
    captured = {}

    def operation(*args, **kwargs):
        captured["args"], captured["kwargs"] = args, kwargs
        return {
            "lines": [
                '2026-10-16T12:00:02Z {"event":"cancellation_acknowledged","request_id":"req-1","status":499,"body":"secret"}',
                '2026-10-16T12:00:03Z {"event":"unrecognized","request_id":"req-2","status":200}',
            ],
            "observed_runtime": {"manager": "docker", "identity": "a" * 64, "image_id": "sha256:" + "b" * 64},
            "bounds": {"tail_lines": 3, "capture_byte_limit": 1024, "truncation": "unknown"},
        }

    result = collect_server_log_metadata(SELECTOR, STARTED, ENDED, operation=operation)
    assert captured["args"] == ("logs", "candidate")
    assert captured["kwargs"] == {"tail": 3, "since": STARTED, "until": ENDED, "timestamps": True, "dry_run": True}
    assert result["events"] == [{"event_type": "cancellation_acknowledged", "request_id": "req-1", "status": 499, "timestamp": "2026-10-16T12:00:02Z", "timestamp_source": "docker_prefix"}]
    assert result["bounds"]["unknown_lines"] == 1
    assert "secret" not in repr(result)
    assert result["observed_timestamps"]["completeness"] == "not_inferred_from_observed_timestamps"


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


def test_collector_returns_typed_unavailable_error_without_raw_output():
    result = collect_server_log_metadata(
        SELECTOR, STARTED, ENDED,
        operation=lambda *_args, **_kwargs: (_ for _ in ()).throw(ServiceError("timeout", "raw secret")),
    )
    assert result == {
        "status": "unavailable", "selector": SELECTOR,
        "requested_interval": {"started_at": STARTED, "ended_at": ENDED},
        "collection_errors": [{"code": "timeout"}],
    }


def test_metadata_event_limit_is_explicit_truncation():
    lines = ['2026-10-16T12:00:02Z {"event":"admission"}'] * 201
    result = collect_server_log_metadata(
        {"service": "candidate", "tail": 1000}, STARTED, ENDED,
        operation=lambda *_args, **_kwargs: {
            "lines": lines, "observed_runtime": {},
            "bounds": {"tail_lines": 1000, "capture_byte_limit": 1024, "truncation": "not_truncated"},
        },
    )
    assert len(result["events"]) == 200
    assert result["status"] == "partial"
    assert result["bounds"]["truncation"] == "metadata_event_limit"


def test_collector_orders_cross_stream_timestamped_events_without_claiming_clock_sync():
    result = collect_server_log_metadata(
        SELECTOR, STARTED, ENDED,
        operation=lambda *_args, **_kwargs: {
            "lines": [
                '2026-10-16T12:00:02Z {"event":"done"}',
                '2026-10-16T12:00:01Z {"event":"admission"}',
            ], "observed_runtime": {}, "bounds": {"tail_lines": 3, "truncation": "not_truncated"},
        },
    )
    assert [event["event_type"] for event in result["events"]] == ["admission", "done"]
    assert result["observed_timestamps"]["order"] == "timestamp_ordered; ties and clock synchronization unknown"


def test_collector_uses_configured_bounds_not_untrusted_response_values():
    result = collect_server_log_metadata(
        SELECTOR, STARTED, ENDED,
        operation=lambda *_args, **_kwargs: {
            "lines": [], "observed_runtime": {"manager": []},
            "bounds": {"tail_lines": "private prompt", "capture_byte_limit": True, "truncation": []},
        },
    )
    assert result["bounds"]["line_limit"] == 3
    assert result["bounds"]["byte_limit"] == 1024 * 1024
