"""Contract tests for ``GET /v1/models/capacity``."""
from __future__ import annotations

import json
import threading
from dataclasses import replace

import pytest

from anvil_serving.router.admission import AdmissionSnapshot, TierAdmission
from anvil_serving.router.availability import AlwaysAvailable, AvailabilityResult
from anvil_serving.router.config import RouterConfig, ServerConfig, Tier
from anvil_serving.router import serve as serve_module
from anvil_serving.router.front_door import MODEL_CAPACITY_ENDPOINT, make_server
from anvil_serving.router.model_capacity import (
    MetricsSnapshot,
    build_model_capacity,
    fetch_vllm_metrics,
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
        },
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


def test_capacity_snapshot_joins_config_readiness_and_live_engine_metrics():
    result = build_model_capacity(_config(), AlwaysAvailable(), _live, {})
    (row,) = result["data"]
    assert result["object"] == "list"
    assert row["id"] == "primary-local"
    assert row["aliases"] == ["llm.primary", "vision.general"]
    assert row["model"] == "qwen35-122b-a10b-nvfp4"
    assert row["loaded"] is True
    assert row["engine"] == {"name": "vllm", "quantization": "nvfp4"}
    assert row["capacity"] == {
        "context_limit_tokens": 262_144,
        "kv_cache_capacity_tokens": 571_950,
        "full_context_concurrency": 2.182,
        "configured_max_concurrency": 1,
        "scheduler_max_num_seqs": 1,
        "model_memory_gib": 73.22,
    }
    assert row["multimodal"] == {
        "admission_enabled": True,
        "image_limit": 1,
        "video_limit": 1,
        "image_tokens_estimate": 2048,
        "video_tokens_estimate": 8192,
    }
    assert row["live"]["kv_cache_used_tokens_estimate"] == 142_988
    assert row["live"]["kv_cache_remaining_tokens_estimate"] == 428_962


def test_only_allowlisted_capacity_metadata_is_emitted():
    body = json.dumps(build_model_capacity(_config(), AlwaysAvailable(), _live, {}))
    assert "private_note" not in body
    assert "never-emit-me" not in body
    assert "127.0.0.1" not in body
    assert "30002" not in body
    assert "ANVIL_TEST_KEY" not in body


def test_one_image_scenario_is_allowed_when_image_tokens_are_supplied():
    result = build_model_capacity(
        _config(),
        AlwaysAvailable(),
        _live,
        {
            "model": ["llm.primary"],
            "images": ["1"],
            "input_tokens": ["1000"],
            "image_tokens": ["2048"],
            "output_tokens": ["4096"],
        },
    )
    scenario = result["data"][0]["scenario"]
    assert scenario["context_tokens"] == 7144
    assert scenario["within_image_limit"] is True
    assert scenario["within_context_limit"] is True
    assert scenario["allowed"] is True


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


def test_two_image_scenario_is_rejected_by_the_declared_limit():
    result = build_model_capacity(
        _config(),
        AlwaysAvailable(),
        _live,
        {"images": ["2"]},
    )
    scenario = result["data"][0]["scenario"]
    assert scenario["within_image_limit"] is False
    assert scenario["within_context_limit"] is True
    assert scenario["allowed"] is False


def test_video_scenario_accounts_for_count_and_visual_tokens():
    result = build_model_capacity(
        _config(),
        AlwaysAvailable(),
        _live,
        {
            "videos": ["1"],
            "input_tokens": ["1000"],
            "video_tokens": ["8192"],
            "output_tokens": ["16384"],
        },
    )
    scenario = result["data"][0]["scenario"]
    assert scenario["videos"] == 1
    assert scenario["context_tokens"] == 25_576
    assert scenario["within_video_limit"] is True
    assert scenario["allowed"] is True


def test_video_count_uses_declared_visual_token_estimate():
    result = build_model_capacity(
        _config(),
        AlwaysAvailable(),
        _live,
        {"videos": ["1"]},
    )
    scenario = result["data"][0]["scenario"]
    assert scenario["within_video_limit"] is True
    assert scenario["video_tokens"] == 8192
    assert scenario["video_tokens_source"] == "configured_estimate"
    assert scenario["within_context_limit"] is True
    assert scenario["allowed"] is True


def test_missing_visual_estimate_remains_indeterminate_not_free():
    config = _config()
    capacity = config.tiers[0].params["capacity"]
    capacity.pop("image_tokens_estimate")
    capacity.pop("video_tokens_estimate")
    scenario = build_model_capacity(
        config,
        AlwaysAvailable(),
        _live,
        {"images": ["1"], "videos": ["1"]},
    )["data"][0]["scenario"]
    assert scenario["within_context_limit"] is None
    assert scenario["allowed"] is None
    assert "configured image_tokens_estimate is required" in scenario["note"]
    assert "configured video_tokens_estimate is required" in scenario["note"]


def test_video_limit_and_context_overflow_are_rejected():
    over_count = build_model_capacity(
        _config(), AlwaysAvailable(), _live, {"videos": ["2"]}
    )["data"][0]["scenario"]
    over_context = build_model_capacity(
        _config(),
        AlwaysAvailable(),
        _live,
        {"videos": ["1"], "video_tokens": ["262144"], "output_tokens": ["1"]},
    )["data"][0]["scenario"]

    assert over_count["within_video_limit"] is False
    assert over_count["allowed"] is False
    assert over_context["within_context_limit"] is False
    assert over_context["allowed"] is False


def test_metrics_provider_fault_degrades_only_live_values():
    def broken(_tier):
        raise RuntimeError("must not escape")

    result = build_model_capacity(_config(), AlwaysAvailable(), broken, {})
    (row,) = result["data"]
    assert row["loaded"] is True
    assert row["live"]["status"] == "unavailable"
    assert row["live"]["reason"] == "metrics_provider"


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


def test_bad_query_is_400_and_plain_backend_returns_empty_list():
    with _server() as (host, port):
        status, _, raw = _get(
            host, port, MODEL_CAPACITY_ENDPOINT + "?images=not-an-int"
        )
    assert status == 400
    assert json.loads(raw)["error"]["type"] == "invalid_request"

    httpd = make_server("127.0.0.1", 0, StaticBackend(["ok"]))
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = httpd.server_address[:2]
        status, _, raw = _get(host, port, MODEL_CAPACITY_ENDPOINT, token=None)
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)
    assert status == 200
    assert json.loads(raw) == {"object": "list", "data": []}


def test_vllm_metrics_fetch_is_bounded_and_model_filtered():
    payload = b"""\
# HELP vllm:num_requests_running Number running
vllm:num_requests_running{model_name="other"} 99
vllm:num_requests_running{model_name="qwen35-122b-a10b-nvfp4"} 1
vllm:num_requests_waiting{model_name="qwen35-122b-a10b-nvfp4"} 2
vllm:kv_cache_usage_perc{model_name="qwen35-122b-a10b-nvfp4"} 0.5
"""

    class _Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self, _amount):
            return payload

    seen = {}

    def opener(request, timeout):
        seen["url"] = request.full_url
        seen["authorization"] = request.get_header("Authorization")
        seen["timeout"] = timeout
        return _Response()

    result = fetch_vllm_metrics(
        _tier(),
        env={"ANVIL_TEST_KEY": "upstream-secret"},
        opener=opener,
        timeout=0.5,
    )
    assert result.status == "available"
    assert result.values["requests_running"] == 1.0
    assert result.values["requests_waiting"] == 2.0
    assert result.values["kv_cache_usage_fraction"] == 0.5
    assert seen == {
        "url": "http://127.0.0.1:30002/metrics",
        "authorization": "Bearer upstream-secret",
        "timeout": 0.5,
    }


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


def test_replica_capacity_absent_or_inconsistent_admission_is_not_idle():
    config = _replica_config()
    cases = [None]
    for snapshot in (
        AdmissionSnapshot("wrong", "admitting", "secret", 0),
        AdmissionSnapshot("primary-local", "admitting", "secret", 4, member_active_requests=(("member-a", 1), ("member-b", 2))),
        AdmissionSnapshot("primary-local", "admitting", "secret", True, member_active_requests=(("member-a", 1), ("member-b", 0))),
        AdmissionSnapshot("primary-local", "secret", "secret", 0, member_active_requests=(("member-a", 0), ("member-b", 0))),
        AdmissionSnapshot("primary-local", "admitting", "secret", 0, member_active_requests=(("member-a", 0), ("secret-member", 0))),
        AdmissionSnapshot("primary-local", "admitting", "secret", 0, True, member_active_requests=(("member-a", 0), ("member-b", 0))),
    ):
        class Source:
            def __init__(self, value):
                self.value = value

            def snapshot(self, tier):
                return self.value
        cases.append(Source(snapshot))
    for source in cases:
        row = build_model_capacity(config, _MemberAvailability(), _live, {}, admission=source)["data"][0]
        assert row["admission"]["status"] == "unavailable"
        assert row["admission"]["active_requests"] is None
        assert "secret" not in json.dumps(row)


@pytest.mark.parametrize(
    "count,available", [((1 << 53) - 1, True), (1 << 53, False), (10**5000, False)],
    ids=["maximum-safe", "overflow", "serialization-overflow"],
)
def test_replica_admission_counts_are_bounded_exact_json_integers(count, available):
    class Source:
        def snapshot(self, tier):
            return AdmissionSnapshot(
                tier, "quiesced", "private reason", count, True,
                member_active_requests=(("member-a", count), ("member-b", 0)),
            )

    row = build_model_capacity(_replica_config(), _MemberAvailability(), _live, {}, admission=Source())["data"][0]
    assert row["admission"]["status"] == ("available" if available else "unavailable")
    assert row["admission"]["active_requests"] == (count if available else None)
    assert json.loads(json.dumps(row)) == row


@pytest.mark.parametrize("available,state", [(False, "ready"), (True, "unavailable")])
def test_replica_readiness_cannot_publish_success_for_inconsistent_result(available, state):
    class Source:
        def check_member(self, tier, member):
            return AvailabilityResult(available, state, "identity_passed", tier.model, tier.model)

    row = build_model_capacity(_replica_config(), Source(), _live, {})["data"][0]
    assert row["loaded"] is False
    for member in row["members"]:
        assert member["readiness"] == {"loaded": False, "state": "unavailable", "reason": "unavailable"}


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


@pytest.mark.parametrize("size", [0, 1, 17])
def test_replica_projection_refuses_invalid_member_bounds(size):
    # Empty tuple denotes a direct tier by contract, so exercise the pure
    # replica projection boundary itself rather than pretending it is parsed.
    from anvil_serving.router.model_capacity import replica_metadata
    config = _replica_config()
    tier = replace(config.tiers[0], replicas=config.tiers[0].replicas[:1] * size)
    with pytest.raises(ValueError, match="invalid replica projection"):
        replica_metadata(tier, _MemberAvailability())


def test_replica_projection_rejects_unsafe_declared_metadata_before_probing():
    from anvil_serving.router.model_capacity import replica_metadata
    tier = _replica_config().tiers[0]
    source = _MemberAvailability()
    for member in (
        replace(tier.replicas[0], id="http://private"),
        replace(tier.replicas[0], qualification_ref="C:/private/path"),
        replace(tier.replicas[0], id=tier.replicas[1].id),
    ):
        with pytest.raises(ValueError, match="invalid replica projection"):
            replica_metadata(replace(tier, replicas=(member, tier.replicas[1])), source)
    assert source.calls == []


def test_replica_http_capacity_uses_the_exact_owner_for_every_lifecycle_state():
    config = _replica_config()
    tier = config.tiers[0]

    class Owner(TierAdmission):
        reads = 0

        def __bool__(self):
            return False  # An explicitly injected empty owner must be retained.

        def snapshot(self, tier_id):
            self.reads += 1
            return super().snapshot(tier_id)

    owner = Owner([tier.id], replica_members={tier.id: ("member-a", "member-b")})
    metrics_calls = []
    routing = RoutingBackend(
        config, {tier.id: StaticBackend(["unused"])}, admission=owner,
        availability=_MemberAvailability(),
        capacity_metrics=lambda tier: metrics_calls.append(tier) or _live(tier),
    )
    assert routing._admission is owner
    lease = None
    with server_context(routing, token=TOKEN) as (host, port):
        assert _get(host, port, MODEL_CAPACITY_ENDPOINT, token=None)[0] == 401
        assert owner.reads == 0

        def check(state, active, members):
            before = owner.reads
            status, headers, raw = _get(host, port, MODEL_CAPACITY_ENDPOINT)
            assert status == 200
            assert headers["Cache-Control"] == "no-store"
            assert owner.reads == before + 1
            row = json.loads(raw)["data"][0]
            assert row["admission"] == {
                "status": "available", "state": state, "active_requests": active,
                "draining": False, "member_active_requests": members,
            }
            assert row["aliases"] == ["llm.primary"]
            assert row["runtime_deployment_identity_verified"] is False
            for prohibited in ("127.0.0.1", "private", "ANVIL_TEST_KEY", "operator_reason"):
                assert prohibited not in raw.decode()

        try:
            check("admitting", 0, {"member-a": 0, "member-b": 0})
            lease = owner.acquire_member(tier.id, {
                member.id: AvailabilityResult(True, "ready", "identity_passed")
                for member in tier.replicas
            })
            assert lease is not None
            check("admitting", 1, {"member-a": 1, "member-b": 0})
            routing.quiesce_tier(tier.id, "operator_reason")
            check("quiesced", 1, {"member-a": 1, "member-b": 0})
            lease.release()
            check("quiesced", 0, {"member-a": 0, "member-b": 0})
        finally:
            if lease is not None:
                lease.release()
    assert metrics_calls == []


@pytest.mark.parametrize("durable", [False, True])
def test_built_server_default_replica_owner_exposes_complete_zero_counts(
    tmp_path, monkeypatch, durable,
):
    config = _replica_config()
    intent_path = str(tmp_path / "admission.json") if durable else None
    monkeypatch.setattr(serve_module, "load", lambda path: config)
    monkeypatch.setattr(serve_module, "load_server_config", lambda path: ServerConfig(
        auth_env="ANVIL_TEST_KEY", admission_state_path=intent_path,
    ))
    httpd = serve_module.build_server(
        "synthetic-config.toml", host="127.0.0.1", port=0,
        backends={config.tiers[0].id: StaticBackend(["unused"])},
        availability=_MemberAvailability(), env={"ANVIL_TEST_KEY": TOKEN},
    )
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = httpd.server_address[:2]
        status, _, raw = _get(host, port, MODEL_CAPACITY_ENDPOINT)
        assert status == 200
        assert json.loads(raw)["data"][0]["admission"] == {
            "status": "available", "state": "admitting", "active_requests": 0,
            "draining": False,
            "member_active_requests": {"member-a": 0, "member-b": 0},
        }
        if durable:
            httpd.anvil_admission.quiesce(config.tiers[0].id, "maintenance")
            restored = serve_module._durable_admission(intent_path, config)
            assert restored.snapshot(config.tiers[0].id).state == "quiesced"
            assert dict(restored.snapshot(config.tiers[0].id).member_active_requests) == {
                "member-a": 0, "member-b": 0,
            }
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)
    assert not thread.is_alive()


@pytest.mark.parametrize("missing", [False, True])
def test_http_replica_owner_failure_stays_unavailable_not_idle(missing):
    class ForgedOwner:
        def snapshot(self, tier):
            return AdmissionSnapshot(tier, "admitting", "private reason", 0)

    config = _replica_config()
    routing = RoutingBackend(config, {config.tiers[0].id: StaticBackend(["unused"])},
                             availability=_MemberAvailability())
    routing._admission = None if missing else ForgedOwner()
    with server_context(routing, token=TOKEN) as (host, port):
        status, _, raw = _get(host, port, MODEL_CAPACITY_ENDPOINT)
    assert status == 200
    admission = json.loads(raw)["data"][0]["admission"]
    assert admission["status"] == "unavailable"
    assert admission["active_requests"] is None
    assert "private" not in raw.decode()
