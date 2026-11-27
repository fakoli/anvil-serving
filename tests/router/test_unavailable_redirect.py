"""The gateway's one fallback: an unavailable alias redirected exactly one hop."""
from dataclasses import replace
import http.client
import json
import secrets
import threading
import time

import pytest

from anvil_serving.router.availability import (AvailabilityResult, HttpHealthAvailability, RuntimeModelMetadata,
                                               probe_found_unavailable)
from anvil_serving.router.backends.relay import RelayBackend
from anvil_serving.router.config import ConfigError, ServerConfig, load_bytes
from anvil_serving.router.decision_log import DecisionLog
from anvil_serving.router.internal import InternalRequest, Message, NoAvailableTierError
from anvil_serving.router.model_metadata import build_router_status
from anvil_serving.router.serve import ReplicaRuntime, RoutingBackend
from anvil_serving.router.usage_store import RouteAssociation
from tests.router.helpers import server_context
from tests.router.key_fixtures import tmp_path as tmp_path
from tests.router.test_usage_dispatch import CHAT, server, wait_details
from tests.router.test_usage_lifecycle import rows, store as store

TOKEN = "synthetic-token"
DOWN = "health_transport_ConnectionRefusedError"
HEADER = "X-Anvil-Redirected-From"
# Today's refusal for a tier that is not serving, in the Chat Completions dialect.
UNAVAILABLE = b'{"error": {"type": "service_unavailable", "message": "the configured model service is unavailable"}}'
REDIRECT = 'llm.secondary = "llm.primary"'


def toml(redirects=REDIRECT, routes="", tiers=""):
    def tier(name, port):
        return f"""
[[router.tiers]]
id = "{name}-local"
base_url = "http://127.0.0.1:{port}/v1"
model = "{name}-model"
dialect = "openai"
context_limit = 4096
privacy = "local"
tool_support = true
auth_env = "SYNTHETIC_{name.upper()}_KEY"
health_path = "/health"
"""
    table = f"\n[router.unavailable_redirects]\n{redirects}\n" if redirects is not None else ""
    return ("[router]\n" + tier("primary", 30002) + tier("secondary", 30003) + tiers + f"""
[router.model_routes]
llm.primary = "primary-local"
llm.secondary = "secondary-local"
{routes}
""" + table).encode()


def configured(redirects=REDIRECT, **changes):
    """The two-tier fixture, with per-tier field changes keyed ``primary``/``secondary``."""
    config = load_bytes(toml(redirects))
    return replace(config, tiers=tuple(
        replace(tier, **changes.get(tier.id.removesuffix("-local"), {})) for tier in config.tiers))


class Probes:
    """Readiness by tier; a tier that is not named is ready.

    Every answer is a completed observation, and ``during`` runs inside the
    source's probe, where a real one would be waiting on the network.
    """

    def __init__(self, during=None, **reasons):
        self.reasons, self.during, self.checked = reasons, during, []

    def check(self, tier):
        self.checked.append(tier.id)
        reason = self.reasons.get(tier.id.removesuffix("-local"))
        if reason is None:
            return AvailabilityResult(True, "ready", "health_passed")
        if isinstance(reason, AvailabilityResult):
            return reason
        return AvailabilityResult(False, "unavailable", reason)

    def check_completed(self, tier):
        if self.during is not None:
            self.during()
        return self.check(tier)

    def check_member(self, tier, member_id):
        if self.reasons.get(tier.id.removesuffix("-local")) is not None:
            return AvailabilityResult(False, "unavailable", "health_http_503")
        return AvailabilityResult(True, "ready", "identity_passed", tier.model, tier.model)


def rig(config=None, *, probes=None, fail=(), log=None, extra=None):
    """A routed gateway whose two upstreams record exactly what they were sent."""
    config = config or configured()
    sent = {"primary": [], "secondary": []}

    def upstream(name):
        def transport(url, *, data, headers, timeout):
            sent[name].append(json.loads(data))
            if name in fail:
                raise ConnectionResetError("synthetic upstream fault")
            return json.dumps({"choices": [{"message": {"content": name}, "finish_reason": "stop"}]}).encode()
        return RelayBackend(config.tier(f"{name}-local"), transport=transport)

    routing = RoutingBackend(config, {**{f"{name}-local": upstream(name) for name in sent}, **(extra or {})},
                             availability=probes or Probes(secondary=DOWN), decision_log=log)
    return routing, sent


def call(conn, path=CHAT, *, alias="llm.secondary", token=TOKEN, content="hello", **fields):
    body = {"model": alias, **fields}
    if path == "/v1/responses":
        body["input"] = content
    else:
        body["messages"] = [{"role": "user", "content": content}]
        if path == "/v1/messages":
            body.setdefault("max_tokens", 20)
    conn.request("POST", path, json.dumps(body), {
        "Content-Type": "application/json", **({"Authorization": "Bearer " + token} if token else {})})
    response = conn.getresponse()
    return response.status, dict(response.getheaders()), response.read()


def named_models(body, stream):
    """Every ``model`` value a response names, at any depth of any frame."""
    documents = ([json.loads(line[6:]) for line in body.decode().splitlines() if line.startswith("data: {")]
                 if stream else [json.loads(body)])
    found = set()

    def walk(value):
        if isinstance(value, dict):
            found.update(item for key, item in value.items() if key == "model")
            value = list(value.values())
        if isinstance(value, list):
            for item in value:
                walk(item)

    walk(documents)
    return found


def refused(response, sent):
    """Today's unavailable response, with no upstream reached and nothing redirected."""
    status, headers, body = response
    assert (status, body) == (503, UNAVAILABLE)
    assert HEADER not in headers
    assert sent == {"primary": [], "secondary": []}


# --- configuration ---------------------------------------------------------

def test_absent_and_empty_tables_are_the_same_configuration():
    absent, empty, declared = (load_bytes(toml(table)) for table in (None, "", REDIRECT))
    assert dict(absent.unavailable_redirects) == dict(empty.unavailable_redirects) == {}
    assert absent == empty
    identity = [build_router_status(config, started_at=0)["config_sha256"] for config in (absent, empty, declared)]
    assert identity[0] == identity[1] != identity[2]
    # Computed by the release before this table existed: a table-less
    # configuration keeps the identity it always had.
    assert identity[0] == "0be5baf20bccd99faa3aa444cd39ac488d5d25018238ad089c5b7941116b3c5a"
    assert dict(declared.unavailable_redirects) == {"llm.secondary": "llm.primary"}
    # Spelling is normalized exactly like [router.model_routes].
    spelled = load_bytes(toml('"LLM.Secondary" = " LLM.Primary "'))
    assert spelled.unavailable_redirects == declared.unavailable_redirects


_REPLICA_SOURCE = """
[[router.tiers]]
id = "pool-local"
model = "pool-model"
dialect = "openai"
context_limit = 4096
privacy = "local"
tool_support = true
auth_env = "SYNTHETIC_POOL_KEY"
health_path = "/health"
model_identity = true
replicas = [
  { id = "member-a", base_url = "http://127.0.0.1:30010/v1", host_id = "host-a", resource_id = "gpu-a", qualification_ref = "qualification:pool-a" },
  { id = "member-b", base_url = "http://127.0.0.1:30011/v1", host_id = "host-a", resource_id = "gpu-b", qualification_ref = "qualification:pool-b" },
]
replica_identity = { model_revision = "revision-1", engine_version = "engine-1.0", image_digest = "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", config_fingerprint = "sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb" }
"""


@pytest.mark.parametrize("redirects,routes,tiers,message", [
    ('llm.secondary = "llm.missing"', "", "", "unknown capability alias: 'llm.missing'"),
    ('llm.missing = "llm.primary"', "", "", "unknown capability alias: 'llm.missing'"),
    ("llm.secondary = 5", "", "", "unknown capability alias: 5"),
    ('llm.secondary = "llm.secondary"', "", "", "different tier"),
    ('llm.secondary = "llm.alternate"', 'llm.alternate = "secondary-local"', "", "different tier"),
    ('llm.secondary = "llm.primary"\nllm.primary = "llm.alternate"', 'llm.alternate = "secondary-local"', "",
     "one hop only"),
    ('llm.secondary = "llm.primary"\nllm.primary = "llm.secondary"', "", "", "one hop only"),
    ('llm.secondary = "llm.primary"\n"LLM.Secondary" = "llm.primary"', "", "", "duplicate unavailable redirect"),
    ('llm.pool = "llm.primary"', 'llm.pool = "pool-local"', _REPLICA_SOURCE, "requires a direct source tier"),
], ids=["unknown-target", "unknown-source", "non-string", "self", "same-tier", "chain", "cycle", "duplicate",
        "replica-source"])
def test_invalid_redirect_tables_are_rejected_at_load(redirects, routes, tiers, message):
    with pytest.raises(ConfigError, match=message):
        load_bytes(toml(redirects, routes, tiers))


def test_a_source_tier_with_no_readiness_probe_is_rejected():
    probe = b'health_path = "/health"\n'
    head, _, tail = toml().rpartition(probe)  # the secondary tier is declared last
    with pytest.raises(ConfigError, match="requires health_path on its source tier"):
        load_bytes(head + tail)
    # Only the source needs one: it is the tier whose outage is the trigger.
    assert load_bytes(toml().replace(probe, b"", 1)).unavailable_redirects


def test_table_shape_and_unknown_router_keys_are_rejected():
    with pytest.raises(ConfigError, match="unavailable_redirects must be a table"):
        load_bytes(toml(None).replace(b"[router]\n", b'[router]\nunavailable_redirects = "llm.primary"\n', 1))
    with pytest.raises(ConfigError, match="unknown field"):
        load_bytes(toml(None).replace(b"[router]\n", b'[router]\nunavailable_redirect = "llm.primary"\n', 1))


def test_the_table_cannot_change_in_a_live_route_update():
    from anvil_serving.router.route_updates import RouteUpdateError, freeze_config, validate_change
    with pytest.raises(RouteUpdateError, match="route_field_restart_required"):
        validate_change(freeze_config(load_bytes(toml(None))), freeze_config(load_bytes(toml())))


# --- trigger ---------------------------------------------------------------

@pytest.mark.parametrize("reason,triggers", [
    ("health_http_503", True),
    (DOWN, True),
    ("identity_mismatch", True),
    ("identity_http_500", True),
    ("identity_transport_TimeoutError", True),
    ("identity_malformed", True),
    ("upstream_metadata_model_count", True),
    ("upstream_metadata_context_missing", True),
    ("probe_pending", False),
    ("probe_invalidated", False),
    ("availability_check_RuntimeError", False),
    ("upstream_metadata_not_configured", False),
    ("member_selection_required", False),
    ("identity_not_verified", False),
    ("anything else", False),
])
def test_only_a_completed_negative_probe_triggers(store, reason, triggers):
    assert probe_found_unavailable(AvailabilityResult(False, "unavailable", reason)) is triggers
    routing, sent = rig(probes=Probes(secondary=reason))
    with server(routing, store, accounting=False) as conn:
        response = call(conn)
    if triggers:
        assert response[0] == 200 and response[1][HEADER] == "llm.secondary"
        assert (len(sent["primary"]), sent["secondary"]) == (1, [])
    else:
        refused(response, sent)


def test_a_ready_or_malformed_result_never_triggers():
    assert not probe_found_unavailable(AvailabilityResult(True, "ready", "health_http_503"))
    assert not probe_found_unavailable(AvailabilityResult(False, "unavailable", 503))
    assert not probe_found_unavailable(None)


# --- the redirect ----------------------------------------------------------

@pytest.mark.parametrize("managed", [True, False])
@pytest.mark.parametrize("stream", [True, False])
@pytest.mark.parametrize("path", [CHAT, "/v1/messages", "/v1/responses"])
def test_redirect_serves_from_the_target_and_names_it_everywhere(store, managed, stream, path):
    usage, *_ = store
    log = DecisionLog()
    routing, sent = rig(log=log)
    with server(routing, store, managed=managed) as conn:
        status, headers, body = call(conn, path, stream=stream)
    assert status == 200
    assert headers[HEADER] == "llm.secondary"
    assert named_models(body, stream) == {"llm.primary"}
    # The target is called exactly once, with its own model; the source never.
    assert [request["model"] for request in sent["primary"]] == ["primary-model"] and sent["secondary"] == []

    record = log.last
    assert (record.route, record.requested_tier, record.served_tier) == (
        "llm.secondary", "secondary-local", "primary-local")
    assert [(a.tier_id, a.succeeded, a.reason, a.outcome) for a in record.attempts] == [
        ("secondary-local", False, "unavailable", "skipped"), ("primary-local", True, "served", "served")]
    assert record.readiness_reason == "health_transport"

    [terminal] = wait_details(usage)
    [start] = rows(usage, "usage_starts")
    # Accounting keeps the alias the caller asked for and the tier that served.
    assert start["model"] == "llm.secondary"
    assert RouteAssociation.from_json(start["route_association"]).route_id == "primary-local"
    assert (terminal.route.route_id, terminal.route.configured_model) == ("primary-local", "primary-model")
    assert terminal.outcome == "success" and terminal.dispatched is True and terminal.error_code is None


@pytest.mark.parametrize("managed", [True, False])
@pytest.mark.parametrize("stream", [True, False])
def test_without_the_table_an_unavailable_tier_answers_exactly_as_before(store, managed, stream):
    usage, *_ = store
    log = DecisionLog()
    routing, sent = rig(configured(None), log=log)
    with server(routing, store, managed=managed) as conn:
        response = call(conn, stream=stream)
    refused(response, sent)
    assert sorted(response[1]) == sorted(
        ["Content-Length", "Content-Type", "Date", "Server", "X-Anvil-Request-Id", "X-Request-Id"]
        + (["Connection"] if "Connection" in response[1] else []))
    assert [(a.tier_id, a.reason) for a in log.last.attempts] == [("secondary-local", "unavailable")]
    assert (log.last.requested_tier, log.last.served_tier) == ("secondary-local", None)
    [terminal] = wait_details(usage)
    assert (terminal.route.route_id, terminal.error_code) == ("secondary-local", "unavailable")


def test_available_source_is_served_by_itself(store):
    routing, sent = rig(probes=Probes())
    with server(routing, store, accounting=False) as conn:
        status, headers, body = call(conn)
    assert status == 200 and HEADER not in headers and named_models(body, False) == {"llm.secondary"}
    assert (len(sent["secondary"]), sent["primary"]) == (1, [])


def test_target_alias_itself_never_redirects(store):
    routing, sent = rig(probes=Probes(primary=DOWN))
    with server(routing, store, accounting=False) as conn:
        refused(call(conn, alias="llm.primary"), sent)


def test_the_targets_output_cap_applies(store):
    # Media admission counts the requested completion budget, so this request
    # fits the target only once the target's own cap has been applied to it.
    media = {"params": {"capacity": {"media_admission_enabled": True, "image_limit": 1, "video_limit": 0,
                                     "image_tokens_estimate": 1, "video_tokens_estimate": 1}}}
    routing, sent = rig(configured(primary={"max_output_tokens": 64, **media}))
    with server(routing, store, accounting=False) as conn:
        status, headers, _ = call(conn, max_tokens=100_000)
    assert status == 200 and headers[HEADER] == "llm.secondary"
    assert headers["X-Anvil-Max-Tokens-Applied"] == "64"
    assert [request["max_tokens"] for request in sent["primary"]] == [64]


def test_a_target_without_a_configured_model_is_sent_its_own_alias(store):
    routing, sent = rig(configured(primary={"model": None}))
    with server(routing, store, accounting=False) as conn:
        assert call(conn)[0] == 200
    assert [request["model"] for request in sent["primary"]] == ["llm.primary"]


def _discovered(context_limit):
    return AvailabilityResult(True, "ready", "upstream_metadata_passed", None, "observed-model",
                              runtime_metadata=RuntimeModelMetadata("observed-model", context_limit))


@pytest.mark.parametrize("primary,redirected", [
    (_discovered(4096), True),
    (_discovered(8), False),
    ("upstream_metadata_context_missing", False),
], ids=["fits", "over-discovered-context", "metadata-unresolved"])
def test_an_upstream_metadata_target_is_judged_by_what_it_reports(store, primary, redirected):
    """Fit is decided on the target's discovered limits; unknown limits never redirect."""
    upstream = {"metadata_source": "upstream", "model": None, "context_limit": 0}
    routing, sent = rig(configured(primary=upstream), probes=Probes(secondary=DOWN, primary=primary))
    with server(routing, store, accounting=False) as conn:
        response = call(conn, content="word " * 200)
    if redirected:
        assert response[0] == 200 and response[1][HEADER] == "llm.secondary"
        assert [request["model"] for request in sent["primary"]] == ["observed-model"]
    else:
        refused(response, sent)


def test_an_upstream_metadata_source_redirects_when_its_metadata_cannot_be_adopted(store):
    upstream = {"metadata_source": "upstream", "model": None, "context_limit": 0}
    routing, sent = rig(configured(secondary=upstream), probes=Probes(secondary="upstream_metadata_model_count"))
    with server(routing, store, accounting=False) as conn:
        status, headers, _ = call(conn)
    assert status == 200 and headers[HEADER] == "llm.secondary" and len(sent["primary"]) == 1


def asked(alias="llm.secondary"):
    return InternalRequest(model=alias, messages=(Message("user", "hello"),), raw={})


def test_a_route_bound_outside_the_front_door_never_redirects():
    routing, sent = rig()
    assert routing.bind_route_request(asked()).redirect is None
    with pytest.raises(NoAvailableTierError):
        list(routing.generate(asked()))
    assert sent == {"primary": [], "secondary": []}
    # The same binding with the caller's grant check supplied is the redirect.
    bound = routing.bind_route_request(asked(), redirect_allowed=lambda alias: alias == "llm.primary")
    assert (bound.alias, bound.tier.id, bound.route_association.route_id) == (
        "llm.primary", "primary-local", "primary-local")
    assert (bound.redirect.alias, bound.redirect.tier.id, bound.redirect.readiness_reason) == (
        "llm.secondary", "secondary-local", DOWN)


# --- authorization ---------------------------------------------------------

@pytest.mark.parametrize("tracked", [True, False])
@pytest.mark.parametrize("models,redirected", [(["llm.secondary"], False), (["llm.secondary", "llm.primary"], True)])
def test_a_device_key_redirects_only_onto_an_alias_it_already_holds(store, tracked, models, redirected):
    usage, *_ = store
    _, secret = usage.key_store.create("device", models, [CHAT])
    routing, sent = rig()
    keys = ServerConfig(api_keys_path=str(usage.key_store.path))
    with server(routing, store, accounting=tracked, server_config=keys) as conn:
        response = call(conn, token=secret)
    if redirected:
        assert response[0] == 200 and response[1][HEADER] == "llm.secondary"
        assert (len(sent["primary"]), sent["secondary"]) == (1, [])
    else:
        refused(response, sent)
    if tracked:
        [terminal] = wait_details(usage)
        assert terminal.route.route_id == ("primary-local" if redirected else "secondary-local")


@pytest.mark.parametrize("tracked", [True, False])
def test_a_rate_limited_key_is_refused_not_redirected(store, tracked):
    usage, *_ = store
    _, secret = usage.key_store.create("device", ["llm.secondary", "llm.primary"], [CHAT], rpm=1)
    routing, sent = rig()
    keys = ServerConfig(api_keys_path=str(usage.key_store.path))
    with server(routing, store, accounting=tracked, server_config=keys) as conn:
        assert call(conn, token=secret)[0] == 200
    with server(routing, store, accounting=tracked, server_config=keys) as conn:
        status, headers, _ = call(conn, token=secret)
    assert status == 429 and HEADER not in headers and len(sent["primary"]) == 1


def test_a_device_key_without_the_source_grant_is_still_denied(store):
    usage, *_ = store
    _, secret = usage.key_store.create("device", ["llm.primary"], [CHAT])
    routing, sent = rig()
    with server(routing, store, server_config=ServerConfig(api_keys_path=str(usage.key_store.path))) as conn:
        assert call(conn, token=secret)[0] == 403
    assert sent == {"primary": [], "secondary": []}


def test_the_admitted_grant_is_reasserted_for_the_target(store, monkeypatch):
    from anvil_serving.router.identity import AdmissionDecision, _digest
    from anvil_serving.router.keys import KeyStore
    usage, *_ = store
    _, secret = usage.key_store.create("device", ["llm.secondary", "llm.primary"], [CHAT])
    admit = KeyStore.admit

    def narrowed(self, *args, **kwargs):
        """Admit as if the key's grant lost the target between authentication and admission."""
        decision = admit(self, *args, **kwargs)
        if not isinstance(decision, AdmissionDecision):
            return decision
        caller, models = decision.caller_snapshot, ("llm.secondary",)
        grant = caller.grant
        policy = {"models": models, "paths": grant.paths, "rpm": grant.rpm,
                  "created_at": grant.created_at, "expires_at": grant.expires_at}
        return AdmissionDecision(0, replace(caller, grant=replace(grant, models=models, policy_digest=_digest(policy))))

    monkeypatch.setattr(KeyStore, "admit", narrowed)
    routing, sent = rig()
    with server(routing, store, server_config=ServerConfig(api_keys_path=str(usage.key_store.path))) as conn:
        status, headers, body = call(conn, token=secret)
    assert status == 503 and b"accounting_unavailable" in body and HEADER not in headers
    assert sent == {"primary": [], "secondary": []}
    assert rows(usage, "usage_starts") == []


def test_a_configured_scope_client_is_not_model_scoped_and_redirects(store, tmp_path):
    from anvil_serving.control_plane.authorization import INFERENCE_USE
    from tests.router.test_front_door_auth import _scoped_policy
    policy = _scoped_policy(tmp_path, [{"id": "scope_fixture", "credential_env": "SYNTHETIC_SCOPE_TOKEN",
                                        "token": "synthetic-scope-token", "scopes": [INFERENCE_USE]}])
    routing, sent = rig()
    with server(routing, store, authorization_policy=policy) as conn:
        status, headers, _ = call(conn, token="synthetic-scope-token")
    assert status == 200 and headers[HEADER] == "llm.secondary" and len(sent["primary"]) == 1


# --- what never redirects --------------------------------------------------

@pytest.mark.parametrize("source_down", [True, False])
def test_an_operator_quiesce_of_the_source_never_redirects(store, source_down):
    routing, sent = rig(probes=Probes(secondary=DOWN) if source_down else Probes())
    routing._admission.quiesce("secondary-local", "operator")
    with server(routing, store, accounting=False) as conn:
        refused(call(conn), sent)


@pytest.mark.parametrize("held", ["source", "target"])
def test_an_operator_route_hold_never_redirects(store, held):
    routing, sent = rig()
    routing.begin_route_update_hold(update_id="update-fixture", expected_revision=0, aliases=(
        "llm.secondary" if held == "source" else "llm.primary",))
    with server(routing, store, accounting=False) as conn:
        status, headers, body = call(conn)
    assert status == 503 and HEADER not in headers and sent == {"primary": [], "secondary": []}
    # A held source keeps the hold's own answer; a held target leaves the source's.
    assert (b"model_unavailable" in body) is (held == "source")


_MEDIA_OFF = {"params": {"capacity": {"media_admission_enabled": True, "image_limit": 0, "video_limit": 0,
                                      "image_tokens_estimate": 1, "video_tokens_estimate": 1}}}
_IMAGE = [{"type": "text", "text": "look"}, {"type": "image_url", "image_url": {"url": "data:image/png;base64,AA=="}}]
_TOOLS = [{"type": "function", "function": {"name": "lookup", "parameters": {"type": "object"}}}]


@pytest.mark.parametrize("change,request_fields,status", [
    ({"context_limit": 8}, {"content": "word " * 200}, 413),
    (_MEDIA_OFF, {"content": _IMAGE}, 413),
    ({"tool_support": False}, {"tools": _TOOLS}, 503),
], ids=["context", "media", "tools"])
@pytest.mark.parametrize("unfit", ["secondary", "primary"])
def test_a_request_either_tier_would_refuse_never_redirects(store, change, request_fields, status, unfit):
    """The source's own refusal stands; a request the target cannot admit stays put."""
    routing, sent = rig(configured(**{unfit: change}))
    with server(routing, store, accounting=False) as conn:
        response = call(conn, **request_fields)
    if unfit == "primary":
        refused(response, sent)
    else:
        assert response[0] == status and HEADER not in response[1]
        assert sent == {"primary": [], "secondary": []}


def test_replica_admission_never_redirects(store):
    config = load_bytes(toml(None, 'llm.pool = "pool-local"', _REPLICA_SOURCE))
    # Load-time validation refuses this pair; the runtime guard must hold too.
    config = replace(config, unavailable_redirects={"llm.pool": "llm.primary"})
    sent = []

    class Upstream:
        def generate(self, request):
            sent.append(request)
            yield "unreachable"

    backends = {"pool-local": ReplicaRuntime({"member-a": Upstream(), "member-b": Upstream()}),
                "primary-local": Upstream(), "secondary-local": Upstream()}
    with server(RoutingBackend(config, backends, availability=Probes(pool=DOWN)), store, accounting=False) as conn:
        status, headers, body = call(conn, alias="llm.pool")
    assert (status, body) == (503, UNAVAILABLE) and HEADER not in headers and sent == []


def test_a_pair_collapsed_onto_one_tier_never_redirects():
    # A live alias move can do what load-time validation refuses.
    config = replace(configured(), model_routes={"llm.primary": "secondary-local", "llm.secondary": "secondary-local"})
    routing, _ = rig(config)
    assert routing.bind_route_request(asked(), redirect_allowed=lambda alias: True).redirect is None


@pytest.mark.parametrize("failing,alive", [("secondary", Probes()), ("primary", Probes(secondary=DOWN))],
                         ids=["source-after-dispatch", "target-after-dispatch"])
def test_a_failure_after_dispatch_is_returned_not_retried(store, failing, alive):
    other = "primary" if failing == "secondary" else "secondary"
    routing, sent = rig(probes=alive, fail=(failing,))
    with server(routing, store, accounting=False) as conn:
        status, headers, _ = call(conn)
    assert status >= 500 and (len(sent[failing]), sent[other]) == (1, [])
    # An error from the target still says the request was redirected to it.
    assert headers.get(HEADER) == ("llm.secondary" if failing == "primary" else None)


def test_an_unavailable_target_answers_for_itself_with_no_bounce(store):
    usage, *_ = store
    log = DecisionLog()
    routing, sent = rig(probes=Probes(secondary=DOWN, primary="health_http_503"), log=log)
    with server(routing, store) as conn:
        status, headers, body = call(conn)
    assert (status, body, headers[HEADER]) == (503, UNAVAILABLE, "llm.secondary")
    assert sent == {"primary": [], "secondary": []}
    record = log.last
    assert (record.requested_tier, record.served_tier) == ("secondary-local", None)
    assert [(a.tier_id, a.reason, a.outcome) for a in record.attempts] == [
        ("secondary-local", "unavailable", "skipped"), ("primary-local", "unavailable", "skipped")]
    # The refusal that ended the request names its own readiness family.
    assert record.readiness_reason == "health_http"
    [terminal] = wait_details(usage)
    assert (terminal.route.route_id, terminal.error_code, terminal.dispatched) == ("primary-local", "unavailable", False)


# --- review hardening ------------------------------------------------------

def bound(routing, allowed=lambda alias: True):
    return routing.bind_route_request(asked(), redirect_allowed=allowed).redirect


def test_a_quiesce_that_lands_during_the_source_probe_wins(store):
    probes = Probes(secondary=DOWN)
    routing, sent = rig(probes=probes)
    probes.during = lambda: routing._admission.quiesce("secondary-local", "operator")
    with server(routing, store, accounting=False) as conn:
        refused(call(conn), sent)


def test_an_expired_result_served_while_a_probe_is_in_flight_never_redirects():
    config, now, recovered = configured(), [0.0], []
    entered, release = threading.Event(), threading.Event()
    source = config.tier("secondary-local")

    class Healthy:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

    def opener(request, timeout):
        if ":30003/" not in request.full_url:
            return Healthy()
        if not recovered:
            raise ConnectionRefusedError("synthetic outage")
        entered.set()
        assert release.wait(5)
        return Healthy()

    availability = HttpHealthAvailability(config, opener=opener, clock=lambda: now[0])
    routing, _ = rig(config, probes=availability)
    assert bound(routing).readiness_reason == DOWN  # a current, completed failure redirects
    now[0] += config.availability_probe_interval + 1
    recovered.append(True)
    prober = threading.Thread(target=availability.check, args=(source,))
    prober.start()
    try:
        assert entered.wait(5)
        # check() still serves the expired failure, which is right for a refusal
        # and is not evidence of an outage.
        assert availability.check(source).reason == DOWN
        assert availability.check_completed(source) is None
        assert bound(routing) is None
    finally:
        release.set()
        prober.join(5)
    assert availability.check_completed(source).available and bound(routing) is None


def test_a_provider_that_cannot_attest_a_completed_probe_never_redirects():
    class Unattested:
        def check(self, tier):
            return AvailabilityResult(False, "unavailable", DOWN)

    routing, _ = rig(probes=Unattested())
    assert bound(routing) is None


def test_a_plan_made_against_a_replaced_route_snapshot_is_dropped():
    probes = Probes(secondary=DOWN)
    routing, _ = rig(probes=probes)

    def republish():
        active = routing.active_route_snapshot()
        if active.route_revision == 0:
            routing.publish_route_runtime(replace(active, route_revision=1), expected_revision=0)

    probes.during = republish
    assert bound(routing) is None
    # The next request plans against the published snapshot.
    assert bound(routing) is not None


def test_an_unauthorized_caller_never_causes_a_probe():
    probes = Probes(secondary=DOWN)
    routing, _ = rig(probes=probes)
    assert bound(routing, lambda alias: False) is None and probes.checked == []
    assert bound(routing) is not None and probes.checked == ["secondary-local"]


@pytest.mark.parametrize("fault", ["probe", "grant"])
def test_a_fault_while_planning_leaves_the_request_on_its_own_alias(store, fault):
    def broken(*args):
        raise RuntimeError("synthetic planning fault")

    probes = Probes(secondary=DOWN, during=broken if fault == "probe" else None)
    routing, sent = rig(probes=probes)
    assert bound(routing, broken if fault == "grant" else (lambda alias: True)) is None
    if fault == "probe":
        with server(routing, store, accounting=False) as conn:
            refused(call(conn), sent)


def test_planning_time_is_part_of_the_recorded_latency(store):
    usage, *_ = store
    log = DecisionLog()
    routing, _ = rig(probes=Probes(secondary=DOWN, during=lambda: time.sleep(0.08)), log=log)
    # The direct path has no delivery poll, so nothing else is this slow.
    with server(routing, store, managed=False) as conn:
        assert call(conn)[0] == 200
    [terminal] = wait_details(usage)
    assert log.last.latency_ms >= 70 and terminal.latency_ms >= 70


def test_the_header_value_is_percent_encoded():
    from types import SimpleNamespace
    from anvil_serving.router.front_door import _response_headers
    binding = SimpleNamespace(alias="llm.primary", redirect=SimpleNamespace(alias="llm.s\u00e9condary\r\nX: y"))
    request = SimpleNamespace(raw={"_anvil_route_binding": binding})
    assert _response_headers(request)[HEADER] == "llm.s%C3%A9condary%0D%0AX%3A%20y"


def test_with_authentication_disabled_every_alias_is_already_reachable():
    routing, sent = rig()
    with server_context(routing, token=None) as address:
        conn = http.client.HTTPConnection(*address, timeout=5)
        try:
            status, headers, _ = call(conn, token=None)
        finally:
            conn.close()
    assert status == 200 and headers[HEADER] == "llm.secondary" and len(sent["primary"]) == 1


def test_a_quiesced_target_answers_for_itself(store):
    log = DecisionLog()
    routing, sent = rig(log=log)
    routing._admission.quiesce("primary-local", "operator")
    with server(routing, store, accounting=False) as conn:
        status, headers, body = call(conn)
    assert (status, body, headers[HEADER]) == (503, UNAVAILABLE, "llm.secondary")
    assert sent == {"primary": [], "secondary": []}
    assert [(a.tier_id, a.reason) for a in log.last.attempts] == [
        ("secondary-local", "unavailable"), ("primary-local", "quiesced")]


@pytest.mark.parametrize("admitted", [True, False])
def test_a_replica_target_is_served_or_named_in_the_record(store, admitted):
    config = load_bytes(toml('llm.secondary = "llm.pool"', 'llm.pool = "pool-local"', _REPLICA_SOURCE))
    served = []

    class Member:
        def generate(self, request):
            served.append(request)
            yield "pool"

    log = DecisionLog()
    probes = Probes(secondary=DOWN) if admitted else Probes(secondary=DOWN, pool=DOWN)
    routing, sent = rig(config, probes=probes, log=log,
                        extra={"pool-local": ReplicaRuntime({"member-a": Member(), "member-b": Member()})})
    with server(routing, store, accounting=False) as conn:
        status, headers, body = call(conn)
    record = log.last
    assert headers[HEADER] == "llm.secondary" and sent == {"primary": [], "secondary": []}
    assert (record.requested_tier, record.attempts[0].tier_id) == ("secondary-local", "secondary-local")
    if admitted:
        assert status == 200 and named_models(body, False) == {"llm.pool"} and len(served) == 1
        assert (record.served_tier, record.replica_selection) == ("pool-local", "identity_passed")
        assert [(a.tier_id, a.reason) for a in record.attempts[1:]] == [("pool-local", "served")]
    else:
        assert (status, body, served) == (503, UNAVAILABLE, [])
        # No member was tried, and the record still names the tier that was.
        assert (record.served_tier, record.replica_selection) == (None, "not_admitted")
        assert [(a.tier_id, a.reason, a.outcome) for a in record.attempts[1:]] == [
            ("pool-local", "unavailable", "skipped")]


@pytest.mark.parametrize("models,redirected", [(["llm.secondary"], False), (["llm.secondary", "llm.primary"], True)])
def test_a_connect_key_follows_the_same_grant_rule(store, monkeypatch, models, redirected):
    from anvil_serving.router import connect_keys
    usage, *_ = store
    keys = usage.key_store
    keys.owner_check = lambda *actor: True
    owner, administrator, fence = "human:" + "a" * 64, "human:" + "c" * 64, "f" * 64
    portal = connect_keys.ConnectKeys(keys, ["llm.primary", "llm.secondary"])

    def dispatch(principal, admin=False, **operation):
        return portal.dispatch({"principal": principal, "generation": "1", "epoch": fence,
                                "administrator": admin, "operation": operation})

    dispatch(owner, action="request")
    dispatch(administrator, True, action="approve", owner=owner, status="approved", models=models, paths=[CHAT],
             rpm=60, expires_days=30, revision=dispatch(owner, action="view")["account"]["revision"])
    account = dispatch(owner, action="view")["account"]
    secret = dispatch(owner, action="create", name="Laptop", models=models, paths=[CHAT], rpm=account["rpm"],
                      expires_days=30, revision=account["revision"])["secret"]
    monkeypatch.setenv("SYNTHETIC_CONNECT_SIGNING", secrets.token_urlsafe(32))
    monkeypatch.setenv("SYNTHETIC_CONNECT_CHECK", secrets.token_urlsafe(32))
    monkeypatch.setattr(connect_keys, "principal_checker", lambda *args: keys.owner_check)
    config = ServerConfig(api_keys_path=str(keys.path), connect_keys_env="SYNTHETIC_CONNECT_SIGNING",
                          connect_check_env="SYNTHETIC_CONNECT_CHECK", connect_home_url="https://home.example.test")
    routing, sent = rig()
    with server(routing, store, server_config=config) as conn:
        response = call(conn, token=secret)
    if redirected:
        assert response[0] == 200 and response[1][HEADER] == "llm.secondary" and len(sent["primary"]) == 1
    else:
        refused(response, sent)
    [terminal] = wait_details(usage)
    assert terminal.route.route_id == ("primary-local" if redirected else "secondary-local")
