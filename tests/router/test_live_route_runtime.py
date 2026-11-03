"""Runtime-only route bindings retain their original generation until terminal."""
from dataclasses import replace
from pathlib import Path

from anvil_serving.router.route_updates import freeze_config, route_digest
from anvil_serving.router.serve import RoutingBackend, RouteRuntimeSnapshot
from tests.router.helpers import StaticBackend
from anvil_serving.router.internal import InternalRequest, Message
from anvil_serving.router.availability import AvailabilityResult
import pytest


def _runtime():
    from anvil_serving.router.config import load
    config = load(Path(__file__).resolve().parents[2] / "configs" / "example.toml")
    return RoutingBackend(config, {
        "primary-local": StaticBackend(["primary"]),
        "omni-local": StaticBackend(["omni"]),
    })


def test_old_generation_binding_blocks_later_tier_drain_after_alias_switch():
    routing = _runtime()
    class EmptyLedger:
        def unresolved_route_starts(self, tier_ids, *, authority_scope):
            return 0
    routing.set_route_drain_usage(EmptyLedger(), lambda: object())
    request = InternalRequest("llm.primary", (Message("user", "hello"),))
    binding = routing.bind_route_request(request)
    assert binding.snapshot.route_revision == 0

    active = routing.active_route_snapshot()
    routes = dict(active.config.model_routes)
    routes["llm.primary"] = "omni-local"
    candidate = freeze_config(replace(active.config, model_routes=routes))
    published = RouteRuntimeSnapshot(1, route_digest(candidate), candidate, active.backends)
    routing.publish_route_runtime(published, expected_revision=0, alias_only=True)

    hold = routing.begin_route_update_hold(update_id="a" * 32, expected_revision=1,
                                           tier_ids=("primary-local",))
    assert routing.wait_route_update_drain(hold, 0)["drained"] is False
    routing.finish_route_request(request)
    assert routing.wait_route_update_drain(hold, 0)["drained"] is True


@pytest.mark.parametrize("tier_id,model", [
    ("a/b", "model/name"),
    ("tier space", "model with spaces"),
    ("t" * 129, "m" * 256),
])
def test_binding_retains_valid_bounded_configuration_text(tier_id, model):
    from anvil_serving.router.config import load
    config = load(Path(__file__).resolve().parents[2] / "configs" / "example.toml")
    original = config.tier("primary-local")
    changed = replace(original, id=tier_id, model=model)
    routes = dict(config.model_routes)
    for alias, selected in tuple(routes.items()):
        if selected == "primary-local":
            routes[alias] = tier_id
    config = replace(config, tiers=(changed,) + tuple(tier for tier in config.tiers if tier.id != "primary-local"),
                     model_routes=routes)
    routing = RoutingBackend(config, {tier_id: StaticBackend(["ok"]), "omni-local": StaticBackend(["omni"])})
    request = InternalRequest("llm.primary", (Message("user", "hello"),))
    binding = routing.bind_route_request(request)
    assert binding.route_association.route_id == tier_id
    assert binding.route_association.configured_model == model
    routing.finish_route_request(request)


def test_direct_pre_iterator_failure_releases_route_binding():
    class Unavailable:
        def check(self, tier):
            return AvailabilityResult(False, "unavailable", "test")
    routing = _runtime()
    routing._availability = Unavailable()
    request = InternalRequest("llm.primary", (Message("user", "hello"),))
    with pytest.raises(Exception):
        routing.generate(request)
    assert routing._route_terminals == {}


def test_direct_close_before_first_next_releases_route_binding():
    routing = _runtime()
    request = InternalRequest("llm.primary", (Message("user", "hello"),))
    stream = routing.generate(request)
    stream.close()
    assert routing._route_terminals == {}


def test_direct_backend_setup_failure_releases_route_binding():
    class FailingBackend:
        def generate(self, request):
            raise RuntimeError("setup failed")
    from anvil_serving.router.config import load
    config = load(Path(__file__).resolve().parents[2] / "configs" / "example.toml")
    routing = RoutingBackend(config, {"primary-local": FailingBackend(), "omni-local": StaticBackend(["ok"])})
    request = InternalRequest("llm.primary", (Message("user", "hello"),))
    with pytest.raises(RuntimeError, match="setup failed"):
        routing.generate(request)
    assert routing._route_terminals == {}


def test_admission_policy_revision_fences_quiesce_readmit_aba():
    routing = _runtime()
    initial = routing.route_admission_fingerprint()
    routing._admission.quiesce("primary-local", "operator")
    held = routing.route_admission_fingerprint()
    routing._admission.readmit("primary-local")
    restored = routing.route_admission_fingerprint()
    assert initial != held != restored
    assert initial != restored
    assert routing._admission.policy_revision() == 2
    assert routing._admission.readmit("primary-local").state == "admitting"
    assert routing._admission.policy_revision() == 2


def test_frozen_nested_extra_body_is_thawed_at_relay_boundary():
    from anvil_serving.router.backends.relay import RelayBackend
    from anvil_serving.router.config import load
    config = load(Path(__file__).resolve().parents[2] / "configs" / "example.toml")
    primary = replace(config.tier("primary-local"),
                      extra_body={"nested": {"options": ["one", {"two": 2}]}})
    frozen = freeze_config(replace(config, tiers=(primary,) + tuple(
        tier for tier in config.tiers if tier.id != "primary-local")))
    body = RelayBackend(frozen.tier("primary-local"))._apply_tier_extra_body({})
    assert body["nested"] == {"options": ["one", {"two": 2}]}
    assert type(body["nested"]) is dict and type(body["nested"]["options"][1]) is dict
