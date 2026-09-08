import copy

import pytest

from anvil_serving.observability.dashboard.controller_adapter import ControllerAdapter
from anvil_serving.observability.dashboard.contracts import ObservatoryError
from anvil_serving.transports import TransportError, TransportResult


class FakeTransport:
    instances = []

    def __init__(self, endpoint, **kwargs):
        self.endpoint = endpoint
        self.kwargs = kwargs
        self.calls = []
        self.status_calls = []
        type(self).instances.append(self)

    def tool_catalog(self):
        names = {"serves_status", "serves_manage", "serves_probe", "serves_profile",
                 "router_transition",
                 "benchmark_job_preflight", "benchmark_job_submit"}
        return tuple({"name": name, "inputSchema": {"type": "object"}} for name in names)

    def execute(self, operation, **kwargs):
        self.calls.append((operation, kwargs))
        if operation.name == "serves_status":
            data = {"ok": True, "data": {"serves": [{"name": "chat", "state": "running"}]}}
        elif operation.name == "serves_manage":
            data = {"ok": True, "data": {"plan": [{"kind": "docker_start", "target": "chat"}]}}
        elif operation.name == "benchmark_job_status":
            data = {"ok": True, "data": {
                "spec": {"run_id": "smoke", "suite": "context"}, "spec_sha256": "a" * 64,
                "state": "completed", "revision": 3, "failure": None,
            }}
        elif operation.name == "router_transition" and operation.arguments.get("action") == "status":
            data = {"ok": True, "data": {"tiers": [
                {"tier_id": "primary", "state": "admitting", "active_requests": 0}
            ]}}
        elif operation.name == "serves_probe":
            data = {"ok": True, "data": {
                "passed": True, "bounded": True,
                "parameters": {"prompt": "Reply with the single word READY.", "expected": "READY"},
                "probe": {"serve": "chat", "recognized_excerpt": "READY"},
                "verification": {"status": "passed", "message": "Matched READY."},
            }}
        elif operation.name == "serves_profile":
            data = {"ok": True, "data": {
                "profile": operation.arguments["profile"],
                "applied": operation.arguments.get("action") == "apply"
                and operation.arguments.get("dry_run") is False,
                "dry_run": operation.arguments.get("dry_run") is not False,
            }}
        else:
            data = {"ok": True, "data": {"status": "ok"}}
        return TransportResult(operation.name, "controller", data)

    def operation_status(self, key):
        self.status_calls.append(key)
        return TransportResult("operation-status", "controller", {"key": key, "status": "succeeded"})


def config():
    return {
        "controller": {
            "url": "https://127.0.0.1:8765",
            "expected_node": "host-a",
            "token_env": "ANVIL_CONTROLLER_TOKEN_TOKEN",
            "topology": "fleet-a",
            "execution_host": "host-a",
            "execution_runtime": "native",
        },
        "resources": [{
            "id": "serve.chat", "label": "Chat", "host_id": "host-a", "kind": "serve",
            "manifest": "/private/serves.toml", "serve": "chat", "tier": "primary",
            "aliases": ["chat"], "gpu_ids": ["gpu0"],
        }],
    }


def adapter():
    FakeTransport.instances.clear()
    return ControllerAdapter(config(), {}, transport_factory=FakeTransport, clock=lambda: 10.0)


def test_catalog_gates_declared_resource_actions_and_hides_bindings():
    value = adapter()
    summary = value.snapshot()
    assert summary["status"] == "available"
    assert summary["resources"] == [{
        "id": "serve.chat", "label": "Chat", "host_id": "host-a", "kind": "serve",
        "status": "available",
    }]
    controls = value.controls("serve.chat")
    assert {item["id"] for item in controls["actions"] if item["supported"]} == {
        "serve.start", "serve.stop", "serve.restart", "serve.probe",
        "tier.quiesce", "tier.drain", "tier.readmit",
    }
    assert "/private" not in repr(controls)


def test_preview_binds_exact_private_resource_and_execute_uses_durable_context():
    value = adapter()
    preview = value.preview("serve.chat", "serve.stop")
    assert preview["stop_semantics"] == "stop_remove"
    assert "logs" in preview["effect"]
    assert preview["binding"]["tool"] == "serves_manage"
    assert preview["binding"]["arguments"] == {
        "action": "down", "manifest": "/private/serves.toml", "names": ["chat"]
    }
    first = copy.deepcopy(preview)
    result = value.execute(first, "intent.123")
    assert result["ok"] is True
    operation, kwargs = FakeTransport.instances[-1].calls[-1]
    assert operation.arguments["confirm"] is True
    assert operation.arguments["dry_run"] is False
    assert kwargs == {
        "idempotency_key": "intent.123",
        "idempotency_context": {
            "topology": "fleet-a", "execution_host": "host-a", "execution_runtime": "native"
        },
    }


def test_reconcile_reads_operation_record_without_dispatch():
    value = adapter()
    preview = value.preview("serve.chat", "serve.start")
    transport = FakeTransport.instances[-1]
    dispatches = len(transport.calls)
    result = value.reconcile(preview, "intent.123")
    assert result["execution_outcome"] == "succeeded"
    assert len(transport.calls) == dispatches
    assert transport.status_calls == ["intent.123"]


@pytest.mark.parametrize("native,expected", [("restored", "succeeded"), ("failed", "failed")])
def test_owner_configuration_failure_projects_recovery_without_private_error_text(native, expected):
    value = adapter()
    preview = value.preview("serve.chat", "serve.stop")
    def fail(*_args, **_kwargs):
        raise TransportError("controller_operation_failed", "private secret", execution_state="remote_failed",
            details={"controller_error": {"code": "configuration_apply_failed", "message": "/private/secret/path",
                                          "details": {"recovery": native, "credential": "secret-canary"}}})
    FakeTransport.instances[-1].execute = fail
    result = value.execute(preview, "intent.recovery")
    assert result["execution_outcome"] == "failed" and result["recovery"]["status"] == expected
    assert result["evidence"]["failure"] == "configuration_apply_failed"
    assert "private" not in repr(result) and "canary" not in repr(result)


def test_evidence_projection_excludes_model_output_and_raw_owner_failures():
    probe = ControllerAdapter._evidence({
        "passed": False,
        "bounded": True,
        "parameters": {
            "prompt": "Reply with the single word READY.",
            "request_kind": "chat_completion",
            "expected": "READY",
            "timeout_seconds": 10,
            "max_tokens": 32,
        },
        "probe": {
            "serve": "chat",
            "recognized_excerpt": "secret generated text",
            "recognized_characters": 21,
            "finish_reason": "stop",
            "incomplete": False,
            "endpoint": "http://private.invalid/v1",
        },
        "verification": {"status": "failed", "message": "secret owner message"},
    })
    assert probe == {
        "kind": "serve_probe",
        "passed": False,
        "bounded": True,
        "parameters": {
            "request_kind": "chat_completion",
            "expected": "READY",
            "timeout_seconds": 10,
            "max_tokens": 32,
            "prompt": "Reply with the single word READY.",
        },
        "result": {
            "serve": "chat",
            "recognized_characters": 21,
            "finish_reason": "stop",
            "incomplete": False,
        },
        "verification": {"status": "failed"},
    }

    job = ControllerAdapter._evidence({
        "spec": {"run_id": "run-a", "suite": "context"},
        "spec_sha256": "a" * 64,
        "state": "failed",
        "revision": 4,
        "failure": {"class": "worker_runtime", "code": "OSError", "message": "/private/token=value"},
        "artifact": {
            "schema": "artifact/v1",
            "completeness": "failed",
            "results": {"count": 2, "timing_seconds": 1.25, "invalid": float("nan"),
                        "output": "secret model text"},
            "failure": {"class": "worker_runtime", "code": "OSError", "message": "/private/token=value"},
        },
    })
    assert job["failure"] == {"class": "worker_runtime", "code": "OSError"}
    assert job["artifact"] == {
        "schema": "artifact/v1",
        "completeness": "failed",
        "results": {"count": 2, "timing_seconds": 1.25},
        "failure": {"class": "worker_runtime", "code": "OSError"},
    }
    assert "secret" not in repr(job) and "/private" not in repr(job)

    assert ControllerAdapter._planned_steps({"plan": [
        {"kind": "docker_start", "target": "chat", "command": "cat /private/token"},
        {"kind": "unsafe", "target": "https://private.invalid", "detail": "secret"},
        "verify_restore",
        "private command --token secret",
    ]}) == [
        {"kind": "docker_start", "target": "chat"},
        {"kind": "unsafe"},
        "verify_restore",
    ]


def test_unknown_action_and_parameter_fail_closed():
    value = adapter()
    with pytest.raises(ObservatoryError):
        value.preview("serve.chat", "remove")
    with pytest.raises(ObservatoryError):
        value.preview("serve.chat", "serve.start", parameters={"command": "oops"})


def test_probe_preview_does_not_run_probe_and_start_verification_checks_health():
    value = adapter()
    preview = value.preview("serve.chat", "serve.probe")
    assert preview["binding"]["arguments"]["max_tokens"] == 256
    assert preview["binding"]["arguments"]["timeout_seconds"] == 60
    assert all(call[0].name != "serves_probe" for call in FakeTransport.instances[-1].calls)
    result = value.execute(preview, "intent.probe")
    assert [call[0].name for call in FakeTransport.instances[-1].calls].count("serves_probe") == 1
    assert result["evidence"]["parameters"]["expected"] == "READY"
    assert value.verify(preview, result) == {
        "status": "passed", "message": "The bounded probe completed."
    }
    assert [call[0].name for call in FakeTransport.instances[-1].calls].count("serves_probe") == 1

    start = value.preview("serve.chat", "serve.start")
    assert value.verify(start, {"execution_outcome": "succeeded"}) == {
        "status": "failed", "message": "The declared serve postcondition failed."
    }


def test_serve_baseline_ignores_metrics_but_tracks_owner_and_admission(monkeypatch):
    value = adapter()
    transport = FakeTransport.instances[-1]
    original = transport.execute
    metric = ["0, 1000, 97887"]
    docker_state = ["running"]
    admission_state = ["admitting"]

    def execute(operation, **kwargs):
        result = original(operation, **kwargs)
        data = copy.deepcopy(dict(result.data))
        if operation.name == "serves_status":
            data["data"].update({
                "gpu_memory_lines": list(metric),
                "selected": ["chat"],
                "reservations": {"gpu_roles": []},
                "operating_mode": {"mode": "split", "gpu_ownership": []},
                "recipe_ownership": {"owners": [], "discovery_error": None},
            })
            data["data"]["serves"][0].update({
                "container": "chat", "docker_state": docker_state[0],
                "running": docker_state[0] == "running", "model": "org/chat",
                "engine": "sglang", "health_status": 200,
            })
        elif operation.name == "router_transition" and operation.arguments.get("action") == "status":
            data["data"]["tiers"][0].update({
                "state": admission_state[0], "observed_model": "org/chat",
                "active_requests": 17,
            })
        return TransportResult(operation.name, "controller", data)

    monkeypatch.setattr(transport, "execute", execute)
    first = value.preview("serve.chat", "serve.start")
    metric[0] = "0, 64000, 97887"
    second = value.preview("serve.chat", "serve.start")
    assert second["baseline_digest"] == first["baseline_digest"]

    docker_state[0] = "exited"
    third = value.preview("serve.chat", "serve.start")
    assert third["baseline_digest"] != first["baseline_digest"]

    docker_state[0] = "running"
    admission_state[0] = "quiesced"
    fourth = value.preview("serve.chat", "serve.start")
    assert fourth["baseline_digest"] != first["baseline_digest"]


def test_snapshot_uses_router_observed_model():
    value = adapter()
    transport = FakeTransport.instances[-1]
    original = transport.execute

    def execute(operation, **kwargs):
        result = original(operation, **kwargs)
        data = copy.deepcopy(dict(result.data))
        if operation.name == "router_transition" and operation.arguments.get("action") == "status":
            data["data"]["tiers"][0]["observed_model"] = "org/runtime-model"
        return TransportResult(operation.name, "controller", data)

    transport.execute = execute
    assert value.snapshot()["serves"][0]["observed_model"] == "org/runtime-model"


def _profile_resource():
    return {
        "id": "profile.exclusive", "label": "Exclusive profile",
        "host_id": "host-a", "kind": "profile",
        "manifest": "/private/serves.toml", "profiles": "/private/profiles.toml",
        "profile": "exclusive", "mode": "dual-gpu-exclusive",
        "active_serves": ["chat"], "inactive_serves": ["split"],
        "admissions": {"primary": "admitting"},
        "gpu_owners": {"gpu-role-a": ["chat"], "gpu-role-b": ["chat"]},
    }


def test_profile_control_requires_declared_structured_postcondition():
    value_config = config()
    value_config["resources"].append({
        "id": "profile.legacy", "host_id": "host-a", "kind": "profile",
        "manifest": "/private/serves.toml", "profile": "legacy",
    })
    value = ControllerAdapter(value_config, {}, transport_factory=FakeTransport)
    (action,) = value.controls("profile.legacy")["actions"]
    assert action["supported"] is False
    assert "postconditions" in action["reason"]


def test_profile_verification_checks_members_mode_physical_owners_and_admission(monkeypatch):
    value_config = config()
    value_config["resources"].append(_profile_resource())
    value = ControllerAdapter(value_config, {}, transport_factory=FakeTransport)
    transport = FakeTransport.instances[-1]
    original = transport.execute
    actual_owners = {
        "gpu-role-a": ["chat"],
        "gpu-role-b": ["chat"],
    }

    def execute(operation, **kwargs):
        result = original(operation, **kwargs)
        if operation.name == "serves_status":
            return TransportResult(operation.name, "controller", {"ok": True, "data": {
                "serves": [
                    {"name": "chat", "running": True, "health_status": 200},
                    {"name": "split", "running": False, "health_status": None},
                ],
                "selected": ["chat", "split"],
                "reservations": {"gpu_roles": []},
                "operating_mode": {
                    "mode": "dual-gpu-exclusive",
                    "gpu_ownership": [
                        {"gpu_role": role, "owners": owners}
                        for role, owners in actual_owners.items()
                    ],
                },
                "recipe_ownership": {"owners": [], "discovery_error": None},
            }})
        if operation.name == "router_transition":
            return TransportResult(operation.name, "controller", {"ok": True, "data": {
                "tiers": [{
                    "tier_id": operation.arguments["tier"],
                    "state": "admitting", "ready": True,
                }]
            }})
        return result

    monkeypatch.setattr(transport, "execute", execute)
    preview = value.preview("profile.exclusive", "profile.apply")
    result = value.execute(preview, "intent.profile")
    assert value.verify(preview, result)["status"] == "passed"

    actual_owners["gpu-role-b"] = ["unexpected-owner"]
    assert value.verify(preview, result) == {
        "status": "failed",
        "message": "The owner state does not match the declared profile postcondition.",
    }


def test_replica_profile_owner_refusal_has_no_lifecycle_fallback():
    class RefusingProfileTransport(FakeTransport):
        def execute(self, operation, **kwargs):
            if operation.name == "serves_profile":
                self.calls.append((operation, kwargs))
                raise TransportError(
                    "remote_operation_failed",
                    "profile uses a replica tier whose lifecycle is externally owned",
                    execution_state="remote_failed",
                )
            return super().execute(operation, **kwargs)

    value_config = config()
    value_config["resources"].append(_profile_resource())
    value = ControllerAdapter(
        value_config, {}, transport_factory=RefusingProfileTransport
    )
    with pytest.raises(ObservatoryError, match="owner"):
        value.preview("profile.exclusive", "profile.apply")
    called = [call[0].name for call in RefusingProfileTransport.instances[-1].calls]
    assert called == ["serves_profile"]
    assert "serves_manage" not in called


def test_experiment_preview_runs_preflight_but_never_submits():
    value_config = config()
    value_config["resources"].append({
        "id": "experiment.smoke", "host_id": "host-a", "kind": "experiment",
        "suite": "context", "spec": {"suite": "context", "run_id": "smoke"},
    })
    FakeTransport.instances.clear()
    value = ControllerAdapter(value_config, {}, transport_factory=FakeTransport, clock=lambda: 10.0)
    preview = value.preview("experiment.smoke", "experiment.start")
    names = [call[0].name for call in FakeTransport.instances[-1].calls]
    assert "benchmark_job_preflight" in names
    assert "benchmark_job_submit" not in names
    value.execute(preview, "intent.experiment")
    assert [call[0].name for call in FakeTransport.instances[-1].calls].count("benchmark_job_submit") == 1
    reconciled = value.reconcile(preview, "intent.experiment")
    assert reconciled["native_state"] == "completed"
    assert reconciled["execution_outcome"] == "succeeded"
    assert reconciled["evidence"]["spec_sha256"] == "a" * 64
    assert value.verify(preview, reconciled)["status"] == "unavailable"


def test_readmit_verification_uses_native_admitting_vocabulary():
    value = adapter()
    preview = value.preview("serve.chat", "tier.readmit")
    assert value.verify(preview, {"execution_outcome": "succeeded"}) == {
        "status": "passed", "message": "The declared admission postcondition passed."
    }


def test_expected_catalog_digest_mismatch_fails_controls_closed():
    value_config = config()
    value_config["controller"]["expected_catalog_digest"] = "0" * 64
    value = ControllerAdapter(value_config, {}, transport_factory=FakeTransport, clock=lambda: 10.0)
    assert value.snapshot()["status"] == "unavailable"
    assert not any(action["supported"] for action in value.controls("serve.chat")["actions"])
