from __future__ import annotations

import io
import json
import urllib.error

import pytest

from anvil_serving.benchmarking.jobs import BenchmarkJobError, JOB_SPEC_SCHEMA
from anvil_serving.benchmarking.preflight import (
    require_benchmark_preflight,
    run_benchmark_preflight,
)


class Response:
    def __init__(self, value):
        self.value = value

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def read(self, _limit):
        return json.dumps(self.value).encode()


def _spec(**changes):
    value = {
        "schema": JOB_SPEC_SCHEMA,
        "run_id": "preflight-001",
        "ownership_id": "campaign-001",
        "suite": "swe",
        "profile": "swe-smoke-v1",
        "endpoint": {"base_url": "http://127.0.0.1:8000/v1", "model": "deepseek"},
        "worker": {"id": "benchmark-worker"},
        "submitted_at": "2026-08-03T12:00:00Z",
        "timeout_s": 600,
        "parameters": {"model_host_id": "model-host"},
    }
    value.update(changes)
    return value


def _opener(models=None):
    value = models or [{"id": "deepseek", "max_model_len": 650000}]
    return lambda *_args, **_kwargs: Response({"data": value})


def test_records_worker_endpoint_and_unsupported_telemetry(tmp_path):
    artifact = run_benchmark_preflight(
        _spec(),
        run_root=str(tmp_path / "runs"),
        requirements={"min_free_disk_bytes": 1, "model_host_id": "model-host"},
        opener=_opener(),
        container_binary="docker",
    )
    assert artifact["passed"] is True
    assert artifact["observed"]["endpoint"]["configured_context"] == 650000
    assert artifact["side_effects"] == {"model_lifecycle": False, "route_mutation": False}
    assert artifact["observed"]["endpoint"]["authentication"] == "none"


def test_missing_model_host_identity_fails_isolation_closed(tmp_path):
    artifact = run_benchmark_preflight(
        _spec(parameters={}),
        run_root=str(tmp_path / "runs"),
        opener=_opener(),
    )

    isolation = next(item for item in artifact["checks"] if item["name"] == "worker_isolation")
    assert isolation == {
        "name": "worker_isolation",
        "passed": False,
        "code": "model_host_identity_absent",
        "detail": "model host identity was not declared",
    }


def test_missing_credentials_and_authorization_are_distinct(monkeypatch, tmp_path):
    secured = _spec(endpoint={
        "base_url": "http://127.0.0.1:8000/v1",
        "model": "deepseek",
        "auth_env": "ANVIL_ROUTER_TOKEN",
    })
    monkeypatch.delenv("ANVIL_ROUTER_TOKEN", raising=False)
    missing = run_benchmark_preflight(
        secured, run_root=str(tmp_path / "one"), opener=_opener()
    )
    assert "missing_credentials" in {item["code"] for item in missing["checks"]}

    monkeypatch.setenv("ANVIL_ROUTER_TOKEN", "not-recorded")
    error = urllib.error.HTTPError("url", 401, "denied", {}, io.BytesIO())
    denied = run_benchmark_preflight(
        secured,
        run_root=str(tmp_path / "two"),
        opener=lambda *_args, **_kwargs: (_ for _ in ()).throw(error),
    )
    assert "authorization_denied" in {item["code"] for item in denied["checks"]}
    assert "not-recorded" not in json.dumps(denied)


def test_endpoint_auth_normalizes_crlf_dotenv_value(monkeypatch, tmp_path):
    secured = _spec(endpoint={
        "base_url": "http://127.0.0.1:8000/v1",
        "model": "deepseek",
        "auth_env": "ANVIL_ROUTER_TOKEN",
    })
    monkeypatch.setenv("ANVIL_ROUTER_TOKEN", "  routed-token\r")

    def opener(request, **_kwargs):
        assert request.get_header("Authorization") == "Bearer routed-token"
        return Response({"data": [{"id": "deepseek", "max_model_len": 650000}]})

    artifact = run_benchmark_preflight(
        secured, run_root=str(tmp_path / "normalized"), opener=opener
    )

    assert artifact["passed"] is True


@pytest.mark.parametrize(
    ("models", "code"),
    [([{"id": "other"}], "model_mismatch")],
)
def test_endpoint_failure_taxonomy(tmp_path, models, code):
    artifact = run_benchmark_preflight(
        _spec(), run_root=str(tmp_path), opener=_opener(models)
    )
    assert code in {item["code"] for item in artifact["checks"]}


def test_worker_disk_architecture_and_assets_fail_closed(tmp_path):
    class Usage:
        free = 5

    artifact = run_benchmark_preflight(
        _spec(),
        run_root=str(tmp_path / "runs"),
        requirements={
            "min_free_disk_bytes": 10,
            "architectures": ["impossible-arch"],
            "container_required": True,
            "harness_assets": ["adapter.lock"],
            "model_host_id": "benchmark-worker",
        },
        assets_root=str(tmp_path / "assets"),
        opener=_opener(),
        disk_usage=lambda _path: Usage(),
        container_binary="",
    )
    codes = {item["code"] for item in artifact["checks"]}
    assert {
        "worker_not_isolated",
        "incompatible_architecture",
        "insufficient_disk",
        "container_capability_absent",
        "absent_harness_assets",
    } <= codes
    with pytest.raises(BenchmarkJobError) as exc:
        require_benchmark_preflight(artifact)
    assert exc.value.code == "preflight_failed"


def test_harness_lock_is_verified(tmp_path):
    assets = tmp_path / "assets"
    assets.mkdir()
    locked = assets / "adapter.lock"
    locked.write_text("pinned\n", encoding="utf-8")
    artifact = run_benchmark_preflight(
        _spec(),
        run_root=str(tmp_path / "runs"),
        requirements={"harness_locks": {"adapter.lock": "0" * 64}},
        assets_root=str(assets),
        opener=_opener(),
    )
    assert "harness_lock_mismatch" in {item["code"] for item in artifact["checks"]}
