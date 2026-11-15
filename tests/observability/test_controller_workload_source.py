from __future__ import annotations

import io
import json
import urllib.error
from datetime import datetime, timedelta, timezone

import pytest

from anvil_serving.observability.probes.controller_workloads import (
    read_controller_fleet_workloads,
    read_controller_workloads,
)
from anvil_serving.observability.fleet_workload_collection import build_fleet_workloads
from anvil_serving.observability.workload_collection import build_node_workloads
from anvil_serving.observability.workloads import (
    FleetResult,
    ObservationQuality,
    ResultStatus,
    SourceAuthority,
    SourceResult,
    Truncation,
    WorkloadError,
    WorkloadErrorCode,
    WorkloadKind,
    WorkloadOutcome,
    WorkloadOwner,
    WorkloadPhase,
    WorkloadQuery,
    WorkloadRecord,
    WorkloadState,
    fleet_result_to_dict,
    node_result_to_dict,
    workload_id,
)


HOST = "node-a"
TOKEN = "test-token-test-token"
NOW = datetime(2026, 9, 5, 12, 0, tzinfo=timezone.utc)
ENDPOINT = "http://127.0.0.1:8765"


class _Response:
    def __init__(
        self,
        body: bytes,
        *,
        close_raises: bool = False,
        on_close=None,
    ) -> None:
        self.body = body
        self.close_raises = close_raises
        self.closed = False
        self.close_calls = 0
        self.on_close = on_close

    def read(self, amount: int = -1) -> bytes:
        assert amount >= len(self.body)
        return self.body

    def close(self) -> None:
        self.closed = True
        self.close_calls += 1
        if self.on_close is not None:
            self.on_close()
        if self.close_raises:
            raise RuntimeError("private-close-detail")


def _health_response(node: str = HOST) -> _Response:
    return _Response(
        json.dumps(
            {
                "status": "ok",
                "service": "anvil-serving-controller",
                "request_id": "health-request-1",
                "node": node,
            },
            separators=(",", ":"),
        ).encode("ascii")
    )


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
    values = list(responses or [_health_response(), _Response(_payload())])

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


def _fleet(
    *,
    host: str = "worker-a",
    query: WorkloadQuery | None = None,
    collected: datetime = NOW,
    node=None,
) -> FleetResult:
    selected_query = WorkloadQuery() if query is None else query
    if node is None:
        source = SourceResult(
            WorkloadOwner.CONTROLLER,
            ResultStatus.COMPLETE,
            collected,
            (),
            Truncation(0, 0),
        )
        node = build_node_workloads(
            host, selected_query, collected, {WorkloadOwner.CONTROLLER: source}
        )
    return build_fleet_workloads((host,), selected_query, collected, {host: node})


def _fleet_payload(value: FleetResult, **extra: object) -> bytes:
    envelope: dict[str, object] = {
        "ok": True,
        "data": fleet_result_to_dict(value),
    }
    envelope.update(extra)
    return json.dumps(envelope, separators=(",", ":")).encode("ascii")


def _read_fleet(
    *,
    fleet: FleetResult | None = None,
    query: WorkloadQuery | None = None,
    now: datetime = NOW,
    responses=None,
    monotonic=lambda: 0.0,
):
    selected_query = WorkloadQuery() if query is None else query
    value = _fleet(query=selected_query) if fleet is None else fleet
    values = list(responses or [_health_response(), _Response(_fleet_payload(value))])
    requests: list[tuple[object, float]] = []

    def opener(request, timeout):
        requests.append((request, timeout))
        response = values.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response

    result = read_controller_fleet_workloads(
        ENDPOINT,
        "CONTROLLER_TOKEN",
        HOST,
        selected_query,
        now,
        environment={"CONTROLLER_TOKEN": TOKEN},
        monotonic=monotonic,
        _open=opener,
    )
    return result, requests


def test_wrong_health_identity_stops_before_workload_post() -> None:
    result, requests = _read(responses=[_health_response("other")])
    assert _controller_source(result).error is WorkloadErrorCode.UNAVAILABLE
    assert len(requests) == 1


def test_shared_deadline_prevents_post_after_health() -> None:
    clocks = iter((0.0, 0.0, 0.0, 0.0, 0.0, 2.0))
    result, requests = _read(
        monotonic=lambda: next(clocks),
        responses=[_health_response()],
    )
    assert _controller_source(result).error is WorkloadErrorCode.UNAVAILABLE
    assert len(requests) == 1


def test_shared_deadline_shrinks_post_timeout_after_health() -> None:
    clocks = iter((0.0, 0.0, 0.0, 0.0, 0.0, 1.5, 1.5, 1.5, 1.5, 1.5, 1.5))
    result, requests = _read(monotonic=lambda: next(clocks))
    assert _controller_source(result).status is ResultStatus.COMPLETE
    assert requests[0][1] == 2.0
    assert requests[1][1] == 0.5


def test_fleet_credential_capture_is_single_value_and_hermetic() -> None:
    seen: list[str] = []

    class _Environment(dict):
        def get(self, key, default=None):
            seen.append(key)
            return super().get(key, default)

        def __iter__(self):
            raise AssertionError("environment iterated")

    fleet = _fleet()
    responses = iter((_health_response(), _Response(_fleet_payload(fleet))))
    result = read_controller_fleet_workloads(
        ENDPOINT,
        "CONTROLLER_TOKEN",
        HOST,
        WorkloadQuery(),
        NOW,
        environment=_Environment({"CONTROLLER_TOKEN": TOKEN, "PRIVATE_UNUSED": "private-marker"}),
        monotonic=lambda: 0.0,
        _open=lambda *_args, **_kwargs: next(responses),
    )
    assert result == fleet
    assert seen == ["CONTROLLER_TOKEN"]


def test_fleet_receipt_time_controls_recent_selection() -> None:
    observed = NOW - timedelta(hours=1)
    query = WorkloadQuery(recent_seconds=60)
    record = WorkloadRecord(
        workload_id(
            "worker-a",
            WorkloadKind.CONTROLLER_OPERATION,
            WorkloadOwner.CONTROLLER,
            "operation-1",
        ),
        WorkloadKind.CONTROLLER_OPERATION,
        WorkloadOwner.CONTROLLER,
        "worker-a",
        WorkloadState.TERMINAL,
        WorkloadPhase.COMPLETED,
        WorkloadOutcome.SUCCESS,
        observed,
        observed,
        observed,
        SourceAuthority.CONTROLLER_STORE,
        ObservationQuality.RECORDED,
    )
    source = SourceResult(
        WorkloadOwner.CONTROLLER,
        ResultStatus.COMPLETE,
        observed,
        (record,),
        Truncation(1, 0),
    )
    node = build_node_workloads("worker-a", query, observed, {WorkloadOwner.CONTROLLER: source})
    remote = build_fleet_workloads(("worker-a",), query, observed, {"worker-a": node})

    with pytest.raises(WorkloadError) as raised:
        _read_fleet(fleet=remote, query=query, now=NOW)
    assert raised.value.code is WorkloadErrorCode.INVALID


def test_fleet_http_and_cleanup_failures_are_fixed() -> None:
    error = urllib.error.HTTPError(
        ENDPOINT + "/health", 503, "private-marker", {}, io.BytesIO(b"private-body")
    )
    with pytest.raises(WorkloadError) as raised:
        _read_fleet(responses=[error])
    assert raised.value.code is WorkloadErrorCode.UNAVAILABLE
    assert "private" not in str(raised.value)

    with pytest.raises(WorkloadError) as raised:
        _read_fleet(
            responses=[
                _health_response(),
                _Response(_fleet_payload(_fleet()), close_raises=True),
            ]
        )
    assert raised.value.code is WorkloadErrorCode.UNAVAILABLE


@pytest.mark.parametrize("surface", ("node", "fleet"))
@pytest.mark.parametrize("arguments", ({}, {"owner": "controller", "host": HOST, "limit": 1}))
def test_real_scoped_controller_reads_accept_omitted_filters(tmp_path, monkeypatch, surface, arguments):
    from anvil_serving.control_plane.controller import server as controller_server
    from anvil_serving.observability.workload_tools import parse_node_workload_query
    from anvil_serving.transports import _urlopen_no_proxy_no_redirect
    from tests.control_plane.test_controller_fleet_workloads import SCOPED, _server

    seen = []

    def source(host, query, now):
        assert host == HOST
        return SourceResult(WorkloadOwner.CONTROLLER, ResultStatus.COMPLETE, now, (), Truncation(0, 0))

    monkeypatch.setattr(controller_server, "build_workload_readers", lambda *_a, **_kw: {WorkloadOwner.CONTROLLER: source})

    def opener(request, timeout):
        seen.append((request.get_method(), json.loads(request.data) if request.data else None))
        return _urlopen_no_proxy_no_redirect(request, timeout=timeout)

    query = parse_node_workload_query(arguments)
    with _server(tmp_path, monkeypatch) as (server, collector, _):
        endpoint = f"http://127.0.0.1:{server.server_address[1]}"
        reader = read_controller_workloads if surface == "node" else read_controller_fleet_workloads
        result = reader(endpoint, "TEST_WORKLOAD_TOKEN", HOST, query, NOW,
                        environment={"TEST_WORKLOAD_TOKEN": SCOPED}, _open=opener)
        if surface == "node":
            assert _controller_source(result).status is ResultStatus.COMPLETE
        else:
            assert collector.calls == [(query, NOW)]
            assert result == build_fleet_workloads((HOST,), query, NOW, {})
    assert [method for method, _ in seen] == ["GET", "POST"]
    assert seen[1][1] == {"name": surface + "_workloads", "arguments": {
        "active_only": False, "recent_seconds": 3600, "limit": 200, **arguments,
    }}
