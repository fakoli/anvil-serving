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


# ---- manifest parsing -------------------------------------------------------

def test_load_manifest_parses_defaults_and_resolves_dir(tmp_path):
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
    assert s["up"] == "bash %s/serve.sh" % os.path.dirname(os.path.abspath(path))


def test_load_manifest_rejects_missing_required_fields(tmp_path):
    path = _manifest(tmp_path, '[[serve]]\nname = "x"\n')  # no container/port
    with pytest.raises(ValueError):
        serves.load_manifest(path)


def test_shipped_fakoli_manifest_is_valid():
    # the committed examples/fakoli-dark/serves.toml must always parse
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

def test_docker_state_running_exited_absent():
    assert serves.docker_state("c", _run=lambda *a, **k: proc(0, "running\n")) == "running"
    assert serves.docker_state("c", _run=lambda *a, **k: proc(0, "exited\n")) == "exited"
    assert serves.docker_state("c", _run=lambda *a, **k: proc(1, "", "no such")) == "absent"


def test_docker_state_absent_when_docker_missing():
    def boom(*a, **k):
        raise FileNotFoundError("docker not installed")
    assert serves.docker_state("c", _run=boom) == "absent"


# ---- down -------------------------------------------------------------------

def test_cmd_down_stops_only_running():
    serv = [{"name": "heavy", "container": "sglang", "port": 30000, "health": "/health"},
            {"name": "fast", "container": "vllm-gptoss", "port": 30001, "health": "/health"}]
    calls = []

    def run(argv, **k):
        calls.append(argv)
        if argv[:2] == ["docker", "inspect"]:
            return proc(0, "running\n" if "sglang" in argv else "exited\n")
        return proc(0)

    assert serves.cmd_down(serv, [], _run=run) == 0
    stops = [c for c in calls if c[:2] == ["docker", "stop"]]
    assert stops == [["docker", "stop", "sglang"]]  # only the running one


def test_cmd_down_reports_failure():
    serv = [{"name": "h", "container": "sglang", "port": 1, "health": "/health"}]

    def run(argv, **k):
        if argv[:2] == ["docker", "inspect"]:
            return proc(0, "running\n")
        return proc(1, "", "boom")  # docker stop fails

    assert serves.cmd_down(serv, [], _run=run) == 1


# ---- up ---------------------------------------------------------------------

def test_cmd_up_restarts_exited_container_not_fresh():
    serv = [{"name": "fast", "container": "vllm-gptoss", "port": 1,
             "health": "/health", "up": "bash x.sh"}]
    calls = []

    def run(argv, **k):
        calls.append(argv)
        if argv[:2] == ["docker", "inspect"]:
            return proc(0, "exited\n")
        return proc(0)

    assert serves.cmd_up(serv, [], _run=run) == 0
    assert ["docker", "start", "vllm-gptoss"] in calls
    # the `up` shell command must NOT be used for an existing (exited) container
    assert not any(isinstance(c, str) for c in calls)


def test_cmd_up_dry_run_starts_nothing():
    serv = [{"name": "fast", "container": "vllm-gptoss", "port": 1, "health": "/health"}]
    calls = []

    def run(argv, **k):
        calls.append(argv)
        return proc(0, "exited\n")

    serves.cmd_up(serv, [], dry_run=True, _run=run)
    assert not any(c[:2] == ["docker", "start"] for c in calls if isinstance(c, list))


def test_cmd_up_absent_without_up_command_errors():
    serv = [{"name": "x", "container": "x", "port": 1, "health": "/health"}]  # no up

    def run(argv, **k):
        return proc(1)  # inspect fails -> absent

    assert serves.cmd_up(serv, [], _run=run) == 1


def test_cmd_up_absent_with_up_runs_shell_command():
    serv = [{"name": "fast", "container": "vllm-gptoss", "port": 1,
             "health": "/health", "up": "bash x.sh"}]
    ran = {}

    def run(argv, shell=False, **k):
        if isinstance(argv, list) and argv[:2] == ["docker", "inspect"]:
            return proc(1)  # absent
        ran["cmd"], ran["shell"] = argv, shell
        return proc(0)

    assert serves.cmd_up(serv, [], _run=run) == 0
    assert ran["cmd"] == "bash x.sh" and ran["shell"] is True
