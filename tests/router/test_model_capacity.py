"""Contract tests for ``GET /v1/models/capacity``."""
from __future__ import annotations

import json
import threading
import time
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from anvil_serving.router.admission import (
    TierAdmission,
)
from anvil_serving.router.availability import AlwaysAvailable, AvailabilityResult
from anvil_serving.router.config import RouterConfig, ServerConfig, Tier
from anvil_serving.router import serve as serve_module
from anvil_serving.router.front_door import (
    MODEL_CAPACITY_ENDPOINT,
    PROMETHEUS_ENDPOINT,
)
from anvil_serving.router.model_capacity import (
    MetricsSnapshot,
    ReplicaPressureCache,
    build_model_capacity,
    fetch_engine_metrics,
    fetch_vllm_metrics,
)
from anvil_serving.router.replica_scheduler import (
    PressureFreshness,
    PressureSignalState,
    ReplicaPressure,
)
from anvil_serving.router.serve import RoutingBackend
from tests.router.helpers import StaticBackend
from tests.router.helpers import http_get as _http_get
from tests.router.helpers import server_context
from tests.router.test_model_metadata import _MemberAvailability, _replica_config

TOKEN = "router-test-token"


def _tier() -> Tier:
    return Tier(
        id="primary-local",
        base_url="http://127.0.0.1:30002/v1",
        model="qwen35-122b-a10b-nvfp4",
        dialect="openai",
        context_limit=262_144,
        privacy="local",
        tool_support=True,
        auth_env="ANVIL_TEST_KEY",
        engine="vllm",
        quantization="nvfp4",
        max_concurrency=1,
        params={
            "private_note": "never-emit-me",
            "capacity": {
                "gpu_role": "primary",
                "gpu_name": "NVIDIA RTX PRO 6000 Blackwell Workstation Edition",
                "gpu_memory_total_mib": 97_887,
                "model_memory_gib": 73.22,
                "kv_cache_capacity_tokens": 571_950,
                "scheduler_max_num_seqs": 1,
                "image_limit": 1,
                "video_limit": 1,
                "media_admission_enabled": True,
                "image_tokens_estimate": 2048,
                "video_tokens_estimate": 8192,
            },
        },
    )


def _config() -> RouterConfig:
    return RouterConfig(
        tiers=(_tier(),),
        model_routes={
            "llm.primary": "primary-local",
            "vision.general": "primary-local",
        },
    )


def _live(_tier) -> MetricsSnapshot:
    return MetricsSnapshot(
        "available",
        {
            "requests_running": 1.0,
            "requests_waiting": 2.0,
            "kv_cache_usage_fraction": 0.25,
            "preemptions_total": 3.0,
            "multimodal_cache_queries_total": 5.0,
            "multimodal_cache_hits_total": 4.0,
            "generation_tokens_total": 6.0,
            "generation_throughput_tokens_per_second": 7.0,
            "prefix_cache_queries_total": 8.0,
            "prefix_cache_hits_total": 4.0,
            "prefix_cache_hit_rate": 0.5,
        },
    )


def _capacity_replica_config(*, tier_max=6):
    original = _replica_config()
    tier = original.tiers[0]
    members = tuple(replace(member, max_concurrency=index + 3) for index, member in enumerate(tier.replicas))
    tier = replace(tier, replica_strategy="capacity", max_concurrency=tier_max, replicas=members)
    return replace(original, tiers=(tier,))


def _capacity_admission(config):
    tier = config.tiers[0]
    return TierAdmission(
        [tier.id],
        replica_members={tier.id: tuple(member.id for member in tier.replicas)},
        tier_max_concurrency={tier.id: tier.max_concurrency} if tier.max_concurrency else None,
        member_max_concurrency={
            tier.id: {member.id: member.max_concurrency for member in tier.replicas}
        },
        replica_strategies={tier.id: "capacity"},
    )


def _routing() -> RoutingBackend:
    return RoutingBackend(
        _config(),
        {"primary-local": StaticBackend(["ok"])},
        availability=AlwaysAvailable(),
        capacity_metrics=_live,
    )


def _server():
    return server_context(_routing(), token=TOKEN)


def _get(host, port, path, *, token=TOKEN):
    return _http_get(host, port, path, token=token)


def test_image_count_uses_declared_visual_token_estimate():
    result = build_model_capacity(
        _config(),
        AlwaysAvailable(),
        _live,
        {"model": ["llm.primary"], "images": ["1"]},
    )
    scenario = result["data"][0]["scenario"]
    assert scenario["within_image_limit"] is True
    assert scenario["image_tokens"] == 2048
    assert scenario["image_tokens_source"] == "configured_estimate"
    assert scenario["within_context_limit"] is True
    assert scenario["allowed"] is True


def test_endpoint_is_authenticated_filterable_and_not_cached():
    with _server() as (host, port):
        no_auth, _, _ = _get(host, port, MODEL_CAPACITY_ENDPOINT, token=None)
        status, headers, raw = _get(
            host, port, MODEL_CAPACITY_ENDPOINT + "?gpu_role=primary"
        )
        missing, _, missing_raw = _get(
            host, port, MODEL_CAPACITY_ENDPOINT + "?model=unknown"
        )
    assert no_auth == 401
    assert status == 200
    assert headers["Cache-Control"] == "no-store"
    assert len(json.loads(raw)["data"]) == 1
    assert missing == 404
    assert json.loads(missing_raw)["error"]["type"] == "model_not_found"


@pytest.mark.parametrize("engine", ["sglang", "SGLang-v0.4.3-custom"])
def test_engine_metrics_aggregates_sglang_data_parallel_schedulers_without_tp_pp_duplicates(engine):
    payload = b"""\
sglang:num_running_reqs{model_name="qwen35-122b-a10b-nvfp4",dp_rank="0",tp_rank="0",pp_rank="0"} 2
sglang:num_running_reqs{model_name="qwen35-122b-a10b-nvfp4",dp_rank="0",tp_rank="1",pp_rank="0"} 2
sglang:num_running_reqs{model_name="qwen35-122b-a10b-nvfp4",dp_rank="0",tp_rank="0",pp_rank="1"} 2
sglang:num_running_reqs{model_name="qwen35-122b-a10b-nvfp4",dp_rank="1",tp_rank="0",pp_rank="0"} 3
sglang:num_running_reqs{model_name="qwen35-122b-a10b-nvfp4",dp_rank="1",tp_rank="1",pp_rank="0"} 3
sglang:num_running_reqs{model_name="qwen35-122b-a10b-nvfp4",dp_rank="1",tp_rank="0",pp_rank="1"} 3
sglang:num_queue_reqs{model_name="qwen35-122b-a10b-nvfp4",dp_rank="0",tp_rank="0",pp_rank="0"} 4
sglang:num_queue_reqs{model_name="qwen35-122b-a10b-nvfp4",dp_rank="1",tp_rank="0",pp_rank="0"} 5
sglang:token_usage{model_name="qwen35-122b-a10b-nvfp4",dp_rank="0",tp_rank="0",pp_rank="0"} 0.25
sglang:token_usage{model_name="qwen35-122b-a10b-nvfp4",dp_rank="1",tp_rank="0",pp_rank="0"} 0.75
sglang:generation_tokens_total{model_name="qwen35-122b-a10b-nvfp4",dp_rank="0",tp_rank="0",pp_rank="0"} 12
sglang:generation_tokens_total{model_name="qwen35-122b-a10b-nvfp4",dp_rank="1",tp_rank="0",pp_rank="0"} 18
sglang:gen_throughput{model_name="qwen35-122b-a10b-nvfp4",dp_rank="0",tp_rank="0",pp_rank="0"} 42.5
sglang:gen_throughput{model_name="qwen35-122b-a10b-nvfp4",dp_rank="1",tp_rank="0",pp_rank="0"} 57.5
sglang:cache_hit_rate{model_name="qwen35-122b-a10b-nvfp4",dp_rank="0",tp_rank="0",pp_rank="0"} 0.25
sglang:cache_hit_rate{model_name="qwen35-122b-a10b-nvfp4",dp_rank="1",tp_rank="0",pp_rank="0"} 0.75
vllm:num_requests_running{model_name="qwen35-122b-a10b-nvfp4"} 100
"""

    class _Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self, _amount):
            return payload

    result = fetch_engine_metrics(
        replace(_tier(), engine=engine), env={}, opener=lambda *_args, **_kwargs: _Response()
    )
    assert result == MetricsSnapshot("available", {
        "requests_running": 5.0,
        "requests_waiting": 9.0,
        "kv_cache_usage_fraction": 0.5,
        "generation_tokens_total": 30.0,
        "generation_throughput_tokens_per_second": 100.0,
        "prefix_cache_hit_rate": 0.5,
    })


def test_default_metrics_transport_interrupts_slow_headers_and_releases_cache_workers():
    class _SlowHeaders(BaseHTTPRequestHandler):
        def do_GET(self):
            with self.server.lock:
                self.server.requests += 1
            self.connection.sendall(b"HTTP/1.1 200 OK\r\n")
            for byte in b"Content-Type: text/plain\r\n\r\n":
                try:
                    self.connection.sendall(bytes((byte,)))
                except OSError:
                    return
                time.sleep(0.01)

        def log_message(self, _format, *_args):
            return

    class _Server(ThreadingHTTPServer):
        daemon_threads = True

    server = _Server(("127.0.0.1", 0), _SlowHeaders)
    server.lock = threading.Lock()
    server.requests = 0
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    thread.start()
    base_url = "http://127.0.0.1:%s/v1" % server.server_port
    try:
        started = time.monotonic()
        result = fetch_vllm_metrics(replace(_tier(), base_url=base_url), env={}, timeout=0.06)
        assert result == MetricsSnapshot("unavailable", {}, "metrics_transport")
        assert time.monotonic() - started < 0.3

        capacity_tier = _capacity_replica_config().tiers[0]
        capacity_tier = replace(
            capacity_tier,
            replicas=tuple(
                replace(member, base_url=base_url) for member in capacity_tier.replicas
            ),
        )
        cache = ReplicaPressureCache(
            (capacity_tier,),
            metrics_provider=lambda tier: fetch_vllm_metrics(tier, env={}, timeout=0.06),
        )
        try:
            cache.snapshot(capacity_tier.id)
            deadline = time.monotonic() + 0.8
            while time.monotonic() < deadline:
                pressure = cache.peek(capacity_tier.id)
                if all(value.freshness is PressureFreshness.FAILED for value in pressure.values()):
                    break
                time.sleep(0.01)
            assert all(value.freshness is PressureFreshness.FAILED for value in pressure.values())
            with cache._condition:
                assert all(not entry.running and not entry.queued for entry in cache._entries.values())
                for entry in cache._entries.values():
                    entry.completed_at = None
            with server.lock:
                completed_requests = server.requests
            cache.snapshot(capacity_tier.id)
            deadline = time.monotonic() + 0.8
            while time.monotonic() < deadline:
                with server.lock:
                    if server.requests >= completed_requests + len(capacity_tier.replicas):
                        break
                time.sleep(0.01)
            with server.lock:
                assert server.requests >= completed_requests + len(capacity_tier.replicas)
        finally:
            cache.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=1)


def test_replica_capacity_reads_one_atomic_admission_snapshot_and_no_sentinel_metrics():
    config = _replica_config()
    tier = config.tiers[0]
    admission = TierAdmission([tier.id], replica_members={tier.id: [member.id for member in tier.replicas]})
    ready = {member.id: AvailabilityResult(True, "ready", "identity_passed") for member in tier.replicas}
    lease = admission.acquire_member(tier.id, ready)
    assert lease is not None
    calls = []

    def forbidden_metrics(tier):
        calls.append(tier)
        raise AssertionError("logical replica tier is not a metrics endpoint")

    try:
        (row,) = build_model_capacity(config, _MemberAvailability(), forbidden_metrics, {}, admission=admission)["data"]
        assert row["aliases"] == ["llm.primary"]
        assert row["loaded"] is True
        assert row["admission"] == {
            "status": "available", "state": "admitting", "active_requests": 1,
            "draining": False, "member_active_requests": {"member-a": 1, "member-b": 0},
        }
        assert row["live"]["status"] == "unavailable"
        assert row["live"]["reason"] == "replica_metrics_not_aggregated"
        assert row["capacity"]["context_limit_tokens"] == tier.context_limit
        assert row["capacity"]["kv_cache_capacity_tokens"] is None
        assert row["capacity"]["full_context_concurrency"] is None
        assert row["runtime_deployment_identity_verified"] is False
        assert calls == []
        admission.quiesce(tier.id, reason="private_operator_reason")
        quiesced = build_model_capacity(config, _MemberAvailability(), forbidden_metrics, {}, admission=admission)["data"][0]
        assert quiesced["admission"]["state"] == "quiesced"
        assert quiesced["admission"]["active_requests"] == 1
        assert "private_operator_reason" not in json.dumps(quiesced)
    finally:
        lease.release()
    assert build_model_capacity(config, _MemberAvailability(), forbidden_metrics, {}, admission=admission)["data"][0]["admission"]["active_requests"] == 0


def test_replica_capacity_redacts_all_untrusted_member_fields_and_exceptions():
    config = _replica_config()

    class Broken:
        def check_member(self, tier, member):
            if member == "member-a":
                raise RuntimeError("raw-private-error http://127.0.0.1/private")
            return AvailabilityResult(False, "private_state", "credential_like_ascii", "private-expected", "private-observed")

    row = build_model_capacity(config, Broken(), _live, {})["data"][0]
    assert row["loaded"] is False
    assert row["members"][0]["readiness"]["reason"] == "availability_member_check_failed"
    assert row["members"][1]["readiness"]["reason"] == "unavailable"
    text = json.dumps(row)
    for forbidden in ("127.0.0.1", "private", "credential_like_ascii", "node-a", "resource-a", "auth_env", "base_url"):
        assert forbidden not in text


@pytest.mark.parametrize("tier_max,expected_ceiling", [(6, 6), (None, 7)])
def test_capacity_strategy_projects_one_reconciled_atomic_owner_snapshot(
    tier_max, expected_ceiling,
):
    config = _capacity_replica_config(tier_max=tier_max)
    tier = config.tiers[0]
    owner = _capacity_admission(config)
    ready = {member.id: AvailabilityResult(True, "ready", "identity_passed") for member in tier.replicas}
    lease = owner.acquire_member(tier.id, ready, pressure={
        member.id: ReplicaPressure() for member in tier.replicas
    })
    assert lease is not None
    try:
        row = build_model_capacity(config, _MemberAvailability(), _live, {}, admission=owner)["data"][0]
        assert row["admission"] == {
            "status": "available",
            "state": "admitting",
            "active_requests": 1,
            "draining": False,
            "member_active_requests": {"member-a": 1, "member-b": 0},
            "max_concurrency": expected_ceiling,
            "members": [
                {
                    "id": "member-a", "state": "admitting", "active_requests": 1,
                    "max_concurrency": 4, "draining": False,
                },
                {
                    "id": "member-b", "state": "admitting", "active_requests": 0,
                    "max_concurrency": 3, "draining": False,
                },
            ],
        }
        assert all(member["freshness"] == "unknown" for member in row["members"])
        assert "reason" not in json.dumps(row["admission"])
        owner.quiesce_member(tier.id, "member-a", reason="private-maintenance")
        changed = build_model_capacity(
            config, _MemberAvailability(), _live, {}, admission=owner
        )["data"][0]["admission"]
        assert changed["members"][0] == {
            "id": "member-a", "state": "quiesced", "active_requests": 1,
            "max_concurrency": 4, "draining": False,
        }
        assert "private-maintenance" not in json.dumps(changed)
    finally:
        lease.release()


def test_build_server_capacity_and_metrics_use_existing_owners_without_side_effects(
    monkeypatch,
):
    config = _capacity_replica_config()
    tier = config.tiers[0]

    class Owner(TierAdmission):
        reads = 0

        def snapshot(self, tier_id):
            self.reads += 1
            return super().snapshot(tier_id)

    owner = Owner(
        [tier.id],
        replica_members={tier.id: tuple(member.id for member in tier.replicas)},
        tier_max_concurrency={tier.id: tier.max_concurrency},
        member_max_concurrency={
            tier.id: {member.id: member.max_concurrency for member in tier.replicas}
        },
        replica_strategies={tier.id: "capacity"},
    )
    ready = {
        member.id: AvailabilityResult(True, "ready", "identity_passed")
        for member in tier.replicas
    }
    lease = owner.acquire_member(
        tier.id, ready,
        pressure={member.id: ReplicaPressure() for member in tier.replicas},
    )
    assert lease is not None
    owner.quiesce(tier.id, reason="private-maintenance")

    fresh_zero = ReplicaPressure(
        PressureFreshness.FRESH,
        0,
        PressureSignalState.MISSING,
        PressureSignalState.VALID,
    )

    class PressureOwner:
        def __init__(self):
            self.peek_calls = 0
            self.closed = False

        def peek(self, tier_id):
            self.peek_calls += 1
            assert tier_id == tier.id
            return {"member-a": fresh_zero, "member-b": ReplicaPressure()}

        def snapshot(self, *_args):
            raise AssertionError("visibility must not schedule a pressure snapshot")

        def close(self):
            self.closed = True

    pressure = PressureOwner()

    def forbidden_metrics(_tier):
        raise AssertionError("visibility must not fetch replica metrics")

    monkeypatch.setattr(serve_module, "load", lambda _path: config)
    monkeypatch.setattr(
        serve_module,
        "load_server_config",
        lambda _path: ServerConfig(auth_env="ANVIL_TEST_KEY"),
    )
    httpd = serve_module.build_server(
        "synthetic-config.toml",
        host="127.0.0.1",
        port=0,
        backends={tier.id: StaticBackend(["must-not-dispatch"])},
        availability=_MemberAvailability(),
        admission=owner,
        capacity_metrics=forbidden_metrics,
        env={"ANVIL_TEST_KEY": TOKEN},
    )
    routing = httpd.anvil_routing
    routing._replica_pressure.close()
    routing._replica_pressure = pressure
    cursor_before = owner._tiers[tier.id].cursor
    thread = threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    thread.start()
    try:
        host, port = httpd.server_address[:2]
        assert _get(host, port, MODEL_CAPACITY_ENDPOINT, token=None)[0] == 401
        assert _get(host, port, PROMETHEUS_ENDPOINT, token=None)[0] == 401
        assert owner.reads == 0
        assert pressure.peek_calls == 0

        status, headers, raw = _get(host, port, MODEL_CAPACITY_ENDPOINT)
        assert status == 200
        assert headers["Cache-Control"] == "no-store"
        row = json.loads(raw)["data"][0]
        assert row["admission"] == {
            "status": "available", "state": "quiesced", "active_requests": 1,
            "draining": False,
            "member_active_requests": {"member-a": 1, "member-b": 0},
            "max_concurrency": 6,
            "members": [
                {"id": "member-a", "state": "admitting", "active_requests": 1,
                 "max_concurrency": 4, "draining": False},
                {"id": "member-b", "state": "admitting", "active_requests": 0,
                 "max_concurrency": 3, "draining": False},
            ],
        }
        assert [
            (member["id"], member["freshness"], member["pressure_ppm"])
            for member in row["members"]
        ] == [("member-a", "fresh", 0), ("member-b", "unknown", None)]
        assert owner.reads == 1
        assert pressure.peek_calls == 1

        metrics_status, _, metrics = _get(host, port, PROMETHEUS_ENDPOINT)
        assert metrics_status == 200
        rendered = metrics.decode()
        assert 'anvil_router_replica_tier_active_requests{tier="primary-local"} 1' in rendered
        assert (
            'anvil_router_replica_member_pressure_ppm{tier="primary-local",member="member-a"} 0'
            in rendered
        )
        assert (
            'anvil_router_replica_member_pressure_freshness{tier="primary-local",member="member-b",freshness="unknown"} 1'
            in rendered
        )
        assert owner.reads == 2
        assert pressure.peek_calls == 2
        assert owner._tiers[tier.id].cursor == cursor_before
        assert owner.snapshot(tier.id).active_requests == 1
        assert owner.snapshot(tier.id).state == "quiesced"

        class UnavailableOwner:
            def snapshot(self, _tier_id):
                raise RuntimeError("private-owner-error")

        routing._admission = UnavailableOwner()
        failed_status, _, failed_raw = _get(host, port, MODEL_CAPACITY_ENDPOINT)
        assert failed_status == 200
        failed = json.loads(failed_raw)["data"][0]["admission"]
        assert failed == {
            "status": "unavailable", "state": None, "active_requests": None,
            "draining": None, "member_active_requests": None,
            "max_concurrency": None, "members": None,
        }
        assert "private-owner-error" not in failed_raw.decode()
    finally:
        lease.release()
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)
    assert not thread.is_alive()
    assert pressure.closed is True
