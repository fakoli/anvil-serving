from __future__ import annotations

import io
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from http.client import HTTPMessage

import pytest

from anvil_serving.control_plane.authorization import (
    AuthorizationPolicy,
    load_authorization_policy,
)
from anvil_serving.observability import workload_http
from anvil_serving.observability.workload_http import WorkloadHTTPService, workload_http_error
from anvil_serving.observability.workload_tools import parse_node_workload_query
from anvil_serving.observability.workloads import (
    MAX_JSON_BYTES,
    FleetResult,
    ResultStatus,
    Truncation,
    WorkloadQuery,
    fleet_result_from_dict,
    fleet_result_to_dict,
)


NOW = datetime(2026, 9, 5, 12, tzinfo=timezone.utc)
ENDPOINT = "http://127.0.0.1:8765"
TOKEN = "reader-fixture-credential"
ADMIN = "admin-fixture-credential"
PRIVATE = "private-exception-prompt-path-marker"


def _headers(*entries):
    headers = HTTPMessage()
    for key, value in entries or (("Authorization", "Bearer " + TOKEN),):
        headers[key] = value
    return headers


@pytest.fixture
def policy(tmp_path):
    path = tmp_path / "policy.json"
    path.write_text(json.dumps({"schema_version": 1, "clients": [
        {"id": "reader", "scopes": ["workloads:read"], "credential_env": "READER"},
        {"id": "admin", "scopes": ["node-admin:bootstrap"], "credential_env": "ADMIN"},
    ]}), encoding="utf-8")
    return load_authorization_policy(path, env={"READER": TOKEN, "ADMIN": ADMIN})


def _empty():
    return FleetResult(ResultStatus.COMPLETE, NOW, (), Truncation(0, 0))


def _explode(*_args, **_kwargs):
    raise RuntimeError(PRIVATE)


def _service(policy, **kwargs):
    kwargs.setdefault("clock", lambda: NOW)
    kwargs.setdefault("reader", lambda *_args, **_kwargs: _empty())
    return WorkloadHTTPService(ENDPOINT, "aggregator-a", policy, **kwargs)


def _error(result, status, code, message):
    assert result[0] == status
    assert type(result[1]) is bytes
    assert json.loads(result[1]) == {"ok": False, "error": {"code": code, "message": message}}
    assert PRIVATE.encode() not in result[1]
    assert TOKEN.encode() not in result[1]


class _UnreachableSlots:
    acquire = _explode
    release = _explode


@pytest.mark.parametrize("headers", [
    HTTPMessage(), object(),
    _headers(("Authorization", "bearer " + TOKEN)),
    _headers(("Authorization", "Basic " + TOKEN)),
    _headers(("Authorization", "Bearer ")),
    _headers(("Authorization", "Bearer short")),
    _headers(("Authorization", "Bearer " + "a" * 4097)),
    _headers(("Authorization", "Bearer  " + TOKEN)),
    _headers(("Authorization", "Bearer " + TOKEN + " ")),
    _headers(("Authorization", "Bearer " + TOKEN + "\t")),
    _headers(("Authorization", "Bearer " + TOKEN + "\x00")),
    _headers(("Authorization", "Bearer " + TOKEN + "é")),
    _headers(("Authorization", "Bearer " + TOKEN + ", Bearer " + TOKEN)),
    _headers(("Authorization", "Bearer " + TOKEN), ("authorization", "Bearer " + TOKEN)),
    _headers(("X-Api-Key", TOKEN)),
    _headers(("Authorization", "Bearer " + TOKEN), ("X-Api-Key", "")),
])
def test_authentication_rejected_before_query_clock_slot_or_reader(policy, headers):
    service = _service(policy, clock=_explode, reader=_explode)
    service._slots = _UnreachableSlots()
    _error(service.read(object(), headers), 401, "authentication_error", "workload authentication required")


@pytest.mark.parametrize("selected_policy,token", [
    (None, TOKEN), (object(), TOKEN), (AuthorizationPolicy(()), TOKEN),
    ("valid", ADMIN), ("valid", "unknown-fixture-credential"),
])
def test_scope_denied_before_query_clock_slot_or_reader(policy, selected_policy, token):
    service = _service(policy if selected_policy == "valid" else selected_policy, clock=_explode, reader=_explode)
    service._slots = _UnreachableSlots()
    _error(service.read(object(), _headers(("Authorization", "Bearer " + token))),
           403, "authorization_scope_denied", "authorization scope denied")


@pytest.mark.parametrize("endpoint", [
    None, 5, "", "x" * 2049, " http://127.0.0.1", "http://127.0.0.1\n",
    "http://127.0.0.1/é", "ssh://127.0.0.1", "http://example.invalid",
    "http://8.8.8.8", "http://user:pass@127.0.0.1", "http://127.0.0.1?q=1",
    "http://127.0.0.1#fragment", "http://127.0.0.1:99999",
    "http://127.0.0.1:invalid", "http://[invalid]",
])
def test_bad_binding_has_fixed_diagnostic_without_io(endpoint):
    with pytest.raises(ValueError, match="^invalid workload controller binding$"):
        WorkloadHTTPService(endpoint, "aggregator-a", None, clock=_explode, reader=_explode)


@pytest.mark.parametrize("host", [None, "", 1, "../node", "node.a", "a b", "a" * 1025])
def test_expected_node_must_be_explicit_safe_identifier(host):
    with pytest.raises(ValueError, match="^invalid workload controller binding$"):
        WorkloadHTTPService(ENDPOINT, host, None, clock=_explode, reader=_explode)


@pytest.mark.parametrize("raw", [
    None, b"", object(), "x" * 8193, "host=é", "host=%", "host=%0", "host=%gg",
    "host=%FF", "host=%C0%AF", "host=%ED%A0%80", "host=%00", "host=worker.a",
    "unknown=value", "limit=1&limit=2", "limit=1&%6cimit=2",
    "&" * 7, "owner=", "kind=", "state=", "host=", "owner=not-an-owner",
    "limit=", "limit=0", "limit=1001", "limit=-1", "limit=+1", "limit=1.0",
    "limit=%31%20", "limit=%EF%BC%91", "limit=000001", "limit=99999",
    "recent_seconds=0", "recent_seconds=86401", "recent_seconds=100000",
    "active_only=1", "active_only=True", "active_only=false%20", "active_only=",
    "owner=controller&kind=controller-operation&state=running&host=worker-a"
    "&active_only=true&limit=1&recent_seconds=1&extra=x",
])
def test_bad_query_fails_before_clock_slot_or_reader(policy, raw):
    service = _service(policy, clock=_explode, reader=_explode)
    service._slots = _UnreachableSlots()
    _error(service.read(raw, _headers()), 400, "invalid_workload_query", "invalid workload query")


@pytest.mark.parametrize("raw,expected", [
    ("", WorkloadQuery()),
    ("owner=controller&kind=controller-operation&state=running&host=worker-a"
     "&active_only=true&recent_seconds=86400&limit=01000", None),
    ("active_only=false&recent_seconds=00001&limit=00001", WorkloadQuery(recent_seconds=1, limit=1)),
    ("%68ost=worker%2Da", WorkloadQuery(host="worker-a")),
])
def test_valid_query_forwards_only_presented_credential_and_exact_binding(policy, raw, expected):
    events = []

    def clock():
        events.append("clock")
        return NOW

    def reader(endpoint, reference, node, query, now, **kwargs):
        events.append("reader")
        assert endpoint == ENDPOINT
        assert node == "aggregator-a" and now == NOW
        assert kwargs == {"environment": {reference: TOKEN}, "monotonic": _explode}
        assert reference == "WORKLOAD_REQUEST_CREDENTIAL"
        if expected is None:
            assert query.owner.value == "controller" and query.kind.value == "controller-operation"
            assert query.state.value == "running" and query.host == "worker-a"
            assert query.active_only is True and query.recent_seconds == 86400 and query.limit == 1000
        else:
            assert query == expected
        return _empty()

    headers = _headers(("Authorization", "Bearer " + TOKEN), ("X-Context", PRIVATE),
                       ("Idempotency-Key", PRIVATE), ("Cookie", PRIVATE))
    service = _service(policy, clock=clock, reader=reader, monotonic=_explode)
    status, body = service.read(raw, headers)
    assert status == 200 and json.loads(body) == {"ok": True, "data": fleet_result_to_dict(_empty())}
    assert events == ["clock", "reader"]


def _literal_fleet(status, omitted):
    records = [] if status == "unavailable" else [{
        "schema": "anvil-workloads/v1", "id": "a" * 64,
        "kind": "controller-operation", "owner": "controller", "host": "worker-a",
        "label": "Controller Operation",
        "state": "terminal", "phase": "completed", "outcome": "success",
        "created_at": "2026-09-05T11:00:00.000001Z",
        "updated_at": "2026-09-05T11:01:00.000002Z",
        "source_timestamp": "2026-09-05T11:02:00.000003Z",
        "source_authority": "controller-store", "observation_quality": "recorded",
    }]
    return {
        "schema": "anvil-workloads/v1", "status": status,
        "collection_timestamp": "2026-09-05T11:05:00.000006Z",
        "nodes": [{"schema": "anvil-workloads/v1", "host": "worker-a", "status": status,
                   "collection_timestamp": "2026-09-05T11:04:00.000005Z", "sources": [{
                       "schema": "anvil-workloads/v1", "owner": "controller", "status": status,
                       "collection_timestamp": "2026-09-05T11:03:00.000004Z", "records": records,
                       "truncation": {"returned": len(records), "omitted": omitted},
                       "error": "workload-source-unavailable" if status == "unavailable" else None,
                   }]}], "truncation": {"returned": len(records), "omitted": omitted},
    }


@pytest.mark.parametrize("status,omitted", [("complete", 0), ("partial", 9), ("partial", None), ("unavailable", None)])
def test_canonical_data_preserves_original_provenance_and_omissions(policy, status, omitted):
    wire = _literal_fleet(status, omitted)
    fleet = fleet_result_from_dict(wire)
    service = _service(policy, reader=lambda *_a, **_kw: fleet)
    code, body = service.read("", _headers())
    assert code == 200
    assert json.loads(body) == {"ok": True, "data": wire}


@pytest.mark.parametrize("failure", ["clock", "invalid-clock", "reader", "type", "forged", "codec", "serialize", "size"])
def test_capacity_released_after_every_failed_stage(policy, monkeypatch, failure):
    service = _service(policy)
    with monkeypatch.context() as patch:
        if failure == "clock":
            patch.setattr(service, "_clock", _explode)
        elif failure == "invalid-clock":
            patch.setattr(service, "_clock", lambda: datetime(2026, 9, 5))
            patch.setattr(service, "_reader", _explode)
        elif failure == "reader":
            patch.setattr(service, "_reader", _explode)
        elif failure == "type":
            patch.setattr(service, "_reader", lambda *_a, **_kw: {"private": PRIVATE})
        elif failure == "forged":
            forged = _empty()
            object.__setattr__(forged, "truncation", Truncation(1, 0))
            patch.setattr(service, "_reader", lambda *_a, **_kw: forged)
        elif failure == "codec":
            patch.setattr(workload_http, "fleet_result_from_json", _explode)
        elif failure == "serialize":
            patch.setattr(workload_http, "fleet_result_to_json", _explode)
        else:
            patch.setattr(workload_http, "MAX_JSON_BYTES", 1)
        for _ in range(8):
            _error(service.read("", _headers()), 503, "workload_source_unavailable", "workload source unavailable")
    assert service.read("", _headers())[0] == 200


def test_envelope_not_just_canonical_data_counts_toward_byte_limit(policy, monkeypatch):
    service = _service(policy)
    status, body = service.read("", _headers())
    assert status == 200 and workload_http.MAX_JSON_BYTES == MAX_JSON_BYTES
    monkeypatch.setattr(workload_http, "MAX_JSON_BYTES", len(body))
    assert service.read("", _headers()) == (200, body)
    monkeypatch.setattr(workload_http, "MAX_JSON_BYTES", len(body) - 1)
    _error(service.read("", _headers()), 503, "workload_source_unavailable", "workload source unavailable")


def test_four_slots_and_fifth_immediate_denial_before_clock(policy):
    rendezvous = threading.Barrier(5)
    release = threading.Event()
    clocks = []

    def reader(*_a, **_kw):
        rendezvous.wait(timeout=5)
        assert release.wait(timeout=5)
        return _empty()

    def clock():
        clocks.append(NOW)
        return NOW

    service = _service(policy, reader=reader, clock=clock)
    with ThreadPoolExecutor(max_workers=5) as pool:
        active = [pool.submit(service.read, "", _headers()) for _ in range(4)]
        try:
            rendezvous.wait(timeout=5)
            denied = pool.submit(service.read, "", _headers()).result(timeout=1)
            _error(denied, 503, "workload_source_unavailable", "workload source unavailable")
            assert clocks == [NOW] * 4
        finally:
            release.set()
        assert [future.result(timeout=5)[0] for future in active] == [200] * 4
    service._reader = lambda *_a, **_kw: _empty()
    assert service.read("", _headers())[0] == 200


def test_default_reader_is_hermetic_and_uses_same_caller_credential(policy, monkeypatch):
    from anvil_serving.observability.probes import controller_workloads

    calls = []

    def opener(request, timeout):
        assert request.get_header("Authorization") == "Bearer " + TOKEN
        assert timeout == 7.0
        calls.append(request)
        if len(calls) == 1:
            return io.BytesIO(b'{"node":"aggregator-a"}')
        sent = json.loads(request.data)
        assert sent == {"name": "fleet_workloads", "arguments": {
            "active_only": False, "recent_seconds": 3600, "limit": 200,
        }}
        assert parse_node_workload_query(sent["arguments"]) == WorkloadQuery()
        return io.BytesIO(json.dumps({"ok": True, "data": fleet_result_to_dict(_empty())}).encode())

    class NoEnvironment(dict):
        get = _explode
        __getitem__ = _explode

    service = WorkloadHTTPService(ENDPOINT, "aggregator-a", policy, clock=lambda: NOW, monotonic=lambda: 0.0)
    with monkeypatch.context() as patch:
        patch.setattr(controller_workloads, "_urlopen_no_proxy_no_redirect", opener)
        patch.setattr(controller_workloads.os, "environ", NoEnvironment())
        status, body = service.read("", _headers())
    assert status == 200 and json.loads(body)["ok"] is True
    assert [request.full_url for request in calls] == [ENDPOINT + "/health", ENDPOINT + "/tools/call"]


def test_error_helper_never_echoes_unknown_input():
    for value in (PRIVATE, None, [], object()):
        _error(workload_http_error(value), 503, "workload_source_unavailable", "workload source unavailable")
