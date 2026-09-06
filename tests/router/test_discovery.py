"""Model discovery exposes only configured direct capability aliases."""
from __future__ import annotations

import http.client
import json
import threading
from contextlib import contextmanager

from tests.router.helpers import StaticBackend
from anvil_serving.router.availability import AvailabilityResult
from anvil_serving.router.config import ReplicaIdentity, ReplicaMember, RouterConfig, Tier
from anvil_serving.router.discovery import models_payload
from anvil_serving.router.front_door import make_server
from anvil_serving.router.serve import ReplicaRuntime, RoutingBackend


@contextmanager
def _server(routes=("llm.primary", "llm.voice")):
    server = make_server("127.0.0.1", 0, StaticBackend(["ok"]), model_routes=routes)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_address[:2]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _models(host, port):
    connection = http.client.HTTPConnection(host, port, timeout=5)
    try:
        connection.request("GET", "/v1/models")
        response = connection.getresponse()
        return response.status, json.loads(response.read())
    finally:
        connection.close()


def test_models_endpoint_advertises_exact_configured_aliases():
    with _server() as (host, port):
        status, payload = _models(host, port)

    assert status == 200
    assert payload["object"] == "list"
    assert [model["id"] for model in payload["data"]] == ["llm.primary", "llm.voice"]
    assert all(model["object"] == "model" for model in payload["data"])


def test_models_endpoint_is_deterministic_and_does_not_invent_presets():
    with _server(("llm.voice",)) as (host, port):
        first_status, first = _models(host, port)
        second_status, second = _models(host, port)

    assert first_status == second_status == 200
    assert first == second
    assert [model["id"] for model in first["data"]] == ["llm.voice"]
    assert "chat" not in json.dumps(first)


# --------------------------------------------------------------------------- #
# Qualified replica sets T010 — one alias per logical tier, safe projection
# --------------------------------------------------------------------------- #
def _replica_config(member_count: int = 2, aliases: tuple[str, ...] = ("llm.primary",)):
    members = tuple(
        ReplicaMember(
            f"member-{chr(ord('a') + index)}",
            f"http://127.0.0.1:{34000 + index}/v1",
            "node-private",
            f"resource-{index}",
            f"qualification:{index}",
        )
        for index in reversed(range(member_count))
    )
    tier = Tier(
        id="replica-tier",
        base_url="",
        dialect="openai",
        context_limit=8192,
        max_output_tokens=256,
        privacy="local",
        tool_support=True,
        auth_env="PRIVATE_AUTH_ENV",
        model="expected-model",
        health_path="/health",
        model_identity=True,
        replicas=members,
        replica_identity=ReplicaIdentity(
            model_revision="revision-1",
            engine_version="engine-1",
            image_digest="sha256:" + "a" * 64,
            config_fingerprint="sha256:" + "b" * 64,
        ),
    )
    return RouterConfig(
        tiers=(tier,), model_routes={alias: tier.id for alias in aliases}
    )


class _ReplicaAvailability:
    def __init__(self, *, all_unavailable: bool = False) -> None:
        self.all_unavailable = all_unavailable
        self.member_calls: list[str] = []

    def check(self, _tier):
        raise AssertionError("replica discovery must not use direct safe_check")

    def check_member(self, tier, member_id):
        self.member_calls.append(member_id)
        if self.all_unavailable:
            return AvailabilityResult(
                False, "unavailable", "identity_mismatch", tier.model,
                "private-observed-model",
            )
        return AvailabilityResult(True, "ready", "identity_passed", tier.model, tier.model)


def _replica_routing(config: RouterConfig, availability: _ReplicaAvailability) -> RoutingBackend:
    tier = config.tiers[0]
    members = {member.id: StaticBackend([member.id]) for member in tier.replicas}
    return RoutingBackend(
        config, {tier.id: ReplicaRuntime(members)}, availability=availability
    )


@contextmanager
def _replica_server(config: RouterConfig, availability: _ReplicaAvailability):
    routing = _replica_routing(config, availability)
    server = make_server(
        "127.0.0.1", 0, routing, model_routes=config.model_routes
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_address[:2]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_replica_models_http_has_one_alias_and_only_safe_projection_fields():
    config = _replica_config()
    availability = _ReplicaAvailability()
    with _replica_server(config, availability) as (host, port):
        status, payload = _models(host, port)

    assert status == 200
    assert [row["id"] for row in payload["data"]] == ["llm.primary"]
    row = payload["data"][0]
    assert row["logical_tier"] == "replica-tier"
    assert set(row).issuperset({
        "logical_tier", "members", "replica_identity",
        "deployment_identity_source", "runtime_deployment_identity_verified",
    })
    assert row["deployment_identity_source"] == "declared"
    assert row["runtime_deployment_identity_verified"] is False
    assert [member["id"] for member in row["members"]] == ["member-a", "member-b"]
    rendered = json.dumps(row)
    for forbidden in (
        "127.0.0.1", "node-private", "resource-", "PRIVATE_AUTH_ENV",
        "private-observed-model", "http://",
    ):
        assert forbidden not in rendered
    assert availability.member_calls == ["member-a", "member-b"]


def test_replica_models_projection_is_once_per_tier_per_payload_and_bounded():
    config = _replica_config(16, ("llm.one", "llm.two"))
    availability = _ReplicaAvailability()

    payload = models_payload(config, availability)

    assert [row["id"] for row in payload["data"]] == ["llm.one", "llm.two"]
    assert availability.member_calls == [f"member-{chr(ord('a') + index)}" for index in range(16)]
    assert payload["data"][0]["members"] == payload["data"][1]["members"]
    assert len(payload["data"][0]["members"]) == 16
    assert [member["id"] for member in payload["data"][0]["members"]] == [
        f"member-{chr(ord('a') + index)}" for index in range(16)
    ]


def test_replica_discovery_is_fail_closed_without_provider_or_ready_members():
    config = _replica_config()
    missing = models_payload(config)
    unavailable_provider = _ReplicaAvailability(all_unavailable=True)
    unavailable = models_payload(config, unavailable_provider)

    for payload in (missing, unavailable):
        row = payload["data"][0]
        assert row["id"] == "llm.primary"
        assert [member["readiness"]["loaded"] for member in row["members"]] == [False, False]
        assert all(member["served_identity"]["observed"] is None for member in row["members"])
        assert "private-observed-model" not in json.dumps(row)
    assert unavailable_provider.member_calls == ["member-a", "member-b"]


def test_direct_and_iterable_discovery_shapes_remain_unchanged():
    direct = Tier(
        id="direct", base_url="http://127.0.0.1:34500/v1", dialect="openai",
        context_limit=32, privacy="local", tool_support=True, model="direct-model",
        auth_env="DIRECT_TEST_KEY",
    )
    config = RouterConfig(tiers=(direct,), model_routes={"llm.direct": direct.id})

    assert models_payload(config) == {
        "object": "list",
        "data": [{
            "id": "llm.direct", "object": "model", "name": "llm.direct",
            "description": "Configured serving capability", "owned_by": "anvil-serving",
            "created": 1_700_000_000, "context_window": 32,
            "max_output_tokens": None,
        }],
    }
    assert models_payload(("llm.direct",)) == {
        "object": "list",
        "data": [{
            "id": "llm.direct", "object": "model", "name": "llm.direct",
            "description": "Configured serving capability", "owned_by": "anvil-serving",
            "created": 1_700_000_000,
        }],
    }
