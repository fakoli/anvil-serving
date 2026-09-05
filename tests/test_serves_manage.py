"""Tests for the `serves` management verbs — rm / adopt / up --compose.

Docker is injected via the module's `_run` seam, so these run with no docker, no
GPU, and no network. Mirrors tests/test_serves.py's fake-`_run` style.
"""
import json
import sys
import textwrap

import pytest

from anvil_serving import guard, serves
from tests.conftest import proc


@pytest.fixture(autouse=True)
def _isolated_host_policy(monkeypatch, tmp_path):
    """Keep a developer's enabled machine policy out of dispatch unit tests."""
    monkeypatch.setenv("ANVIL_SERVING_HOME", str(tmp_path / ".anvil-serving"))


def _manifest(tmp_path, body):
    p = tmp_path / "serves.toml"
    p.write_text(textwrap.dedent(body), encoding="utf-8")
    return str(p)


def _inspect_returning(state, op_rc=0, op_err=""):
    """A fake _run: `docker inspect` -> `state`; any other command -> proc(op_rc)."""
    calls = []

    def run(argv, **k):
        calls.append(argv)
        if isinstance(argv, list) and argv[:2] == ["docker", "inspect"]:
            if state == "absent":
                return proc(1, "", "Error: No such object")
            if state == "error":
                return proc(1, "", "Cannot connect to the Docker daemon")
            return proc(0, state + "\n")
        return proc(op_rc, "", op_err)

    run.calls = calls
    return run


DUAL_MODE_MANIFEST = """
    [[gpu_roles]]
    id = "dark-compute-a"
    vram_mib = 97887
    reserve_mib = 3072

    [[gpu_roles]]
    id = "dark-compute-b"
    vram_mib = 97887
    reserve_mib = 3072

    [[serve]]
    name = "split-a"
    container = "split-a"
    runtime = "docker"
    port = 30001
    model = "split-a-local"
    engine = "vllm"
    gpu_role = "dark-compute-a"
    vram_mib = 80000
    residency = "resident"
    router_tier = "llm-a"
    groups = ["split-stack"]
    up = "docker compose -f {dir}/compose.yml up -d split-a"

    [[serve]]
    name = "split-b"
    container = "split-b"
    runtime = "docker"
    port = 30002
    model = "split-b-local"
    engine = "vllm"
    gpu_role = "dark-compute-b"
    vram_mib = 80000
    residency = "resident"
    groups = ["split-stack"]
    up = "docker compose -f {dir}/compose.yml up -d split-b"

    [[serve]]
    name = "tp2"
    container = "tp2"
    runtime = "docker"
    port = 30003
    model = "candidate-local"
    engine = "vllm"
    gpu_roles = ["dark-compute-a", "dark-compute-b"]
    vram_mib = 90000
    residency = "on-demand"
    operating_mode = "dual-gpu-exclusive"
    tensor_parallel_size = 2
    up = "docker compose -f {dir}/compose.yml up -d tp2"
"""


def _routed_mode_manifest(tmp_path):
    def router_config(path, model):
        path.write_text(textwrap.dedent(f"""
            [router]
            [[router.tiers]]
            id = "llm-a"
            base_url = "http://127.0.0.1:30003/v1"
            model = "{model}"
            dialect = "openai"
            context_limit = 4096
            privacy = "local"
            tool_support = true
            auth_env = "ANVIL_PRIMARY_KEY"
            health_path = "/health"
            model_identity = true
            [router.model_routes]
            llm.primary = "llm-a"
        """), encoding="utf-8")

    target_config = tmp_path / "router-target.toml"
    rollback_config = tmp_path / "router-rollback.toml"
    router_config(target_config, "candidate-local")
    router_config(rollback_config, "split-a-local")
    body = DUAL_MODE_MANIFEST.replace(
        'tensor_parallel_size = 2\n',
        'tensor_parallel_size = 2\n'
        '    router_tier = "llm-a"\n'
        '    router_config = "{dir}/router-target.toml"\n'
        '    rollback_router_config = "{dir}/router-rollback.toml"\n',
    )
    loaded = serves.load_manifest(_manifest(tmp_path, body))
    return loaded, target_config, rollback_config


def _mode_run(states, fail_service=None, restart_on_stop=None):
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        if argv[:3] == ["docker", "ps", "-a"]:
            if any(state == "error" for state in states.values()):
                return proc(1, err="Cannot connect to the Docker daemon")
            rows = [
                json.dumps({"Names": name, "State": state})
                for name, state in states.items()
                if state != "absent"
            ]
            return proc(0, "\n".join(rows))
        if argv[:2] == ["docker", "inspect"]:
            state = states.get(argv[-1], "absent")
            if state == "absent":
                return proc(1, err="Error: No such object")
            if state == "error":
                return proc(1, err="Cannot connect to the Docker daemon")
            return proc(0, state + "\n")
        if argv[:2] == ["docker", "stop"]:
            states[argv[-1]] = (
                "running" if argv[-1] == restart_on_stop else "exited"
            )
        elif argv[:3] == ["docker", "rm", "-f"]:
            states[argv[-1]] = "absent"
        elif argv[:2] == ["docker", "compose"]:
            service = argv[-1]
            if service == fail_service:
                return proc(1, err="synthetic start failure")
            states[service] = "running"
        return proc(0)

    run.calls = calls
    return run


class _HealthyResponse:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def getcode(self):
        return 200


# ---- rm ---------------------------------------------------------------------

def test_cmd_rm_removes_manifest_serve_by_name():
    # a token matching a manifest serve's name resolves to that serve's container.
    serv = [{"name": "heavy", "container": "sglang", "port": 1, "health": "/health"}]
    run = _inspect_returning("running")
    assert serves.cmd_rm(serv, ["heavy"], assume_yes=True, _run=run) == 0
    assert ["docker", "rm", "-f", "sglang"] in run.calls


def test_cmd_rm_removes_literal_non_manifest_container():
    # THE key case: a container NOT in the manifest (experiment squatting a port) is
    # removed by its literal name — no manifest entry required.
    serv = [{"name": "heavy", "container": "sglang", "port": 1, "health": "/health"}]
    run = _inspect_returning("running")
    assert serves.cmd_rm(serv, ["vllm-experiment"], assume_yes=True, _run=run) == 0
    assert ["docker", "rm", "-f", "vllm-experiment"] in run.calls


def test_cmd_rm_absent_container_is_noop_success(capsys):
    serv = [{"name": "heavy", "container": "sglang", "port": 1, "health": "/health"}]
    run = _inspect_returning("absent")
    assert serves.cmd_rm(serv, ["ghost"], assume_yes=True, _run=run) == 0
    assert not any(c[:3] == ["docker", "rm", "-f"] for c in run.calls)  # nothing removed
    assert "nothing to remove" in capsys.readouterr().out


def test_cmd_rm_error_state_is_not_false_success():
    # docker daemon unreachable -> cannot remove, must NOT claim rc 0.
    serv = [{"name": "heavy", "container": "sglang", "port": 1, "health": "/health"}]
    run = _inspect_returning("error")
    assert serves.cmd_rm(serv, ["sglang"], assume_yes=True, _run=run) == 1
    assert not any(c[:3] == ["docker", "rm", "-f"] for c in run.calls)


def test_cmd_rm_reports_remove_failure():
    serv = [{"name": "heavy", "container": "sglang", "port": 1, "health": "/health"}]
    run = _inspect_returning("running", op_rc=1, op_err="boom")
    assert serves.cmd_rm(serv, ["sglang"], assume_yes=True, _run=run) == 1


def test_cmd_rm_dry_run_removes_nothing(capsys):
    serv = [{"name": "heavy", "container": "sglang", "port": 1, "health": "/health"}]
    run = _inspect_returning("running")
    assert serves.cmd_rm(serv, ["sglang"], dry_run=True, assume_yes=True, _run=run) == 0
    assert not any(c[:3] == ["docker", "rm", "-f"] for c in run.calls)
    assert "rm -f sglang" in capsys.readouterr().out  # printed the plan


def test_cmd_rm_no_names_errors():
    assert serves.cmd_rm([], [], assume_yes=True, _run=_inspect_returning("running")) == 1


def test_cmd_rm_ambiguous_token_refuses(capsys):
    # token "shared" is serve A's NAME and serve B's CONTAINER -> ambiguous -> refuse (rc 1)
    # and remove NOTHING, rather than destroy an untargeted serve (Greptile #373).
    serv = [{"name": "shared", "container": "cont-a", "port": 1, "health": "/health"},
            {"name": "b", "container": "shared", "port": 2, "health": "/health"}]
    run = _inspect_returning("running")
    assert serves.cmd_rm(serv, ["shared"], assume_yes=True, _run=run) == 1
    assert not any(c[:3] == ["docker", "rm", "-f"] for c in run.calls)  # removed nothing
    assert "ambiguous" in capsys.readouterr().out


# ---- adopt ------------------------------------------------------------------

def test_cmd_adopt_recreates_manifest_serve_under_compose(capsys):
    # adopt an externally-started (running) compose serve -> `docker rm -f` + `up`.
    serv = [{"name": "heavy", "container": "sglang", "port": 1, "health": "/health",
             "model": "qwen35-awq-local",
             "up": ["docker", "compose", "-f", "/x/docker-compose.yml", "up", "-d"]}]
    run = _inspect_returning("running")
    assert serves.cmd_adopt(serv, ["heavy"], assume_yes=True, _run=run) == 0
    # ORDER matters: `docker rm -f` MUST precede `up` (the whole point of recreate — a
    # reordered/up-before-rm regression would leave the stale container or name-clash).
    i_rm = run.calls.index(["docker", "rm", "-f", "sglang"])
    i_up = run.calls.index([
        "docker", "compose", "--project-name", "anvil-serving",
        "-f", "/x/docker-compose.yml", "up", "-d",
    ])
    assert i_rm < i_up
    out = capsys.readouterr().out
    assert "adopting heavy" in out


def test_cmd_adopt_no_match_errors():
    serv = [{"name": "heavy", "container": "sglang", "port": 1, "health": "/health"}]
    run = _inspect_returning("running")
    assert serves.cmd_adopt(serv, ["nope"], assume_yes=True, _run=run) == 1


def test_cmd_adopt_dry_run_touches_nothing(capsys):
    serv = [{"name": "heavy", "container": "sglang", "port": 1, "health": "/health",
             "model": "qwen35-awq-local",
             "up": ["docker", "compose", "-f", "/x/docker-compose.yml", "up", "-d"]}]
    run = _inspect_returning("running")
    assert serves.cmd_adopt(serv, ["heavy"], dry_run=True, assume_yes=True, _run=run) == 0
    assert not any(c[:3] == ["docker", "rm", "-f"] for c in run.calls)
    assert not any(c[:2] == ["docker", "compose"] for c in run.calls)
    assert "adopting heavy" in capsys.readouterr().out


# ---- down --dry-run (safety: a preview must NOT stop serving) ----------------

def test_cmd_down_dry_run_does_not_stop(capsys):
    # `down` frees GPUs / kills in-flight serving, so --dry-run must only PRINT the plan.
    serv = [{"name": "heavy", "container": "sglang", "port": 1, "health": "/health"}]
    run = _inspect_returning("running")
    assert serves.cmd_down(serv, ["heavy"], dry_run=True, _run=run) == 0
    assert not any(c[:2] == ["docker", "stop"] for c in run.calls)  # nothing stopped
    assert not any(c[:2] == ["docker", "rm"] for c in run.calls)    # nothing removed
    assert "stop sglang" in capsys.readouterr().out                 # printed the plan


def test_cmd_down_keep_container_dry_run_omits_remove(capsys):
    serv = [{"name": "heavy", "container": "sglang", "port": 1, "health": "/health"}]
    run = _inspect_returning("running")
    assert serves.cmd_down(
        serv, ["heavy"], dry_run=True, keep_container=True, _run=run
    ) == 0
    out = capsys.readouterr().out
    assert "stop sglang" in out
    assert "rm -f sglang" not in out


# ---- up --compose -----------------------------------------------------------

def test_cmd_up_compose_runs_compose_up_argv():
    calls = []

    def run(argv, **k):
        calls.append(argv)
        return proc(0)

    assert serves.cmd_up_compose("/x/experiment.yml", ["svc-a", "svc-b"], _run=run) == 0
    assert calls == [["docker", "compose", "-f", "/x/experiment.yml",
                      "up", "-d", "svc-a", "svc-b"]]


def test_cmd_up_compose_no_services_brings_up_whole_file():
    calls = []

    def run(argv, **k):
        calls.append(argv)
        return proc(0)

    assert serves.cmd_up_compose("/x/experiment.yml", [], _run=run) == 0
    assert calls == [["docker", "compose", "-f", "/x/experiment.yml", "up", "-d"]]


def test_cmd_up_compose_reports_failure():
    def run(argv, **k):
        return proc(1, "", "compose blew up")
    assert serves.cmd_up_compose("/x/experiment.yml", [], _run=run) == 1


def test_cmd_up_compose_dry_run_runs_nothing(capsys):
    calls = []

    def run(argv, **k):
        calls.append(argv)
        return proc(0)

    assert serves.cmd_up_compose("/x/experiment.yml", ["svc"], dry_run=True, _run=run) == 0
    assert calls == []  # nothing executed
    assert "/x/experiment.yml" in capsys.readouterr().out  # printed the plan


# ---- explicit dual-GPU operating mode ---------------------------------------

def test_mode_preview_lists_roles_competitors_and_rollback_without_mutation(
    tmp_path, capsys,
):
    loaded = serves.load_manifest(_manifest(tmp_path, DUAL_MODE_MANIFEST))
    run = _mode_run({"split-a": "running", "split-b": "running", "tp2": "absent"})
    assert serves.cmd_mode(
        loaded,
        "preview",
        "tp2",
        "split-stack",
        _run=run,
    ) == 0
    out = capsys.readouterr().out
    assert "dark-compute-a, dark-compute-b" in out
    assert "drain split-a via router tier llm-a" in out
    assert "stop: split-a, split-b" in out
    assert "rollback group split-stack: split-a, split-b" in out
    assert not any(call[:2] in (["docker", "stop"], ["docker", "compose"])
                   for call in run.calls)


def test_ordinary_up_cannot_start_exclusive_target(tmp_path, capsys):
    loaded = serves.load_manifest(_manifest(tmp_path, DUAL_MODE_MANIFEST))
    run = _mode_run({"split-a": "absent", "split-b": "absent", "tp2": "absent"})
    assert serves.cmd_up(loaded, ["tp2"], ledger_serves=loaded, _run=run) == 1
    assert "must be started with `serves mode enter" in capsys.readouterr().out
    assert not any(call[:2] == ["docker", "compose"] for call in run.calls)


def test_active_exclusive_owner_blocks_split_start_before_container_command(
    tmp_path, capsys,
):
    loaded = serves.load_manifest(_manifest(tmp_path, DUAL_MODE_MANIFEST))
    run = _mode_run({"split-a": "absent", "split-b": "absent", "tp2": "running"})
    assert serves.cmd_up(loaded, ["split-a"], ledger_serves=loaded, _run=run) == 1
    out = capsys.readouterr().out
    assert "tp2 owns both GPU roles; blocked: split-a" in out
    assert "no container command was run" in out
    assert not any(call[:2] == ["docker", "compose"] for call in run.calls)


def test_exclusive_mode_covers_legacy_unreserved_experiment_but_not_cpu_sidecar(
    tmp_path, capsys,
):
    loaded = serves.load_manifest(_manifest(tmp_path, DUAL_MODE_MANIFEST + """
        [[serve]]
        name = "legacy-experiment"
        container = "legacy-experiment"
        runtime = "docker"
        port = 39000
        model = "legacy-local"
        engine = "vllm"
        up = "docker compose -f {dir}/compose.yml up -d legacy-experiment"

        [[serve]]
        name = "realtime-proxy"
        container = "realtime-proxy"
        runtime = "docker"
        port = 8765
        model = "realtime-proxy"
        engine = "audio"
        gpu_inference = false
        up = "docker compose -f {dir}/compose.yml up -d realtime-proxy"
    """))
    states = {
        "split-a": "absent",
        "split-b": "absent",
        "tp2": "absent",
        "legacy-experiment": "running",
        "realtime-proxy": "running",
    }
    plan = serves.operating_mode_plan(
        loaded,
        "tp2",
        "split-stack",
        lambda container: states.get(container, "absent"),
    )
    assert plan["stop"] == ["legacy-experiment"]
    assert "legacy-experiment" in plan["blocked"]
    assert "realtime-proxy" not in plan["blocked"]

    states["legacy-experiment"] = "absent"
    states["tp2"] = "running"
    run = _mode_run(states)
    assert serves.cmd_up(
        loaded, ["legacy-experiment"], ledger_serves=loaded, _run=run
    ) == 1
    assert "blocked: legacy-experiment" in capsys.readouterr().out
    assert not any(call[:2] == ["docker", "compose"] for call in run.calls)


@pytest.mark.parametrize("owner_state", ["running", "error", "unknown"])
def test_ad_hoc_compose_cannot_bypass_active_or_unresolved_exclusive_owner(
    tmp_path, owner_state,
):
    manifest = _manifest(tmp_path, DUAL_MODE_MANIFEST)
    run = _mode_run({"split-a": "absent", "split-b": "absent", "tp2": owner_state})
    denial = serves.deny_ad_hoc_compose_during_exclusive(manifest, _run=run)
    assert denial
    assert "no container command was run" in denial
    assert not any(call[:2] == ["docker", "compose"] for call in run.calls)


def test_mode_entry_refuses_unresolved_competitor_before_mutation(tmp_path, capsys):
    loaded = serves.load_manifest(_manifest(tmp_path, DUAL_MODE_MANIFEST))
    run = _mode_run({"split-a": "error", "split-b": "running", "tp2": "absent"})
    assert serves.cmd_mode(
        loaded,
        "enter",
        "tp2",
        "split-stack",
        confirm=True,
        _run=run,
    ) == 1
    assert "UNRESOLVED: split-a state error" in capsys.readouterr().out
    assert not any(call[:2] in (["docker", "stop"], ["docker", "compose"])
                   for call in run.calls)


def test_mode_entry_accepts_dispatcher_confirmation_scope(
    tmp_path, monkeypatch, capsys,
):
    loaded = serves.load_manifest(_manifest(tmp_path, DUAL_MODE_MANIFEST))
    run = _mode_run({
        "split-a": "absent",
        "split-b": "absent",
        "tp2": "absent",
        serves.DEFAULT_ROUTER_CONTAINER: "exited",
    })
    monkeypatch.setattr(serves, "cmd_up", lambda *args, **kwargs: 0)

    with guard.confirmation_scope(True):
        assert serves.cmd_mode(
            loaded,
            "enter",
            "tp2",
            "split-stack",
            _run=run,
        ) == 0

    assert "mode entered: tp2" in capsys.readouterr().out


def test_canonical_cli_enters_and_leaves_synthetic_exclusive_mode(
    tmp_path, monkeypatch, capsys,
):
    # The documented canonical command: the dispatcher consumes --confirm and
    # forwards it back in argv (forward_confirm_flag), so the legacy leaf
    # parser sees confirm=True on both transitions.
    from anvil_serving import cli

    path = _manifest(tmp_path, DUAL_MODE_MANIFEST)
    states = {
        "split-a": "absent",
        "split-b": "absent",
        "tp2": "absent",
        serves.DEFAULT_ROUTER_CONTAINER: "exited",
    }
    run = _mode_run(states)
    seen_confirm = []
    real_cmd_mode = serves.cmd_mode

    def cmd_mode_with_fake_docker(*args, **kwargs):
        seen_confirm.append(kwargs.get("confirm"))
        return real_cmd_mode(*args, _run=run, **kwargs)

    monkeypatch.setattr(serves, "cmd_mode", cmd_mode_with_fake_docker)

    def fake_up(serves_list, names, **kwargs):
        for name in names:
            states[name] = "running"
        return 0

    monkeypatch.setattr(serves, "cmd_up", fake_up)

    tail = ["tp2", "--restore-group", "split-stack", "--manifest", path, "--confirm"]
    # `--skip-preflight-checks`: the real preflight gate has no `_run`
    # injection seam at the dispatcher level, and DUAL_MODE_MANIFEST's
    # split-a/split-b `up` names a compose file this test never creates on
    # disk -- irrelevant to what "enter" is exercising here (the confirm-flag
    # round trip through the declarative CLI).
    assert cli.main(["serves", "mode", "enter", *tail, "--skip-preflight-checks"]) == 0
    assert "mode entered: tp2" in capsys.readouterr().out

    assert cli.main(["serves", "mode", "leave", *tail]) == 0
    assert "mode left: restored split group split-stack" in capsys.readouterr().out
    assert seen_confirm == [True, True]


def test_failed_mode_entry_restores_split_stack_in_transaction_order(
    tmp_path, capsys,
):
    loaded = serves.load_manifest(_manifest(tmp_path, DUAL_MODE_MANIFEST))
    states = {"split-a": "running", "split-b": "running", "tp2": "absent"}
    run = _mode_run(states, fail_service="tp2")
    transitions = []

    def transition(action, tier, timeout=None):
        transitions.append((action, tier, timeout))
        return 0

    assert serves.cmd_mode(
        loaded,
        "enter",
        "tp2",
        "split-stack",
        confirm=True,
        _transition=transition,
        _run=run,
        _open=lambda *args, **kwargs: _HealthyResponse(),
        _sleep=lambda _: None,
    ) == 1
    mutations = [
        call for call in run.calls
        if call[:2] in (["docker", "stop"], ["docker", "compose"])
    ]
    stop_a = mutations.index(["docker", "stop", "split-a"])
    stop_b = mutations.index(["docker", "stop", "split-b"])
    tp_start = next(i for i, call in enumerate(mutations) if call[-1] == "tp2")
    restore_a = max(i for i, call in enumerate(mutations) if call[-1] == "split-a")
    restore_b = max(i for i, call in enumerate(mutations) if call[-1] == "split-b")
    assert stop_a < stop_b < tp_start < restore_a < restore_b
    assert transitions[:2] == [("quiesce", "llm-a", None), ("drain", "llm-a", 120)]
    assert transitions[-1] == ("readmit", "llm-a", None)
    assert states["split-a"] == states["split-b"] == "running"
    assert "restoring split stack" in capsys.readouterr().out


def test_mode_entry_preserve_on_failure_keeps_stopped_target_and_restores_split(
    tmp_path, monkeypatch, capsys,
):
    loaded = serves.load_manifest(_manifest(tmp_path, DUAL_MODE_MANIFEST))
    states = {"split-a": "running", "split-b": "running", "tp2": "absent"}
    run = _mode_run(states)
    real_cmd_up = serves.cmd_up

    def fail_target(serves_list, names, **kwargs):
        if names == ["tp2"]:
            states["tp2"] = "exited"
            return 1
        return real_cmd_up(serves_list, names, **kwargs)

    monkeypatch.setattr(serves, "cmd_up", fail_target)

    assert serves.cmd_mode(
        loaded,
        "enter",
        "tp2",
        "split-stack",
        confirm=True,
        preserve_on_failure=True,
        _transition=lambda *args, **kwargs: 0,
        _run=run,
        _open=lambda *args, **kwargs: _HealthyResponse(),
        _sleep=lambda _: None,
    ) == 1

    assert states == {
        "split-a": "running",
        "split-b": "running",
        "tp2": "exited",
    }
    assert ["docker", "rm", "-f", "tp2"] not in run.calls
    out = capsys.readouterr().out
    assert "preserved failed target tp2 in state exited" in out
    assert "anvil-serving serves logs tp2" in out


def test_mode_entry_preserve_on_failure_removes_restarting_target_before_restore(
    tmp_path, monkeypatch, capsys,
):
    loaded = serves.load_manifest(_manifest(tmp_path, DUAL_MODE_MANIFEST))
    states = {"split-a": "running", "split-b": "running", "tp2": "absent"}
    run = _mode_run(states, restart_on_stop="tp2")
    real_cmd_up = serves.cmd_up

    def fail_target(serves_list, names, **kwargs):
        if names == ["tp2"]:
            states["tp2"] = "running"
            return 1
        return real_cmd_up(serves_list, names, **kwargs)

    monkeypatch.setattr(serves, "cmd_up", fail_target)

    assert serves.cmd_mode(
        loaded,
        "enter",
        "tp2",
        "split-stack",
        confirm=True,
        preserve_on_failure=True,
        _transition=lambda *args, **kwargs: 0,
        _run=run,
        _open=lambda *args, **kwargs: _HealthyResponse(),
        _sleep=lambda _: None,
    ) == 1

    assert states["tp2"] == "absent"
    assert states["split-a"] == states["split-b"] == "running"
    remove = run.calls.index(["docker", "rm", "-f", "tp2"])
    restore_a = next(
        index for index, call in enumerate(run.calls)
        if index > remove and call[:2] == ["docker", "compose"]
        and call[-1] == "split-a"
    )
    assert remove < restore_a
    assert "could not be safely preserved" in capsys.readouterr().out


def test_mode_entry_skips_transitions_when_managed_router_is_stopped(
    tmp_path, capsys,
):
    loaded = serves.load_manifest(_manifest(tmp_path, DUAL_MODE_MANIFEST))
    states = {
        "split-a": "running",
        "split-b": "running",
        "tp2": "absent",
        serves.DEFAULT_ROUTER_CONTAINER: "exited",
    }
    run = _mode_run(states)

    assert serves.cmd_mode(
        loaded,
        "enter",
        "tp2",
        "split-stack",
        confirm=True,
        _run=run,
        _open=lambda *args, **kwargs: _HealthyResponse(),
        _sleep=lambda _: None,
    ) == 0

    assert states["split-a"] == states["split-b"] == "absent"
    assert states["tp2"] == "running"
    out = capsys.readouterr().out
    assert "router anvil-router is exited; quiesce" in out
    assert "router anvil-router is exited; drain" in out
    assert not any(
        isinstance(call, list) and call[:3] == [
            sys.executable, "-m", "anvil_serving.cli"
        ] and "transition" in call
        for call in run.calls
    )


def test_routed_mode_entry_installs_profile_then_guardedly_readmits_alias(
    tmp_path, capsys,
):
    loaded, target_config, _rollback_config = _routed_mode_manifest(tmp_path)
    states = {
        "split-a": "running",
        "split-b": "running",
        "tp2": "absent",
        serves.DEFAULT_ROUTER_CONTAINER: "running",
    }
    run = _mode_run(states)
    events = []

    def transition(action, tier, timeout=None):
        events.append((action, tier, timeout))
        return 0

    def install(config_file, **_kwargs):
        events.append(("install", config_file, None))
        return 0

    assert serves.cmd_mode(
        loaded,
        "enter",
        "tp2",
        "split-stack",
        confirm=True,
        _transition=transition,
        _install_config=install,
        _run=run,
        _open=lambda *args, **kwargs: _HealthyResponse(),
        _sleep=lambda _: None,
    ) == 0

    installed = events.index(("install", str(target_config), None))
    readmitted = max(
        index for index, event in enumerate(events)
        if event == ("readmit", "llm-a", None)
    )
    assert installed < readmitted
    config = __import__(
        "anvil_serving.router.config", fromlist=["load"]
    ).load(str(target_config))
    assert config.model_routes["llm.primary"] == "llm-a"
    assert config.tier("llm-a").model == "candidate-local"
    assert states["tp2"] == "running"
    assert "mode entered: tp2" in capsys.readouterr().out


def test_routed_mode_entry_router_failure_restores_profile_and_split_stack(
    tmp_path, capsys,
):
    loaded, target_config, rollback_config = _routed_mode_manifest(tmp_path)
    states = {
        "split-a": "running",
        "split-b": "running",
        "tp2": "absent",
        serves.DEFAULT_ROUTER_CONTAINER: "running",
    }
    run = _mode_run(states)
    installed = []

    def install(config_file, **_kwargs):
        installed.append(config_file)
        return 1 if config_file == str(target_config) else 0

    assert serves.cmd_mode(
        loaded,
        "enter",
        "tp2",
        "split-stack",
        confirm=True,
        _transition=lambda *args, **kwargs: 0,
        _install_config=install,
        _run=run,
        _open=lambda *args, **kwargs: _HealthyResponse(),
        _sleep=lambda _: None,
    ) == 1

    assert installed == [str(target_config), str(rollback_config)]
    assert states["tp2"] == "absent"
    assert states["split-a"] == states["split-b"] == "running"
    assert "failed while activating router tier" in capsys.readouterr().out


def test_routed_mode_entry_readmit_failure_restores_profile_and_split_stack(
    tmp_path,
):
    loaded, target_config, rollback_config = _routed_mode_manifest(tmp_path)
    states = {
        "split-a": "running",
        "split-b": "running",
        "tp2": "absent",
        serves.DEFAULT_ROUTER_CONTAINER: "running",
    }
    run = _mode_run(states)
    installed = []
    failed_target_readmit = False

    def install(config_file, **_kwargs):
        installed.append(config_file)
        return 0

    def transition(action, _tier, timeout=None):
        nonlocal failed_target_readmit
        del timeout
        if (
            action == "readmit"
            and installed
            and installed[-1] == str(target_config)
            and not failed_target_readmit
        ):
            failed_target_readmit = True
            return 1
        return 0

    assert serves.cmd_mode(
        loaded,
        "enter",
        "tp2",
        "split-stack",
        confirm=True,
        _transition=transition,
        _install_config=install,
        _run=run,
        _open=lambda *args, **kwargs: _HealthyResponse(),
        _sleep=lambda _: None,
    ) == 1

    assert failed_target_readmit is True
    assert installed == [str(target_config), str(rollback_config)]
    assert states["tp2"] == "absent"
    assert states["split-a"] == states["split-b"] == "running"


def test_routed_mode_leave_drains_target_and_restores_split_router_profile(
    tmp_path,
):
    loaded, _target_config, rollback_config = _routed_mode_manifest(tmp_path)
    states = {
        "split-a": "absent",
        "split-b": "absent",
        "tp2": "running",
        serves.DEFAULT_ROUTER_CONTAINER: "running",
    }
    run = _mode_run(states)
    events = []

    def transition(action, tier, timeout=None):
        events.append((action, tier, timeout))
        return 0

    def install(config_file, **_kwargs):
        events.append(("install", config_file, None))
        return 0

    assert serves.cmd_mode(
        loaded,
        "leave",
        "tp2",
        "split-stack",
        confirm=True,
        _transition=transition,
        _install_config=install,
        _run=run,
        _open=lambda *args, **kwargs: _HealthyResponse(),
        _sleep=lambda _: None,
    ) == 0

    assert events[:2] == [
        ("quiesce", "llm-a", None),
        ("drain", "llm-a", 120),
    ]
    assert ("install", str(rollback_config), None) in events
    assert events[-1] == ("readmit", "llm-a", None)
    assert states["tp2"] == "absent"
    assert states["split-a"] == states["split-b"] == "running"


def test_routed_exclusive_manifest_requires_complete_router_profiles(tmp_path):
    body = DUAL_MODE_MANIFEST.replace(
        'tensor_parallel_size = 2\n',
        'tensor_parallel_size = 2\n    router_tier = "llm-a"\n',
    )
    with pytest.raises(ValueError, match="must declare router_config"):
        serves.load_manifest(_manifest(tmp_path, body))


def test_mode_leave_force_releases_exclusive_owner_before_split_restore(
    tmp_path, capsys,
):
    loaded = serves.load_manifest(_manifest(tmp_path, DUAL_MODE_MANIFEST))
    states = {"split-a": "absent", "split-b": "absent", "tp2": "running"}
    run = _mode_run(states)

    assert serves.cmd_mode(
        loaded,
        "leave",
        "tp2",
        "split-stack",
        confirm=True,
        _transition=lambda *args, **kwargs: 0,
        _run=run,
        _open=lambda *args, **kwargs: _HealthyResponse(),
        _sleep=lambda _: None,
    ) == 0
    mutations = [
        call for call in run.calls
        if call[:3] == ["docker", "rm", "-f"] or call[:2] == ["docker", "compose"]
    ]
    release = mutations.index(["docker", "rm", "-f", "tp2"])
    restore_a = next(i for i, call in enumerate(mutations) if call[-1] == "split-a")
    restore_b = next(i for i, call in enumerate(mutations) if call[-1] == "split-b")
    assert release < restore_a < restore_b
    assert states["tp2"] == "absent"
    assert states["split-a"] == states["split-b"] == "running"
    assert "mode left: restored split group split-stack" in capsys.readouterr().out


def test_split_restore_skips_readmit_when_default_router_is_stopped(
    tmp_path, capsys,
):
    loaded = serves.load_manifest(_manifest(tmp_path, DUAL_MODE_MANIFEST))
    states = {
        "split-a": "absent",
        "split-b": "absent",
        "tp2": "absent",
        serves.DEFAULT_ROUTER_CONTAINER: "exited",
    }
    run = _mode_run(states)
    transitions = []
    plan = serves.operating_mode_plan(
        loaded,
        "tp2",
        "split-stack",
        lambda container: states.get(container, "absent"),
    )

    assert serves._restore_split_stack(
        loaded,
        plan,
        transition=lambda *args: transitions.append(args) or 0,
        _run=run,
        _open=lambda *args, **kwargs: _HealthyResponse(),
        _sleep=lambda _: None,
        skip_readmit_when_router_stopped=True,
    ) == 0
    assert transitions == []
    assert "need no live readmit" in capsys.readouterr().out


# ---- main() dispatch --------------------------------------------------------
#
# The cmd_* functions capture `_run=subprocess.run` as a def-time default, so these
# dispatch tests patch the cmd_* functions themselves (proving routing) rather than
# subprocess — no real docker is touched.

def test_main_mode_enter_forwards_preserve_on_failure(tmp_path, monkeypatch):
    path = _manifest(tmp_path, DUAL_MODE_MANIFEST)
    seen = {}

    def fake(serves_list, action, target, restore_group, **kwargs):
        seen.update(
            action=action,
            target=target,
            restore_group=restore_group,
            preserve_on_failure=kwargs["preserve_on_failure"],
        )
        return 0

    monkeypatch.setattr(serves, "cmd_mode", fake)

    # `--skip-preflight-checks`: the preflight gate runs for real (it has no
    # `_run` injection seam of its own at the dispatcher level) and
    # DUAL_MODE_MANIFEST's split-a/split-b `up` names a compose file that
    # does not exist on disk in this test — irrelevant to what this test
    # covers (forwarding of --preserve-on-failure to cmd_mode).
    assert serves.main([
        "mode", "enter", "tp2",
        "--restore-group", "split-stack",
        "--manifest", path,
        "--preserve-on-failure",
        "--confirm",
        "--skip-preflight-checks",
    ]) == 0
    assert seen == {
        "action": "enter",
        "target": "tp2",
        "restore_group": "split-stack",
        "preserve_on_failure": True,
    }


@pytest.mark.parametrize("action", ["status", "preview", "leave"])
def test_main_mode_rejects_preserve_on_failure_outside_enter(
    action, tmp_path, capsys,
):
    path = _manifest(tmp_path, DUAL_MODE_MANIFEST)
    argv = ["mode", action]
    if action != "status":
        argv += ["tp2", "--restore-group", "split-stack"]
    argv += ["--manifest", path, "--preserve-on-failure"]

    assert serves.main(argv) == 2
    assert "only valid with mode enter" in capsys.readouterr().err

def test_main_up_compose_needs_no_manifest(tmp_path, monkeypatch):
    # `up --compose` is independent of serves.toml: it dispatches BEFORE the manifest is
    # loaded, so a missing serves.toml does not error out (rc 2 for missing manifest).
    monkeypatch.chdir(tmp_path)  # no serves.toml here
    seen = {}

    def fake(compose_file, services, dry_run=False):
        seen["compose"], seen["services"], seen["dry_run"] = compose_file, services, dry_run
        return 0

    monkeypatch.setattr(serves, "cmd_up_compose", fake)
    rc = serves.main(["up", "--compose", "/x/experiment.yml", "svc-a", "svc-b"])
    assert rc == 0
    assert seen == {"compose": "/x/experiment.yml", "services": ["svc-a", "svc-b"],
                    "dry_run": False}


def test_main_compose_rejected_for_non_up_action(capsys):
    # --compose only means something for `up`; using it with any other action is a usage error.
    rc = serves.main(["down", "--compose", "/x/experiment.yml"])
    assert rc == 2
    assert "only valid with `up`" in capsys.readouterr().err


def test_main_compose_with_recreate_rejected(capsys):
    # --recreate is meaningless with --compose (compose up -d already recreates on change).
    rc = serves.main(["up", "--compose", "/x/experiment.yml", "--recreate"])
    assert rc == 2
    assert "--recreate" in capsys.readouterr().err


def test_main_down_forwards_keep_container(tmp_path, monkeypatch):
    path = _manifest(tmp_path, """
        [[serve]]
        name = "heavy"
        container = "sglang"
        runtime = "docker"
        port = 30000
        model = "primary-local"
        engine = "sglang"
    """)
    seen = {}

    def fake(serves_list, names, dry_run=False, keep_container=False, **_kwargs):
        seen.update(
            names=names,
            dry_run=dry_run,
            keep_container=keep_container,
        )
        return 0

    monkeypatch.setattr(serves, "cmd_down", fake)
    assert serves.main(
        ["down", "heavy", "--keep-container", "--manifest", path]
    ) == 0
    assert seen == {
        "names": ["heavy"],
        "dry_run": False,
        "keep_container": True,
    }


def test_main_rm_dispatches(tmp_path, monkeypatch):
    path = _manifest(tmp_path, """
        [[serve]]
        name = "heavy"
        container = "sglang"
        runtime = "docker"
        port = 30000
        model = "primary-local"
        engine = "sglang"
    """)
    seen = {}

    def fake(serves_list, names, dry_run=False, assume_yes=False):
        seen["names"], seen["dry_run"] = names, dry_run
        seen["assume_yes"] = assume_yes
        return 0

    monkeypatch.setattr(serves, "cmd_rm", fake)
    rc = serves.main(["rm", "port-squatter", "--dry-run", "--yes", "--manifest", path])
    assert rc == 0
    assert seen == {"names": ["port-squatter"], "dry_run": True, "assume_yes": True}


def test_main_adopt_dispatches(tmp_path, monkeypatch):
    path = _manifest(tmp_path, """
        [[serve]]
        name = "heavy"
        container = "sglang"
        runtime = "docker"
        port = 30000
        model = "primary-local"
        engine = "sglang"
    """)
    seen = {}

    def fake(serves_list, names, dry_run=False, assume_yes=False):
        seen["names"], seen["dry_run"] = names, dry_run
        seen["assume_yes"] = assume_yes
        return 0

    monkeypatch.setattr(serves, "cmd_adopt", fake)
    rc = serves.main(["adopt", "heavy", "--manifest", path])
    assert rc == 0
    assert seen == {"names": ["heavy"], "dry_run": False, "assume_yes": False}


# ---- logs -------------------------------------------------------------------

_TWO = [{"name": "heavy", "container": "vllm-heavy"}, {"name": "fast", "container": "vllm-fast"}]


def test_serves_logs_resolves_name_and_prints(capsys):
    def fake(argv, **kw):
        if argv[:2] == ["docker", "inspect"]:
            return proc(0, "running\n")
        return proc(0, "HEAVY LOG\n", "warn\n")
    rc = serves.cmd_logs(_TWO, ["heavy"], tail="5", _run=fake)
    assert rc == 0
    out = capsys.readouterr()
    assert "HEAVY LOG" in out.out and "warn" in out.err


def test_serves_logs_escapes_unicode_unsupported_by_console_codec():
    class NarrowConsole:
        encoding = "cp1252"

        def __init__(self):
            self.text = ""

        def write(self, value):
            value.encode(self.encoding)
            self.text += value

    stream = NarrowConsole()
    serves._write_console_safe(stream, "load █ done\n")
    assert stream.text == "load \\u2588 done\n"


def test_serves_logs_argv_targets_the_named_container():
    seen = {}
    def fake(argv, **kw):
        if argv[:2] == ["docker", "inspect"]:
            return proc(0, "running\n")
        seen["argv"] = argv
        seen["kwargs"] = kw
        return proc(0)
    serves.cmd_logs(_TWO, ["fast"], _run=fake)
    assert seen["argv"][:2] == ["docker", "logs"] and seen["argv"][-1] == "vllm-fast"
    assert seen["kwargs"]["encoding"] == "utf-8"
    assert seen["kwargs"]["errors"] == "replace"


def test_serves_logs_requires_a_name(capsys):
    # `logs` targets ONE serve, so no name is an error — NOT "all" (which would pick the sole
    # serve on a 1-serve manifest but error on a 2-serve one). Never touches docker.
    rc = serves.cmd_logs(_TWO, [], _run=lambda *a, **k: proc(0, "running\n"))
    assert rc == 2
    assert "needs a serve name" in capsys.readouterr().err


def test_serves_logs_multiple_names_refuses(capsys):
    rc = serves.cmd_logs(_TWO, ["heavy", "fast"], _run=lambda *a, **k: proc(0, "running\n"))
    assert rc == 2
    assert "ONE serve" in capsys.readouterr().err


def test_serves_logs_no_match_errors(capsys):
    rc = serves.cmd_logs(_TWO, ["nope"], _run=lambda *a, **k: proc(0))
    assert rc == 1
    assert "no matching serve" in capsys.readouterr().err


def test_serves_logs_absent_container(capsys):
    def fake(argv, **kw):
        if argv[:2] == ["docker", "inspect"]:
            return proc(1, "", "No such object")
        return proc(0)
    rc = serves.cmd_logs([{"name": "heavy", "container": "c"}], ["heavy"], _run=fake)
    assert rc == 1
    assert "does not exist" in capsys.readouterr().err


def test_serves_logs_dispatched_from_main(tmp_path, monkeypatch):
    path = _manifest(tmp_path, """
        [[serve]]
        name = "heavy"
        container = "vllm-heavy"
        runtime = "docker"
        port = 30002
        model = "primary-local"
        engine = "vllm"
        base_url = "http://127.0.0.1:30002/v1"
    """)
    seen = {}
    monkeypatch.setattr(serves, "cmd_logs",
                        lambda s, names, **k: seen.update(names=names, **k) or 0)
    rc = serves.main(["logs", "heavy", "--tail", "3", "--manifest", path])
    assert rc == 0 and seen["names"] == ["heavy"] and seen["tail"] == "3"


# ---- confirm gate (guard.confirm) -------------------------------------------

def test_cmd_rm_prompt_declined_removes_nothing(capsys):
    serv = [{"name": "h", "container": "sglang", "port": 1, "health": "/health"}]
    run = _inspect_returning("running")
    rc = serves.cmd_rm(serv, ["sglang"], _run=run, _input=lambda p: "n")
    assert rc == 1
    assert not any(c[:3] == ["docker", "rm", "-f"] for c in run.calls)
    assert "aborted" in capsys.readouterr().out


def test_cmd_rm_no_tty_answers_no():
    # EOF (piped/automation without --yes) must fail-safe to No.
    def eof(_prompt):
        raise EOFError
    serv = [{"name": "h", "container": "sglang", "port": 1, "health": "/health"}]
    run = _inspect_returning("running")
    assert serves.cmd_rm(serv, ["sglang"], _run=run, _input=eof) == 1
    assert not any(c[:3] == ["docker", "rm", "-f"] for c in run.calls)


def test_cmd_rm_dry_run_needs_no_confirmation():
    serv = [{"name": "h", "container": "sglang", "port": 1, "health": "/health"}]
    run = _inspect_returning("running")
    def explode(_prompt):
        raise AssertionError("dry-run must not prompt")
    assert serves.cmd_rm(serv, ["sglang"], dry_run=True, _run=run, _input=explode) == 0
    assert not any(c[:3] == ["docker", "rm", "-f"] for c in run.calls)


def test_cmd_adopt_prompt_declined_recreates_nothing(capsys):
    serv = [{"name": "h", "container": "sglang", "port": 1, "health": "/health",
             "up": ["docker", "compose", "-f", "x.yml", "up", "-d", "sglang"]}]
    run = _inspect_returning("running")
    rc = serves.cmd_adopt(serv, ["h"], _run=run, _input=lambda p: "")
    assert rc == 1
    assert not any(c[:3] == ["docker", "rm", "-f"] for c in run.calls)
    assert "aborted" in capsys.readouterr().out
