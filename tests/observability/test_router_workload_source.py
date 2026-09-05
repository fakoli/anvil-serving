import datetime as dt
import io
import json
import urllib.error

import pytest

from anvil_serving.observability.probes import router_workloads
from anvil_serving.observability.probes.router_workloads import read_router_workloads
from anvil_serving.observability.workload_collection import build_node_workloads
from anvil_serving.observability.workloads import (
    MAX_JSON_BYTES,
    ResultStatus,
    SourceResult,
    Truncation,
    WorkloadError,
    WorkloadErrorCode,
    WorkloadOwner,
    WorkloadQuery,
)
from anvil_serving.paths import LOOPBACK_ALIAS_ENV


NOW = dt.datetime(2026, 9, 5, 12, 0, tzinfo=dt.timezone.utc)
HOST = "node-a"
TOKEN = "a" * 16
TIMESTAMP = "2026-09-05T12:00:00.000000Z"
RECORD_ID = "061bd9ef51c36b5541a36f39032e1b92d138f016ad15e1f74d70d4a694a465e1"


def _wire(*, records=None, status="complete", omitted=0, error=None, host=HOST, collected=TIMESTAMP):
    records = [] if records is None else records
    source = {
        "schema": "anvil-workloads/v1",
        "owner": "router",
        "status": status,
        "collection_timestamp": collected,
        "records": records,
        "truncation": {"returned": len(records), "omitted": omitted},
        "error": error,
    }
    node_status = "complete" if status == "complete" else status
    return json.dumps(
        {
            "schema": "anvil-workloads/v1",
            "host": host,
            "status": node_status,
            "collection_timestamp": collected,
            "sources": [source],
        },
        separators=(",", ":"),
    ).encode("ascii")


def _record():
    return {
        "schema": "anvil-workloads/v1",
        "id": RECORD_ID,
        "kind": "router-request",
        "owner": "router",
        "host": HOST,
        "label": "Router Request",
        "state": "checking",
        "phase": "checking",
        "created_at": "2026-09-05T11:59:59.000000Z",
        "updated_at": TIMESTAMP,
        "source_timestamp": TIMESTAMP,
        "source_authority": "router-memory",
        "observation_quality": "recorded",
    }


class _Response:
    def __init__(self, payload, code=200):
        self.payload = payload
        self.code = code
        self.read_limit = None
        self.closed = False

    def getcode(self):
        return self.code

    def read(self, limit):
        self.read_limit = limit
        return self.payload

    def close(self):
        self.closed = True


def _read(payload, *, endpoint="http://127.0.0.1:8765/v1", environment=None, query=None, opened=None):
    response = _Response(payload)
    calls = []

    def opener(request, timeout):
        calls.append((request, timeout))
        return response

    result = read_router_workloads(
        endpoint,
        "ROUTER_WORKLOAD_TOKEN",
        HOST,
        query or WorkloadQuery(),
        NOW,
        environment={"ROUTER_WORKLOAD_TOKEN": TOKEN} if environment is None else environment,
        _open=opener if opened is None else opened,
    )
    return result, response, calls


@pytest.mark.parametrize(
    "payload,status,records,omitted",
    [
        (_wire(records=[_record()]), ResultStatus.COMPLETE, 1, 0),
        (_wire(), ResultStatus.COMPLETE, 0, 0),
        (_wire(status="partial", omitted=None, error="workload-source-unavailable"), ResultStatus.PARTIAL, 0, None),
        (_wire(status="unavailable", omitted=None, error="workload-source-unavailable"), ResultStatus.UNAVAILABLE, 0, None),
    ],
)
def test_literal_router_wire_preserves_canonical_source(payload, status, records, omitted):
    result, response, calls = _read(payload)
    assert result.status is status
    assert len(result.records) == records
    assert result.truncation.omitted == omitted
    assert result.collection_timestamp == NOW
    assert response.closed and response.read_limit == MAX_JSON_BYTES + 1
    assert len(calls) == 1


def test_request_is_one_bounded_get_with_exact_canonical_query_and_headers():
    query = WorkloadQuery(owner=WorkloadOwner.ROUTER, active_only=True, recent_seconds=12, limit=7)
    result, _, calls = _read(_wire(), endpoint="http://127.0.0.1:8765/v1/", query=query)
    request, timeout = calls[0]
    assert result.status is ResultStatus.COMPLETE and timeout == 1.0
    assert request.get_method() == "GET"
    assert request.full_url == (
        "http://127.0.0.1:8765/v1/workloads?owner=router&active_only=true&recent_seconds=12&limit=7"
    )
    assert request.get_header("Accept") == "application/json"
    assert request.get_header("Authorization") == "Bearer " + TOKEN


@pytest.mark.parametrize(
    "endpoint,auth_env,environment",
    [
        ("http://localhost:8765/v1", "ROUTER_WORKLOAD_TOKEN", {"ROUTER_WORKLOAD_TOKEN": TOKEN}),
        ("https://127.0.0.1:8765/v1", "ROUTER_WORKLOAD_TOKEN", {"ROUTER_WORKLOAD_TOKEN": TOKEN}),
        ("http://127.0.0.1/v1", "ROUTER_WORKLOAD_TOKEN", {"ROUTER_WORKLOAD_TOKEN": TOKEN}),
        ("http://127.0.0.1:08765/v1", "ROUTER_WORKLOAD_TOKEN", {"ROUTER_WORKLOAD_TOKEN": TOKEN}),
        ("http://127.0.0.1:8765/v1?private", "ROUTER_WORKLOAD_TOKEN", {"ROUTER_WORKLOAD_TOKEN": TOKEN}),
        ("http://127.0.0.1:8765/v1", "bad-reference!", {"ROUTER_WORKLOAD_TOKEN": TOKEN}),
        ("http://127.0.0.1:8765/v1", "ROUTER_WORKLOAD_TOKEN", {}),
        ("http://127.0.0.1:8765/v1", "ROUTER_WORKLOAD_TOKEN", {"ROUTER_WORKLOAD_TOKEN": "too-short"}),
    ],
)
def test_bad_configuration_or_hermetic_environment_never_opens(endpoint, auth_env, environment):
    calls = []

    def opener(*args):
        calls.append(args)
        raise AssertionError("must not open")

    result = read_router_workloads(endpoint, auth_env, HOST, WorkloadQuery(), NOW, environment=environment, _open=opener)
    assert result.status is ResultStatus.UNAVAILABLE
    assert result.error is WorkloadErrorCode.UNAVAILABLE
    assert calls == []


def test_runtime_alias_applies_only_after_declared_loopback_validation():
    result, _, calls = _read(
        _wire(),
        environment={"ROUTER_WORKLOAD_TOKEN": TOKEN, LOOPBACK_ALIAS_ENV: "router-host"},
    )
    assert result.status is ResultStatus.COMPLETE
    assert calls[0][0].full_url.startswith("http://router-host:8765/v1/workloads?")


def test_http_error_is_closed_without_reading_failure_body():
    body = io.BytesIO(b"private-error-body")
    error = urllib.error.HTTPError("http://127.0.0.1:8765/v1/workloads", 503, "private", {}, body)

    def opener(*args, **kwargs):
        raise error

    result = read_router_workloads(
        "http://127.0.0.1:8765/v1", "ROUTER_WORKLOAD_TOKEN", HOST, WorkloadQuery(), NOW,
        environment={"ROUTER_WORKLOAD_TOKEN": TOKEN}, _open=opener,
    )
    assert result.error is WorkloadErrorCode.UNAVAILABLE
    assert body.closed
    assert "private" not in repr(result)


def test_default_opener_uses_the_proxy_free_no_redirect_transport(monkeypatch):
    response = _Response(_wire())
    calls = []

    def opener(request, timeout):
        calls.append((request, timeout))
        return response

    monkeypatch.setattr(router_workloads, "_urlopen_no_proxy_no_redirect", opener)
    result = read_router_workloads(
        "http://127.0.0.1:8765/v1", "ROUTER_WORKLOAD_TOKEN", HOST, WorkloadQuery(), NOW,
        environment={"ROUTER_WORKLOAD_TOKEN": TOKEN},
    )
    assert result.status is ResultStatus.COMPLETE
    assert len(calls) == 1 and response.closed


@pytest.mark.parametrize(
    "payload,code",
    [
        (b"not-json", WorkloadErrorCode.INVALID),
        (_wire(host="wrong-node"), WorkloadErrorCode.INVALID),
        (_wire(collected="2026-09-05T12:00:30.000001Z"), WorkloadErrorCode.FUTURE),
        (b"{" + b"x" * (MAX_JSON_BYTES + 1), WorkloadErrorCode.INVALID),
    ],
    ids=("not-json", "wrong-node", "future-node", "oversized"),
)
def test_malformed_identity_or_oversized_wire_is_fixed_invalid(payload, code):
    result, response, _ = _read(payload)
    assert result.status is ResultStatus.UNAVAILABLE
    assert result.error is code
    assert response.closed


def test_future_source_is_fixed_future_and_healthy_peer_survives_composition():
    payload = _wire(collected="2026-09-05T12:00:30.000001Z")
    result, _, _ = _read(payload)
    assert result.error is WorkloadErrorCode.FUTURE

    source = SourceResult(
        WorkloadOwner.MEDIA, ResultStatus.COMPLETE, NOW, (), Truncation(0, 0)
    )
    combined = build_node_workloads(HOST, WorkloadQuery(), NOW, {result.owner: result, source.owner: source})
    by_owner = {item.owner: item for item in combined.sources}
    assert by_owner[WorkloadOwner.MEDIA] == source


@pytest.mark.parametrize("change", ("extra", "duplicate-source", "wrong-owner"))
def test_closed_router_wire_requires_one_exact_router_source(change):
    data = json.loads(_wire())
    if change == "extra":
        data["unexpected"] = "private"
    elif change == "duplicate-source":
        data["sources"].append(data["sources"][0])
    else:
        data["sources"][0]["owner"] = "media"
    result, _, _ = _read(json.dumps(data, separators=(",", ":")).encode("ascii"))
    assert result.status is ResultStatus.UNAVAILABLE
    assert result.error is WorkloadErrorCode.INVALID


def test_invalid_arguments_raise_before_opener():
    calls = []

    def opener(*args):
        calls.append(args)
        raise AssertionError("must not open")

    with pytest.raises(WorkloadError):
        read_router_workloads("http://127.0.0.1:8765/v1", "ROUTER_WORKLOAD_TOKEN", HOST, object(), NOW, _open=opener)
    with pytest.raises(WorkloadError):
        read_router_workloads("http://127.0.0.1:8765/v1", "ROUTER_WORKLOAD_TOKEN", "", WorkloadQuery(), NOW, _open=opener)
    assert calls == []
