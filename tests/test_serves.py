"""Tests for `anvil-serving serves` — the model-serve lifecycle verb.

Docker + nvidia-smi + HTTP are injected (the module exposes `_run`/`_open`
seams), so these run with no docker, no GPU, and no network.
"""
import os
import textwrap
import types

import pytest

from anvil_serving import reservations, serves


def proc(rc=0, out="", err=""):
    return types.SimpleNamespace(returncode=rc, stdout=out, stderr=err)


def _manifest(tmp_path, body):
    p = tmp_path / "serves.toml"
    p.write_text(textwrap.dedent(body), encoding="utf-8")
    return str(p)


def _inspect_returning(state, stop_rc=0, stop_err="", state_after_stop="exited"):
    """A fake _run: `docker inspect` -> `state` (or `state_after_stop` once a
    successful `docker stop` has run — cmd_down re-checks state to verify the
    stop STUCK), anything else -> proc(stop_rc)."""
    calls = []
    stopped = []

    def run(argv, **k):
        calls.append(argv)
        if isinstance(argv, list) and argv[:2] == ["docker", "inspect"]:
            st = state_after_stop if stopped else state
            if st == "absent":
                return proc(1, "", "Error: No such object")
            if st == "error":
                return proc(1, "", "Cannot connect to the Docker daemon")
            return proc(0, st + "\n")
        if isinstance(argv, list) and argv[:2] == ["docker", "stop"] and stop_rc == 0:
            stopped.append(argv)
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
    assert s["_manifest_dir"] == mdir
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
        port = 30000
        model = "legacy-local"
        up = "{up}"
    """)
    (serve,) = serves.load_manifest(path)
    assert serve["engine"] == expected


def test_load_manifest_accepts_audio_engine_for_non_llm_serves(tmp_path):
    path = _manifest(tmp_path, """
        [[serve]]
        name = "stt"
        container = "anvil-voice-stt"
        port = 30010
        model = "tdt_ctc-110m"
        engine = "audio"
        up = "docker compose -f {dir}/docker-compose.voice-audio.yml up -d stt"
    """)
    (serve,) = serves.load_manifest(path)
    assert serve["engine"] == "audio"


@pytest.mark.parametrize(
    ("name", "engine"),
    [("embeddings", "embedding"), ("reranker", "reranker")],
)
def test_load_manifest_accepts_purpose_model_engines(tmp_path, name, engine):
    # gpu-reservations:T009 — the purpose-model vocabulary (ADR-0017 §7)
    # extends the "audio" precedent: truthful labels for non-chat-LLM serves.
    path = _manifest(tmp_path, f"""
        [[serve]]
        name = "{name}"
        container = "vllm-qwen3-{name}"
        port = 30005
        model = "qwen3-{name}-0.6b"
        engine = "{engine}"
        up = "docker compose -f {{dir}}/docker-compose.yml up -d {name}"
    """)
    (serve,) = serves.load_manifest(path)
    assert serve["engine"] == engine


# ---- reservation fields (ADR-0017 GPU residency reservations) ---------------

def test_load_manifest_accepts_and_normalizes_reservation_fields(tmp_path):
    path = _manifest(tmp_path, """
        [[serve]]
        name = "stt"
        container = "anvil-voice-stt"
        port = 30010
        model = "tdt_ctc-110m"
        engine = "audio"
        gpu_role = " fast "
        vram_mib = 3072
        residency = "Resident"
    """)
    (s,) = serves.load_manifest(path)
    assert s["gpu_role"] == "fast"       # whitespace-normalized
    assert s["vram_mib"] == 3072
    assert s["residency"] == "resident"  # case-normalized


def test_load_manifest_normalizes_on_demand_residency_spelling(tmp_path):
    path = _manifest(tmp_path, """
        [[serve]]
        name = "fast"
        container = "vllm-fast"
        port = 30001
        model = "fast-local"
        engine = "vllm"
        residency = "On_Demand"
    """)
    (s,) = serves.load_manifest(path)
    assert s["residency"] == "on-demand"


@pytest.mark.parametrize("residency", ['"always"', '"leased"', '""', "3", "true"])
def test_load_manifest_rejects_invalid_residency_with_clear_error(tmp_path, residency):
    path = _manifest(tmp_path, f"""
        [[serve]]
        name = "fast"
        container = "vllm-fast"
        port = 30001
        model = "fast-local"
        engine = "vllm"
        residency = {residency}
    """)
    with pytest.raises(
        ValueError, match=r"residency must be one of .*resident.*evictable.*on-demand"
    ):
        serves.load_manifest(path)


@pytest.mark.parametrize("vram", ["0", "-512", '"20000"', "true", "1.5"])
def test_load_manifest_rejects_non_positive_integer_vram_mib(tmp_path, vram):
    path = _manifest(tmp_path, f"""
        [[serve]]
        name = "fast"
        container = "vllm-fast"
        port = 30001
        model = "fast-local"
        engine = "vllm"
        vram_mib = {vram}
    """)
    with pytest.raises(ValueError, match="vram_mib must be a positive integer"):
        serves.load_manifest(path)


@pytest.mark.parametrize("gpu_role", ['""', '"   "', "5"])
def test_load_manifest_rejects_empty_or_non_string_gpu_role(tmp_path, gpu_role):
    path = _manifest(tmp_path, f"""
        [[serve]]
        name = "fast"
        container = "vllm-fast"
        port = 30001
        model = "fast-local"
        engine = "vllm"
        gpu_role = {gpu_role}
    """)
    with pytest.raises(ValueError, match="gpu_role must be a non-empty string"):
        serves.load_manifest(path)


def test_load_manifest_without_reservation_fields_parses_unchanged(tmp_path):
    """A pre-reservation manifest entry parses to exactly today's dict shape."""
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
    mdir = os.path.dirname(os.path.abspath(path))
    assert s == {
        "name": "fast",
        "container": "vllm-gptoss",
        "port": 30001,
        "model": "fast-local",
        "served_name": "fast-local",
        "engine": "vllm",
        "_manifest_dir": mdir,
        "health": "/health",
        "up": ["bash", mdir + "/serve.sh"],
    }  # no reservation keys are invented for entries that never declared them


# ---- serve groups: field parse/validation --------------------------------

def test_load_manifest_parses_groups_field(tmp_path):
    path = _manifest(tmp_path, """
        [[serve]]
        name = "fast"
        container = "vllm-fast"
        port = 30001
        model = "fast-local"
        engine = "vllm"
        groups = ["fast-only", "llm-stack"]
        up = "docker compose up -d fast"
    """)
    (s,) = serves.load_manifest(path)
    assert s["groups"] == ["fast-only", "llm-stack"]


def test_load_manifest_groups_absent_adds_no_key(tmp_path):
    """Omitting `groups` must not invent a key — backward compatible."""
    path = _manifest(tmp_path, """
        [[serve]]
        name = "fast"
        container = "vllm-fast"
        port = 30001
        model = "fast-local"
        engine = "vllm"
    """)
    (s,) = serves.load_manifest(path)
    assert "groups" not in s


def test_load_manifest_groups_dedupes_preserving_order(tmp_path):
    path = _manifest(tmp_path, """
        [[serve]]
        name = "fast"
        container = "vllm-fast"
        port = 30001
        model = "fast-local"
        engine = "vllm"
        groups = ["llm-stack", "fast-only", "llm-stack"]
    """)
    (s,) = serves.load_manifest(path)
    assert s["groups"] == ["llm-stack", "fast-only"]


@pytest.mark.parametrize("groups", ['"llm-stack"', "42", "[1, 2]", '["ok", ""]', '["ok", "  "]'])
def test_load_manifest_rejects_invalid_groups(tmp_path, groups):
    path = _manifest(tmp_path, f"""
        [[serve]]
        name = "fast"
        container = "vllm-fast"
        port = 30001
        model = "fast-local"
        engine = "vllm"
        groups = {groups}
    """)
    with pytest.raises(ValueError, match="groups must be a list of non-empty strings"):
        serves.load_manifest(path)


@pytest.mark.parametrize("reserved", ['["all"]', '["fast-only", "ALL"]'])
def test_load_manifest_rejects_reserved_group_all(tmp_path, reserved):
    path = _manifest(tmp_path, f"""
        [[serve]]
        name = "fast"
        container = "vllm-fast"
        port = 30001
        model = "fast-local"
        engine = "vllm"
        groups = {reserved}
    """)
    with pytest.raises(ValueError, match="reserved group 'all'"):
        serves.load_manifest(path)


def test_load_manifest_rejects_conflicting_legacy_engine_markers(tmp_path):
    path = _manifest(tmp_path, """
        [[serve]]
        name = "legacy"
        container = "vllm-old-model"
        port = 30000
        model = "legacy-local"
        up = "docker compose -f old.yml up -d sglang"
    """)
    with pytest.raises(ValueError, match="conflicting legacy engine markers"):
        serves.load_manifest(path)


@pytest.mark.parametrize("engine", ["", "unknown", "VLLM "])
def test_load_manifest_rejects_malformed_explicit_engine(tmp_path, engine):
    path = _manifest(tmp_path, f"""
        [[serve]]
        name = "bad"
        container = "vllm-model"
        port = 30000
        model = "bad-local"
        engine = "{engine}"
    """)
    with pytest.raises(ValueError, match="engine must be one of"):
        serves.load_manifest(path)


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


def test_shipped_fakoli_manifest_purpose_model_serves():
    # gpu-reservations:T009 — the embeddings/reranker serves are resident
    # ADR-0017 reservations on the multi-tenant 5090 with truthful engine
    # labels, and the resident set fits the declared dark-fast budget.
    serves_list = serves.load_manifest(serves.EXAMPLE_MANIFEST)
    by_name = {s["name"]: s for s in serves_list}
    emb, rr = by_name["embeddings"], by_name["reranker"]
    assert emb["engine"] == "embedding"
    assert rr["engine"] == "reranker"
    for s in (emb, rr):
        assert s["gpu_role"] == "dark-fast"
        assert s["residency"] == "resident"
        assert s["health"] == "/health"
    # HONEST-MEASURED budgets (see the manifest comments): weights + the fixed
    # vLLM/WSL2 runtime floor + the serve's KV window.
    assert emb["vram_mib"] == 3200
    assert rr["vram_mib"] == 3456
    assert emb["port"] == 30005 and emb["model"] == "qwen3-embedding-0.6b"
    assert rr["port"] == 30006 and rr["model"] == "qwen3-reranker-0.6b"
    # The T009 resident trio (fast + embeddings + reranker) must fit the role
    # budget — the manifest-level guarantee that `serves up` admits all three
    # together.
    by = {s["name"]: s for s in serves_list}
    budget = reservations.budgets_of(serves_list)["dark-fast"].budget_mib
    trio = sum(by[n]["vram_mib"] for n in ("fast", "embeddings", "reranker"))
    assert trio <= budget, (trio, budget)


def test_shipped_fakoli_manifest_ocr_serve():
    # gpu-reservations:T011 — PaddleOCR-VL-1.6 is a declared `resident`
    # ADR-0017 reservation behind the router's "ocr" preset (ocr-local, :30007).
    serves_list = serves.load_manifest(serves.EXAMPLE_MANIFEST)
    by_name = {s["name"]: s for s in serves_list}
    ocr = by_name["ocr"]
    assert ocr["engine"] == "vllm"
    assert ocr["gpu_role"] == "dark-fast"
    assert ocr["residency"] == "resident"
    assert ocr["health"] == "/health"
    # HONEST-MEASURED budget (see the manifest/compose comments): 1.82 GiB
    # weights + the ~2.65 GiB multimodal-profiling floor + the serve's
    # 16384-token KV window.
    assert ocr["vram_mib"] == 5120
    assert ocr["port"] == 30007 and ocr["model"] == "paddleocr-vl-1.6"
    # The FULL declared RESIDENT set (fast + embeddings + reranker + ocr) FITS
    # the dark-fast budget after the 2026-07-13 T011 operator rebalance (fast
    # 18432 -> 14336 via the pre-quantized FP8-Dynamic checkpoint, reserve
    # 7168 -> 4608), resolving the previously pinned T015 oversubscription
    # (full - budget == 4769). This pin keeps the next rebalance deliberate:
    # the resident set must keep fitting. (T013: the sum is residency-filtered —
    # `evictable` serves like `vision` are OUTSIDE the always-on guarantee and
    # pinned separately in test_shipped_fakoli_manifest_vision_serve.)
    budget = reservations.budgets_of(serves_list)["dark-fast"].budget_mib
    resident = sum(
        s["vram_mib"] for s in serves_list
        if s.get("gpu_role") == "dark-fast" and s.get("residency") == "resident"
    )
    assert resident == 26112 and budget == 27999, (resident, budget)
    assert budget - resident == 1887, (resident, budget)


def test_shipped_fakoli_manifest_vision_serve():
    # gpu-reservations:T013 — Qwen3-VL-4B-Instruct is the first `evictable`
    # ADR-0017 reservation on dark-fast, behind the router's "vision" preset
    # (vision-local, :30008). Evictable means: admitted only when the ledger
    # has headroom, and stopped (drain-first, via the declared router_tier)
    # when an `on-demand` acquisition needs the VRAM (T005 eviction flow).
    serves_list = serves.load_manifest(serves.EXAMPLE_MANIFEST)
    by_name = {s["name"]: s for s in serves_list}
    vision = by_name["vision"]
    assert vision["engine"] == "vllm"
    assert vision["gpu_role"] == "dark-fast"
    assert vision["residency"] == "evictable"
    assert vision["health"] == "/health"
    # The ADR-0018 drain hook: eviction quiesces + drains this router tier
    # before the container is stopped (serves._evict_victims).
    assert vision["router_tier"] == "vision-local"
    # HONEST-MEASURED budget (see the manifest/compose comments): bf16 weights
    # + the multimodal-profiling floor + the fp8-KV 16384-token window.
    assert vision["vram_mib"] == 12288
    assert vision["port"] == 30008 and vision["model"] == "qwen3-vl-4b-instruct"
    # CAPACITY (deliberate, not an accident): vision does NOT fit alongside the
    # full resident set — that is what `evictable` residency is for. This pin
    # keeps the trade-off visible: if the resident set shrinks enough that
    # vision becomes co-residable, or vision's budget changes, re-decide
    # (gpu-reservations:T015) instead of silently drifting.
    budget = reservations.budgets_of(serves_list)["dark-fast"].budget_mib
    resident = sum(
        s["vram_mib"] for s in serves_list
        if s.get("gpu_role") == "dark-fast" and s.get("residency") == "resident"
    )
    assert resident + vision["vram_mib"] > budget, (resident, vision["vram_mib"], budget)


def test_load_manifest_accepts_image_engine(tmp_path):
    # gpu-reservations:T012 — "image" extends the truthful non-chat-LLM engine
    # vocabulary (audio/embedding/reranker precedent) for the ComfyUI tenant.
    path = _manifest(tmp_path, """
        [[serve]]
        name = "comfyui"
        container = "comfyui"
        port = 8188
        model = "comfyui-v0.27.1"
        engine = "image"
        up = "docker compose -f {dir}/docker-compose.comfyui.yml up -d comfyui"
    """)
    (serve,) = serves.load_manifest(path)
    assert serve["engine"] == "image"


COMFYUI_MANIFEST = os.path.join(
    os.path.dirname(serves.EXAMPLE_MANIFEST), "serves.comfyui.toml")


def test_shipped_comfyui_manifest_on_demand_tenant():
    # gpu-reservations:T012 — ComfyUI is the dedicated-manifest `on-demand`
    # tenant (docs/VOICE.md isolation rule: NOT in the shared serves.toml).
    serves_list = serves.load_manifest(COMFYUI_MANIFEST)
    by_name = {s["name"]: s for s in serves_list}
    comfyui = by_name["comfyui"]
    assert comfyui["engine"] == "image"
    assert comfyui["gpu_role"] == "dark-fast"
    assert comfyui["residency"] == "on-demand"
    assert comfyui["health"] == "/system_stats"
    assert comfyui["port"] == 8188
    # The vision-sized slot the F006 design gives the tenant; the compose file
    # enforces it engine-side with --reserve-vram (change them together).
    assert comfyui["vram_mib"] == 12288
    # v0.27.1 pin travels in the label so `serves status` shows what runs.
    assert comfyui["model"] == "comfyui-v0.27.1"
    # Own compose project, own compose file — never the shared docker-compose.yml.
    assert "docker-compose.comfyui.yml" in " ".join(comfyui["up"])
    # The tenant is the ONLY lifecycle target: every other row is a ledger
    # mirror and must NOT be startable from this manifest.
    for name, s in by_name.items():
        if name != "comfyui":
            assert not s.get("up"), name


def test_shipped_comfyui_manifest_mirrors_main_manifest():
    # The comfyui manifest re-declares the dark-fast ledger (capacity row +
    # reservation mirrors) because ADR-0017 ledgers are derived per manifest.
    # This pin turns the KEEP IN SYNC comment into a checked invariant: a
    # rebalance of serves.toml that forgets the mirrors fails here instead of
    # silently letting `serves up comfyui` admit against stale budgets.
    main_list = serves.load_manifest(serves.EXAMPLE_MANIFEST)
    main = {s["name"]: s for s in main_list}
    comfy_list = serves.load_manifest(COMFYUI_MANIFEST)
    comfy = {s["name"]: s for s in comfy_list}
    main_budget = reservations.budgets_of(main_list)["dark-fast"]
    comfy_budget = reservations.budgets_of(comfy_list)["dark-fast"]
    assert (comfy_budget.vram_mib, comfy_budget.reserve_mib) == (
        main_budget.vram_mib, main_budget.reserve_mib)
    mirrors = [n for n in comfy if n != "comfyui"]
    # Every serves.toml dark-fast reservation must be mirrored — a missing
    # mirror makes comfyui admission blind to that serve's committed VRAM.
    main_reserved = {
        n for n, s in main.items()
        if s.get("gpu_role") == "dark-fast" and isinstance(s.get("vram_mib"), int)
    }
    assert set(mirrors) == main_reserved, (sorted(mirrors), sorted(main_reserved))
    for name in mirrors:
        for field in ("container", "port", "gpu_role", "vram_mib", "residency"):
            assert comfy[name].get(field) == main[name].get(field), (name, field)
        # The eviction drain hook must travel with the vision mirror.
        assert comfy[name].get("router_tier") == main[name].get("router_tier"), name


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


def test_manifest_dotenv_isolation_survives_repeated_loads_and_object_churn(tmp_path, monkeypatch):
    home = tmp_path / "home"
    config_home = tmp_path / "config"
    home.mkdir()
    config_home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("ANVIL_SERVING_HOME", str(config_home))
    monkeypatch.delenv("DEPLOYMENT_SECRET", raising=False)

    records = []
    for name in ("alpha", "beta"):
        directory = tmp_path / name
        directory.mkdir()
        (directory / ".env").write_text(
            f"DEPLOYMENT_SECRET={name}-secret\n", encoding="utf-8"
        )
        path = _manifest(directory, f"""
            [[serve]]
            name = "{name}"
            container = "vllm-{name}"
            port = 30000
            model = "{name}-local"
        """)
        records.append((name, path))

    for _ in range(100):
        loaded = [serves.load_manifest(path)[0] for _name, path in records]
        assert serves._serve_env(loaded[0])["DEPLOYMENT_SECRET"] == "alpha-secret"
        assert serves._serve_env(loaded[1])["DEPLOYMENT_SECRET"] == "beta-secret"
        junk = [{"value": value} for value in range(200)]
        assert junk[-1]["value"] == 199

    assert not hasattr(serves, "_SERVE_MANIFEST_DIRS")


def test_manifest_dotenv_shell_value_wins_without_printing_secret(tmp_path, monkeypatch, capsys):
    path = _manifest(tmp_path, """
        [[serve]]
        name = "secure"
        container = "vllm-secure"
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


def test_cmd_down_detects_restart_policy_revival():
    # `docker stop` succeeded but a `restart: always` policy revived the
    # container — the GPU was NOT freed, and down must not claim success.
    serv = [{"name": "h", "container": "sglang", "port": 1, "health": "/health"}]
    run = _inspect_returning("running", state_after_stop="running")
    assert serves.cmd_down(serv, [], _run=run) == 1


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


# ---- guarded promotion ------------------------------------------------------

def _promotion_manifest(tmp_path):
    for name, model in (("new.toml", "new-heavy"), ("old.toml", "old-heavy")):
        (tmp_path / name).write_text(
            """
[router]
mapping_version = "test"
[[router.tiers]]
id = "heavy-local"
base_url = "http://127.0.0.1:30002/v1"
model = "%s"
dialect = "openai"
context_limit = 131072
privacy = "local"
tool_support = true
auth_env = "ANVIL_HEAVY_KEY"
health_path = "/health"
model_identity = true
[router.presets]
chat = ["heavy-local"]
""" % model,
            encoding="utf-8",
        )
    for name in ("new.json", "old.json"):
        (tmp_path / name).write_text("{}", encoding="utf-8")
    return _manifest(tmp_path, """
        [[serve]]
        name = "candidate"
        container = "candidate-c"
        port = 39031
        model = "candidate-model"
        engine = "vllm"

        [[serve]]
        name = "heavy"
        container = "heavy-c"
        port = 30002
        model = "new-heavy"
        engine = "vllm"
        up = "docker compose -f {dir}/compose.yml up -d heavy"

        [[serve]]
        name = "old-heavy"
        container = "old-heavy-c"
        port = 30002
        model = "old-heavy"
        engine = "vllm"
        up = "docker compose -f {dir}/compose.yml --profile rollback up -d old-heavy"

        [[promotion]]
        name = "heavy-v2"
        candidate = "candidate"
        target = "heavy"
        rollback = "old-heavy"
        router_config = "{dir}/new.toml"
        router_profile = "{dir}/new.json"
        rollback_router_config = "{dir}/old.toml"
        rollback_router_profile = "{dir}/old.json"
        affected_tiers = ["heavy-local"]
        needle_ctx = 131072
        tool_batch = 20

        [[promotion.gate]]
        name = "functional"
        checks = "smoke,json,needle,tools"
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


def test_load_promotions_resolves_complete_router_state(tmp_path):
    path = _promotion_manifest(tmp_path)
    (plan,) = serves.load_promotions(path)
    assert plan["name"] == "heavy-v2"
    assert plan["target"] == "heavy"
    assert plan["rollback"] == "old-heavy"
    assert plan["router_config"] == str(tmp_path / "new.toml")
    assert plan["rollback_router_profile"] == str(tmp_path / "old.json")
    assert [gate["name"] for gate in plan["gate"]] == ["functional", "quality"]
    assert plan["gate"][1]["reasoning_headroom_tokens"] == 4096
    assert plan["rollback_gate"][0]["thinking_mode"] == "unsupported"


def test_load_promotions_resolves_plain_relative_paths_from_manifest(tmp_path):
    path = _promotion_manifest(tmp_path)
    text = (tmp_path / "serves.toml").read_text(encoding="utf-8")
    (tmp_path / "serves.toml").write_text(text.replace("{dir}/new.toml", "new.toml"), encoding="utf-8")
    (plan,) = serves.load_promotions(path)
    assert plan["router_config"] == str(tmp_path / "new.toml")


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
    assert "start heavy" in out
    assert "eval preflight --tier heavy" in out
    assert "gate functional" in out
    assert "gate quality" in out
    assert "--thinking-mode enabled" in out
    assert "--reasoning-headroom-tokens 4096" in out
    assert "router promote" in out


def test_promotion_topology_rejects_same_port_and_model_on_wrong_host(tmp_path):
    path = _promotion_manifest(tmp_path)
    new_path = tmp_path / "new.toml"
    new_path.write_text(
        new_path.read_text(encoding="utf-8").replace("127.0.0.1", "10.0.0.9"),
        encoding="utf-8",
    )
    assert serves.cmd_promote(
        serves.load_manifest(path), serves.load_promotions(path),
        "heavy-v2", path, dry_run=True,
    ) == 1


def test_promotion_topology_rejects_ipv6_loopback_alias(tmp_path):
    path = _promotion_manifest(tmp_path)
    new_path = tmp_path / "new.toml"
    new_path.write_text(
        new_path.read_text(encoding="utf-8").replace(
            "http://127.0.0.1:30002/v1", "http://[::1]:30002/v1"
        ),
        encoding="utf-8",
    )
    assert serves.cmd_promote(
        serves.load_manifest(path), serves.load_promotions(path),
        "heavy-v2", path, dry_run=True,
    ) == 1


def test_promotion_topology_requires_complete_endpoint_alias_coverage(tmp_path):
    path = _promotion_manifest(tmp_path)
    for filename, model in (("new.toml", "new-heavy"), ("old.toml", "old-heavy")):
        config = tmp_path / filename
        text = config.read_text(encoding="utf-8")
        alias = """
[[router.tiers]]
id = "heavy-alias"
base_url = "http://127.0.0.1:30002/v1"
model = "%s"
dialect = "openai"
context_limit = 131072
privacy = "local"
tool_support = true
auth_env = "ANVIL_HEAVY_KEY"
health_path = "/health"
model_identity = true
""" % model
        config.write_text(
            text.replace("[router.presets]", alias + "\n[router.presets]"),
            encoding="utf-8",
        )
    assert serves.cmd_promote(
        serves.load_manifest(path), serves.load_promotions(path),
        "heavy-v2", path, dry_run=True,
    ) == 1


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

    monkeypatch.setattr(serves, "_promotion_cli", lambda *a, **k: 0)

    def transition(_plan, action, tier_id, **kwargs):
        actions.append((action, tier_id))
        return 1 if action == "quiesce" else readmit_rc

    monkeypatch.setattr(serves, "_promotion_transition_cli", transition)
    rc = serves._promotion_transition(
        managed, plan, path, require_candidate=False
    )
    assert rc == expected
    assert actions == [
        ("quiesce", "heavy-local"),
        ("readmit", "heavy-local"),
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


def test_resume_skips_candidate_requirement_for_interrupted_transaction(tmp_path, monkeypatch):
    path = _promotion_manifest(tmp_path)
    managed = serves.load_manifest(path)
    plans = serves.load_promotions(path)
    received = {}

    def transition(_serves, _plan, _manifest, **kwargs):
        received.update(kwargs)
        return 0

    monkeypatch.setattr(serves, "_promotion_transition", transition)
    assert serves.cmd_promote(managed, plans, "heavy-v2", path, resume=True) == 0
    assert received["resume"] is True
    assert received["require_candidate"] is False


@pytest.mark.parametrize(("first_identity", "expect_recreate"), [(True, False), (False, True)])
def test_resume_reuses_only_running_healthy_exact_identity_target(
    tmp_path, monkeypatch, first_identity, expect_recreate
):
    path = _promotion_manifest(tmp_path)
    managed = serves.load_manifest(path)
    (plan,) = serves.load_promotions(path)
    up_calls = []
    identities = iter([first_identity, True])

    monkeypatch.setattr(serves, "_promotion_cli", lambda *a, **k: 0)
    monkeypatch.setattr(serves, "_promotion_transition_cli", lambda *a, **k: 0)
    monkeypatch.setattr(serves, "cmd_down", lambda *a, **k: 0)
    monkeypatch.setattr(serves, "docker_state", lambda *a, **k: "running")
    monkeypatch.setattr(
        serves, "cmd_up", lambda *a, **k: up_calls.append((a, k)) or 0
    )
    monkeypatch.setattr(serves, "_health", lambda *a, **k: 200)
    monkeypatch.setattr(serves, "_await_healthy", lambda *a, **k: True)
    monkeypatch.setattr(
        serves, "_serve_identity_ready", lambda *a, **k: next(identities)
    )
    monkeypatch.setattr(serves, "_gateway_status", lambda *a, **k: 200)

    assert serves._promotion_transition(
        managed, plan, path, resume=True, require_candidate=False
    ) == 0
    assert bool(up_calls) is expect_recreate


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


def test_safe_promotion_orders_quiesce_drain_before_heavy_mutation(tmp_path):
    path = _promotion_manifest(tmp_path)
    managed = serves.load_manifest(path)
    # An unrelated resident Fast serve is present but outside the managed pair.
    managed.append({
        "name": "fast", "container": "fast-c", "port": 30003,
        "model": "fast-model", "served_name": "fast-model",
        "health": "/health", "up": ["docker", "start", "fast-c"],
    })
    plans = serves.load_promotions(path)
    run = _inspect_returning("running")

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
    post_restart = next(i for i, call in enumerate(calls) if "transition-status" in call)
    assert quiesce < drain < first_stop < post_restart
    assert not any("fast-c" in call for call in calls)


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
    port = 30003
    model = "fast-local"
    engine = "vllm"
    gpu_role = "dark-fast"
    vram_mib = 20480
    residency = "on-demand"

    [[serve]]
    name = "stt"
    container = "anvil-voice-stt"
    port = 30010
    model = "tdt_ctc-110m"
    engine = "audio"
    gpu_role = "dark-fast"
    vram_mib = 4096
    residency = "resident"

    [[serve]]
    name = "plain"
    container = "vllm-plain"
    port = 30030
    model = "plain-local"
    engine = "vllm"
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


def test_cmd_status_prints_the_per_role_reservation_ledger(tmp_path, capsys):
    loaded = serves.load_manifest(_manifest(tmp_path, STATUS_LEDGER_MANIFEST))
    run = _status_states_run({"vllm-fast": "running", "anvil-voice-stt": "exited"})
    assert serves.cmd_status(loaded, _run=run, _open=_open_down) == 0
    out = capsys.readouterr().out
    # per-gpu_role capacity / reserve / committed / free (the acceptance criterion):
    assert "GPU reservations" in out
    assert ("gpu_role 'dark-fast': capacity 32768 MiB, reserve 2048 MiB, "
            "committed 20480 MiB, free 10240 MiB") in out
    # ... and the per-serve reservations with their observed docker state:
    assert "fast 20480 MiB (on-demand, running)" in out
    assert "stt 4096 MiB (resident, exited) [not committed]" in out


def test_cmd_status_ledger_reuses_the_probed_states(tmp_path):
    """The ledger section adds no docker calls: one inspect per manifest serve."""
    loaded = serves.load_manifest(_manifest(tmp_path, STATUS_LEDGER_MANIFEST))
    run = _status_states_run({"vllm-fast": "running", "anvil-voice-stt": "exited"})
    assert serves.cmd_status(loaded, _run=run, _open=_open_down) == 0
    inspects = [c for c in run.calls if c[:2] == ["docker", "inspect"]]
    assert sorted(c[-1] for c in inspects) == [
        "anvil-voice-stt", "vllm-fast", "vllm-plain"]


def test_cmd_status_without_gpu_roles_prints_no_reservation_section(tmp_path, capsys):
    loaded = serves.load_manifest(_manifest(tmp_path, """
        [[serve]]
        name = "fast"
        container = "vllm-fast"
        port = 30003
        model = "fast-local"
        engine = "vllm"
    """))
    run = _status_states_run({"vllm-fast": "running"})
    assert serves.cmd_status(loaded, _run=run, _open=_open_down) == 0
    assert "GPU reservations" not in capsys.readouterr().out


def test_status_summary_reports_the_ledger_structurally(tmp_path):
    loaded = serves.load_manifest(_manifest(tmp_path, STATUS_LEDGER_MANIFEST))
    run = _status_states_run({"vllm-fast": "running", "anvil-voice-stt": "exited"})
    data = serves.status_summary(loaded, _run=run, _open=_open_down)
    (role,) = data["reservations"]["gpu_roles"]
    assert role["gpu_role"] == "dark-fast"
    assert role["capacity_mib"] == 32768
    assert role["reserve_mib"] == 2048
    assert role["budget_mib"] == 30720
    assert role["committed_mib"] == 20480
    assert role["free_mib"] == 10240
    by_serve = {r["serve"]: r for r in role["reservations"]}
    assert set(by_serve) == {"fast", "stt"}  # `plain` declares no reservation
    assert by_serve["fast"] == {
        "serve": "fast", "container": "vllm-fast", "vram_mib": 20480,
        "residency": "on-demand", "state": "running", "committed": True,
    }
    assert by_serve["stt"]["state"] == "exited"
    assert by_serve["stt"]["committed"] is False


def test_status_summary_ledger_spans_the_whole_manifest_despite_names(tmp_path):
    """A name-filtered status still reports role-wide commitments — a ledger
    filtered to the selection would misreport `free`."""
    loaded = serves.load_manifest(_manifest(tmp_path, STATUS_LEDGER_MANIFEST))
    run = _status_states_run({"vllm-fast": "running", "anvil-voice-stt": "exited"})
    data = serves.status_summary(loaded, ["plain"], _run=run, _open=_open_down)
    assert data["selected"] == ["plain"]
    (role,) = data["reservations"]["gpu_roles"]
    assert role["committed_mib"] == 20480


def test_status_summary_without_gpu_roles_has_empty_ledger_and_no_extra_probes(tmp_path):
    loaded = serves.load_manifest(_manifest(tmp_path, """
        [[serve]]
        name = "fast"
        container = "vllm-fast"
        port = 30003
        model = "fast-local"
        engine = "vllm"
    """))
    run = _status_states_run({"vllm-fast": "running"})
    data = serves.status_summary(loaded, _run=run, _open=_open_down)
    assert data["reservations"] == {"gpu_roles": []}
    inspects = [c for c in run.calls if c[:2] == ["docker", "inspect"]]
    assert len(inspects) == 1
