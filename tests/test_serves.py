"""Tests for `anvil-serving serves` — the model-serve lifecycle verb.

Docker + nvidia-smi + HTTP are injected (the module exposes `_run`/`_open`
seams), so these run with no docker, no GPU, and no network.
"""
import os
import textwrap
import types

import pytest

from anvil_serving import serves


def proc(rc=0, out="", err=""):
    return types.SimpleNamespace(returncode=rc, stdout=out, stderr=err)


def _manifest(tmp_path, body):
    p = tmp_path / "serves.toml"
    p.write_text(textwrap.dedent(body), encoding="utf-8")
    return str(p)


def _inspect_returning(state, stop_rc=0, stop_err=""):
    """A fake _run: `docker inspect` -> `state`, anything else -> proc(stop_rc)."""
    calls = []

    def run(argv, **k):
        calls.append(argv)
        if isinstance(argv, list) and argv[:2] == ["docker", "inspect"]:
            if state == "absent":
                return proc(1, "", "Error: No such object")
            if state == "error":
                return proc(1, "", "Cannot connect to the Docker daemon")
            return proc(0, state + "\n")
        return proc(stop_rc, "", stop_err)

    run.calls = calls
    return run


# ---- manifest parsing -------------------------------------------------------

def test_load_manifest_parses_up_into_argv_list(tmp_path):
    path = _manifest(tmp_path, """
        [[serve]]
        name = "fast"
        container = "vllm-gptoss"
        port = 30001
        up = "bash {dir}/serve.sh"
    """)
    (s,) = serves.load_manifest(path)
    assert (s["name"], s["container"], s["port"]) == ("fast", "vllm-gptoss", 30001)
    assert s["health"] == "/health"  # defaulted
    mdir = os.path.dirname(os.path.abspath(path))
    assert s["up"] == ["bash", mdir + "/serve.sh"]  # shlex-split argv list, not a string


def test_load_manifest_up_keeps_spaced_dir_as_one_token(tmp_path):
    d = tmp_path / "a b"  # a directory with a space
    d.mkdir()
    path = str(d / "serves.toml")
    with open(path, "w", encoding="utf-8") as f:
        f.write('[[serve]]\nname="x"\ncontainer="x"\nport=1\nup="bash {dir}/s.sh"\n')
    (s,) = serves.load_manifest(path)
    assert s["up"] == ["bash", os.path.dirname(os.path.abspath(path)) + "/s.sh"]
    assert len(s["up"]) == 2  # the space in {dir} did NOT split the path token


def test_load_manifest_rejects_missing_required_fields(tmp_path):
    path = _manifest(tmp_path, '[[serve]]\nname = "x"\n')  # no container/port
    with pytest.raises(ValueError):
        serves.load_manifest(path)


def test_shipped_fakoli_manifest_is_valid():
    serves_list = serves.load_manifest(serves.DEFAULT_MANIFEST)
    names = {s["name"] for s in serves_list}
    assert {"heavy", "fast"} <= names


# ---- selection --------------------------------------------------------------

def test_select_by_name_container_or_all():
    serv = [{"name": "heavy", "container": "sglang", "port": 30000},
            {"name": "fast", "container": "vllm-gptoss", "port": 30001}]
    assert len(serves._select(serv, [])) == 2
    assert [s["name"] for s in serves._select(serv, ["fast"])] == ["fast"]
    assert [s["name"] for s in serves._select(serv, ["sglang"])] == ["heavy"]
    assert serves._select(serv, ["nope"]) == []


# ---- docker_state -----------------------------------------------------------

def test_docker_state_reports_raw_status():
    for st in ("running", "exited", "created", "paused", "restarting"):
        assert serves.docker_state("c", _run=lambda *a, _s=st, **k: proc(0, _s + "\n")) == st


def test_docker_state_distinguishes_absent_from_error():
    assert serves.docker_state("c", _run=lambda *a, **k: proc(1, "", "Error: No such object: c")) == "absent"
    # daemon down / permission denied is NOT absence
    assert serves.docker_state("c", _run=lambda *a, **k: proc(1, "", "Cannot connect to the Docker daemon")) == "error"


def test_docker_state_error_when_docker_missing():
    def boom(*a, **k):
        raise FileNotFoundError("docker not installed")
    assert serves.docker_state("c", _run=boom) == "error"


# ---- down -------------------------------------------------------------------

def test_cmd_down_stops_running():
    serv = [{"name": "h", "container": "sglang", "port": 1, "health": "/health"}]
    run = _inspect_returning("running")
    assert serves.cmd_down(serv, [], _run=run) == 0
    assert ["docker", "stop", "sglang"] in run.calls


def test_cmd_down_stops_paused_container_too():
    # a PAUSED container still holds 100% of its VRAM — `down` must stop it.
    serv = [{"name": "f", "container": "vllm", "port": 1, "health": "/health"}]
    run = _inspect_returning("paused")
    assert serves.cmd_down(serv, [], _run=run) == 0
    assert ["docker", "stop", "vllm"] in run.calls


def test_cmd_down_skips_already_stopped():
    serv = [{"name": "f", "container": "vllm", "port": 1, "health": "/health"}]
    run = _inspect_returning("exited")
    assert serves.cmd_down(serv, [], _run=run) == 0
    assert not any(c[:2] == ["docker", "stop"] for c in run.calls)


def test_cmd_down_error_state_is_not_false_success():
    # docker daemon unreachable -> we cannot stop, and must NOT claim rc 0.
    serv = [{"name": "f", "container": "vllm", "port": 1, "health": "/health"}]
    run = _inspect_returning("error")
    assert serves.cmd_down(serv, [], _run=run) == 1
    assert not any(c[:2] == ["docker", "stop"] for c in run.calls)


def test_cmd_down_reports_stop_failure():
    serv = [{"name": "h", "container": "sglang", "port": 1, "health": "/health"}]
    run = _inspect_returning("running", stop_rc=1, stop_err="boom")
    assert serves.cmd_down(serv, [], _run=run) == 1


# ---- up ---------------------------------------------------------------------

def test_cmd_up_restarts_exited_with_docker_start():
    serv = [{"name": "f", "container": "vllm", "port": 1, "health": "/health", "up": ["bash", "x.sh"]}]
    run = _inspect_returning("exited")
    assert serves.cmd_up(serv, [], _run=run) == 0
    assert ["docker", "start", "vllm"] in run.calls
    # the fresh-create `up` must NOT be used for an existing container
    assert ["bash", "x.sh"] not in run.calls


def test_cmd_up_unpauses_paused():
    serv = [{"name": "f", "container": "vllm", "port": 1, "health": "/health", "up": ["bash", "x.sh"]}]
    run = _inspect_returning("paused")
    assert serves.cmd_up(serv, [], _run=run) == 0
    assert ["docker", "unpause", "vllm"] in run.calls


def test_cmd_up_dead_is_not_auto_created():
    # a dead/exotic state must not silently trigger fresh-create (collision/destroy).
    serv = [{"name": "f", "container": "vllm", "port": 1, "health": "/health", "up": ["bash", "x.sh"]}]
    run = _inspect_returning("dead")
    assert serves.cmd_up(serv, [], _run=run) == 1
    assert all(c[:2] == ["docker", "inspect"] for c in run.calls)  # only inspected


def test_cmd_up_error_state_does_not_create():
    serv = [{"name": "f", "container": "vllm", "port": 1, "health": "/health", "up": ["bash", "x.sh"]}]
    run = _inspect_returning("error")
    assert serves.cmd_up(serv, [], _run=run) == 1
    assert all(c[:2] == ["docker", "inspect"] for c in run.calls)


def test_cmd_up_absent_runs_up_argv_list_no_shell():
    serv = [{"name": "f", "container": "vllm", "port": 1, "health": "/health", "up": ["bash", "x.sh"]}]
    ran = {}

    def run(argv, shell=False, **k):
        if isinstance(argv, list) and argv[:2] == ["docker", "inspect"]:
            return proc(1, "", "No such object")  # absent
        ran["argv"], ran["shell"] = argv, shell
        return proc(0)

    assert serves.cmd_up(serv, [], _run=run) == 0
    assert ran["argv"] == ["bash", "x.sh"] and ran["shell"] is False  # argv list, never shell=True


def test_cmd_up_absent_without_up_command_errors():
    serv = [{"name": "x", "container": "x", "port": 1, "health": "/health"}]  # no up
    run = _inspect_returning("absent")
    assert serves.cmd_up(serv, [], _run=run) == 1


def test_cmd_up_dry_run_starts_nothing():
    serv = [{"name": "f", "container": "vllm", "port": 1, "health": "/health"}]
    run = _inspect_returning("exited")
    serves.cmd_up(serv, [], dry_run=True, _run=run)
    assert not any(c[:2] == ["docker", "start"] for c in run.calls if isinstance(c, list))
