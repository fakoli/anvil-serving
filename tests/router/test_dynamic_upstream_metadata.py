"""Runtime-owned model identity and context for a stable direct alias."""
from __future__ import annotations

import json
from urllib.parse import urlsplit

import pytest

from anvil_serving.router.availability import (
    AvailabilityResult,
    HttpHealthAvailability,
    RuntimeModelMetadata,
)
from anvil_serving.router.config import ConfigError, load
from anvil_serving.router.internal import InternalRequest, Message, NoAvailableTierError
from anvil_serving.router.model_capacity import MetricsSnapshot
from anvil_serving.router.serve import RoutingBackend, build_backends


_CONFIG = """
[router]
availability_probe_interval = 5
availability_probe_timeout = 1

[[router.tiers]]
id = "mid-mod"
base_url = "http://100.64.0.10:39038/v1"
dialect = "openai"
metadata_source = "upstream"
privacy = "local"
tool_support = true
auth_env = "ANVIL_TEST_KEY"
health_path = "/health"

[router.tiers.params.capabilities]
modalities = ["text", "image", "video"]
images_per_request = 8
video_per_request = 2

[router.model_routes]
llm.secondary = "mid-mod"
"""


def _write_config(tmp_path, body: str = _CONFIG):
    path = tmp_path / "router.toml"
    path.write_text(body, encoding="utf-8")
    return path


class _Response:
    def __init__(self, payload=b"", status=200):
        self._payload = payload
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def getcode(self):
        return self.status

    def read(self, limit=-1):
        return self._payload if limit < 0 else self._payload[:limit]


class _RuntimeOpener:
    def __init__(self):
        self.model = "qwen38-q4-mtp3-262k"
        self.context = 262_144
        self.engine = "llamacpp"
        self.extra_models = []
        self.props_model = None
        self.calls = []

    def __call__(self, request, *, timeout):
        del timeout
        path = urlsplit(request.full_url).path
        self.calls.append(path)
        if path == "/health":
            return _Response()
        if path == "/v1/models":
            entry = {"id": self.model, "owned_by": self.engine}
            if self.engine != "llamacpp" and self.context is not None:
                entry["max_model_len"] = self.context
            return _Response(json.dumps({
                "data": [entry, *self.extra_models],
            }).encode())
        if path == "/props":
            return _Response(json.dumps({
                "model_alias": self.props_model or self.model,
                "model_ftype": "Q4_0",
                "build_info": "b10548-a298422da",
                "total_slots": 1,
                "modalities": {"vision": True, "video": True, "audio": False},
                "default_generation_settings": {"n_ctx": self.context},
            }).encode())
        raise AssertionError(path)


class _CaptureBackend:
    def __init__(self):
        self.models = []

    def generate(self, request):
        self.models.append(request.model)
        return iter(("ok",))


class _MutableAvailability:
    def __init__(self, metadata: RuntimeModelMetadata | None):
        self.metadata = metadata

    def check(self, _tier):
        return AvailabilityResult(
            self.metadata is not None,
            "ready" if self.metadata is not None else "unavailable",
            "upstream_metadata_passed" if self.metadata is not None else "metadata_missing",
            observed_model=self.metadata.model if self.metadata is not None else None,
            runtime_metadata=self.metadata,
        )


def test_upstream_metadata_config_has_no_static_model_or_context(tmp_path):
    config = load(str(_write_config(tmp_path)))
    tier = config.tier("mid-mod")

    assert tier.metadata_source == "upstream"
    assert tier.model is None
    assert tier.context_limit == 0


@pytest.mark.parametrize(
    "field",
    [
        'model = "stale-model"',
        "context_limit = 131072",
        'engine = "stale-engine"',
        'quantization = "stale-quant"',
        "model_identity = true",
        'params = { fingerprint = { engine_version = "stale-build" } }',
    ],
)
def test_upstream_metadata_config_rejects_duplicate_static_authority(tmp_path, field):
    body = _CONFIG.replace(
        'health_path = "/health"',
        'health_path = "/health"\n' + field,
    )
    with pytest.raises(ConfigError, match="upstream|model_identity"):
        load(str(_write_config(tmp_path, body)))


def test_upstream_metadata_requires_health_path(tmp_path):
    with pytest.raises(ConfigError, match="requires health_path"):
        load(str(_write_config(tmp_path, _CONFIG.replace('health_path = "/health"\n', ""))))


def test_llamacpp_metadata_uses_models_identity_and_props_configuration(tmp_path):
    config = load(str(_write_config(tmp_path)))
    tier = config.tier("mid-mod")
    opener = _RuntimeOpener()
    availability = HttpHealthAvailability(config, opener=opener, env={})

    result = availability.check(tier)

    assert result.available is True
    assert result.reason == "upstream_metadata_passed"
    assert result.runtime_metadata == RuntimeModelMetadata(
        model="qwen38-q4-mtp3-262k",
        context_limit=262_144,
        engine="llamacpp",
        quantization="Q4_0",
        engine_version="b10548-a298422da",
        max_concurrency=1,
        modalities=("image", "text", "video"),
    )
    assert opener.calls == ["/health", "/v1/models", "/props"]


@pytest.mark.parametrize(
    ("mutation", "reason"),
    [
        (
            lambda opener: opener.extra_models.append({
                "id": "second-model",
                "owned_by": "llamacpp",
            }),
            "upstream_metadata_model_count",
        ),
        (
            lambda opener: setattr(opener, "props_model", "different-model"),
            "upstream_metadata_identity_mismatch",
        ),
        (
            lambda opener: (
                setattr(opener, "engine", "vllm"),
                setattr(opener, "context", None),
            ),
            "upstream_metadata_context_missing",
        ),
    ],
)
def test_upstream_metadata_fails_closed_on_ambiguous_or_incomplete_facts(
    tmp_path, mutation, reason
):
    config = load(str(_write_config(tmp_path)))
    tier = config.tier("mid-mod")
    opener = _RuntimeOpener()
    mutation(opener)

    result = HttpHealthAvailability(config, opener=opener, env={}).check(tier)

    assert result.available is False
    assert result.reason == reason
    assert result.runtime_metadata is None


def test_upstream_metadata_rejects_context_below_router_output_policy(tmp_path):
    body = _CONFIG.replace(
        'health_path = "/health"',
        'health_path = "/health"\nmax_output_tokens = 300000',
    )
    config = load(str(_write_config(tmp_path, body)))
    tier = config.tier("mid-mod")
    opener = _RuntimeOpener()

    result = HttpHealthAvailability(config, opener=opener, env={}).check(tier)

    assert result.available is False
    assert result.reason == "upstream_metadata_output_limit_conflict"


def test_cached_metadata_refresh_adopts_a_new_model_and_context(tmp_path):
    now = [0.0]
    config = load(str(_write_config(tmp_path)))
    tier = config.tier("mid-mod")
    opener = _RuntimeOpener()
    availability = HttpHealthAvailability(
        config, opener=opener, clock=lambda: now[0], env={}
    )

    first = availability.check(tier)
    opener.model = "qwen38-vllm-196k"
    opener.context = 196_608
    opener.engine = "vllm"
    cached = availability.check(tier)
    now[0] = 6.0
    refreshed = availability.check(tier)

    assert cached.runtime_metadata == first.runtime_metadata
    assert refreshed.runtime_metadata == RuntimeModelMetadata(
        model="qwen38-vllm-196k",
        context_limit=196_608,
        engine="vllm",
    )
    assert opener.calls[-2:] == ["/health", "/v1/models"]


def test_routing_uses_observed_model_but_preserves_public_alias(tmp_path):
    config = load(str(_write_config(tmp_path)))
    capture = _CaptureBackend()
    availability = _MutableAvailability(RuntimeModelMetadata(
        model="qwen38-q4-mtp3-262k",
        context_limit=262_144,
        engine="llamacpp",
        quantization="Q4_0",
    ))
    routing = RoutingBackend(
        config,
        {"mid-mod": capture},
        availability=availability,
        capacity_metrics=lambda _tier: MetricsSnapshot("unavailable", {}),
    )
    request = InternalRequest("llm.secondary", [Message("user", "hello")])

    assert list(routing.generate(request)) == ["ok"]
    assert capture.models == ["qwen38-q4-mtp3-262k"]
    assert request.model == "llm.secondary"
    assert routing.model_discovery()["data"][0]["context_window"] == 262_144
    capability = routing.model_capabilities({})["data"][0]
    assert capability["model"] == "qwen38-q4-mtp3-262k"
    assert capability["context_limit_tokens"] == 262_144
    fingerprint = routing.model_fingerprints({})["data"][0]
    assert fingerprint["served_identity"] == {
        "expected": None,
        "observed": "qwen38-q4-mtp3-262k",
    }
    assert fingerprint["served_configuration"]["metadata_source"] == "upstream"
    assert fingerprint["served_configuration"]["quantization"] == "Q4_0"
    assert fingerprint["served_configuration"]["modalities"] == []


def test_dynamic_context_is_the_admission_limit_and_missing_metadata_fails_closed(tmp_path):
    config = load(str(_write_config(tmp_path)))
    capture = _CaptureBackend()
    availability = _MutableAvailability(RuntimeModelMetadata(
        model="small-model",
        context_limit=1,
    ))
    routing = RoutingBackend(
        config, {"mid-mod": capture}, availability=availability
    )

    with pytest.raises(NoAvailableTierError) as over_context:
        routing.generate(InternalRequest(
            "llm.secondary", [Message("user", "two words")]
        ))
    assert over_context.value.kind == "over_context"
    assert capture.models == []

    availability.metadata = None
    with pytest.raises(NoAvailableTierError) as unavailable:
        routing.generate(InternalRequest(
            "llm.secondary", [Message("user", "ok")]
        ))
    assert unavailable.value.kind == "unavailable"


def test_dynamic_backend_build_never_freezes_startup_discovery(tmp_path):
    config = load(str(_write_config(tmp_path)))

    def forbidden_discovery(*_args, **_kwargs):
        raise AssertionError("dynamic tiers must refresh through availability")

    backends, skipped = build_backends(
        config,
        env={},
        transport=lambda *_args, **_kwargs: b"{}",
        model_discovery_transport=forbidden_discovery,
    )

    assert set(backends) == {"mid-mod"}
    assert skipped == []
