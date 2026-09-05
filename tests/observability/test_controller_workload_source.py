from __future__ import annotations

import io
import json
import urllib.error
from datetime import datetime, timezone

import pytest

from anvil_serving.observability.probes.controller_workloads import (
    read_controller_workloads,
)
from anvil_serving.observability.workload_collection import build_node_workloads
from anvil_serving.observability.workloads import (
    ResultStatus,
    SourceResult,
    Truncation,
    WorkloadError,
    WorkloadErrorCode,
    WorkloadOwner,
    WorkloadQuery,
    node_result_to_dict,
)


HOST = "node-a"
TOKEN = "0123456789abcdef"
NOW = datetime(2026, 9, 5, 12, 0, tzinfo=timezone.utc)
ENDPOINT = "http://127.0.0.1:8765"


class _Response:
    def __init__(self, body: bytes, *, close_raises: bool = False) -> None:
        self.body = body
        self.close_raises = close_raises
        self.closed = False

    def read(self, amount: int = -1) -> bytes:
        assert amount >= len(self.body)
        return self.body

    def close(self) -> None:
        self.closed = True
        if self.close_raises:
            raise RuntimeError("private-close-detail")


def _node():
    source = SourceResult(
        WorkloadOwner.CONTROLLER, ResultStatus.COMPLETE, NOW, (), Truncation(0, 0)
    )
    return build_node_workloads(HOST, WorkloadQuery(), NOW, {source.owner: source})


def _payload(*, node=None, extra: bool = False) -> bytes:
    value = {"ok": True, "data": node_result_to_dict(_node() if node is None else node)}
    if extra:
        value["private"] = "private-response-marker"
    return json.dumps(value, separators=(",", ":")).encode("ascii")


def _read(*, responses=None, **kwargs):
    requests: list[tuple[object, float]] = []
    values = list(responses or [_Response(b'{"node":"node-a"}'), _Response(_payload())])

    def opener(request, timeout):
        requests.append((request, timeout))
        value = values.pop(0)
        if isinstance(value, BaseException):
            raise value
        return value

    monotonic = kwargs.pop("monotonic", lambda: 0.0)
    result = read_controller_workloads(
        ENDPOINT,
        "CONTROLLER_TOKEN",
        HOST,
        WorkloadQuery(),
        NOW,
        environment={"CONTROLLER_TOKEN": TOKEN},
        monotonic=monotonic,
        _open=opener,
        **kwargs,
    )
    return result, requests


def _controller_source(result):
    return next(source for source in result.sources if source.owner is WorkloadOwner.CONTROLLER)


def test_expected_node_transport_uses_health_then_exact_workload_operation() -> None:
    result, requests = _read()
    assert _controller_source(result).status is ResultStatus.COMPLETE
    assert [request.full_url for request, _ in requests] == [
        ENDPOINT + "/health", ENDPOINT + "/tools/call"
    ]
    assert all(request.get_header("Authorization") == "Bearer " + TOKEN for request, _ in requests)
    assert json.loads(requests[1][0].data) == {
        "name": "node_workloads",
        "arguments": {
            "owner": None, "kind": None, "state": None, "host": None,
            "active_only": False, "recent_seconds": 3600, "limit": 200,
        },
    }
    assert requests[1][0].get_header("X-anvil-idempotency-key") is None


def test_wrong_health_identity_stops_before_workload_post() -> None:
    result, requests = _read(responses=[_Response(b'{"node":"other"}')])
    assert _controller_source(result).error is WorkloadErrorCode.UNAVAILABLE
    assert len(requests) == 1


def test_excluded_query_returns_complete_without_environment_clock_or_network() -> None:
    class _Trap(dict):
        def get(self, key, default=None):
            raise AssertionError(key)

    result = read_controller_workloads(
        ENDPOINT, "CONTROLLER_TOKEN", HOST, WorkloadQuery(host="other"), NOW,
        environment=_Trap(), monotonic=lambda: (_ for _ in ()).throw(AssertionError("clock")),
        _open=lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("open")),
    )
    assert result.status is ResultStatus.COMPLETE
    assert all(source.status is ResultStatus.COMPLETE for source in result.sources)


@pytest.mark.parametrize("endpoint,environment", [
    ("http://example.invalid:8765", {"CONTROLLER_TOKEN": TOKEN}),
    (ENDPOINT + " \n", {"CONTROLLER_TOKEN": TOKEN}),
    (ENDPOINT, {}),
])
def test_bad_configuration_never_opens(endpoint, environment) -> None:
    result = read_controller_workloads(
        endpoint, "CONTROLLER_TOKEN", HOST, WorkloadQuery(), NOW,
        environment=environment, monotonic=lambda: 0.0,
        _open=lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("open")),
    )
    assert result.status is ResultStatus.UNAVAILABLE
    assert all(source.error is WorkloadErrorCode.UNAVAILABLE for source in result.sources)


def test_invalid_canonical_arguments_raise_before_open() -> None:
    with pytest.raises(WorkloadError):
        read_controller_workloads(
            ENDPOINT, "CONTROLLER_TOKEN", HOST, object(), NOW,
            environment={"CONTROLLER_TOKEN": TOKEN}, monotonic=lambda: 0.0,
            _open=lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("open")),
        )


def test_shared_deadline_prevents_post_after_health() -> None:
    clocks = iter((0.0, 0.0, 0.0, 0.0, 0.0, 2.0))
    result, requests = _read(
        monotonic=lambda: next(clocks),
        responses=[_Response(b'{"node":"node-a"}')],
    )
    assert _controller_source(result).error is WorkloadErrorCode.UNAVAILABLE
    assert len(requests) == 1


def test_shared_deadline_shrinks_post_timeout_after_health() -> None:
    clocks = iter((0.0, 0.0, 0.0, 0.0, 0.0, 1.5, 1.5, 1.5, 1.5, 1.5))
    result, requests = _read(monotonic=lambda: next(clocks))
    assert _controller_source(result).status is ResultStatus.COMPLETE
    assert requests[0][1] == 2.0
    assert requests[1][1] == 0.5


@pytest.mark.parametrize("payload,code", [
    (_payload(extra=True), WorkloadErrorCode.INVALID),
    (json.dumps({"ok": True, "data": {"schema": "unsupported"}}).encode("ascii"), WorkloadErrorCode.UNSUPPORTED),
])
def test_closed_application_envelope_is_projected_to_fixed_error(payload, code) -> None:
    result, _ = _read(responses=[_Response(b'{"node":"node-a"}'), _Response(payload)])
    assert _controller_source(result).error is code


def test_http_error_and_close_failure_do_not_expose_raw_detail() -> None:
    error = urllib.error.HTTPError(
        ENDPOINT + "/health", 503, "private-error", {}, io.BytesIO(b"private-body")
    )
    result, _ = _read(responses=[error])
    assert _controller_source(result).error is WorkloadErrorCode.UNAVAILABLE
    assert "private" not in json.dumps(node_result_to_dict(result))

    result, _ = _read(
        responses=[_Response(b'{"node":"node-a"}'), _Response(_payload(), close_raises=True)]
    )
    assert _controller_source(result).error is WorkloadErrorCode.UNAVAILABLE


def test_wrong_host_and_future_wire_are_fixed_without_raw_payload() -> None:
    wrong = _node()
    object.__setattr__(wrong, "host", "other")
    result, _ = _read(responses=[_Response(b'{"node":"node-a"}'), _Response(_payload(node=wrong))])
    assert _controller_source(result).error is WorkloadErrorCode.INVALID

    future = _node()
    object.__setattr__(future, "collection_timestamp", NOW.replace(second=31))
    result, _ = _read(responses=[_Response(b'{"node":"node-a"}'), _Response(_payload(node=future))])
    assert _controller_source(result).error is WorkloadErrorCode.FUTURE
