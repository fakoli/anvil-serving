"""Independent transaction oracle: persistence phases, not implementation calls."""
from dataclasses import dataclass
import json
import os
import threading
from types import SimpleNamespace

import pytest

from anvil_serving.router import route_updates as updates
from anvil_serving.router.config import load_bytes
from anvil_serving.router.serve import RoutingBackend

pytestmark = pytest.mark.skipif(os.name != "posix", reason="route authority requires POSIX durability")


def configuration(*, selected="a", model="model-a", concurrency=1, extra=""):
    return f'''[server]
auth_env = "ROUTER_TOKEN"
[router.model_routes]
"llm.primary" = "{selected}"
"llm.anchor_a" = "a"
"llm.anchor_b" = "b"
[[router.tiers]]
id = "a"
base_url = "http://127.0.0.1:30001/v1"
dialect = "openai"
privacy = "local"
tool_support = true
auth_env = "MODEL_TOKEN"
model = "{model}"
context_limit = 8192
max_concurrency = {concurrency}
health_path = "/health"
model_identity = true
{extra}
[[router.tiers]]
id = "b"
base_url = "http://127.0.0.1:30002/v1"
dialect = "openai"
privacy = "local"
tool_support = true
auth_env = "MODEL_TOKEN"
model = "model-b"
context_limit = 4096
health_path = "/health"
model_identity = true
'''.encode()


@dataclass
class Snapshot:
    config: object
    route_revision: int
    effective_route_sha256: str


class Runtime:
    def __init__(self, raw):
        config = load_bytes(raw)
        self.snapshot = Snapshot(config, 0, updates.route_digest(config))
        self.holds = {}
        self.admission = "admitting"
        self.drained = True
        self.ready = True
        self.ledger_ready = True
        self.events = []

    def active_route_snapshot(self):
        return self.snapshot

    def route_admission_fingerprint(self):
        return self.admission

    def route_drain_accounting_ready(self):
        return self.ledger_ready

    def prepare_route_runtime(self, candidate, *, revision, digest):
        return Snapshot(candidate, revision, digest)

    def begin_route_update_hold(self, *, update_id, expected_revision, tier_ids=(), aliases=()):
        assert self.snapshot.route_revision == expected_revision
        hold = SimpleNamespace(id=update_id, tiers=tier_ids, aliases=aliases)
        self.holds[update_id] = hold
        self.events.append("hold")
        return hold

    def wait_route_update_drain(self, hold, timeout):
        self.events.append("drain")
        return {"drained": self.drained}

    def probe_route_update_targets(self, runtime, tiers, *, require_admitting=False):
        self.events.append("probe")
        if not self.ready:
            raise updates.RouteUpdateError("route_identity_unavailable")

    def publish_route_runtime(self, prepared, *, expected_revision, hold=None, alias_only=False):
        assert self.snapshot.route_revision == expected_revision
        if not alias_only:
            assert self.drained
        self.events.append("publish")
        self.snapshot = prepared

    def open_route_update_hold(self, hold):
        self.events.append("open")
        self.holds.pop(hold.id)

    def route_update_status(self):
        return {"route_revision": self.snapshot.route_revision, "holds": sorted(self.holds)}


@pytest.fixture
def manager(tmp_path):
    raw = configuration()
    path = tmp_path / "routes.json"
    updates.initialize_route_state(raw, path)
    value = updates.RouteUpdateManager(Runtime(raw), raw, path)
    value.restore()
    yield value
    value.close()


def apply(manager, raw, **kwargs):
    preview = manager.preview(raw)
    return manager.apply(preview["preview_id"], confirm=True, **kwargs)


@pytest.mark.parametrize("loss", ["missing", "corrupt"])
def test_status_and_restart_guard_refuse_lost_durable_authority(manager, loss):
    from anvil_serving import router_manage
    assert manager.status()["authority_available"] is True
    if loss == "missing":
        manager.path.unlink()
    else:
        manager.path.write_bytes(b'{"unexpected":true}')
    observed = manager.status()
    assert observed["authority_available"] is False
    assert observed["state"] == "reconciliation_required"
    with pytest.raises(ValueError, match="authority status is unavailable"):
        router_manage._guard_route_authority_base_config(
            configuration(), _route_status=lambda *_args, **_kwargs: manager.status(),
        )


def test_alias_switch_commits_without_hold_or_drain_and_retry_is_idempotent(manager):
    preview = manager.preview(configuration(selected="b"), mode="alias_only")
    result = manager.apply(preview["preview_id"], confirm=True)
    assert result["route_revision"] == 1
    assert manager.routing.snapshot.config.model_routes["llm.primary"] == "b"
    assert "hold" not in manager.routing.events and "drain" not in manager.routing.events
    assert manager.apply(preview["preview_id"], confirm=True)["route_revision"] == 1
    state = json.loads(manager.path.read_text())
    assert state["operation"]["phase"] == "complete" and not state["operation"]["publish_pending"]
    assert state["previous"]["projection"]["model_routes"]["llm.primary"] == "a"


def test_replacement_requires_complete_drain_and_recovers_same_prepared_operation(manager):
    manager.routing.drained = False
    with pytest.raises(updates.RouteUpdateError, match="route_drain_timeout"):
        apply(manager, configuration(model="replacement"))
    assert manager.status()["route_revision"] == 0
    assert manager.routing.holds
    state = json.loads(manager.path.read_text())
    assert state["operation"]["phase"] == "prepared"
    manager.routing.drained = True
    preview = manager.resume(state["operation"]["transaction_id"])
    result = manager.apply(preview["preview_id"], confirm=True)
    assert result["route_revision"] == 1 and not manager.routing.holds
    assert manager.routing.snapshot.config.tier("a").model == "replacement"


def test_tier_replacement_refuses_missing_durable_drain_accounting_before_hold(manager):
    manager.routing.ledger_ready = False
    with pytest.raises(updates.RouteUpdateError, match="route_drain_accounting_required"):
        manager.preview(configuration(model="replacement"))
    assert not manager.routing.holds and "hold" not in manager.routing.events


def test_real_runtime_refuses_missing_ledger_before_preview_and_prepared_restore(tmp_path):
    raw = configuration()
    path = tmp_path / "routes.json"
    updates.initialize_route_state(raw, path)
    routing = RoutingBackend(load_bytes(raw), {})
    manager = updates.RouteUpdateManager(routing, raw, path)
    try:
        manager.restore()
        with pytest.raises(updates.RouteUpdateError, match="route_drain_accounting_required"):
            manager.preview(configuration(model="replacement"))
        assert routing.route_update_status()["holds"] == []

        class DurableLedger:
            def unresolved_route_starts(self, tier_ids, *, authority_scope):
                return 0

        routing.set_route_drain_usage(DurableLedger(), lambda: object())
        prepared = apply(manager, configuration(model="replacement"), prepare_only=True)
        assert prepared["state"] == "held"
    finally:
        manager.close()

    recovered_routing = RoutingBackend(load_bytes(raw), {})
    recovered = updates.RouteUpdateManager(recovered_routing, raw, path)
    try:
        with pytest.raises(updates.RouteUpdateError, match="route_drain_accounting_required"):
            recovered.restore()
        assert recovered_routing.route_update_status()["holds"] == []
    finally:
        recovered.close()


def test_prepared_promotion_survives_restart_without_publication(manager):
    result = apply(manager, configuration(model="replacement"), prepare_only=True)
    assert result["state"] == "held"
    manager.close()
    restored = updates.RouteUpdateManager(Runtime(configuration()), configuration(), manager.path)
    try:
        restored.restore()
        assert restored.status()["state"] == "held"
        assert restored.routing.snapshot.config.tier("a").model == "model-a"
        preview = restored.resume(result["transaction_id"], owner_evidence={
            "source": "campaign_activation/v1", "binding_sha256": "a" * 64,
            "container_id": "b" * 64, "served_model": "replacement"})
        assert restored.apply(preview["preview_id"], confirm=True)["route_revision"] == 1
    finally:
        restored.close()


def test_restore_retains_only_pending_scope_after_prior_completed_revision(manager):
    first = configuration(model="replacement-a")
    apply(manager, first)
    second = first.replace(b'model = "model-b"', b'model = "replacement-b"')
    prepared = apply(manager, second, prepare_only=True)
    assert prepared["state"] == "held"
    manager.close()
    restored = updates.RouteUpdateManager(Runtime(configuration()), configuration(), manager.path)
    try:
        restored.restore()
        hold = next(iter(restored.routing.holds.values()))
        assert hold.tiers == ("b",)
        assert "a" not in hold.tiers
    finally:
        restored.close()


def test_readiness_failure_commits_held_revision_and_resume_does_not_increment(manager):
    manager.routing.ready = False
    with pytest.raises(updates.RouteUpdateError, match="identity"):
        apply(manager, configuration(model="replacement"))
    assert manager.status()["route_revision"] == 1
    assert manager.routing.holds
    manager.routing.ready = True
    preview = manager.resume(manager.status()["transaction_id"])
    assert manager.apply(preview["preview_id"], confirm=True)["route_revision"] == 1


def test_noop_and_explicit_mode_do_not_mutate(manager):
    assert apply(manager, configuration())["no_op"]
    assert manager.status()["route_revision"] == 0
    for raw in (configuration(), configuration(selected="b")):
        preview = manager.preview(raw)
        with pytest.raises(updates.RouteUpdateError, match="prepare_requires_replacement"):
            manager.apply(preview["preview_id"], confirm=True, prepare_only=True)
    with pytest.raises(updates.RouteUpdateError, match="mode_mismatch"):
        manager.preview(configuration(model="replacement"), mode="alias_only")
    assert not manager.routing.holds


def test_stale_preview_and_bounded_cache(manager):
    preview = manager.preview(configuration(selected="b"))
    manager.routing.admission = "operator-held"
    with pytest.raises(updates.RouteUpdateError, match="preview_stale"):
        manager.apply(preview["preview_id"], confirm=True)
    for _ in range(3):
        manager.preview(configuration())
    with pytest.raises(updates.RouteUpdateError, match="preview_capacity"):
        manager.preview(configuration())
    assert manager.status()["route_revision"] == 0


@pytest.mark.parametrize("change", [
    lambda raw: raw.replace(b'auth_env = "MODEL_TOKEN"', b'auth_env = "OTHER_TOKEN"'),
    lambda raw: raw.replace(b'dialect = "openai"', b'dialect = "anthropic"'),
    lambda raw: raw.replace(b'[router.model_routes]', b'[router]\nrelay_timeout = 99\n[router.model_routes]'),
])
def test_restart_only_fields_reject_before_mutation(manager, change):
    with pytest.raises(updates.RouteUpdateError, match="restart_required"):
        manager.preview(change(configuration()))
    assert not manager.routing.holds and manager.status()["route_revision"] == 0


def test_rollback_is_a_new_revision(manager):
    apply(manager, configuration(selected="b"))
    preview = manager.rollback(0)
    result = manager.apply(preview["preview_id"], confirm=True)
    assert result["route_revision"] == 2
    assert manager.routing.snapshot.config.model_routes["llm.primary"] == "a"


def test_crash_after_alias_commit_reconciles_committed_target(manager, monkeypatch):
    def fail(*args, **kwargs):
        raise RuntimeError("publication fault")
    monkeypatch.setattr(manager.routing, "publish_route_runtime", fail)
    with pytest.raises(RuntimeError, match="publication"):
        apply(manager, configuration(selected="b"))
    assert manager.status()["route_revision"] == 1
    hold = next(iter(manager.routing.holds.values()))
    assert hold.tiers == () and hold.aliases == ("llm.primary",)
    manager.close()
    restored = updates.RouteUpdateManager(Runtime(configuration()), configuration(), manager.path)
    try:
        restored.restore()
        assert restored.routing.snapshot.config.model_routes["llm.primary"] == "b"
        assert not restored.routing.holds and restored.status()["route_revision"] == 1
    finally:
        restored.close()


def test_readmission_durable_cleanup_failure_does_not_rehold(manager, monkeypatch):
    original = updates._write_state
    def fail_cleanup(path, value, *, expected):
        if value["operation"]["phase"] == "complete":
            raise OSError("cleanup fault")
        original(path, value, expected=expected)
    monkeypatch.setattr(updates, "_write_state", fail_cleanup)
    with pytest.raises(updates.RouteUpdateError, match="reconciliation"):
        apply(manager, configuration(model="replacement"))
    assert not manager.routing.holds
    assert json.loads(manager.path.read_text())["operation"]["phase"] == "readmitted"
    monkeypatch.setattr(updates, "_write_state", original)
    preview = manager.resume(manager.status()["transaction_id"])
    assert manager.apply(preview["preview_id"], confirm=True)["route_revision"] == 1


@pytest.mark.parametrize("fault", ["missing", "corrupt", "permissions", "symlink", "base_drift"])
def test_invalid_authority_fails_before_admission(tmp_path, fault):
    raw = configuration()
    path = tmp_path / "routes.json"
    updates.initialize_route_state(raw, path)
    if fault == "missing":
        path.unlink()
    elif fault == "corrupt":
        path.write_text("{")
    elif fault == "permissions":
        path.chmod(0o644)
    elif fault == "symlink":
        target = tmp_path / "other.json"
        path.rename(target)
        path.symlink_to(target)
    else:
        raw += b"\n# changed bootstrap bytes\n"
    value = updates.RouteUpdateManager(Runtime(raw), raw, path)
    with pytest.raises(updates.RouteUpdateError, match="state_unavailable"):
        value.restore()
    assert not value.routing.events


def test_single_authority_lock_survives_atomic_replace(manager):
    apply(manager, configuration(selected="b"))
    contender = updates.RouteUpdateManager(Runtime(configuration()), configuration(), manager.path)
    with pytest.raises(updates.RouteUpdateError, match="unavailable"):
        contender.restore()


def test_deep_freeze_detaches_metadata():
    raw = configuration(extra='params = { capabilities = { modalities = ["text"] } }')
    config = updates.freeze_config(load_bytes(raw))
    with pytest.raises(TypeError):
        config.tier("a").params["capabilities"]["modalities"] = ("image",)
    assert config.tier("a").params["capabilities"]["modalities"] == ("text",)


def test_initialize_reconciles_lost_directory_sync_and_is_exactly_idempotent(tmp_path, monkeypatch):
    original = updates._sync_directory
    calls = []
    def lose_once(path):
        calls.append(path)
        if len(calls) == 1:
            raise OSError("lost directory sync")
        original(path)
    monkeypatch.setattr(updates, "_sync_directory", lose_once)
    path = tmp_path / "routes.json"
    assert updates.initialize_route_state(configuration(), path)["route_revision"] == 0
    assert updates.initialize_route_state(configuration(), path)["route_revision"] == 0
    with pytest.raises(updates.RouteUpdateError, match="already_initialized"):
        updates.initialize_route_state(configuration(selected="b"), path)


def test_revision_zero_cannot_claim_different_routes(manager):
    manager.close()
    value = json.loads(manager.path.read_text())
    config = load_bytes(configuration(selected="b"))
    value["projection"] = updates.route_projection(config)
    value["effective_route_sha256"] = updates.route_digest(config)
    manager.path.write_text(json.dumps(value))
    restored = updates.RouteUpdateManager(Runtime(configuration()), configuration(), manager.path)
    with pytest.raises(updates.RouteUpdateError, match="unavailable"):
        restored.restore()
    assert not restored.routing.events


def test_completed_restore_never_rewrites_authority(manager, monkeypatch):
    apply(manager, configuration(selected="b"))
    manager.close()
    original = manager.path.read_bytes()
    monkeypatch.setattr(updates, "_write_state", lambda *a, **kw: pytest.fail("completed state was rewritten"))
    restored = updates.RouteUpdateManager(Runtime(configuration()), configuration(), manager.path)
    try:
        restored.restore()
        assert not restored.routing.holds
        assert manager.path.read_bytes() == original
    finally:
        restored.close()


@pytest.mark.parametrize("route_path", ["/protected/policy", "/protected/./policy", "/protected/policy.lock"])
def test_route_authority_cannot_alias_policy_or_lock(route_path):
    from anvil_serving.router.config import ConfigError
    raw = configuration().replace(b'[server]', f'[server]\nroute_state_path = "{route_path}"\nauthorization_policy_path = "/protected/policy"'.encode())
    with pytest.raises(ConfigError, match="distinct"):
        load_bytes(raw)


def test_preview_checks_complete_authority_envelope_before_preparation(tmp_path):
    extra = 'params = { custom = "' + 'x' * 350_000 + '" }'
    raw = configuration(extra=extra)
    path = tmp_path / "routes.json"
    updates.initialize_route_state(raw, path)
    manager = updates.RouteUpdateManager(Runtime(raw), raw, path)
    try:
        manager.restore()
        with pytest.raises(updates.RouteUpdateError, match="state_too_large"):
            manager.preview(configuration(selected="b", extra=extra))
        assert not manager.routing.holds
    finally:
        manager.close()


def test_applied_noops_do_not_consume_preview_slots(manager):
    for _ in range(10):
        assert apply(manager, configuration())["no_op"]


def test_prepare_rejects_shared_nonchat_endpoint(tmp_path):
    raw = configuration() + b'''\n[[router.purpose_models]]
id = "embedding"
kind = "embedding"
model = "embedding"
base_url = "http://127.0.0.1:30001/v1"
'''
    path = tmp_path / "routes.json"
    updates.initialize_route_state(raw, path)
    manager = updates.RouteUpdateManager(Runtime(raw), raw, path)
    try:
        manager.restore()
        with pytest.raises(updates.RouteUpdateError, match="shared_owner"):
            manager.preview(raw.replace(b'model = "model-a"', b'model = "replacement"'))
        assert not manager.routing.holds and manager.status()["route_revision"] == 0
    finally:
        manager.close()


@pytest.mark.parametrize("phase", ["committed", "readmitted", "complete"])
@pytest.mark.parametrize("failed_reads", [1, 2])
def test_installed_write_with_uncertain_readback_recovers_in_process(manager, monkeypatch, phase, failed_reads):
    write, read = updates._write_state, updates._read_state
    remaining = [0]
    injected = [False]
    def interrupted_write(path, value, *, expected):
        write(path, value, expected=expected)
        if value["operation"]["phase"] == phase and not injected[0]:
            injected[0] = True
            remaining[0] = failed_reads
            raise OSError("lost write receipt")
    def failed_read(path):
        if remaining[0]:
            remaining[0] -= 1
            raise OSError("transient read fault")
        return read(path)
    monkeypatch.setattr(updates, "_write_state", interrupted_write)
    monkeypatch.setattr(updates, "_read_state", failed_read)
    preview = manager.preview(configuration(selected="b"))
    with pytest.raises(updates.RouteUpdateError, match="reconciliation"):
        manager.apply(preview["preview_id"], confirm=True)
    transaction = manager.status()["transaction_id"]
    assert transaction is not None
    if phase == "complete":
        assert manager.apply(preview["preview_id"], confirm=True)["route_revision"] == 1
    else:
        resumed = manager.resume(transaction)
        assert manager.apply(resumed["preview_id"], confirm=True)["route_revision"] == 1
    assert manager.routing.snapshot.config.model_routes["llm.primary"] == "b"
    assert not manager.routing.holds


@pytest.mark.parametrize("restart", [False, True])
def test_held_committed_revision_can_rollback_without_orphaned_hold(manager, restart):
    manager.routing.ready = False
    with pytest.raises(updates.RouteUpdateError, match="identity"):
        apply(manager, configuration(model="replacement"))
    original = manager
    if restart:
        original.close()
        manager = updates.RouteUpdateManager(Runtime(configuration()), configuration(), original.path)
        manager.restore()
    try:
        manager.routing.ready = True
        preview = manager.rollback(0)
        result = manager.apply(preview["preview_id"], confirm=True)
        assert result["route_revision"] == 2
        assert manager.routing.snapshot.config.tier("a").model == "model-a"
        assert not manager.routing.holds
    finally:
        if restart:
            manager.close()


def test_older_operation_id_cannot_replay_after_next_commit(manager):
    first = manager.preview(configuration(selected="b"))
    manager.apply(first["preview_id"], confirm=True)
    apply(manager, configuration())
    with pytest.raises(updates.RouteUpdateError, match="preview_expired"):
        manager.apply(first["preview_id"], confirm=True)
    assert manager.status()["route_revision"] == 2


def test_identity_text_bounds_fail_at_configuration_load():
    from anvil_serving.router.config import ConfigError
    for raw in (configuration(model="x" * 4097), configuration().replace(b'id = "a"', b'id = "' + b'x' * 4097 + b'"')):
        with pytest.raises(ConfigError, match="4096"):
            load_bytes(raw)


def test_cancel_prepared_update_requires_fresh_original_identity_and_survives_restart(manager):
    prepared = apply(manager, configuration(model="replacement"), prepare_only=True)
    original_id = prepared["transaction_id"]
    preview = manager.rollback(0)
    assert preview["action"] == "cancel_prepared_update"
    manager.routing.ready = False
    with pytest.raises(updates.RouteUpdateError, match="identity"):
        manager.apply(preview["preview_id"], confirm=True)
    assert manager.status()["state"] == "held"
    manager.routing.ready = True
    result = manager.apply(preview["preview_id"], confirm=True)
    assert result["route_revision"] == 0 and result["state"] == "cancelled"
    assert result["transaction_id"] == original_id
    assert not manager.routing.holds
    assert manager.apply(preview["preview_id"], confirm=True) == result
    manager.close()
    restored = updates.RouteUpdateManager(Runtime(configuration()), configuration(), manager.path)
    try:
        restored.restore()
        assert restored.status()["state"] == "cancelled" and not restored.routing.holds
        assert restored.routing.snapshot.config.tier("a").model == "model-a"
    finally:
        restored.close()


def test_owner_release_preview_cannot_authorize_two_tiers_with_one_receipt(manager):
    candidate = configuration(model="replacement-a").replace(b'model = "model-b"', b'model = "replacement-b"')
    preview = manager.preview(candidate)
    with pytest.raises(updates.RouteUpdateError, match="owner_scope_unsupported"):
        manager.apply(preview["preview_id"], confirm=True, prepare_only=True)
    assert not manager.routing.holds and manager.status()["route_revision"] == 0


def test_prepared_promotion_requires_preview_bound_owner_evidence(manager):
    result = apply(manager, configuration(model="replacement"), prepare_only=True)
    preview = manager.resume(result["transaction_id"])
    with pytest.raises(updates.RouteUpdateError, match="owner_evidence_required"):
        manager.apply(preview["preview_id"], confirm=True)
    assert manager.status()["route_revision"] == 0 and manager.routing.holds
    evidence = {"source": "campaign_activation/v1", "binding_sha256": "a" * 64,
                "container_id": "b" * 64, "served_model": "replacement"}
    preview = manager.resume(result["transaction_id"], owner_evidence=evidence)
    evidence["container_id"] = "c" * 64
    result = manager.apply(preview["preview_id"], confirm=True)
    assert result["owner_evidence"]["container_id"] == "b" * 64
    assert result["artifact_identity_verified"] is False


def test_target_side_shared_endpoint_fails_at_preview(manager):
    candidate = configuration(model="model-b").replace(b"30001/v1", b"30002/v1")
    with pytest.raises(updates.RouteUpdateError, match="shared_owner"):
        manager.preview(candidate)
    assert not manager.routing.holds


def test_close_keeps_authority_lock_until_inflight_mutation_finishes(manager, monkeypatch):
    entered, release, close_started = threading.Event(), threading.Event(), threading.Event()
    errors = []
    def wait_for_release(hold, timeout):
        entered.set()
        assert release.wait(5)
        return {"drained": True}
    monkeypatch.setattr(manager.routing, "wait_route_update_drain", wait_for_release)
    preview = manager.preview(configuration(model="replacement"))
    def apply_operation():
        try:
            manager.apply(preview["preview_id"], confirm=True)
        except Exception as error:
            errors.append(error)
    def close_manager():
        close_started.set()
        manager.close()
    mutation = threading.Thread(target=apply_operation)
    closing = threading.Thread(target=close_manager)
    mutation.start()
    assert entered.wait(5)
    closing.start()
    assert close_started.wait(5)
    successor = updates.RouteUpdateManager(Runtime(configuration()), configuration(), manager.path)
    try:
        with pytest.raises(updates.RouteUpdateError, match="unavailable"):
            successor.restore()
        assert closing.is_alive()
    finally:
        release.set()
        mutation.join(5)
        closing.join(5)
    assert not errors and not mutation.is_alive() and not closing.is_alive()
    try:
        successor.restore()
        assert successor.status()["route_revision"] == 1
    finally:
        successor.close()
