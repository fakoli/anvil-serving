"""Runtime candidate phases against a separate deterministic owner/runtime."""

import copy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from anvil_serving import router_manage
from anvil_serving.control_plane.mcp.errors import ToolError
from anvil_serving.control_plane.mcp.tools import router
from anvil_serving.control_plane.mcp.tools import runtime_experiment as experiment


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    monkeypatch.setenv("ANVIL_SERVING_HOME", str(tmp_path))
    config = tmp_path / "router.toml"
    baseline = b'# Retain exact formatting and comment.\n[router]\n[[router.tiers]]\nid="primary"\nmax_output_tokens = 4096\n'
    candidate = baseline.replace(b"4096", b"2048")
    config.write_bytes(baseline)
    (tmp_path / "compose.json").write_text('{"services":{"router":{"image":"fixture:one"}}}')
    (tmp_path / "runtime.env").write_text("FIXTURE_TOKEN=synthetic-only\n")
    state = {"installed": baseline, "admissions": {"primary": "admitting", "paused": "quiesced"},
             "calls": [], "probe_results": [True, True], "recreate_fails": False, "crash_probe": None,
             "active_requests": 0, "traffic_after_candidate": 0, "mount_source": str(config)}
    args = {"action": "preview", "config": str(config), "tier": "primary", "alias": "llm.primary",
            "compose": str(tmp_path / "compose.json"), "service": "router", "env_file": str(tmp_path / "runtime.env"),
            "values": {"max_output_tokens": 2048}, "probe_max_tokens": 64, "probe_timeout_seconds": 3}
    # Parsing/ownership is the router's separate authority, supplied as a typed observation.
    monkeypatch.setattr("anvil_serving.router.config.load", lambda _: SimpleNamespace(
        route_tier=lambda alias: SimpleNamespace(id="primary", replicas=[]) if alias == "llm.primary" else None))

    def configuration(values):
        assert values["config"] == str(config) and values["tier"] == "primary"
        assert values["values"] == {"max_output_tokens": 2048}
        assert config.read_bytes() == baseline
        if values.get("expected_baseline_sha256"):
            assert values["expected_baseline_sha256"] == hashlib.sha256(baseline).hexdigest()
        if values["action"] == "apply":
            state["calls"].append("install")
            config.write_bytes(candidate)
            state["installed"] = candidate
        return {"ok": True, "data": {"baseline_sha256": hashlib.sha256(baseline).hexdigest(),
            "candidate_sha256": hashlib.sha256(candidate).hexdigest(), "configured": {"max_output_tokens": 4096}}}

    def probe(binding):
        number = sum(call.startswith("probe") for call in state["calls"])
        state["calls"].append("probe-" + str(number))
        assert binding["alias"] == "llm.primary"
        assert state["installed"] == (baseline if number == 0 else candidate)
        # The marker must exist before the potentially ambiguous request.
        assert experiment._marker_path().exists()
        if state["crash_probe"] == number:
            raise KeyboardInterrupt("simulated abrupt owner interruption")
        if number == 1:
            state["active_requests"] = state["traffic_after_candidate"]
        passed = state["probe_results"][number]
        return {"passed": passed, "incomplete": False, "finish_reason": "stop",
                "recognized_characters": 5, "elapsed_seconds": 0.25 + number * 0.1}

    def up(*_args, **kwargs):
        assert state["active_requests"] == 0, "router recreation dropped active requests"
        state["calls"].append("restore")
        assert kwargs["env_file"] == args["env_file"] and kwargs["recreate"] is True
        if state["recreate_fails"]:
            return 1
        state["installed"] = config.read_bytes()
        # Restart may reset admissions; restoration must return the paused tier too.
        state["admissions"] = {"primary": "admitting", "paused": "admitting"}
        return 0

    def admission(action, **kwargs):
        if action != "status":
            state["calls"].append(action + ":" + kwargs["tier_id"])
            state["admissions"][kwargs["tier_id"]] = "admitting" if action == "readmit" else "quiesced"
        return {"tiers": [{"tier_id": tier, "state": value} for tier, value in state["admissions"].items()]}

    monkeypatch.setattr(router, "tool_router_configuration", configuration)
    monkeypatch.setattr(experiment, "_probe", probe)
    monkeypatch.setattr(router_manage, "cmd_up", up)
    monkeypatch.setattr(router_manage, "transition_request", admission)
    def install(_candidate, *, _install, **kwargs):
        assert kwargs["confirm"] is True and kwargs["dry_run"] is False
        state["calls"].append("drain")
        state["admissions"] = {key: "quiesced" for key in state["admissions"]}
        state["active_requests"] = 0
        if _install(None) != 0:
            raise ValueError("restore recreation failed")
        return {"applied": True}
    monkeypatch.setattr(router_manage, "install_config", install)
    monkeypatch.setattr(router_manage, "docker_state", lambda _: "exited" if state["installed"] is None else "running")
    monkeypatch.setattr(router, "_run_argv", lambda *_args, **_kwargs: {"stdout": json.dumps([
        {"Destination": "/etc/anvil/config.toml", "Source": state["mount_source"], "Type": "bind", "RW": False}])})
    monkeypatch.setattr(router_manage, "installed_fleet_status", lambda **_: {"config_sha256": hashlib.sha256(state["installed"]).hexdigest()})
    return args, state, config, baseline


def confirmed(args, **changes):
    preview = experiment.tool_runtime_experiment(args)["data"]
    return {**args, "action": "apply", "run_id": "fixture-intent", "confirm": True,
            "dry_run": False, "human_approved": True, "expected_baseline_sha256": preview["baseline_sha256"], **changes}


def test_preview_has_exact_revisions_and_no_state_or_probe(runtime):
    args, state, config, baseline = runtime
    before = set(config.parent.iterdir())
    preview = experiment.tool_runtime_experiment(args)["data"]
    assert set(config.parent.iterdir()) == before and state["calls"] == []
    assert config.read_bytes() == baseline
    assert preview["baseline_sha256"] == hashlib.sha256(baseline).hexdigest()
    assert preview["candidate_sha256"] != preview["baseline_sha256"]
    assert [step["kind"] for step in preview["plan"]] == experiment._STEPS
    assert preview["parameters"]["candidate_values"] == {"max_output_tokens": 2048}


def test_candidate_compares_same_probe_and_restores_exact_bytes_runtime_and_admissions(runtime):
    args, state, config, baseline = runtime
    request = confirmed(args)
    result = experiment.tool_runtime_experiment(request)["data"]
    assert result["state"] == "completed" and result["correctness"] == "passed"
    assert result["baseline_result"]["passed"] and result["candidate_result"]["passed"]
    assert result["parameters"]["probe"] == {"prompt": "Reply with the single word READY.",
        "max_tokens": 64, "timeout_seconds": 3, "temperature": 0}
    assert result["recovery"]["status"] == "succeeded"
    assert config.read_bytes() == state["installed"] == baseline
    assert state["admissions"] == {"primary": "admitting", "paused": "quiesced"}
    assert state["calls"] == ["probe-0", "install", "probe-1", "drain", "restore", "quiesce:paused"]
    assert not experiment._marker_path().exists()
    assert "binding" not in result and "baseline_bytes" not in result and str(config) not in json.dumps(result)
    assert experiment.tool_runtime_experiment(request)["data"] == result
    assert len(state["calls"]) == 6


def test_incorrect_candidate_is_retained_and_never_promoted(runtime):
    args, state, config, baseline = runtime
    state["probe_results"][1] = False
    result = experiment.tool_runtime_experiment(confirmed(args))["data"]
    assert result["state"] == "failed" and result["correctness"] == "failed"
    assert result["candidate_result"]["passed"] is False
    assert result["recovery"]["status"] == "succeeded"
    assert config.read_bytes() == state["installed"] == baseline


@pytest.mark.parametrize("crash_probe", [0, 1])
def test_interrupted_probe_is_never_replayed_and_explicit_recovery_only_restores(runtime, crash_probe):
    args, state, config, baseline = runtime
    state["crash_probe"] = crash_probe
    request = confirmed(args)
    with pytest.raises(KeyboardInterrupt):
        experiment.tool_runtime_experiment(request)
    calls = state["calls"][:]
    with pytest.raises(RuntimeError, match="recovery is required"):
        experiment.assert_no_pending_runtime_experiment()
    with pytest.raises(ToolError, match="may only be restored"):
        experiment.tool_runtime_experiment(request)
    assert state["calls"] == calls
    result = experiment.tool_runtime_experiment({**request, "action": "restore"})["data"]
    assert sum(call.startswith("probe") for call in state["calls"]) == crash_probe + 1
    assert result["state"] == "failed" and result["correctness"] == "incomplete"
    assert result["recovery"]["status"] == "succeeded"
    assert config.read_bytes() == state["installed"] == baseline
    experiment.assert_no_pending_runtime_experiment()


def test_failed_restore_remains_fenced_and_can_retry_only_restoration(runtime):
    args, state, config, baseline = runtime
    request = confirmed(args)
    state["recreate_fails"] = True
    failed = experiment.tool_runtime_experiment(request)["data"]
    assert failed["state"] == "manual_recovery_required" and failed["recovery"]["status"] == "failed"
    with pytest.raises(RuntimeError):
        experiment.assert_no_pending_runtime_experiment()
    with pytest.raises(ToolError, match="pending runtime experiment"):
        experiment.tool_runtime_experiment({**request, "run_id": "other-intent"})
    state["recreate_fails"] = False
    restored = experiment.tool_runtime_experiment({**request, "action": "restore"})["data"]
    assert restored["recovery"]["status"] == "succeeded"
    assert config.read_bytes() == state["installed"] == baseline
    assert sum(call.startswith("probe") for call in state["calls"]) == 2


def test_drifted_runtime_is_not_overwritten_during_recovery(runtime):
    args, state, config, _ = runtime
    request = confirmed(args)
    state["crash_probe"] = 1
    with pytest.raises(KeyboardInterrupt):
        experiment.tool_runtime_experiment(request)
    unrelated = b"independent owner revision"
    config.write_bytes(unrelated)
    state["installed"] = unrelated
    result = experiment.tool_runtime_experiment({**request, "action": "restore"})["data"]
    assert result["state"] == "manual_recovery_required"
    assert config.read_bytes() == state["installed"] == unrelated
    assert "restore" not in state["calls"]


def test_interrupted_router_recreation_can_restore_verified_deployment_without_probe_replay(runtime, monkeypatch):
    args, state, config, baseline = runtime
    request = confirmed(args)
    state["crash_probe"] = 1
    with pytest.raises(KeyboardInterrupt):
        experiment.tool_runtime_experiment(request)
    state["installed"] = None
    def observe(**_):
        if state["installed"] is None:
            raise ValueError("router is not running")
        return {"config_sha256": hashlib.sha256(state["installed"]).hexdigest()}
    monkeypatch.setattr(router_manage, "installed_fleet_status", observe)
    result = experiment.tool_runtime_experiment({**request, "action": "restore"})["data"]
    assert result["recovery"]["status"] == "succeeded"
    assert config.read_bytes() == state["installed"] == baseline
    assert sum(call.startswith("probe") for call in state["calls"]) == 2


def test_changed_compose_declaration_blocks_restoration(runtime):
    args, state, _, _ = runtime
    request = confirmed(args)
    state["crash_probe"] = 1
    with pytest.raises(KeyboardInterrupt):
        experiment.tool_runtime_experiment(request)
    Path(args["compose"]).write_text('{"services":{"router":{"image":"other"}}}')
    result = experiment.tool_runtime_experiment({**request, "action": "restore"})["data"]
    assert result["state"] == "manual_recovery_required"
    assert "restore" not in state["calls"]


def test_restore_drains_requests_that_arrived_after_candidate_probe(runtime):
    args, state, _, _ = runtime
    state["traffic_after_candidate"] = 2
    result = experiment.tool_runtime_experiment(confirmed(args))["data"]
    assert result["state"] == "completed"
    assert state["calls"].index("drain") < state["calls"].index("restore")
    assert state["active_requests"] == 0


@pytest.mark.parametrize("drift", ["mount", "environment"])
def test_changed_mount_or_runtime_environment_refuses_restore(runtime, drift):
    args, state, _, _ = runtime
    request = confirmed(args)
    state["crash_probe"] = 1
    with pytest.raises(KeyboardInterrupt):
        experiment.tool_runtime_experiment(request)
    if drift == "mount":
        state["mount_source"] += ".unrelated"
    else:
        Path(args["env_file"]).write_text("FIXTURE_TOKEN=changed-synthetic\n")
    result = experiment.tool_runtime_experiment({**request, "action": "restore"})["data"]
    assert result["state"] == "manual_recovery_required"
    assert "restore" not in state["calls"] and "drain" not in state["calls"]


def test_closed_bounds_and_conflicting_binding_never_dispatch(runtime):
    args, state, _, _ = runtime
    for values in ({"max_output_tokens": True}, {"max_output_tokens": 0}, {"endpoint": 1}, {}):
        with pytest.raises(ToolError):
            experiment.tool_runtime_experiment({**args, "values": values})
    request = confirmed(args)
    with pytest.raises(ToolError, match="human gate"):
        experiment.tool_runtime_experiment({**request, "human_approved": False})
    assert state["calls"] == []
    experiment.tool_runtime_experiment(request)
    changed = copy.deepcopy(request)
    changed["alias"] = "llm.other"
    with pytest.raises(ToolError, match="binding changed"):
        experiment.tool_runtime_experiment(changed)


def test_fixed_probe_uses_router_alias_auth_and_retains_no_response_or_secret(tmp_path, monkeypatch):
    monkeypatch.setenv("ANVIL_ROUTER_TOKEN", "fixture-router-secret")
    requests = []
    class Response:
        def __enter__(self): return self
        def __exit__(self, *_): pass
        def read(self, *_):
            return json.dumps({"choices": [{"message": {"content": "READY"}, "finish_reason": "stop"}]}).encode()
    def open_request(request, **kwargs):
        requests.append((request, kwargs))
        return Response()
    monkeypatch.setattr("anvil_serving.control_plane.mcp.tools.serves._open_safe_probe_request", open_request)
    result = experiment._probe({"router_url": "http://127.0.0.1:8000", "alias": "llm.primary",
                               "probe_max_tokens": 64, "probe_timeout_seconds": 3})
    assert result["passed"] is True
    assert len(requests) == 1
    request, kwargs = requests[0]
    assert request.full_url == "http://127.0.0.1:8000/v1/chat/completions"
    assert request.get_header("Authorization") == "Bearer fixture-router-secret"
    assert json.loads(request.data) == {"model": "llm.primary", "messages": [{"role": "user", "content": "Reply with the single word READY."}],
                                       "max_tokens": 64, "temperature": 0}
    assert kwargs["timeout"] == 3
    assert result["request"]["alias"] == "llm.primary"
    assert "recognized_excerpt" not in result
    assert all(value not in json.dumps(result) for value in ("fixture-router-secret", "http://"))


def test_failed_baseline_never_installs_candidate(runtime):
    args, state, config, baseline = runtime
    state["probe_results"][0] = False
    result = experiment.tool_runtime_experiment(confirmed(args))["data"]
    assert result["correctness"] == "failed" and result["state"] == "failed"
    assert result["baseline_result"]["passed"] is False and result["candidate_result"] is None
    assert state["calls"] == ["probe-0"]
    assert config.read_bytes() == state["installed"] == baseline


def test_interruption_before_marker_is_terminal_before_probe_and_never_replayed(runtime, monkeypatch):
    args, state, config, baseline = runtime
    request = confirmed(args)
    original_write = experiment._write
    def fail_marker(path, value):
        if path == experiment._marker_path():
            raise OSError("simulated marker persistence failure")
        return original_write(path, value)
    monkeypatch.setattr(experiment, "_write", fail_marker)
    with pytest.raises(OSError):
        experiment.tool_runtime_experiment(request)
    record_path = experiment._record_path(request["run_id"])
    before_status = record_path.read_bytes()
    status = experiment.tool_runtime_experiment({**request, "action": "status"})["data"]
    assert status["state"] == "failed" and status["phase"] == "abandoned_before_probe"
    assert record_path.read_bytes() == before_status, "status must remain read-only"
    monkeypatch.setattr(experiment, "_write", original_write)
    repeated = experiment.tool_runtime_experiment(request)["data"]
    assert repeated["state"] == "failed" and repeated["correctness"] == "incomplete"
    assert state["calls"] == [] and config.read_bytes() == state["installed"] == baseline
    assert not experiment._marker_path().exists()


@pytest.fixture
def adapter_runtime(runtime):
    from anvil_serving.observability.dashboard.controller_adapter import ControllerAdapter
    from anvil_serving.transports import TransportError, TransportResult

    args, state, _, _ = runtime
    resource = {key: value for key, value in args.items() if key not in {"action", "values"}}
    resource.update(id="candidate-a", host_id="host-a", label="Runtime candidate", kind="experiment",
                    experiment_class="runtime_candidate", candidate_values=args["values"], aliases=["llm.primary"])
    transport_state = {"calls": [], "expired": False}

    class Transport:
        def __init__(self, *_args, **_kwargs): pass
        def tool_catalog(self): return ({"name": "runtime_experiment", "inputSchema": {"type": "object"}},)
        def execute(self, operation, **kwargs):
            assert operation.name == "runtime_experiment"
            transport_state["calls"].append((copy.deepcopy(dict(operation.arguments)), kwargs))
            try:
                result = experiment.tool_runtime_experiment(operation.arguments)
            except KeyboardInterrupt:
                transport_state["expired"] = True
                raise TransportError("response_lost", "synthetic interruption", execution_state="partial_result") from None
            return TransportResult(operation.name, "controller", result)
        def operation_status(self, key):
            return TransportResult("operation-status", "controller", {"status": "expired" if transport_state["expired"] else "succeeded"})

    adapter = ControllerAdapter({"controller": {"url": "https://controller.example.invalid", "token_env": "FIXTURE_TOKEN",
        "expected_node": "host-a", "topology": "fixture", "execution_host": "host-a", "execution_runtime": "native"},
        "resources": [resource]}, {}, transport_factory=Transport)
    return adapter, transport_state, state


def test_real_adapter_previews_executes_verifies_and_reconciles_exact_runtime_candidate(adapter_runtime):
    adapter, transport, state = adapter_runtime
    controls = adapter.controls("candidate-a")
    assert controls["experiment_class"] == "runtime_candidate"
    assert controls["experiment_settings"][0]["setting_id"] == "max_output_tokens"
    preview = adapter.preview("candidate-a", "experiment.start", parameters={"max_output_tokens": 2048})
    assert state["calls"] == []
    assert preview["diff"] == [{"field": "max_output_tokens", "before": 4096, "after": 2048}]
    result = adapter.execute(preview, "confirmed-candidate")
    assert result["execution_outcome"] == "succeeded"
    assert result["evidence"]["run_id"] == "confirmed-candidate"
    assert adapter.verify(preview, result)["status"] == "passed"
    calls = state["calls"][:]
    assert adapter.reconcile(preview, "confirmed-candidate")["execution_outcome"] == "succeeded"
    assert state["calls"] == calls
    mutations = [args for args, _ in transport["calls"] if args["action"] == "apply"]
    assert len(mutations) == 1 and mutations[0]["run_id"] == "confirmed-candidate"


def test_adapter_recovery_preserves_original_run_and_never_replays_probe(adapter_runtime):
    adapter, _, state = adapter_runtime
    state["crash_probe"] = 1
    preview = adapter.preview("candidate-a", "experiment.start")
    interrupted = adapter.execute(preview, "original-candidate")
    assert interrupted["execution_outcome"] == "unknown"
    retained = adapter.reconcile(preview, "original-candidate")
    assert retained["execution_outcome"] == "failed" and retained["recovery"]["status"] == "failed"
    recovery = adapter.preview("candidate-a", "operation.recover", parameters={"run_id": "original-candidate"})
    restored = adapter.execute(recovery, "separate-recovery-intent")
    assert restored["owner_operation_id"] == "separate-recovery-intent"
    assert restored["evidence"]["run_id"] == "original-candidate"
    assert restored["evidence"]["correctness"] == "incomplete"
    assert restored["execution_outcome"] == "succeeded"
    assert adapter.verify(recovery, restored)["status"] == "passed"
    assert sum(call.startswith("probe") for call in state["calls"]) == 2


@pytest.mark.parametrize("kind,tool", [("configuration", "router_configuration"), ("recipe", "recipe_settings")])
def test_unverified_owner_settings_close_apply_with_reason(kind, tool):
    from anvil_serving.observability.dashboard.controller_adapter import ControllerAdapter
    from anvil_serving.transports import TransportError

    class Unavailable:
        def __init__(self, *_args, **_kwargs): pass
        def tool_catalog(self): return ({"name": tool, "inputSchema": {"type": "object"}},)
        def execute(self, *_args, **_kwargs):
            raise TransportError("owner_unavailable", "synthetic source failure")
    resource = {"id": "settings-a", "host_id": "host-a", "kind": kind,
        "config": "/private/fixture/router.toml", "tier": "primary", "aliases": ["llm.primary"],
        "registry": "/private/fixture/recipes.toml", "model": "fixture/model"}
    adapter = ControllerAdapter({"controller": {"url": "https://controller.example.invalid", "token_env": "FIXTURE_TOKEN",
        "expected_node": "host-a", "topology": "fixture", "execution_host": "host-a", "execution_runtime": "native"},
        "resources": [resource]}, {}, transport_factory=Unavailable)
    controls = adapter.controls("settings-a")
    assert controls["settings"] == []
    assert controls["actions"]
    assert all(action["supported"] is False and action["permitted"] is False for action in controls["actions"])
    assert all("could not verify the installed configuration" in action["reason"] for action in controls["actions"])
