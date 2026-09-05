"""Backend-construction tests for genericity F001 (T005 / T003 / T002).

Hermetic and stdlib-only: every test injects a fake transport, never touching a
real network.

  * T005 — [router].relay_timeout is threaded through build_backends ->
    build_backend_for_tier so a LOCAL tier's backend actually uses it as its
    transport timeout.
  * T003 — a tier's extra_body is merged verbatim into the upstream request
    body (both dialects); absent extra_body is a no-op (no regression).
  * T002 — a local tier with model=None auto-derives its served model id from
    GET {base_url}/v1/models at backend-build time; explicit model= always
    wins; a malformed (0/>1 candidate) catalog is a ConfigError; a network
    failure is non-fatal (model stays None).
"""
from __future__ import annotations

import io
import json
import threading
import urllib.error
from dataclasses import replace
from typing import Dict

import pytest

from anvil_serving.router.backends import relay as relay_module
from anvil_serving.router.backends.relay import (
    RelayBackendError,
    _urlopen_transport,
    discover_single_model,
)
from anvil_serving.router.config import (
    ConfigError,
    ReplicaIdentity,
    ReplicaMember,
    RouterConfig,
    Tier,
)
from anvil_serving.router.internal import (
    BackendClientError,
    InternalRequest,
    Message,
    StructuredResult,
)
from anvil_serving.router.serve import (
    ReplicaRuntime,
    RoutingBackend,
    _ConcurrencyLimitedBackend,
    build_backend_for_tier,
    build_backends,
)


# --------------------------------------------------------------------------- #
# fixtures
# --------------------------------------------------------------------------- #
def _local_tier(**overrides) -> Tier:
    base = dict(
        id="auxiliary-local", base_url="http://127.0.0.1:30001/v1", dialect="openai",
        context_limit=32768, privacy="local", tool_support=True,
        auth_env="ANVIL_AUXILIARY_LOCAL_KEY", model="served-model",
    )
    base.update(overrides)
    return Tier(**base)


def _anthropic_tier(**overrides) -> Tier:
    base = dict(
        id="anthropic-local", base_url="http://127.0.0.1:30002/v1", dialect="anthropic",
        context_limit=200000, privacy="local", tool_support=True,
        auth_env="ANVIL_TEST_CLOUD_KEY", model="claude-opus-4-20250514",
    )
    base.update(overrides)
    return Tier(**base)


def _config(*tiers: Tier, **overrides) -> RouterConfig:
    kwargs: Dict[str, object] = dict(
        tiers=tuple(tiers),
        model_routes={t.id: t.id for t in tiers},
    )
    kwargs.update(overrides)
    return RouterConfig(**kwargs)


def _post_fake(response_body: bytes):
    """A fake POST transport(url, *, data, headers, timeout) capturing the call."""
    captured: Dict[str, object] = {}

    def fake(url, *, data, headers, timeout):
        captured["url"] = url
        captured["headers"] = dict(headers)
        captured["body"] = json.loads(data)
        captured["timeout"] = timeout
        return response_body

    return fake, captured


def _replica_tier(
    tier_id: str = "replica-local", *, first_port: int = 31001
) -> Tier:
    return _local_tier(
        id=tier_id,
        base_url="",
        timeout=7.0,
        health_path="/health",
        model_identity=True,
        replicas=(
            ReplicaMember(
                "member-a",
                f"http://127.0.0.1:{first_port}/v1",
                "node-a",
                f"resource-{tier_id}-a",
                "qualification:a",
            ),
            ReplicaMember(
                "member-b",
                f"http://127.0.0.1:{first_port + 1}/v1",
                "node-a",
                f"resource-{tier_id}-b",
                "qualification:b",
            ),
        ),
        replica_identity=ReplicaIdentity(
            model_revision="revision-1",
            engine_version="engine-1",
            image_digest="sha256:" + "1" * 64,
            config_fingerprint="sha256:" + "2" * 64,
        ),
    )


# --------------------------------------------------------------------------- #
# T005 — configurable relay timeout, plumbed through build_backends
# --------------------------------------------------------------------------- #
def test_relay_timeout_plumbed_through_build_backends_to_local_backend():
    """build_backends threads config.relay_timeout into a LOCAL tier's backend."""
    config = _config(_local_tier(), relay_timeout=5.0)
    backends, skipped = build_backends(config, env={})
    assert not skipped
    assert backends["auxiliary-local"]._timeout == pytest.approx(5.0)


def test_relay_timeout_applies_to_every_direct_local_backend():
    config = _config(
        _local_tier(), _anthropic_tier(),
        relay_timeout=5.0,
    )
    backends, skipped = build_backends(
        config, env={"ANVIL_TEST_CLOUD_KEY": "sk-test-DEADBEEF"}
    )
    assert not skipped
    assert backends["auxiliary-local"]._timeout == pytest.approx(5.0)
    assert backends["anthropic-local"]._timeout == pytest.approx(5.0)


def test_relay_timeout_default_is_20s_end_to_end():
    """No explicit relay_timeout in config -> RouterConfig default (20s) is what
    build_backends actually threads through (not the 120s build_backend_for_tier
    default, which only applies to a direct un-configured call)."""
    config = _config(_local_tier())  # relay_timeout not overridden -> 20.0
    backends, _skipped = build_backends(config, env={})
    assert backends["auxiliary-local"]._timeout == pytest.approx(20.0)


def test_build_backend_for_tier_direct_call_keeps_120s_default():
    """A caller that builds a single backend directly (bypassing build_backends)
    keeps the pre-existing 120s default -- relay_timeout is a build_backends-level
    concern, not a change to build_backend_for_tier's own default."""
    relay = build_backend_for_tier(_local_tier(), env={})
    assert relay._timeout == pytest.approx(120.0)


@pytest.mark.parametrize("status", [400, 413, 415, 422])
def test_caller_correctable_upstream_status_is_sanitized_4xx(monkeypatch, status):
    def fail(*args, **kwargs):
        raise urllib.error.HTTPError(
            "http://private-upstream.invalid/v1/chat/completions",
            status,
            "private provider detail",
            {},
            io.BytesIO(b'{"error":"private response body"}'),
        )

    monkeypatch.setattr(relay_module, "_direct_open", fail)

    with pytest.raises(BackendClientError) as exc_info:
        _urlopen_transport(
            "http://127.0.0.1:30010/v1/chat/completions",
            data=b"{}",
            headers={},
            timeout=1,
        )

    assert exc_info.value.status == status
    assert "private" not in exc_info.value.message


def test_upstream_server_error_remains_internal(monkeypatch):
    def fail(*args, **kwargs):
        raise urllib.error.HTTPError(
            "http://private-upstream.invalid/v1/chat/completions",
            500,
            "private provider detail",
            {},
            io.BytesIO(b'{"error":"private response body"}'),
        )

    monkeypatch.setattr(relay_module, "_direct_open", fail)

    with pytest.raises(RelayBackendError):
        _urlopen_transport(
            "http://127.0.0.1:30010/v1/chat/completions",
            data=b"{}",
            headers={},
            timeout=1,
        )


# --------------------------------------------------------------------------- #
# T007 — per-tier `timeout` overrides the global relay_timeout
# --------------------------------------------------------------------------- #
def test_per_tier_timeout_overrides_relay_timeout_on_local_tier():
    """flexibility:T007 — a LOCAL tier with an explicit `timeout` uses IT (not the
    global relay_timeout) for its backend; a sibling tier without one still uses
    config.relay_timeout."""
    with_override = _local_tier(id="fast-override", timeout=120.0)
    without = _local_tier(id="fast-default")  # timeout=None -> global default
    config = _config(with_override, without, relay_timeout=5.0)
    backends, skipped = build_backends(config, env={})
    assert not skipped
    assert backends["fast-override"]._timeout == pytest.approx(120.0)
    assert backends["fast-default"]._timeout == pytest.approx(5.0)


def test_per_tier_timeout_overrides_direct_default():
    overridden = _anthropic_tier(id="anthropic-fast", timeout=30.0)
    default = _anthropic_tier(id="anthropic-default")
    config = _config(overridden, default, relay_timeout=5.0)
    env = {"ANVIL_TEST_CLOUD_KEY": "sk-test-DEADBEEF"}
    backends, skipped = build_backends(config, env=env)
    assert not skipped
    assert backends["anthropic-fast"]._timeout == pytest.approx(30.0)
    assert backends["anthropic-default"]._timeout == pytest.approx(5.0)


# --------------------------------------------------------------------------- #
# T003 — per-tier extra_body merged into the upstream body
# --------------------------------------------------------------------------- #
def test_extra_body_merged_into_openai_body():
    fake, captured = _post_fake(
        json.dumps({"choices": [{"message": {"content": "ok"}}]}).encode("utf-8")
    )
    tier = _local_tier(
        extra_body={"chat_template_kwargs": {"enable_thinking": False}}
    )
    relay = build_backend_for_tier(tier, env={}, transport=fake)
    list(relay.generate(InternalRequest(model="chat", messages=[Message("user", "hi")])))
    assert captured["body"]["chat_template_kwargs"] == {"enable_thinking": False}
    # Router-set keys are untouched.
    assert captured["body"]["model"] == "served-model"


def test_extra_body_merged_into_anthropic_body():
    fake, captured = _post_fake(
        json.dumps({"content": [{"type": "text", "text": "ok"}]}).encode("utf-8")
    )
    tier = _anthropic_tier(extra_body={"top_k": 40})
    cloud = build_backend_for_tier(
        tier, env={"ANVIL_TEST_CLOUD_KEY": "sk-test-DEADBEEF"}, transport=fake
    )
    list(cloud.generate(InternalRequest(model="chat", messages=[Message("user", "hi")])))
    assert captured["body"]["top_k"] == 40
    assert captured["body"]["model"] == "claude-opus-4-20250514"


def test_extra_body_absent_body_unchanged():
    """No regression: extra_body absent -> the body is byte-for-byte what it was
    before T003 (no extra keys, no key removed)."""
    fake, captured = _post_fake(
        json.dumps({"choices": [{"message": {"content": "ok"}}]}).encode("utf-8")
    )
    relay = build_backend_for_tier(_local_tier(), env={}, transport=fake)
    list(relay.generate(InternalRequest(model="chat", messages=[Message("user", "hi")])))
    assert set(captured["body"].keys()) == {"model", "messages", "stream"}


def test_extra_body_can_override_a_router_set_key_when_operator_configures_it():
    """extra_body is applied last (body.update); an operator who explicitly sets
    a colliding key (e.g. stream) gets the override -- documented, intentional
    passthrough, not accidental clobbering."""
    fake, captured = _post_fake(
        json.dumps({"choices": [{"message": {"content": "ok"}}]}).encode("utf-8")
    )
    tier = _local_tier(extra_body={"stream": True})
    relay = build_backend_for_tier(tier, env={}, transport=fake)
    list(relay.generate(InternalRequest(model="chat", messages=[Message("user", "hi")])))
    assert captured["body"]["stream"] is True


# --------------------------------------------------------------------------- #
# T002 — GET /v1/models auto-derive for a local tier with model=None
# --------------------------------------------------------------------------- #
def _models_fake(model_ids):
    """A fake GET transport(url, *, headers, timeout) advertising `model_ids`."""
    captured: Dict[str, object] = {}

    def fake(url, *, headers, timeout):
        captured["url"] = url
        captured["timeout"] = timeout
        return json.dumps({"data": [{"id": m} for m in model_ids]}).encode("utf-8")

    return fake, captured


def test_auto_derive_model_single_candidate_forwards_that_id():
    """model=None + a stub upstream advertising exactly one model -> the backend
    forwards THAT id (not the caller alias) in the upstream body."""
    discovery_fake, discovery_captured = _models_fake(["qwen3-32b-awq"])
    post_fake, post_captured = _post_fake(
        json.dumps({"choices": [{"message": {"content": "ok"}}]}).encode("utf-8")
    )

    tier = _local_tier(model=None)
    relay = build_backend_for_tier(
        tier, env={}, transport=post_fake, model_discovery_transport=discovery_fake
    )
    list(relay.generate(InternalRequest(model="chat", messages=[Message("user", "hi")])))

    assert discovery_captured["url"] == "http://127.0.0.1:30001/v1/models"
    assert post_captured["body"]["model"] == "qwen3-32b-awq"  # NOT "chat"


def test_auto_derive_model_zero_candidates_raises_config_error():
    discovery_fake, _ = _models_fake([])
    with pytest.raises(ConfigError):
        build_backend_for_tier(
            _local_tier(model=None), env={}, model_discovery_transport=discovery_fake
        )


def test_auto_derive_model_multiple_candidates_raises_config_error():
    discovery_fake, _ = _models_fake(["model-a", "model-b"])
    with pytest.raises(ConfigError) as excinfo:
        build_backend_for_tier(
            _local_tier(model=None), env={}, model_discovery_transport=discovery_fake
        )
    assert "auxiliary-local" in str(excinfo.value)


def test_auto_derive_model_explicit_model_skips_the_probe():
    """An explicit model= always wins: the discovery transport is never called."""
    def _boom(url, *, headers, timeout):
        raise AssertionError("discovery transport must not be called when model= is set")

    post_fake, post_captured = _post_fake(
        json.dumps({"choices": [{"message": {"content": "ok"}}]}).encode("utf-8")
    )
    relay = build_backend_for_tier(
        _local_tier(model="served-model"),  # already set
        env={}, transport=post_fake, model_discovery_transport=_boom,
    )
    list(relay.generate(InternalRequest(model="chat", messages=[Message("user", "hi")])))
    assert post_captured["body"]["model"] == "served-model"


def test_auto_derive_model_network_error_is_non_fatal_leaves_model_none():
    """A network failure during discovery must NOT crash backend construction —
    model stays unresolved and the existing request.model fallback applies."""
    def _network_error(url, *, headers, timeout):
        raise OSError("connection refused")

    post_fake, post_captured = _post_fake(
        json.dumps({"choices": [{"message": {"content": "ok"}}]}).encode("utf-8")
    )
    relay = build_backend_for_tier(
        _local_tier(model=None), env={}, transport=post_fake,
        model_discovery_transport=_network_error,
    )
    list(relay.generate(InternalRequest(model="chat", messages=[Message("user", "hi")])))
    # request.model ("chat") is forwarded, unchanged from today's behaviour.
    assert post_captured["body"]["model"] == "chat"


def test_auto_derive_model_malformed_response_is_non_fatal():
    """A reachable-but-garbage response (not valid JSON) is treated the same as
    a network error -- non-fatal, model stays unresolved."""
    def _garbage(url, *, headers, timeout):
        return b"not json"

    relay = build_backend_for_tier(
        _local_tier(model=None), env={}, model_discovery_transport=_garbage,
    )
    assert relay._tier.model is None


def test_discover_single_model_pure_function_returns_new_tier():
    """discover_single_model() is a pure function: the input Tier is untouched
    (frozen dataclass) and a NEW Tier with model set is returned."""
    fake, _ = _models_fake(["only-model"])
    tier = _local_tier(model=None)
    out = discover_single_model(tier, transport=fake)
    assert tier.model is None  # original untouched
    assert out.model == "only-model"
    assert out.id == tier.id  # every other field carried over


# --------------------------------------------------------------------------- #
# Qualified replica sets T007 — immutable member runtime construction
# --------------------------------------------------------------------------- #
class _MemberBackend:
    def __init__(
        self,
        fragment: str,
        *,
        structured: StructuredResult | None = None,
        invoked: threading.Event | None = None,
        eager_error: BaseException | None = None,
        barrier: threading.Barrier | None = None,
    ) -> None:
        self.fragment = fragment
        self.structured = structured
        self.invoked = invoked
        self.eager_error = eager_error
        self.barrier = barrier
        self.calls = 0

    def generate(self, _request: InternalRequest):
        self.calls += 1
        if self.invoked is not None:
            self.invoked.set()
        if self.eager_error is not None:
            raise self.eager_error

        def fragments():
            if self.barrier is not None:
                self.barrier.wait(timeout=5)
            yield self.fragment

        return fragments()

    def get_last_structured(self) -> StructuredResult | None:
        return self.structured


def _request() -> InternalRequest:
    return InternalRequest(model="chat", messages=[Message("user", "hi")])


def test_replica_build_constructs_exact_member_adapters_from_direct_views():
    transport, captured = _post_fake(b'{"choices":[{"message":{"content":"ok"}}]}')
    tier = _replica_tier()
    config = _config(tier, relay_timeout=99.0)

    backends, skipped = build_backends(config, env={}, transport=transport)

    assert not skipped
    assert captured == {}
    runtime = backends[tier.id]
    assert isinstance(runtime, ReplicaRuntime)
    assert runtime.member_ids == ("member-a", "member-b")
    assert tier.base_url == ""
    for member in tier.replicas:
        relay = runtime.member_backend(member.id)
        assert relay._tier.base_url == member.base_url
        assert relay._tier.replicas == ()
        assert relay._tier.id == tier.id
        assert relay._tier.dialect == tier.dialect
        assert relay._tier.auth_env == tier.auth_env
        assert relay._tier.params == tier.params
        assert relay._timeout == pytest.approx(7.0)
        assert relay._transport is transport


def test_replica_build_isolates_equal_member_ids_between_logical_tiers():
    first = _replica_tier("replica-one", first_port=31101)
    second = _replica_tier("replica-two", first_port=31201)
    backends, _skipped = build_backends(_config(first, second), env={})

    first_runtime = backends[first.id]
    second_runtime = backends[second.id]
    assert isinstance(first_runtime, ReplicaRuntime)
    assert isinstance(second_runtime, ReplicaRuntime)
    assert first_runtime is not second_runtime
    assert first_runtime.member_backend("member-a") is not second_runtime.member_backend(
        "member-a"
    )


def test_direct_build_still_constructs_one_existing_adapter():
    tier = _local_tier()
    backends, _skipped = build_backends(_config(tier), env={})
    assert not isinstance(backends[tier.id], ReplicaRuntime)
    assert backends[tier.id]._tier is tier


def test_routing_backend_wraps_replica_runtime_once_at_logical_tier():
    tier = replace(_replica_tier(), max_concurrency=1)
    config = _config(tier)
    backends, _skipped = build_backends(config, env={})
    runtime = backends[tier.id]

    routing = RoutingBackend(config, backends)

    limited = routing._backends[tier.id]
    assert isinstance(limited, _ConcurrencyLimitedBackend)
    assert limited._inner is runtime
    assert isinstance(runtime, ReplicaRuntime)
    assert all(
        not isinstance(runtime.member_backend(member_id), _ConcurrencyLimitedBackend)
        for member_id in runtime.member_ids
    )


def test_replica_runtime_copies_mapping_and_refuses_implicit_or_unknown_selection():
    member = _MemberBackend("a")
    source = {"member-a": member}
    runtime = ReplicaRuntime(source)
    source["member-b"] = _MemberBackend("b")

    assert runtime.member_ids == ("member-a",)
    assert runtime.member_backend("member-a") is member
    with pytest.raises(RuntimeError, match="^replica member selection is required$"):
        runtime.generate(_request())
    with pytest.raises(ValueError, match="^replica member is not declared$") as exc_info:
        runtime.generate_member("private-unknown-member", _request())
    assert "private-unknown-member" not in str(exc_info.value)
    assert member.calls == 0
    assert runtime.get_last_structured() is None


def test_replica_runtime_resets_structured_owner_on_refusal_and_eager_failure():
    structured = StructuredResult(finish_reason="stop", usage={"input_tokens": 3})
    healthy = _MemberBackend("ok", structured=structured)
    failing = _MemberBackend("bad", eager_error=RuntimeError("private failure"))
    runtime = ReplicaRuntime({"healthy": healthy, "failing": failing})

    assert list(runtime.generate_member("healthy", _request())) == ["ok"]
    assert runtime.get_last_structured() is structured
    with pytest.raises(RuntimeError, match="private failure"):
        runtime.generate_member("failing", _request())
    assert runtime.get_last_structured() is None
    with pytest.raises(RuntimeError, match="selection is required"):
        runtime.generate(_request())
    assert runtime.get_last_structured() is None


def test_one_outer_concurrency_ceiling_covers_members_and_close_before_first():
    first_invoked = threading.Event()
    second_invoked = threading.Event()
    runtime = ReplicaRuntime({
        "member-a": _MemberBackend("a", invoked=first_invoked),
        "member-b": _MemberBackend("b", invoked=second_invoked),
    })
    limited = _ConcurrencyLimitedBackend(runtime, 1)

    first = limited.generate_member("member-a", _request())
    assert first_invoked.is_set()
    second_holder: list[object] = []
    second_started = threading.Event()

    def start_second() -> None:
        second_started.set()
        second_holder.append(limited.generate_member("member-b", _request()))

    thread = threading.Thread(target=start_second)
    thread.start()
    assert second_started.wait(timeout=5)
    assert not second_invoked.wait(timeout=0.05)
    first.close()
    assert second_invoked.wait(timeout=5)
    thread.join(timeout=5)
    assert not thread.is_alive()
    assert list(second_holder.pop()) == ["b"]


def test_outer_concurrency_ceiling_releases_after_eager_member_failure():
    healthy_invoked = threading.Event()
    runtime = ReplicaRuntime({
        "failing": _MemberBackend("bad", eager_error=RuntimeError("failure")),
        "healthy": _MemberBackend("ok", invoked=healthy_invoked),
    })
    limited = _ConcurrencyLimitedBackend(runtime, 1)

    with pytest.raises(RuntimeError, match="failure"):
        limited.generate_member("failing", _request())
    assert list(limited.generate_member("healthy", _request())) == ["ok"]
    assert healthy_invoked.is_set()


def test_structured_result_delegates_through_runtime_and_outer_wrapper():
    structured = StructuredResult(
        finish_reason="tool_calls",
        tool_calls=[{"name": "lookup", "id": "call-1", "arguments": "{}"}],
        usage={"input_tokens": 7, "output_tokens": 2},
    )
    runtime = ReplicaRuntime({"member-a": _MemberBackend("ok", structured=structured)})
    limited = _ConcurrencyLimitedBackend(runtime, 1)

    assert list(limited.generate_member("member-a", _request())) == ["ok"]
    assert runtime.get_last_structured() is structured
    assert limited.get_last_structured() is structured


def test_concurrent_replica_threads_do_not_cross_structured_results():
    barrier = threading.Barrier(2)
    first_result = StructuredResult(finish_reason="stop", usage={"input_tokens": 1})
    second_result = StructuredResult(finish_reason="length", usage={"input_tokens": 2})
    runtime = ReplicaRuntime({
        "member-a": _MemberBackend("a", structured=first_result, barrier=barrier),
        "member-b": _MemberBackend("b", structured=second_result, barrier=barrier),
    })
    observed: dict[str, StructuredResult | None] = {}

    def invoke(member_id: str) -> None:
        assert list(runtime.generate_member(member_id, _request()))
        observed[member_id] = runtime.get_last_structured()

    first = threading.Thread(target=invoke, args=("member-a",))
    second = threading.Thread(target=invoke, args=("member-b",))
    first.start()
    second.start()
    first.join(timeout=5)
    second.join(timeout=5)
    assert not first.is_alive() and not second.is_alive()
    assert observed == {"member-a": first_result, "member-b": second_result}
