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
        model = "fast-local"
        engine = "vllm"
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
        f.write(
            '[[serve]]\nname="x"\ncontainer="x"\nport=1\nmodel="x"\n'
            'engine="vllm"\nup="bash {dir}/s.sh"\n'
        )
    (s,) = serves.load_manifest(path)
    assert s["up"] == ["bash", os.path.dirname(os.path.abspath(path)) + "/s.sh"]
    assert len(s["up"]) == 2  # the space in {dir} did NOT split the path token


def test_load_manifest_rejects_missing_required_fields(tmp_path):
    path = _manifest(tmp_path, '[[serve]]\nname = "x"\n')  # missing most required fields
    with pytest.raises(ValueError) as exc:
        serves.load_manifest(path)
    msg = str(exc.value)
    assert "container" in msg and "port" in msg and "model/served_name" in msg
    assert "engine" in msg


def test_load_manifest_normalizes_llamacpp_alias_and_served_name(tmp_path):
    path = _manifest(tmp_path, """
        [[serve]]
        name = "gguf"
        container = "llamacpp"
        port = 39015
        served_name = "devstral-gguf"
        engine = "llama.cpp"
    """)
    (s,) = serves.load_manifest(path)
    assert s["model"] == "devstral-gguf"
    assert s["engine"] == "llamacpp"


def test_shipped_fakoli_manifest_is_valid():
    serves_list = serves.load_manifest(serves.EXAMPLE_MANIFEST)
    names = {s["name"] for s in serves_list}
    assert {"heavy", "fast", "fast-devstral-small2-llamacpp"} <= names
    by_name = {s["name"]: s for s in serves_list}
    assert by_name["fast-qwen36-35b-a3b"]["engine"] == "vllm"
    assert by_name["fast-glm47-flash-sglang"]["engine"] == "sglang"
    assert by_name["fast-devstral-small2-llamacpp"]["engine"] == "llamacpp"


def test_shipped_fast_candidate_dry_run_uses_manifest_compose(capsys):
    serves_list = serves.load_manifest(serves.EXAMPLE_MANIFEST)
    run = _inspect_returning("absent")
    rc = serves.cmd_up(
        serves_list, ["fast-devstral-small2-llamacpp"], dry_run=True, _run=run
    )
    assert rc == 0
    assert not any(c[:2] == ["docker", "compose"] for c in run.calls)
    out = capsys.readouterr().out
    assert "docker compose" in out
    assert "fast-devstral-small2-llamacpp" in out


def test_cmd_up_loads_manifest_adjacent_dotenv_without_overriding_shell(tmp_path, monkeypatch):
    path = _manifest(tmp_path, """
        [[serve]]
        name = "gepard"
        container = "gepard-fast-tts"
        port = 39111
        model = "gepard-1.0"
        engine = "vllm"
        up = "docker compose -f {dir}/docker-compose.experiment.yml up -d tts-gepard-fast"
    """)
    (serve,) = serves.load_manifest(path)
    (tmp_path / ".env").write_text(
        "HF_TOKEN=file-token\nGEPARD_DATABASE_URL=postgresql://example\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("HF_TOKEN", "shell-token")
    captured_env = {}

    def run(argv, **kwargs):
        if argv[:2] == ["docker", "inspect"]:
            return proc(1, "", "Error: No such object")
        captured_env.update(kwargs.get("env") or {})
        return proc(0, "", "")

    assert serves.cmd_up([serve], [], _run=run) == 0
    assert captured_env["HF_TOKEN"] == "shell-token"
    assert captured_env["GEPARD_DATABASE_URL"] == "postgresql://example"


def test_cmd_up_loads_home_dotenv_as_fallback(tmp_path, monkeypatch):
    home = tmp_path / "home"
    manifest_dir = tmp_path / "manifest"
    home.mkdir()
    manifest_dir.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.delenv("HF_TOKEN", raising=False)
    (home / ".env").write_text("HF_TOKEN=home-token\n", encoding="utf-8")
    path = _manifest(manifest_dir, """
        [[serve]]
        name = "gepard"
        container = "gepard-fast-tts"
        port = 39111
        model = "gepard-1.0"
        engine = "vllm"
        up = "docker compose -f {dir}/docker-compose.experiment.yml up -d tts-gepard-fast"
    """)
    (serve,) = serves.load_manifest(path)
    captured_env = {}

    def run(argv, **kwargs):
        if argv[:2] == ["docker", "inspect"]:
            return proc(1, "", "Error: No such object")
        captured_env.update(kwargs.get("env") or {})
        return proc(0, "", "")

    assert serves.cmd_up([serve], [], _run=run) == 0
    assert captured_env["HF_TOKEN"] == "home-token"


def test_cmd_up_prefers_config_home_dotenv_over_home_fallback(tmp_path, monkeypatch):
    home = tmp_path / "home"
    config_home = tmp_path / "anvil-serving"
    manifest_dir = tmp_path / "manifest"
    home.mkdir()
    config_home.mkdir()
    manifest_dir.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("ANVIL_SERVING_HOME", str(config_home))
    monkeypatch.delenv("HF_TOKEN", raising=False)
    (home / ".env").write_text("HF_TOKEN=home-token\n", encoding="utf-8")
    (config_home / ".env").write_text("HF_TOKEN=config-token\n", encoding="utf-8")
    path = _manifest(manifest_dir, """
        [[serve]]
        name = "gepard"
        container = "gepard-fast-tts"
        port = 39111
        model = "gepard-1.0"
        engine = "vllm"
        up = "docker compose -f {dir}/docker-compose.experiment.yml up -d tts-gepard-fast"
    """)
    (serve,) = serves.load_manifest(path)
    captured_env = {}

    def run(argv, **kwargs):
        if argv[:2] == ["docker", "inspect"]:
            return proc(1, "", "Error: No such object")
        captured_env.update(kwargs.get("env") or {})
        return proc(0, "", "")

    assert serves.cmd_up([serve], [], _run=run) == 0
    assert captured_env["HF_TOKEN"] == "config-token"


# ---- default manifest / missing manifest (genericity:T012) ---------------------

def test_default_manifest_searches_cwd_then_config_home():
    assert serves.DEFAULT_MANIFEST == "./serves.toml"
    candidates = serves.default_manifest_candidates()
    assert candidates[0] == "./serves.toml"
    assert candidates[1].endswith(os.path.join(".anvil-serving", "serves.toml"))
    assert serves.EXAMPLE_MANIFEST.endswith(os.path.join("examples", "fakoli-dark", "serves.toml"))


def test_resolve_manifest_path_uses_config_home_when_cwd_missing(tmp_path, monkeypatch):
    config_home = tmp_path / "anvil-serving"
    config_home.mkdir()
    manifest = config_home / "serves.toml"
    manifest.write_text("[[serve]]\nname='x'\ncontainer='x'\nport=1\nmodel='x'\nengine='vllm'\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ANVIL_SERVING_HOME", str(config_home))
    assert serves.resolve_manifest_path() == str(manifest)


def test_missing_manifest_errors_pointing_to_init(tmp_path, capsys, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ANVIL_SERVING_HOME", str(tmp_path / "missing-home"))
    rc = serves.main(["status"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "anvil-serving init" in err
    assert "serves.toml" in err


def test_missing_explicit_manifest_also_points_to_init(tmp_path, capsys):
    missing = str(tmp_path / "nope.toml")
    rc = serves.main(["status", "--manifest", missing])
    assert rc == 2
    assert "anvil-serving init" in capsys.readouterr().err


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


# ---- drift-safe `up` --------------------------------------------------------
#
# `docker start` resurrects an existing container with whatever model/args it was
# CREATED with, ignoring later serves.toml / compose edits — which once served a
# stale `qwen3-coder-30b-awq` in place of the declared model. `up` must be drift-safe:
#  - compose serve  -> run `docker compose up -d` (recreates natively on config drift),
#  - script serve   -> `docker start` but WARN loudly on model drift,
#  - `--recreate`   -> force `docker rm -f` + `up` for either kind.

def _up_run(state, created_argv=None, step_rc=0, step_err=""):
    """A fake _run for cmd_up: `docker inspect ... .State.Status` -> `state`;
    `docker inspect ... .Config.Cmd/.Args` -> the container's created argv (one
    token per line); any other command (rm / start / unpause / `up`) -> proc(rc).
    """
    calls = []

    def run(argv, **k):
        calls.append(argv)
        if isinstance(argv, list) and argv[:2] == ["docker", "inspect"]:
            tmpl = argv[3] if len(argv) > 3 else ""
            if ".State.Status" in tmpl:
                if state == "absent":
                    return proc(1, "", "Error: No such object")
                if state == "error":
                    return proc(1, "", "Cannot connect to the Docker daemon")
                return proc(0, state + "\n")
            return proc(0, "\n".join(created_argv or []) + "\n")  # created-argv inspect
        return proc(step_rc, "", step_err)

    run.calls = calls
    return run


def test_model_from_argv_prefers_served_name_then_model_path():
    argv = ["python", "-m", "vllm", "--model", "org/repo", "--served-model-name", "declared"]
    assert serves._model_from_argv(argv) == "declared"          # served-name wins
    assert serves._model_from_argv(["--model", "org/repo"]) == "org/repo"  # falls back
    assert serves._model_from_argv(["--model-path", "/w/qwen35-awq"]) == "/w/qwen35-awq"
    assert serves._model_from_argv(["--served-model-name=eq-form"]) == "eq-form"  # --flag=value
    assert serves._model_from_argv(["python", "-m", "vllm"]) is None  # no model flag


def test_is_compose_up_detects_compose_vs_script():
    assert serves._is_compose_up(["docker", "compose", "-f", "x.yml", "up", "-d"])
    assert serves._is_compose_up(["docker-compose", "up", "-d"])  # legacy hyphenated
    assert not serves._is_compose_up(["bash", "serve.sh"])  # docker run script
    assert not serves._is_compose_up(None)


def test_cmd_up_compose_serve_runs_compose_up_not_docker_start():
    # THE fix: an existing (stopped) compose serve is brought up with `docker compose
    # up -d` — which natively recreates on config drift — NOT a blind `docker start`
    # that would resurrect its stale model.
    serv = [{"name": "heavy", "container": "sglang", "port": 1, "health": "/health",
             "model": "qwen35-awq-local",
             "up": ["docker", "compose", "-f", "/x/docker-compose.yml", "up", "-d"]}]
    run = _inspect_returning("exited")
    assert serves.cmd_up(serv, [], _run=run) == 0
    assert ["docker", "compose", "-f", "/x/docker-compose.yml", "up", "-d"] in run.calls
    assert not any(c[:2] == ["docker", "start"] for c in run.calls)  # never blind-started


def test_cmd_up_compose_serve_running_reruns_compose_up_for_drift():
    # THE M1 fix: a RUNNING compose serve is still (re)run through `docker compose up -d`
    # UNCONDITIONALLY — a cheap no-op when the compose config is unchanged, and a native
    # recreate when the compose file drifted (ADR-0002). A blind "already running" short-
    # circuit would silently keep serving a stale model after the compose file was edited.
    serv = [{"name": "heavy", "container": "sglang", "port": 1, "health": "/health",
             "model": "qwen35-awq-local",
             "up": ["docker", "compose", "-f", "/x/docker-compose.yml", "up", "-d"]}]
    run = _inspect_returning("running")
    assert serves.cmd_up(serv, [], _run=run) == 0
    assert ["docker", "compose", "-f", "/x/docker-compose.yml", "up", "-d"] in run.calls
    assert not any(c[:2] == ["docker", "start"] for c in run.calls)  # never blind-started


def test_cmd_up_paused_compose_serve_is_unpaused_not_composed():
    # N1: a PAUSED compose serve must be `docker unpause`d (handled before the compose
    # branch), not routed through `docker compose up -d` — which would not unpause it and
    # would leave the serve stuck paused.
    serv = [{"name": "heavy", "container": "sglang", "port": 1, "health": "/health",
             "model": "qwen35-awq-local",
             "up": ["docker", "compose", "-f", "/x/docker-compose.yml", "up", "-d"]}]
    run = _inspect_returning("paused")
    assert serves.cmd_up(serv, [], _run=run) == 0
    assert ["docker", "unpause", "sglang"] in run.calls
    assert serv[0]["up"] not in run.calls  # did NOT take the compose path


def test_cmd_up_script_serve_warns_on_model_drift(capsys):
    # a `docker run` script serve can't self-heal via compose -> `docker start` + a
    # loud warning naming the STALE served model vs the declared one.
    serv = [{"name": "fast", "container": "vllm-gptoss", "port": 1, "health": "/health",
             "model": "gpt-oss-20b", "up": ["bash", "serve-fast.sh"]}]
    run = _up_run("exited", created_argv=["--served-model-name", "qwen3-coder-30b-awq"])
    assert serves.cmd_up(serv, [], _run=run) == 0
    out = capsys.readouterr().out
    assert "WARNING" in out and "qwen3-coder-30b-awq" in out and "gpt-oss-20b" in out
    assert ["docker", "start", "vllm-gptoss"] in run.calls          # current behavior kept
    assert not any(c[:3] == ["docker", "rm", "-f"] for c in run.calls)  # no auto-recreate


def test_cmd_up_script_serve_no_drift_starts_quietly(capsys):
    serv = [{"name": "fast", "container": "vllm-gptoss", "port": 1, "health": "/health",
             "model": "gpt-oss-20b", "up": ["bash", "serve-fast.sh"]}]
    run = _up_run("exited", created_argv=["--served-model-name", "gpt-oss-20b"])
    assert serves.cmd_up(serv, [], _run=run) == 0
    assert ["docker", "start", "vllm-gptoss"] in run.calls
    assert "WARNING" not in capsys.readouterr().out


def test_cmd_up_script_serve_drift_ignored_when_model_undeterminable(capsys):
    # inspect can't reveal the served model (no model flag) -> no false-positive warning.
    serv = [{"name": "fast", "container": "vllm-gptoss", "port": 1, "health": "/health",
             "model": "gpt-oss-20b", "up": ["bash", "serve-fast.sh"]}]
    run = _up_run("exited", created_argv=["python", "-m", "vllm"])  # no model flag
    assert serves.cmd_up(serv, [], _run=run) == 0
    assert ["docker", "start", "vllm-gptoss"] in run.calls
    assert "WARNING" not in capsys.readouterr().out


def test_cmd_up_recreate_flag_force_removes_then_reups_compose():
    serv = [{"name": "heavy", "container": "sglang", "port": 1, "health": "/health",
             "model": "qwen35-awq-local", "up": ["docker", "compose", "up", "-d"]}]
    run = _inspect_returning("exited")
    assert serves.cmd_up(serv, [], recreate=True, _run=run) == 0
    assert ["docker", "rm", "-f", "sglang"] in run.calls
    assert ["docker", "compose", "up", "-d"] in run.calls
    assert not any(c[:2] == ["docker", "start"] for c in run.calls)


def test_cmd_up_recreate_flag_works_for_script_serve():
    serv = [{"name": "fast", "container": "vllm-gptoss", "port": 1, "health": "/health",
             "model": "gpt-oss-20b", "up": ["bash", "serve-fast.sh"]}]
    run = _inspect_returning("exited")
    assert serves.cmd_up(serv, [], recreate=True, _run=run) == 0
    assert ["docker", "rm", "-f", "vllm-gptoss"] in run.calls
    assert ["bash", "serve-fast.sh"] in run.calls
    assert not any(c[:2] == ["docker", "start"] for c in run.calls)


def test_cmd_up_recreate_without_up_command_fails():
    serv = [{"name": "x", "container": "x", "port": 1, "health": "/health", "model": "m"}]
    run = _inspect_returning("exited")
    assert serves.cmd_up(serv, [], recreate=True, _run=run) == 1
    assert not any(c[:2] == ["docker", "start"] for c in run.calls)


def test_cmd_up_recreate_on_absent_bootstraps_up_without_failing_rm():
    # `up --recreate` on a container that isn't there yet must NOT `docker rm -f` a
    # nonexistent container (that errors -> aborts) — it should just run the fresh `up`.
    serv = [{"name": "heavy", "container": "sglang", "port": 1, "health": "/health",
             "model": "qwen35-awq-local", "up": ["docker", "compose", "up", "-d"]}]
    run = _inspect_returning("absent")
    assert serves.cmd_up(serv, [], recreate=True, _run=run) == 0
    assert ["docker", "compose", "up", "-d"] in run.calls          # the `up` ran
    assert not any(c[:3] == ["docker", "rm", "-f"] for c in run.calls)  # no doomed rm -f


def test_cmd_up_recreate_rescues_dead_container():
    # a `dead` container is terminal (not running), so an explicit --recreate may
    # rm -f + re-up it — unlike the hands-off default (test_cmd_up_dead_is_not_auto_created).
    serv = [{"name": "fast", "container": "vllm-gptoss", "port": 1, "health": "/health",
             "model": "gpt-oss-20b", "up": ["bash", "serve-fast.sh"]}]
    run = _inspect_returning("dead")
    assert serves.cmd_up(serv, [], recreate=True, _run=run) == 0
    assert ["docker", "rm", "-f", "vllm-gptoss"] in run.calls
    assert ["bash", "serve-fast.sh"] in run.calls
