from __future__ import annotations

import io
import json
from datetime import datetime, timezone
from http.client import HTTPMessage

import pytest

from anvil_serving.control_plane.authorization import (
    AuthorizationPolicy,
    load_authorization_policy,
)
from anvil_serving.observability.workload_http import WorkloadHTTPService
from anvil_serving.observability.workload_tools import parse_node_workload_query
from anvil_serving.observability.workloads import (
    FleetResult,
    ResultStatus,
    Truncation,
    WorkloadQuery,
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


def test_default_reader_is_hermetic_and_uses_same_caller_credential(policy, monkeypatch):
    from anvil_serving.observability.probes import controller_workloads

    calls = []

    def opener(request, timeout):
        assert request.get_header("Authorization") == "Bearer " + TOKEN
        assert timeout == 7.0
        calls.append(request)
        if len(calls) == 1:
            return io.BytesIO(
                b'{"status":"ok","service":"anvil-serving-controller",'
                b'"request_id":"health-request-1","node":"aggregator-a"}'
            )
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
