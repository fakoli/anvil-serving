import datetime as dt
import io
import json
import urllib.error


from anvil_serving.observability.probes import router_workloads
from anvil_serving.observability.probes.router_workloads import read_router_workloads
from anvil_serving.observability.workloads import (
    WorkloadErrorCode,
    WorkloadQuery,
)


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


class _ThrowingCloseResponse(_Response):
    def close(self):
        self.closed = True
        raise RuntimeError("private close failure")


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


def _snapshot(payload, **kwargs):
    response = _Response(payload)
    result = router_workloads.read_router_node_workloads(
        "http://127.0.0.1:8765/v1", "ROUTER_WORKLOAD_TOKEN", HOST,
        kwargs.pop("query", WorkloadQuery()), NOW,
        environment={"ROUTER_WORKLOAD_TOKEN": TOKEN},
        _open=lambda *args, **options: response, **kwargs,
    )
    assert response.closed
    return result
