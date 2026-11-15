"""Model discovery exposes only configured direct capability aliases."""
from __future__ import annotations

import http.client
import json
import threading
from contextlib import contextmanager

from tests.router.helpers import StaticBackend
from anvil_serving.router.availability import AvailabilityResult
from anvil_serving.router.config import ReplicaIdentity, ReplicaMember, RouterConfig, Tier
from anvil_serving.router.front_door import make_server
from anvil_serving.router.serve import ReplicaRuntime, RoutingBackend


@contextmanager
def _server(routes=("llm.primary", "llm.voice")):
    server = make_server("127.0.0.1", 0, StaticBackend(["ok"]), model_routes=routes)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
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
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
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


def _media_tier(params):
    return Tier(
        id="media", base_url="http://127.0.0.1:34501/v1", dialect="openai",
        context_limit=64, privacy="local", tool_support=True, model="media-model",
        auth_env="MEDIA_TEST_KEY", params=params,
    )
