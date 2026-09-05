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
    MAX_JSON_BYTES,
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
from anvil_serving.transports import MAX_RESPONSE_BYTES, TransportResult


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
    values = list(responses or [_Response(b'{"node":"node-a"}'), _Response(_fleet_payload(value))])
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


def test_expected_node_transport_uses_health_then_exact_workload_operation() -> None:
    result, requests = _read()
    assert _controller_source(result).status is ResultStatus.COMPLETE
    assert [request.full_url for request, _ in requests] == [
        ENDPOINT + "/health",
        ENDPOINT + "/tools/call",
    ]
    assert all(request.get_header("Authorization") == "Bearer " + TOKEN for request, _ in requests)
    assert json.loads(requests[1][0].data) == {
        "name": "node_workloads",
        "arguments": {
            "owner": None,
            "kind": None,
            "state": None,
            "host": None,
            "active_only": False,
            "recent_seconds": 3600,
            "limit": 200,
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
        ENDPOINT,
        "CONTROLLER_TOKEN",
        HOST,
        WorkloadQuery(host="other"),
        NOW,
        environment=_Trap(),
        monotonic=lambda: (_ for _ in ()).throw(AssertionError("clock")),
        _open=lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("open")),
    )
    assert result.status is ResultStatus.COMPLETE
    assert all(source.status is ResultStatus.COMPLETE for source in result.sources)


@pytest.mark.parametrize(
    "endpoint,environment",
    [
        ("http://example.invalid:8765", {"CONTROLLER_TOKEN": TOKEN}),
        (ENDPOINT + " \n", {"CONTROLLER_TOKEN": TOKEN}),
        (ENDPOINT, {}),
    ],
)
def test_bad_configuration_never_opens(endpoint, environment) -> None:
    result = read_controller_workloads(
        endpoint,
        "CONTROLLER_TOKEN",
        HOST,
        WorkloadQuery(),
        NOW,
        environment=environment,
        monotonic=lambda: 0.0,
        _open=lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("open")),
    )
    assert result.status is ResultStatus.UNAVAILABLE
    assert all(source.error is WorkloadErrorCode.UNAVAILABLE for source in result.sources)


def test_invalid_canonical_arguments_raise_before_open() -> None:
    with pytest.raises(WorkloadError):
        read_controller_workloads(
            ENDPOINT,
            "CONTROLLER_TOKEN",
            HOST,
            object(),
            NOW,
            environment={"CONTROLLER_TOKEN": TOKEN},
            monotonic=lambda: 0.0,
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


@pytest.mark.parametrize(
    "payload,code",
    [
        (_payload(extra=True), WorkloadErrorCode.INVALID),
        (
            json.dumps({"ok": True, "data": {"schema": "unsupported"}}).encode("ascii"),
            WorkloadErrorCode.UNSUPPORTED,
        ),
    ],
)
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


def test_fleet_reader_uses_expected_aggregator_and_exact_operation() -> None:
    query = WorkloadQuery(host="worker-a", active_only=True, recent_seconds=42, limit=17)
    fleet = _fleet(query=query)
    result, requests = _read_fleet(fleet=fleet, query=query)

    assert result == fleet
    assert result is not fleet
    assert [request.full_url for request, _ in requests] == [
        ENDPOINT + "/health",
        ENDPOINT + "/tools/call",
    ]
    assert all(request.get_header("Authorization") == "Bearer " + TOKEN for request, _ in requests)
    assert json.loads(requests[1][0].data) == {
        "name": "fleet_workloads",
        "arguments": {
            "owner": None,
            "kind": None,
            "state": None,
            "host": "worker-a",
            "active_only": True,
            "recent_seconds": 42,
            "limit": 17,
        },
    }
    assert requests[1][0].get_header("X-anvil-idempotency-key") is None


def test_fleet_reader_decodes_literal_canonical_wire() -> None:
    payload = (
        b'{"ok":true,"data":{"schema":"anvil-workloads/v1","status":"complete",'
        b'"collection_timestamp":"2026-09-05T12:00:00.000000Z","nodes":[],'
        b'"truncation":{"returned":0,"omitted":0}}}'
    )
    result, _ = _read_fleet(
        fleet=build_fleet_workloads((), WorkloadQuery(), NOW, {}),
        responses=[_Response(b'{"node":"node-a"}'), _Response(payload)],
    )
    assert result == FleetResult(
        ResultStatus.COMPLETE,
        NOW,
        (),
        Truncation(0, 0),
    )


def test_fleet_reader_transport_configuration_is_closed(monkeypatch) -> None:
    fleet = _fleet()
    seen: dict[str, object] = {}

    class _Transport:
        def __init__(self, endpoint, **kwargs):
            seen["endpoint"] = endpoint
            seen["kwargs"] = kwargs

        def execute(self, operation):
            seen["operation"] = operation
            return TransportResult(
                "fleet-workloads",
                "controller",
                {"ok": True, "data": fleet_result_to_dict(fleet)},
            )

    monkeypatch.setattr(
        "anvil_serving.observability.probes.controller_workloads.ControllerTransport",
        _Transport,
    )
    result = read_controller_fleet_workloads(
        ENDPOINT,
        "CONTROLLER_TOKEN",
        HOST,
        WorkloadQuery(),
        NOW,
        environment={"CONTROLLER_TOKEN": TOKEN},
        monotonic=lambda: 0.0,
    )

    assert result == fleet
    kwargs = seen["kwargs"]
    assert kwargs["allowed_operations"] == ("fleet-workloads",)
    assert kwargs["expected_node"] == HOST
    assert kwargs["timeout_seconds"] == 7.0
    assert kwargs["max_response_bytes"] == MAX_JSON_BYTES
    operation = seen["operation"]
    assert operation.name == "fleet-workloads"
    assert operation.tool_name == "fleet_workloads"


@pytest.mark.parametrize(
    ("expected_node", "query", "now"),
    [
        ("bad host", WorkloadQuery(), NOW),
        (type("DerivedString", (str,), {})(HOST), WorkloadQuery(), NOW),
        (HOST, object(), NOW),
        (HOST, WorkloadQuery(), NOW.replace(tzinfo=None)),
        (
            HOST,
            WorkloadQuery(),
            type("DerivedDateTime", (datetime,), {})(
                2026, 9, 5, 12, 0, tzinfo=timezone.utc
            ),
        ),
    ],
)
def test_fleet_invalid_inputs_fail_before_environment_clock_or_network(
    expected_node, query, now
) -> None:
    class _Trap(dict):
        def get(self, key, default=None):
            raise AssertionError(key)

    with pytest.raises(WorkloadError) as raised:
        read_controller_fleet_workloads(
            ENDPOINT,
            "CONTROLLER_TOKEN",
            expected_node,
            query,
            now,
            environment=_Trap(),
            monotonic=lambda: (_ for _ in ()).throw(AssertionError("clock")),
            _open=lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("open")),
        )
    assert raised.value.code is WorkloadErrorCode.INVALID


def test_fleet_credential_capture_is_single_value_and_hermetic() -> None:
    seen: list[str] = []

    class _Environment(dict):
        def get(self, key, default=None):
            seen.append(key)
            return super().get(key, default)

        def __iter__(self):
            raise AssertionError("environment iterated")

    fleet = _fleet()
    responses = iter((_Response(b'{"node":"node-a"}'), _Response(_fleet_payload(fleet))))
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


def test_fleet_shared_budget_shrinks_and_expires_before_post() -> None:
    clocks = iter((0.0, 0.0, 0.0, 0.0, 0.0, 4.0, 4.0, 4.0, 4.0, 4.0))
    result, requests = _read_fleet(monotonic=lambda: next(clocks))
    assert result == _fleet()
    assert requests[0][1] == 7.0
    assert requests[1][1] == 3.0

    expired = iter((0.0, 0.0, 0.0, 0.0, 0.0, 7.0))
    with pytest.raises(WorkloadError) as raised:
        _read_fleet(
            monotonic=lambda: next(expired),
            responses=[_Response(b'{"node":"node-a"}')],
        )
    assert raised.value.code is WorkloadErrorCode.UNAVAILABLE

    regressed = iter((1.0, 1.0, 1.0, 0.5))
    with pytest.raises(WorkloadError) as raised:
        _read_fleet(
            monotonic=lambda: next(regressed),
            responses=[_Response(b'{"node":"node-a"}')],
        )
    assert raised.value.code is WorkloadErrorCode.UNAVAILABLE


def test_fleet_accepts_canonical_partial_and_unavailable_data() -> None:
    partial = _fleet()
    assert partial.status is ResultStatus.PARTIAL
    assert _read_fleet(fleet=partial)[0] == partial

    unavailable = build_fleet_workloads(("worker-a",), WorkloadQuery(), NOW, {})
    assert unavailable.status is ResultStatus.UNAVAILABLE
    assert _read_fleet(fleet=unavailable)[0] == unavailable


def test_fleet_accepts_canonical_response_above_the_generic_transport_cap() -> None:
    hosts = tuple(f"n{index:04d}" for index in range(1000))
    remote = build_fleet_workloads(hosts, WorkloadQuery(), NOW, {})
    payload = _fleet_payload(remote)
    assert MAX_RESPONSE_BYTES < len(payload) <= MAX_JSON_BYTES

    returned, requests = _read_fleet(
        fleet=remote,
        responses=[_Response(b'{"node":"node-a"}'), _Response(payload)],
    )
    assert returned == remote
    assert len(requests) == 2


def test_fleet_preserves_stale_outer_node_and_source_provenance() -> None:
    observed = NOW - timedelta(hours=2)
    remote = _fleet(collected=observed)
    returned, _ = _read_fleet(fleet=remote)
    assert returned == remote
    assert returned.collection_timestamp == observed
    assert returned.nodes[0].collection_timestamp == observed
    assert all(
        source.collection_timestamp == observed for source in returned.nodes[0].sources
    )


@pytest.mark.parametrize(
    ("operation", "transport"),
    [("node-workloads", "controller"), ("fleet-workloads", "local")],
)
def test_fleet_rejects_wrong_transport_result_identity(
    monkeypatch, operation, transport
) -> None:
    fleet = _fleet()

    class _Transport:
        def __init__(self, *_args, **_kwargs):
            pass

        def execute(self, _operation):
            return TransportResult(
                operation,
                transport,
                {"ok": True, "data": fleet_result_to_dict(fleet)},
            )

    monkeypatch.setattr(
        "anvil_serving.observability.probes.controller_workloads.ControllerTransport",
        _Transport,
    )
    with pytest.raises(WorkloadError) as raised:
        read_controller_fleet_workloads(
            ENDPOINT,
            "CONTROLLER_TOKEN",
            HOST,
            WorkloadQuery(),
            NOW,
            environment={"CONTROLLER_TOKEN": TOKEN},
            monotonic=lambda: 0.0,
        )
    assert raised.value.code is WorkloadErrorCode.INVALID


def test_fleet_wrong_expected_identity_stops_before_post() -> None:
    with pytest.raises(WorkloadError) as raised:
        _read_fleet(responses=[_Response(b'{"node":"other"}')])
    assert raised.value.code is WorkloadErrorCode.UNAVAILABLE


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


@pytest.mark.parametrize("microseconds,accepted", [(30_000_000, True), (30_000_001, False)])
def test_fleet_receipt_future_boundary(microseconds, accepted) -> None:
    future = NOW + timedelta(microseconds=microseconds)
    remote = _fleet(collected=future)
    if accepted:
        assert _read_fleet(fleet=remote)[0] == remote
    else:
        with pytest.raises(WorkloadError) as raised:
            _read_fleet(fleet=remote)
        assert raised.value.code is WorkloadErrorCode.FUTURE


@pytest.mark.parametrize(
    ("payload", "code"),
    [
        (b'{"ok":false,"error":{"code":"private-marker"}}', WorkloadErrorCode.UNAVAILABLE),
        (b'{"ok":true,"data":[],"private":"private-marker"}', WorkloadErrorCode.INVALID),
        (b'{"ok":true,"data":{"schema":"future-schema"}}', WorkloadErrorCode.UNSUPPORTED),
    ],
)
def test_fleet_application_failures_are_fixed_and_private(payload, code) -> None:
    with pytest.raises(WorkloadError) as raised:
        _read_fleet(responses=[_Response(b'{"node":"node-a"}'), _Response(payload)])
    assert raised.value.code is code
    assert "private-marker" not in str(raised.value)
    assert raised.value.__cause__ is None


def test_fleet_duplicate_nodes_are_invalid() -> None:
    remote = fleet_result_to_dict(_fleet())
    remote["nodes"] = [*remote["nodes"], *remote["nodes"]]
    payload = json.dumps({"ok": True, "data": remote}, separators=(",", ":")).encode(
        "ascii"
    )
    with pytest.raises(WorkloadError) as raised:
        _read_fleet(
            responses=[_Response(b'{"node":"node-a"}'), _Response(payload)]
        )
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
                _Response(b'{"node":"node-a"}'),
                _Response(_fleet_payload(_fleet()), close_raises=True),
            ]
        )
    assert raised.value.code is WorkloadErrorCode.UNAVAILABLE
