"""Tests for the `serves` management verbs — rm / adopt / up --compose.

Docker is injected via the module's `_run` seam, so these run with no docker, no
GPU, and no network. Mirrors tests/test_serves.py's fake-`_run` style.
"""
import json
import textwrap

import pytest

from anvil_serving import guard, serves
from anvil_serving.router import config as router_config
from tests.conftest import proc


@pytest.fixture(autouse=True)
def _isolated_host_policy(monkeypatch, tmp_path):
    """Keep a developer's enabled machine policy out of dispatch unit tests."""
    monkeypatch.setenv("ANVIL_SERVING_HOME", str(tmp_path / ".anvil-serving"))


def _manifest(tmp_path, body):
    p = tmp_path / "serves.toml"
    p.write_text(textwrap.dedent(body), encoding="utf-8")
    return str(p)


def _active_router_config(tmp_path, filename="active-router.toml"):
    """Synthetic direct ownership for legacy routed mode transactions."""
    path = tmp_path / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent("""
        [router]
        [[router.tiers]]
        id = "llm-a"
        base_url = "http://127.0.0.1:30001/v1"
        model = "split-a-local"
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
    return router_config.load(path)


def _cmd_mode(tmp_path, *args, **kwargs):
    return serves.cmd_mode(*args, active_config=_active_router_config(tmp_path), **kwargs)


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


EXCLUSIVE_ROLLBACK_MANIFEST = DUAL_MODE_MANIFEST + """
    [[serve]]
    name = "r11"
    container = "r11"
    runtime = "docker"
    port = 30004
    model = "baseline-r11-local"
    engine = "vllm"
    gpu_roles = ["dark-compute-a", "dark-compute-b"]
    vram_mib = 90000
    residency = "on-demand"
    operating_mode = "dual-gpu-exclusive"
    tensor_parallel_size = 2
    router_tier = "llm-a"
    router_config = "{dir}/r11-router.toml"
    rollback_router_config = "{dir}/r11-rollback-router.toml"
    groups = ["r11-rollback"]
    up = "docker compose -f {dir}/compose.yml up -d r11"
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


def test_cmd_rm_dry_run_removes_nothing(capsys):
    serv = [{"name": "heavy", "container": "sglang", "port": 1, "health": "/health"}]
    run = _inspect_returning("running")
    assert serves.cmd_rm(serv, ["sglang"], dry_run=True, assume_yes=True, _run=run) == 0
    assert not any(c[:3] == ["docker", "rm", "-f"] for c in run.calls)
    assert "rm -f sglang" in capsys.readouterr().out  # printed the plan


# ---- adopt ------------------------------------------------------------------


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
    assert _cmd_mode(tmp_path,
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
        assert _cmd_mode(tmp_path,
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
    _active_router_config(tmp_path / ".anvil-serving", "router.toml")
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

    assert _cmd_mode(tmp_path,
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

    assert _cmd_mode(tmp_path,
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

    assert _cmd_mode(tmp_path,
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


def test_mode_restores_single_exclusive_rollback_target_without_router_swap(
    tmp_path,
):
    _active_router_config(tmp_path, "r11-router.toml")
    _active_router_config(tmp_path, "r11-rollback-router.toml")
    loaded = serves.load_manifest(_manifest(tmp_path, EXCLUSIVE_ROLLBACK_MANIFEST))
    installed = []

    def install(config_file, **_kwargs):
        installed.append(config_file)
        return 0

    states = {"split-a": "absent", "split-b": "absent", "tp2": "running", "r11": "absent"}
    run = _mode_run(states)
    transitions = []

    def transition(action, tier, timeout=None):
        transitions.append((action, tier, timeout))
        return 0

    assert _cmd_mode(tmp_path,
        loaded,
        "leave",
        "tp2",
        "r11-rollback",
        confirm=True,
        _transition=transition,
        _install_config=install,
        _run=run,
        _open=lambda *args, **kwargs: _HealthyResponse(),
        _sleep=lambda _: None,
    ) == 0
    assert states["tp2"] == "absent"
    assert states["r11"] == "running"
    assert transitions == [("readmit", "llm-a", None)]
    assert installed == []

    states = {"split-a": "absent", "split-b": "absent", "tp2": "absent", "r11": "running"}
    run = _mode_run(states, fail_service="tp2")
    transitions = []
    assert _cmd_mode(tmp_path,
        loaded,
        "enter",
        "tp2",
        "r11-rollback",
        confirm=True,
        _transition=transition,
        _install_config=install,
        _run=run,
        _open=lambda *args, **kwargs: _HealthyResponse(),
        _sleep=lambda _: None,
    ) == 1
    assert states["tp2"] == "absent"
    assert states["r11"] == "running"
    assert transitions == [
        ("quiesce", "llm-a", None),
        ("drain", "llm-a", 120),
        ("readmit", "llm-a", None),
    ]
    assert installed == []


# ---- main() dispatch --------------------------------------------------------
#
# The cmd_* functions capture `_run=subprocess.run` as a def-time default, so these
# dispatch tests patch the cmd_* functions themselves (proving routing) rather than
# subprocess — no real docker is touched.


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


# ---- confirm gate (guard.confirm) -------------------------------------------

def test_cmd_rm_prompt_declined_removes_nothing(capsys):
    serv = [{"name": "h", "container": "sglang", "port": 1, "health": "/health"}]
    run = _inspect_returning("running")
    rc = serves.cmd_rm(serv, ["sglang"], _run=run, _input=lambda p: "n")
    assert rc == 1
    assert not any(c[:3] == ["docker", "rm", "-f"] for c in run.calls)
    assert "aborted" in capsys.readouterr().out


def test_cmd_rm_dry_run_needs_no_confirmation():
    serv = [{"name": "h", "container": "sglang", "port": 1, "health": "/health"}]
    run = _inspect_returning("running")
    def explode(_prompt):
        raise AssertionError("dry-run must not prompt")
    assert serves.cmd_rm(serv, ["sglang"], dry_run=True, _run=run, _input=explode) == 0
    assert not any(c[:3] == ["docker", "rm", "-f"] for c in run.calls)
