"""Tests for `anvil-serving serves` — the model-serve lifecycle verb.

Docker + nvidia-smi + HTTP are injected (the module exposes `_run`/`_open`
seams), so these run with no docker, no GPU, and no network.
"""
import os
import subprocess
import textwrap
import json

import pytest

from anvil_serving import serves
from tests.conftest import enabled_cache_policy as _enabled_cache_policy
from tests.conftest import proc


@pytest.fixture(autouse=True)
def _isolated_host_policy(monkeypatch, tmp_path):
    """Never let a developer's enabled machine policy affect unit timing."""
    monkeypatch.setenv("ANVIL_SERVING_HOME", str(tmp_path / ".anvil-serving"))


def _manifest(tmp_path, body):
    p = tmp_path / "serves.toml"
    p.write_text(textwrap.dedent(body), encoding="utf-8")
    return str(p)


class _JsonResponse:
    def __init__(self, value):
        self.payload = json.dumps(value).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, limit=-1):
        return self.payload[:limit] if limit >= 0 else self.payload


def _inspect_returning(
    state,
    stop_rc=0,
    stop_err="",
    state_after_stop="exited",
    remove_rc=0,
    remove_err="",
):
    """A fake _run: `docker inspect` -> `state` (or `state_after_stop` once a
    successful `docker stop` has run — cmd_down re-checks state to verify the
    stop STUCK), anything else -> proc(stop_rc)."""
    calls = []
    stopped = []

    def run(argv, **k):
        calls.append(argv)
        if isinstance(argv, list) and argv[:3] == ["docker", "ps", "-a"]:
            st = state_after_stop if stopped else state
            if st == "error":
                return proc(1, "", "Cannot connect to the Docker daemon")
            if st == "absent":
                return proc(0)
            return proc(0, json.dumps({"Names": "vllm", "State": st}) + "\n")
        if isinstance(argv, list) and argv[:2] == ["docker", "inspect"]:
            st = state_after_stop if stopped else state
            if st == "absent":
                return proc(1, "", "Error: No such object")
            if st == "error":
                return proc(1, "", "Cannot connect to the Docker daemon")
            return proc(0, st + "\n")
        if isinstance(argv, list) and argv[:2] == ["docker", "stop"] and stop_rc == 0:
            stopped.append(argv)
        if isinstance(argv, list) and argv[:3] == ["docker", "rm", "-f"]:
            return proc(remove_rc, "", remove_err)
        return proc(stop_rc, "", stop_err)

    run.calls = calls
    return run


# ---- manifest parsing -------------------------------------------------------


def _runtime_entry(tmp_path, body):
    return _manifest(tmp_path, "[[serve]]\n" + body)


@pytest.mark.parametrize(
    ("container", "up", "expected"),
    [
        ("sglang", "docker compose -f old.yml up -d sglang", "sglang"),
        ("vllm-old-model", "docker compose -f old.yml up -d vllm", "vllm"),
        ("llamacpp-old-model", "bash serve-llamacpp.sh", "llamacpp"),
        ("custom-container", "custom-launcher --port 30000", "sglang"),
        ("custom-container", "custom-launcher --model vllm", "sglang"),
    ],
)
def test_load_manifest_infers_pre_engine_entries(tmp_path, container, up, expected):
    path = _manifest(tmp_path, f"""
        [[serve]]
        name = "legacy"
        container = "{container}"
        runtime = "docker"
        port = 30000
        model = "legacy-local"
        up = "{up}"
    """)
    (serve,) = serves.load_manifest(path)
    assert serve["engine"] == expected


# ---- engine-aware functional probes ---------------------------------------


# ---- reservation fields (ADR-0017 GPU residency reservations) ---------------


# ---- serve groups: field parse/validation --------------------------------


COMFYUI_MANIFEST = os.path.join(
    os.path.dirname(serves.EXAMPLE_MANIFEST), "serves.comfyui.toml")


def test_shipped_comfyui_manifest_on_demand_tenant():
    # gpu-reservations:T012 — ComfyUI is the dedicated-manifest `on-demand`
    # tenant (docs/VOICE.md isolation rule: NOT in the shared serves.toml).
    serves_list = serves.load_manifest(COMFYUI_MANIFEST)
    by_name = {s["name"]: s for s in serves_list}
    comfyui = by_name["media-worker"]
    assert comfyui["engine"] == "image"
    assert comfyui["gpu_role"] == "media-compute"
    assert comfyui["residency"] == "on-demand"
    assert comfyui["health"] == "/system_stats"
    assert comfyui["port"] == 8188
    # The public 32 GiB reference envelope; private topology supplies the real
    # target and mirrors any co-resident reservations.
    assert comfyui["vram_mib"] == 28672
    # The app-version pin travels in the label so `serves status` shows what runs.
    assert comfyui["model"] == "comfyui-v0.35.2-ltx25"
    # Own compose project, own compose file — never the shared docker-compose.yml.
    assert "docker-compose.comfyui.yml" in " ".join(comfyui["up"])
    assert set(by_name) == {"media-worker"}


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


def test_manifest_dotenv_shell_value_wins_without_printing_secret(tmp_path, monkeypatch, capsys):
    path = _manifest(tmp_path, """
        [[serve]]
        name = "secure"
        container = "vllm-secure"
        runtime = "docker"
        port = 30000
        model = "secure-local"
    """)
    (tmp_path / ".env").write_text(
        "DEPLOYMENT_SECRET=manifest-secret\n", encoding="utf-8"
    )
    monkeypatch.setenv("DEPLOYMENT_SECRET", "shell-secret")
    (serve,) = serves.load_manifest(path)
    assert serves._serve_env(serve)["DEPLOYMENT_SECRET"] == "shell-secret"
    captured = capsys.readouterr()
    assert "manifest-secret" not in captured.out + captured.err
    assert "shell-secret" not in captured.out + captured.err


def test_cmd_up_loads_home_dotenv_as_fallback(tmp_path, monkeypatch):
    home = tmp_path / "home"
    manifest_dir = tmp_path / "manifest"
    home.mkdir()
    manifest_dir.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("ANVIL_SERVING_NO_HOME_ENV", "0")
    monkeypatch.delenv("HF_TOKEN", raising=False)
    (home / ".env").write_text("HF_TOKEN=home-token\n", encoding="utf-8")
    path = _manifest(manifest_dir, """
        [[serve]]
        name = "gepard"
        container = "gepard-fast-tts"
        runtime = "docker"
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
    monkeypatch.setenv("ANVIL_SERVING_NO_HOME_ENV", "0")
    monkeypatch.setenv("ANVIL_SERVING_HOME", str(config_home))
    monkeypatch.delenv("HF_TOKEN", raising=False)
    (home / ".env").write_text("HF_TOKEN=home-token\n", encoding="utf-8")
    (config_home / ".env").write_text("HF_TOKEN=config-token\n", encoding="utf-8")
    path = _manifest(manifest_dir, """
        [[serve]]
        name = "gepard"
        container = "gepard-fast-tts"
        runtime = "docker"
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


# ---- selection --------------------------------------------------------------


# ---- docker_state -----------------------------------------------------------


# ---- down -------------------------------------------------------------------


def test_cmd_down_force_removes_after_stop_timeout():
    serv = [{"name": "h", "container": "sglang", "port": 1, "health": "/health"}]
    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs.get("timeout")))
        if argv[:2] == ["docker", "inspect"]:
            return proc(0, "running\n")
        if argv[:2] == ["docker", "stop"]:
            raise subprocess.TimeoutExpired(argv, kwargs["timeout"])
        return proc(0)

    assert serves.cmd_down(serv, [], _run=run) == 0
    assert (["docker", "rm", "-f", "sglang"], serves.DOCKER_STOP_COMMAND_TIMEOUT_SECONDS) in calls


def test_cmd_down_stop_timeout_keeps_diagnostic_container_when_requested():
    serv = [{"name": "h", "container": "sglang", "port": 1, "health": "/health"}]
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        if argv[:2] == ["docker", "inspect"]:
            return proc(0, "running\n")
        raise subprocess.TimeoutExpired(argv, kwargs["timeout"])

    assert serves.cmd_down(serv, [], keep_container=True, _run=run) == 1
    assert ["docker", "rm", "-f", "sglang"] not in calls


def test_cmd_down_absent_declared_native_offload_dry_run_does_not_mutate(
    monkeypatch, capsys,
):
    serv = [{
        "name": "h", "container": "deepseek", "port": 1, "health": "/health",
        "native_kv_offload": True,
    }]
    run = _inspect_returning("absent")
    monkeypatch.setattr(
        serves.host_ops, "prepare_native_kv_offload_shared_memory",
        lambda **_kwargs: pytest.fail("dry-run attempted shared-memory cleanup"),
    )

    assert serves.cmd_down(serv, [], dry_run=True, _run=run) == 0
    assert "would inspect/reclaim" in capsys.readouterr().out


# ---- up ---------------------------------------------------------------------


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


def test_cmd_up_refuses_foreign_compose_owner_without_explicit_recreate(capsys):
    serve = [{
        "name": "heavy",
        "stack": "auxiliary",
        "container": "vllm-heavy",
        "port": 1,
        "health": "/health",
        "up": ["docker", "compose", "-f", "x.yml", "up", "-d", "heavy"],
    }]
    calls = []

    def run(argv, **_kwargs):
        calls.append(argv)
        if ".State.Status" in " ".join(argv):
            return proc(0, "exited\n")
        if "com.docker.compose.project" in " ".join(argv):
            return proc(0, "old-directory-name\n")
        return proc(0)

    assert serves.cmd_up(serve, ["heavy"], _run=run) == 1
    assert not any(argv[:2] == ["docker", "compose"] for argv in calls)
    assert "--recreate" in capsys.readouterr().out


def test_command_failure_text_is_bounded_to_actionable_tail_and_redacted():
    result = proc(
        1,
        "prefix:" + ("x" * 9000) + "\nAPI_KEY=visible-secret\nactionable build failure",
    )
    rendered = serves._command_failure_text(
        result,
        {"API_KEY": "visible-secret"},
    )
    assert rendered.startswith("...[truncated to final 8192 characters]...")
    assert len(rendered) < 8300
    assert "actionable build failure" in rendered
    assert "visible-secret" not in rendered
    assert "<redacted>" in rendered


# ---- post-start storage write guard -----------------------------------------

def _storage_run(uid="1000", gid="1000", mounts=None, denied=(),
                 denied_after_repair=(), volume_users=(), chown_rc=0,
                 restart_rc=0, id_rc=0):
    """A fake _run for the storage guard: `docker inspect` -> running,
    `docker exec ... id` -> uid/gid, `docker inspect -f {{json .Mounts}}` ->
    mounts, touch probes -> denied (then denied_after_repair once a chown
    ran), `docker ps -a --filter volume=` -> volume_users."""
    calls = []
    repaired = []
    if mounts is None:
        mounts = [{"Type": "volume", "Name": "data", "RW": True,
                   "Destination": "/app/user"}]

    def run(argv, **k):
        calls.append(argv)
        if argv[:3] == ["docker", "ps", "-a"]:
            if any(str(a).startswith("volume=") for a in argv):
                return proc(0, "\n".join(volume_users) + "\n")
            return proc(0, json.dumps({"Names": "vllm", "State": "running"}) + "\n")
        if argv[:2] == ["docker", "inspect"]:
            if "{{json .Mounts}}" in argv:
                return proc(0, json.dumps(mounts) + "\n")
            return proc(0, "running\n")
        if argv[:2] == ["docker", "exec"] and "id -u; id -g" in argv:
            return proc(id_rc, "%s\n%s\n" % (uid, gid))
        if argv[:2] == ["docker", "exec"] and argv[2] == "--user":
            repaired.append(argv)
            return proc(chown_rc)
        if argv[:2] == ["docker", "exec"] and any("touch" in str(a) for a in argv):
            active = denied_after_repair if repaired else denied
            probed = argv[-1]
            return proc(1 if any(d in probed for d in active) else 0)
        if argv[:2] == ["docker", "restart"]:
            return proc(restart_rc)
        return proc(0)

    run.calls = calls
    return run


def _storage_serve(**extra):
    return [{"name": "t", "container": "vllm", "port": 1, "health": "/health",
             "up": ["docker", "compose", "up", "-d", "t"], **extra}]


def test_cmd_up_storage_repair_reverify_failure_fails_closed(capsys):
    run = _storage_run(denied=("/app/user",), denied_after_repair=("/app/user",))
    assert serves.cmd_up(_storage_serve(), [], _run=run) == 1
    assert "still cannot write" in capsys.readouterr().out


def test_cmd_up_storage_declared_shared_volume_is_never_auto_chowned(capsys):
    # shared_volumes is the deployment's explicit sharing declaration; the
    # guard reports and prints the manual command but must not re-own it.
    run = _storage_run(denied=("/app/user",), volume_users=("other-tenant",))
    serv = _storage_serve(shared_volumes=["data"])
    assert serves.cmd_up(serv, [], _run=run) == 1
    out = capsys.readouterr().out
    assert "declared shared_volumes" in out
    assert "docker exec --user 0 vllm chown -R 1000:1000 /app/user" in out
    assert not any(a[:3] == ["docker", "exec", "--user"] for a in run.calls)


def test_load_manifest_normalizes_shared_volumes(tmp_path):
    p = _manifest(tmp_path, """
        [[serve]]
        name = "t"
        container = "c"
        runtime = "docker"
        port = 1
        model = "m"
        shared_volumes = [" data ", "data", "models"]
    """)
    serve = serves.load_manifest(p)[0]
    assert serve["shared_volumes"] == ["data", "models"]


# ---- guarded promotion ------------------------------------------------------

def _promotion_manifest(tmp_path):
    (tmp_path / "fixture.png").write_bytes(b"bounded promotion image fixture")
    for filename, model in (
        ("router-promoted.toml", "new-heavy"),
        ("router-rollback.toml", "old-heavy"),
    ):
        (tmp_path / filename).write_text(textwrap.dedent(f"""
            [router]
            [[router.tiers]]
            id = "primary-local"
            base_url = "http://127.0.0.1:30002/v1"
            model = "{model}"
            dialect = "openai"
            context_limit = 4096
            privacy = "local"
            tool_support = true
            auth_env = "ANVIL_PRIMARY_LOCAL_KEY"
            health_path = "/health"
            model_identity = true
            [router.model_routes]
            llm.primary = "primary-local"
        """), encoding="utf-8")
    return _manifest(tmp_path, """
        [[serve]]
        name = "candidate"
        container = "candidate-c"
        runtime = "docker"
        port = 39031
        model = "candidate-model"
        engine = "vllm"

        [[serve]]
        name = "primary"
        container = "heavy-c"
        runtime = "docker"
        port = 30002
        model = "new-heavy"
        engine = "vllm"
        up = "docker compose -f {dir}/compose.yml up -d heavy"

        [[serve]]
        name = "old-heavy"
        container = "old-heavy-c"
        runtime = "docker"
        port = 30002
        model = "old-heavy"
        engine = "vllm"
        up = "docker compose -f {dir}/compose.yml --profile rollback up -d old-heavy"

        [[promotion]]
        name = "heavy-v2"
        candidate = "candidate"
        target = "primary"
        rollback = "old-heavy"
        affected_tiers = ["primary-local"]
        router_config = "{dir}/router-promoted.toml"
        rollback_router_config = "{dir}/router-rollback.toml"
        needle_ctx = 131072
        tool_batch = 20

        [[promotion.gate]]
        name = "functional"
        checks = "smoke,json,needle,tools"
        image_path = "{dir}/fixture.png"
        image_expect = ["ANVIL SERVING", "STATUS READY"]
        ocr_expect = ["ANVIL SERVING"]
        thinking_mode = "disabled"
        visible_answer_tokens = 256
        reasoning_headroom_tokens = 0
        reasoning_evidence = "forbidden"

        [[promotion.gate]]
        name = "quality"
        checks = "smoke,json"
        thinking_mode = "enabled"
        visible_answer_tokens = 256
        reasoning_headroom_tokens = 4096
        reasoning_evidence = "required"

        [[promotion.rollback_gate]]
        name = "rollback"
        thinking_mode = "unsupported"
        visible_answer_tokens = 256
        reasoning_headroom_tokens = 4096
        reasoning_evidence = "required"
    """)


def test_load_promotions_resolves_direct_router_configs(tmp_path):
    path = _promotion_manifest(tmp_path)
    (tmp_path / "fixture.png").write_bytes(b"not decoded during manifest load")
    (plan,) = serves.load_promotions(path)
    assert plan["name"] == "heavy-v2"
    assert plan["target"] == "primary"
    assert plan["rollback"] == "old-heavy"
    assert plan["affected_tiers"] == ["primary-local"]
    assert plan["router_config"] == str(tmp_path / "router-promoted.toml")
    assert plan["rollback_router_config"] == str(tmp_path / "router-rollback.toml")
    assert [gate["name"] for gate in plan["gate"]] == ["functional", "quality"]
    assert plan["gate"][0]["image_path"] == str(tmp_path / "fixture.png")
    assert plan["gate"][0]["image_expect"] == ["ANVIL SERVING", "STATUS READY"]
    assert plan["gate"][0]["ocr_expect"] == ["ANVIL SERVING"]
    assert plan["gate"][1]["reasoning_headroom_tokens"] == 4096
    assert plan["rollback_gate"][0]["thinking_mode"] == "unsupported"


def test_load_promotions_rejects_missing_media_before_transition(tmp_path):
    path = _promotion_manifest(tmp_path)
    (tmp_path / "fixture.png").unlink()

    with pytest.raises(
        ValueError,
        match="promotion gate 'functional' has invalid image_path: "
        "image path is not a regular file",
    ):
        serves.load_promotions(path)


@pytest.mark.parametrize(
    ("checks", "remove_line", "expected"),
    [
        ("image", 'image_path = "{dir}/fixture.png"\n', "has no image_path"),
        ("image", 'image_expect = ["ANVIL SERVING", "STATUS READY"]\n',
         "has no image_expect"),
        ("ocr", 'ocr_expect = ["ANVIL SERVING"]\n', "has no ocr_expect"),
    ],
)
def test_load_promotions_rejects_incomplete_image_gate_before_transition(
    tmp_path, checks, remove_line, expected
):
    path = _promotion_manifest(tmp_path)
    manifest = tmp_path / "serves.toml"
    text = manifest.read_text(encoding="utf-8")
    text = text.replace(
        'checks = "smoke,json,needle,tools"', f'checks = "{checks}"', 1
    ).replace(remove_line, "", 1)
    manifest.write_text(text, encoding="utf-8")

    with pytest.raises(ValueError, match=expected):
        serves.load_promotions(path)


def test_load_promotions_rejects_incomplete_video_gate_before_transition(tmp_path):
    path = _promotion_manifest(tmp_path)
    manifest = tmp_path / "serves.toml"
    text = manifest.read_text(encoding="utf-8").replace(
        'checks = "smoke,json,needle,tools"', 'checks = "video"', 1
    )
    manifest.write_text(text, encoding="utf-8")

    with pytest.raises(ValueError, match="has no video_path"):
        serves.load_promotions(path)


def test_load_promotions_rejects_unknown_check_before_transition(tmp_path):
    path = _promotion_manifest(tmp_path)
    manifest = tmp_path / "serves.toml"
    text = manifest.read_text(encoding="utf-8").replace(
        'checks = "smoke,json,needle,tools"', 'checks = "smoke,bogus"', 1
    )
    manifest.write_text(text, encoding="utf-8")

    with pytest.raises(ValueError, match=r"invalid checks.*unknown=\['bogus'\]"):
        serves.load_promotions(path)


@pytest.mark.parametrize(
    ("field", "old", "new", "expected"),
    [
        ("needle_ctx", "131072", "0", "integer from 1 through 1000000"),
        ("tool_batch", "20", "129", "integer from 1 through 128"),
    ],
)
def test_load_promotions_rejects_invalid_preflight_plan_ranges(
    tmp_path, field, old, new, expected
):
    path = _promotion_manifest(tmp_path)
    manifest = tmp_path / "serves.toml"
    text = manifest.read_text(encoding="utf-8").replace(
        f"{field} = {old}", f"{field} = {new}", 1
    )
    manifest.write_text(text, encoding="utf-8")

    with pytest.raises(ValueError, match=expected):
        serves.load_promotions(path)


def test_load_promotions_rejects_invalid_preflight_token_budget(tmp_path):
    path = _promotion_manifest(tmp_path)
    manifest = tmp_path / "serves.toml"
    text = manifest.read_text(encoding="utf-8").replace(
        "reasoning_headroom_tokens = 0", "reasoning_headroom_tokens = 65536", 1
    )
    manifest.write_text(text, encoding="utf-8")

    with pytest.raises(ValueError, match="combined completion allocation"):
        serves.load_promotions(path)


def test_load_promotions_rejects_conflicting_reasoning_controls(tmp_path):
    path = _promotion_manifest(tmp_path)
    manifest = tmp_path / "serves.toml"
    text = manifest.read_text(encoding="utf-8").replace(
        'thinking_mode = "disabled"',
        'thinking_mode = "disabled"\nreasoning_effort = "high"',
        1,
    )
    manifest.write_text(text, encoding="utf-8")

    with pytest.raises(ValueError, match="cannot combine reasoning_effort"):
        serves.load_promotions(path)


def test_load_promotions_rejects_unwritable_preflight_output(tmp_path):
    path = _promotion_manifest(tmp_path)
    manifest = tmp_path / "serves.toml"
    text = manifest.read_text(encoding="utf-8").replace(
        'checks = "smoke,json,needle,tools"',
        'checks = "smoke,json,needle,tools"\njson_out = "{dir}/missing/evidence.json"',
        1,
    )
    manifest.write_text(text, encoding="utf-8")

    with pytest.raises(ValueError, match="invalid json_out.*directory does not exist"):
        serves.load_promotions(path)


def test_load_promotions_rejects_nonpositive_poll_interval(tmp_path):
    path = _promotion_manifest(tmp_path)
    text = (tmp_path / "serves.toml").read_text(encoding="utf-8")
    (tmp_path / "serves.toml").write_text(
        text.replace("tool_batch = 20", "tool_batch = 20\npoll_interval = 0"),
        encoding="utf-8",
    )
    import pytest
    with pytest.raises(ValueError, match="poll_interval must be a finite positive"):
        serves.load_promotions(path)


def test_load_promotions_rejects_removed_profile_fields(tmp_path):
    path = _promotion_manifest(tmp_path)
    manifest = tmp_path / "serves.toml"
    manifest.write_text(
        manifest.read_text(encoding="utf-8").replace(
            'router_config = "{dir}/router-promoted.toml"',
            'router_config = "{dir}/router-promoted.toml"\n'
            'router_profile = "{dir}/legacy-profile.json"',
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="removed profile field"):
        serves.load_promotions(path)


def test_load_promotions_rejects_missing_rollback_router_config(tmp_path):
    # A rollback profile that does not exist must fail at manifest load, not at
    # promotion time — see .tickets/2026-08-08-promotion-router-profiles-not-
    # existence-validated.md. Every read-only surface loads the manifest, so
    # this is where an unusable rollback becomes visible.
    path = _promotion_manifest(tmp_path)
    (tmp_path / "router-rollback.toml").unlink()

    with pytest.raises(ValueError, match="rollback_router_config does not exist"):
        serves.load_promotions(path)


def test_install_router_config_validates_writes_atomically_and_restarts(tmp_path):
    config = tmp_path / "router.toml"
    config.write_text("[router]\n", encoding="utf-8")
    from tests.test_router_manage import _native_gate_output
    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs.get("input")))
        native = _native_gate_output(argv)
        if native is not None:
            return native
        if argv[:4] == ["docker", "inspect", "-f", "{{.Config.Image}}"]:
            return proc(0, "anvil-serving:test\n")
        if argv[:3] == ["docker", "inspect", "-f"] and '"mounts"' in argv[3]:
            return proc(0, '{"cmd":["--config","/cfg/config.toml"],"mounts":[{"Type":"volume","Name":"anvil-router-cfg","Destination":"/cfg"}]}')
        return proc()

    assert serves._install_router_config(str(config), _run=run) == 0
    # Real gate consumes synthetic custody before the image validator/write.
    cutover = next(i for i, (argv, _) in enumerate(calls) if "_local_router_cutover" in argv[-1])
    validator = next(i for i, (argv, _) in enumerate(calls) if argv[:3] == ["docker", "exec", "-i"])
    assert cutover < validator
    assert any(
        call[0][:3] == ["docker", "run", "--rm"]
        and "config.toml.new" in call[0][-1]
        for call in calls
    )
    write_inputs = [
        payload
        for argv, payload in calls
        if argv[:3] == ["docker", "run", "--rm"]
        and "config.toml.new" in argv[-1]
    ]
    assert write_inputs == [b"[router]\n"]
    assert calls[-1][0] == ["docker", "restart", "anvil-router"]


def test_cmd_promote_dry_run_prints_complete_transaction(tmp_path, capsys):
    path = _promotion_manifest(tmp_path)
    managed = serves.load_manifest(path)
    plans = serves.load_promotions(path)
    run = _inspect_returning("exited")
    assert serves.cmd_promote(
        managed, plans, "heavy-v2", path, dry_run=True, _run=run
    ) == 0
    out = capsys.readouterr().out
    assert "stop candidate, old-heavy" in out
    assert "start primary" in out
    assert "eval preflight --tier primary" in out
    assert "gate functional" in out
    assert "gate quality" in out
    assert "--thinking-mode enabled" in out
    assert "--reasoning-headroom-tokens 4096" in out
    assert "planned apply: atomically install" in out
    assert "deferred until live execution" in out
    assert "static admission passed" in out
    assert "runtime stop/start" in out
    assert "were not executed" in out


def test_cmd_promote_dry_run_propagates_target_admission_failure(
    tmp_path, monkeypatch, capsys
):
    path = _promotion_manifest(tmp_path)
    (tmp_path / "fixture.png").write_bytes(b"bounded image fixture")
    managed = serves.load_manifest(path)
    plans = serves.load_promotions(path)
    monkeypatch.setattr(serves, "cmd_down", lambda *args, **kwargs: 0)
    monkeypatch.setattr(serves, "cmd_up", lambda *args, **kwargs: 7)

    assert serves.cmd_promote(
        managed, plans, "heavy-v2", path, dry_run=True
    ) == 7
    out = capsys.readouterr().out
    assert "atomically install" not in out
    assert "verify: router gateway" not in out


def test_cmd_promote_dry_run_allows_exclusive_to_exclusive_swap(tmp_path, capsys):
    path = _promotion_manifest(tmp_path)
    manifest = tmp_path / "serves.toml"
    text = manifest.read_text(encoding="utf-8")
    role_rows = textwrap.dedent("""
        [[gpu_roles]]
        id = "dark-compute-a"
        vram_mib = 100
        reserve_mib = 0

        [[gpu_roles]]
        id = "dark-compute-b"
        vram_mib = 100
        reserve_mib = 0

    """)
    exclusive = textwrap.dedent("""
        gpu_roles = ["dark-compute-a", "dark-compute-b"]
        vram_mib = 100
        residency = "on-demand"
        operating_mode = "dual-gpu-exclusive"
        tensor_parallel_size = 2
    """)
    for model in ("new-heavy", "old-heavy"):
        anchor = f'model = "{model}"\nengine = "vllm"'
        text = text.replace(anchor, anchor + "\n" + exclusive, 1)
    manifest.write_text(role_rows + text, encoding="utf-8")

    managed = serves.load_manifest(path)
    plans = serves.load_promotions(path)
    assert serves.cmd_promote(
        managed,
        plans,
        "heavy-v2",
        path,
        dry_run=True,
        _run=_inspect_returning("running"),
    ) == 0
    out = capsys.readouterr().out
    assert "exclusive admission denied" not in out
    assert "start primary" in out


def test_cmd_promote_failure_runs_complete_rollback(tmp_path, monkeypatch):
    path = _promotion_manifest(tmp_path)
    managed = serves.load_manifest(path)
    plans = serves.load_promotions(path)
    calls = []

    def transition(_serves, _plan, _manifest, **kwargs):
        calls.append(kwargs.get("rollback", False))
        return 0 if kwargs.get("rollback") else 1

    monkeypatch.setattr(serves, "_promotion_transition", transition)
    assert serves.cmd_promote(managed, plans, "heavy-v2", path) == 1
    assert calls == [False, True]


def test_pre_mutation_admission_uncertainty_does_not_trigger_container_rollback(
    tmp_path, monkeypatch
):
    path = _promotion_manifest(tmp_path)
    calls = []

    def transition(*args, **kwargs):
        calls.append(kwargs.get("rollback", False))
        return 3

    monkeypatch.setattr(serves, "_promotion_transition", transition)
    assert serves.cmd_promote(
        serves.load_manifest(path), serves.load_promotions(path), "heavy-v2", path
    ) == 1
    assert calls == [False]


@pytest.mark.parametrize(("readmit_rc", "expected"), [(0, 2), (1, 3)])
def test_ambiguous_quiesce_failure_compensates_current_tier(
    tmp_path, monkeypatch, readmit_rc, expected
):
    path = _promotion_manifest(tmp_path)
    managed = serves.load_manifest(path)
    (plan,) = serves.load_promotions(path)
    actions = []

    def transition(_plan, action, tier_id, **kwargs):
        actions.append((action, tier_id))
        return 1 if action == "quiesce" else readmit_rc

    monkeypatch.setattr(serves, "_promotion_transition_cli", transition)
    rc = serves._promotion_transition(
        managed, plan, path, require_candidate=False
    )
    assert rc == expected
    assert actions == [
        ("quiesce", "primary-local"),
        ("readmit", "primary-local"),
    ]


def test_cmd_promote_runtime_exception_still_runs_rollback(tmp_path, monkeypatch):
    path = _promotion_manifest(tmp_path)
    managed = serves.load_manifest(path)
    plans = serves.load_promotions(path)
    calls = []

    def transition(_serves, _plan, _manifest, **kwargs):
        rollback = kwargs.get("rollback", False)
        calls.append(rollback)
        if not rollback:
            raise TypeError("post-mutation failure")
        return 0

    monkeypatch.setattr(serves, "_promotion_transition", transition)
    assert serves.cmd_promote(managed, plans, "heavy-v2", path) == 1
    assert calls == [False, True]


def test_explicit_rollback_failure_restores_promoted_state(tmp_path, monkeypatch):
    path = _promotion_manifest(tmp_path)
    managed = serves.load_manifest(path)
    plans = serves.load_promotions(path)
    calls = []

    def transition(_serves, _plan, _manifest, **kwargs):
        calls.append((kwargs.get("rollback", False), kwargs.get("require_candidate", True)))
        return 1 if kwargs.get("rollback") else 0

    monkeypatch.setattr(serves, "_promotion_transition", transition)
    assert serves.cmd_promote(managed, plans, "heavy-v2", path, rollback=True) == 1
    assert calls == [(True, True), (False, False)]


def test_cmd_promote_refuses_unhealthy_candidate_without_mutating(tmp_path):
    path = _promotion_manifest(tmp_path)
    managed = serves.load_manifest(path)
    plans = serves.load_promotions(path)
    run = _inspect_returning("exited")
    assert serves.cmd_promote(
        managed, plans, "heavy-v2", path, _run=run,
        _open=lambda *args, **kwargs: (_ for _ in ()).throw(OSError("down")),
    ) == 1
    assert not any(call[:2] == ["docker", "stop"] for call in run.calls)


def test_safe_promotion_orders_quiesce_drain_config_restart_before_readiness(
    tmp_path, monkeypatch
):
    path = _promotion_manifest(tmp_path)
    (tmp_path / "fixture.png").write_bytes(b"not decoded by mocked preflight")
    managed = serves.load_manifest(path)
    # An unrelated resident Fast serve is present but outside the managed pair.
    managed.append({
        "name": "fast", "container": "fast-c", "port": 30003,
        "model": "fast-model", "served_name": "fast-model",
        "health": "/health", "up": ["docker", "start", "fast-c"],
    })
    plans = serves.load_promotions(path)
    run = _inspect_returning("running")
    applied = []
    monkeypatch.setattr(
        serves, "_install_router_config",
        lambda config, **_kwargs: applied.append(config) or 0,
    )

    class Response:
        status = 200

        def __init__(self, body=b""):
            self.body = body

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def getcode(self):
            return self.status

        def read(self, amount=-1):
            return self.body if amount < 0 else self.body[:amount]

    def open_(request, timeout):
        if hasattr(request, "full_url") and request.full_url.endswith("/v1/models"):
            return Response(b'{"data":[{"id":"new-heavy"}]}')
        return Response()

    assert serves.cmd_promote(
        managed, plans, "heavy-v2", path, resume=True,
        _run=run, _open=open_, _sleep=lambda _: None,
    ) == 0

    calls = run.calls
    quiesce = next(i for i, call in enumerate(calls) if "quiesce" in call)
    drain = next(i for i, call in enumerate(calls) if "drain" in call)
    first_stop = next(i for i, call in enumerate(calls) if call[:2] == ["docker", "stop"])
    post_restart = next(i for i, call in enumerate(calls) if "readmit" in call)
    assert quiesce < drain < first_stop < post_restart
    assert applied == [str(tmp_path / "router-promoted.toml")]
    assert not any("fast-c" in call for call in calls)
    preflight = next(call for call in calls if "preflight" in call)
    assert preflight[preflight.index("--image-path") + 1] == str(tmp_path / "fixture.png")
    assert [
        preflight[index + 1]
        for index, token in enumerate(preflight)
        if token == "--image-expect"
    ] == ["ANVIL SERVING", "STATUS READY"]
    assert [
        preflight[index + 1]
        for index, token in enumerate(preflight)
        if token == "--ocr-expect"
    ] == ["ANVIL SERVING"]


def test_promotion_retries_transient_post_restart_readiness(tmp_path, monkeypatch):
    path = _promotion_manifest(tmp_path)
    managed = serves.load_manifest(path)
    (plan,) = serves.load_promotions(path)
    transitions = []
    sleeps = []

    def transition(_plan, action, tier_id, **_kwargs):
        transitions.append((action, tier_id))
        if action == "readmit":
            return int(sum(a == action for a, _ in transitions) == 1)
        return 0

    monkeypatch.setattr(serves, "_promotion_transition_cli", transition)
    monkeypatch.setattr(serves, "_promotion_cli", lambda *a, **k: 0)
    monkeypatch.setattr(serves, "_install_router_config", lambda *a, **k: 0)
    monkeypatch.setattr(serves, "cmd_down", lambda *a, **k: 0)
    monkeypatch.setattr(serves, "docker_state", lambda *a, **k: "running")
    monkeypatch.setattr(serves, "_health", lambda *a, **k: 200)
    monkeypatch.setattr(serves, "_serve_identity_ready", lambda *a, **k: True)
    # The deployed gateway may protect its health endpoint; authentication
    # failure still proves reachability before guarded readmit verifies the tier.
    monkeypatch.setattr(serves, "_gateway_status", lambda *a, **k: 401)

    assert serves._promotion_transition(
        managed,
        plan,
        path,
        resume=True,
        require_candidate=False,
        _sleep=sleeps.append,
    ) == 0
    assert [action for action, _ in transitions].count("readmit") == 2
    assert sleeps == [plan["poll_interval"]]


# ---- status: reservation ledger surface (gpu-reservations:T004) ---------------

# The reference multi-tenant card (mirrors tests/test_reservations.py):
# 32 GiB 5090, 2 GiB display reserve -> 30720 MiB budget.
STATUS_LEDGER_MANIFEST = """
    [[gpu_roles]]
    id = "dark-fast"
    vram_mib = 32768
    reserve_mib = 2048

    [[serve]]
    name = "fast"
    container = "vllm-fast"
    runtime = "docker"
    port = 30003
    model = "auxiliary-local"
    engine = "vllm"
    gpu_role = "dark-fast"
    vram_mib = 20480
    residency = "on-demand"
    groups = ["production"]

    [[serve]]
    name = "stt"
    container = "anvil-voice-stt"
    runtime = "docker"
    port = 30010
    model = "tdt_ctc-110m"
    engine = "audio"
    gpu_role = "dark-fast"
    vram_mib = 4096
    residency = "resident"
    groups = ["production"]

    [[serve]]
    name = "plain"
    container = "vllm-plain"
    runtime = "docker"
    port = 30030
    model = "plain-local"
    engine = "vllm"
    groups = ["production"]
"""


def _status_states_run(states):
    """Fake _run: `docker inspect <c>` -> states[c] ('absent' modeled as the
    docker no-such-object failure); nvidia-smi and anything else -> empty ok."""
    calls = []

    def run(argv, **k):
        calls.append(argv)
        if isinstance(argv, list) and argv[:2] == ["docker", "inspect"]:
            state = states.get(argv[-1], "absent")
            if state == "absent":
                return proc(1, "", "Error: No such object")
            return proc(0, state + "\n")
        return proc(0)

    run.calls = calls
    return run


def _open_down(url, timeout=3):
    raise OSError("health endpoint down")


def test_status_human_and_structured_outputs_agree_on_exclusive_mode(
    tmp_path, capsys,
):
    loaded = serves.load_manifest(_manifest(tmp_path, """
        [[gpu_roles]]
        id = "dark-compute-a"
        vram_mib = 97887

        [[gpu_roles]]
        id = "dark-compute-b"
        vram_mib = 97887

        [[serve]]
        name = "split-a"
        container = "split-a"
        runtime = "docker"
        port = 30001
        model = "split-a-local"
        engine = "vllm"
        gpu_role = "dark-compute-a"
        vram_mib = 80000

        [[serve]]
        name = "split-b"
        container = "split-b"
        runtime = "docker"
        port = 30002
        model = "split-b-local"
        engine = "vllm"
        gpu_role = "dark-compute-b"
        vram_mib = 80000

        [[serve]]
        name = "tp2"
        container = "tp2"
        runtime = "docker"
        port = 30003
        model = "candidate-local"
        engine = "vllm"
        gpu_roles = ["dark-compute-a", "dark-compute-b"]
        vram_mib = 90000
        operating_mode = "dual-gpu-exclusive"
        tensor_parallel_size = 2
    """))
    run = _status_states_run({"split-a": "absent", "split-b": "absent", "tp2": "running"})
    data = serves.status_summary(loaded, _run=run, _open=_open_down)
    mode = data["operating_mode"]
    assert mode == {
        "mode": "dual-gpu-exclusive",
        "exclusive_owner": "tp2",
        "gpu_roles": ["dark-compute-a", "dark-compute-b"],
        "gpu_ownership": [
            {"gpu_role": "dark-compute-a", "owners": ["tp2"]},
            {"gpu_role": "dark-compute-b", "owners": ["tp2"]},
        ],
        "tensor_parallel_size": 2,
        "blocked_workloads": ["split-a", "split-b"],
        "unresolved": [],
    }
    assert serves.cmd_status(loaded, _run=run, _open=_open_down) == 0
    out = capsys.readouterr().out
    assert "Operating mode: dual-gpu-exclusive" in out
    assert "exclusive owner: tp2 (TP=2)" in out
    assert "gpu roles: dark-compute-a, dark-compute-b" in out
    assert "blocked workloads: split-a, split-b" in out


# ---- lifecycle-aware automatic WSL cache reclaim ---------------------------

def _lifecycle_manifest(tmp_path):
    return _manifest(tmp_path, """
        [[serve]]
        name = "heavy"
        container = "vllm-heavy"
        runtime = "docker"
        port = 30002
        model = "primary-local"
        engine = "vllm"
        health = "/health"
        up = "docker compose -f {dir}/compose.yml up -d heavy"
    """)


def test_readiness_timeout_skips_reclaim_but_preserves_success(tmp_path, monkeypatch):
    manifest = _lifecycle_manifest(tmp_path)
    policy = _enabled_cache_policy()
    seen = []
    monkeypatch.setattr(serves.host_ops, "load_cache_reclaim_policy", lambda: policy)
    monkeypatch.setattr(
        serves.host_ops, "capture_cache_before", lambda _policy: {"cached_gb": 10.0}
    )
    monkeypatch.setattr(serves, "ensure_router_healthy", lambda **_kwargs: 0)
    monkeypatch.setattr(serves, "cmd_up", lambda *_args, **_kwargs: 0)
    monkeypatch.setattr(serves, "_await_cache_reclaim_targets", lambda _targets: False)
    monkeypatch.setattr(
        serves.host_ops, "automatic_cache_reclaim",
        lambda _policy, _before, **kwargs: seen.append(kwargs) or {
            "outcome": "readiness-timeout"
        },
    )
    monkeypatch.setattr(serves.host_ops, "render_cache_reclaim_result", lambda _result: None)
    assert serves.main(["up", "heavy", "--manifest", manifest]) == 0
    assert seen == [{"operation": "serves up", "readiness": False}]


@pytest.mark.parametrize(("argv", "operation"), [
    (["promote", "heavy-v2", "--skip-preflight-checks"], "serves promote"),
    (
        ["promote", "heavy-v2", "--rollback", "--skip-preflight-checks"],
        "serves promote --rollback",
    ),
    (["switch", "heavy", "org/model"], "serves switch"),
])
def test_switch_and_promote_reclaim_once_without_a_second_readiness_wait(
        tmp_path, monkeypatch, argv, operation):
    # `--skip-preflight-checks` keeps this focused on the cache-reclaim
    # readiness-wait behavior: the fake `load_promotions` below returns a
    # promotion plan too sparse for the real preflight gate's rollback-check
    # to validate, and that is not what this test is exercising.
    manifest = _lifecycle_manifest(tmp_path)
    policy = _enabled_cache_policy()
    events = []
    monkeypatch.setattr(serves.host_ops, "load_cache_reclaim_policy", lambda: policy)
    monkeypatch.setattr(
        serves.host_ops, "capture_cache_before", lambda _policy: {"cached_gb": 10.0}
    )
    monkeypatch.setattr(serves, "load_promotions", lambda _path: [{"name": "heavy-v2"}])
    monkeypatch.setattr(
        serves.serve_recipes, "load_registry",
        lambda _path: {"schema": "x", "recipe": []},
    )
    if argv[0] == "promote":
        monkeypatch.setattr(serves, "cmd_promote", lambda *_args, **_kwargs: 0)
    else:
        monkeypatch.setattr(serves, "cmd_switch", lambda *_args, **_kwargs: 0)
    monkeypatch.setattr(
        serves, "_await_cache_reclaim_targets",
        lambda _targets: pytest.fail("switch/promote repeated their existing readiness gates"),
    )
    monkeypatch.setattr(
        serves.host_ops, "automatic_cache_reclaim",
        lambda _policy, _before, **kwargs: events.append(kwargs) or {
            "outcome": "no-operation-growth"
        },
    )
    monkeypatch.setattr(serves.host_ops, "render_cache_reclaim_result", lambda _result: None)
    assert serves.main([*argv, "--manifest", manifest]) == 0
    assert events == [{"operation": operation, "readiness": True}]


def test_failed_and_dry_run_serve_operations_never_execute_reclaim(
        tmp_path, monkeypatch, capsys):
    manifest = _lifecycle_manifest(tmp_path)
    policy = _enabled_cache_policy()
    reclaimed = []
    monkeypatch.setattr(serves.host_ops, "load_cache_reclaim_policy", lambda: policy)
    monkeypatch.setattr(serves.host_ops, "capture_cache_before", lambda _policy: {})
    monkeypatch.setattr(serves, "ensure_router_healthy", lambda **_kwargs: 0)
    monkeypatch.setattr(serves, "cmd_up", lambda *_args, **_kwargs: 19)
    monkeypatch.setattr(
        serves.host_ops, "automatic_cache_reclaim",
        lambda *_args, **_kwargs: reclaimed.append(True),
    )
    assert serves.main(["up", "heavy", "--manifest", manifest]) == 19
    assert reclaimed == []

    plans = []
    monkeypatch.setattr(
        serves.host_ops, "render_cache_reclaim_plan",
        lambda resolved, operation: plans.append((resolved, operation)),
    )
    assert serves.main(["up", "heavy", "--manifest", manifest, "--dry-run"]) == 19
    assert reclaimed == []
    assert plans == [(policy, "serves up")]
    capsys.readouterr()


def test_router_owner_bring_up_failure_gates_serves_up(tmp_path, monkeypatch):
    # A non-zero ensure_router_healthy return means the operator topology
    # declares this host the router owner and its bring-up failed: `serves up`
    # aborts with that code before any container mutation.
    manifest = _lifecycle_manifest(tmp_path)
    monkeypatch.setattr(serves.host_ops, "load_cache_reclaim_policy", lambda: None)
    monkeypatch.setattr(serves.host_ops, "capture_cache_before", lambda _policy: None)
    monkeypatch.setattr(serves, "ensure_router_healthy", lambda **_kwargs: 7)
    monkeypatch.setattr(
        serves, "cmd_up", lambda *_args, **_kwargs: pytest.fail("container mutated")
    )
    assert serves.main(["up", "heavy", "--manifest", manifest]) == 7
