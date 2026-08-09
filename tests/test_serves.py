"""Tests for `anvil-serving serves` — the model-serve lifecycle verb.

Docker + nvidia-smi + HTTP are injected (the module exposes `_run`/`_open`
seams), so these run with no docker, no GPU, and no network.
"""
import os
import subprocess
import textwrap
import json

import pytest

from anvil_serving import reservations, serves
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

def test_load_manifest_parses_up_into_argv_list(tmp_path):
    path = _manifest(tmp_path, """
        [[serve]]
        name = "fast"
        container = "vllm-gptoss"
        runtime = "docker"
        port = 30001
        model = "auxiliary-local"
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
            '[[serve]]\nname="x"\ncontainer="x"\nruntime="docker"\nport=1\nmodel="x"\n'
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
    # `container` is no longer unconditionally required — it is required only
    # for runtime = "docker" (ADR-0034), so the unconditional set is what a
    # bare entry must be told about.
    assert "runtime" in msg and "port" in msg and "model/served_name" in msg


def _runtime_entry(tmp_path, body):
    return _manifest(tmp_path, "[[serve]]\n" + body)


def test_load_manifest_rejects_unknown_runtime(tmp_path):
    path = _runtime_entry(tmp_path, (
        'name = "x"\ncontainer = "c"\nruntime = "podman"\nport = 1\nmodel = "m"\n'
    ))
    with pytest.raises(ValueError, match="runtime must be one of"):
        serves.load_manifest(path)


@pytest.mark.parametrize("declared", [" Docker ", "DOCKER", "\tdocker "])
def test_load_manifest_normalizes_runtime_like_residency(tmp_path, declared):
    # `residency` strips and lowercases; `runtime` sits beside it in the same
    # validator, so it forgives the same input rather than surprising an author
    # who learned the convention from the adjacent field.
    path = _runtime_entry(tmp_path, (
        f'name = "x"\ncontainer = "c"\nruntime = "{declared}"\n'
        'port = 1\nmodel = "m"\n'
    ))
    (s,) = serves.load_manifest(path)
    assert s["runtime"] == "docker"


def test_load_manifest_normalizes_native_runtime_before_rejecting(tmp_path):
    # Normalization must happen before the not-implemented guard, or a
    # differently-cased native entry would fall through to the unknown-value
    # error and misreport why it was refused.
    path = _runtime_entry(tmp_path, (
        'name = "x"\nruntime = " NATIVE "\nport = 1\nmodel = "m"\n'
    ))
    with pytest.raises(serves.NativeRuntimeNotSupported, match="not implemented"):
        serves.load_manifest(path)


def test_load_manifest_rejects_non_string_runtime(tmp_path):
    path = _runtime_entry(tmp_path, (
        'name = "x"\ncontainer = "c"\nruntime = 1\nport = 1\nmodel = "m"\n'
    ))
    with pytest.raises(ValueError, match="runtime must be one of"):
        serves.load_manifest(path)


def test_load_manifest_rejects_docker_runtime_without_container(tmp_path):
    path = _runtime_entry(tmp_path, (
        'name = "x"\nruntime = "docker"\nport = 1\nmodel = "m"\n'
    ))
    with pytest.raises(ValueError, match="missing required field.*container"):
        serves.load_manifest(path)


def test_load_manifest_rejects_native_runtime_declaring_container(tmp_path):
    # The container field is meaningless for a native serve; accepting it would
    # let a manifest imply a lifecycle the runtime does not have.
    path = _runtime_entry(tmp_path, (
        'name = "x"\ncontainer = "c"\nruntime = "native"\nport = 1\nmodel = "m"\n'
    ))
    with pytest.raises(ValueError, match="must not declare container"):
        serves.load_manifest(path)


def test_load_manifest_rejects_native_runtime_as_unimplemented(tmp_path):
    # ADR-0034 defines the discriminator; the native lifecycle is not built.
    # Failing at load keeps the schema honest instead of surfacing a KeyError
    # from one of the ~85 places that resolve serve["container"].
    path = _runtime_entry(tmp_path, (
        'name = "mlx-primary"\nruntime = "native"\nport = 1\nmodel = "m"\n'
    ))
    with pytest.raises(serves.NativeRuntimeNotSupported, match="not implemented"):
        serves.load_manifest(path)


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


def test_load_manifest_accepts_audio_engine_for_non_llm_serves(tmp_path):
    path = _manifest(tmp_path, """
        [[serve]]
        name = "stt"
        container = "anvil-voice-stt"
        runtime = "docker"
        port = 30010
        model = "tdt_ctc-110m"
        engine = "audio"
        up = "docker compose -f {dir}/docker-compose.voice-audio.yml up -d stt"
    """)
    (serve,) = serves.load_manifest(path)
    assert serve["engine"] == "audio"


# ---- engine-aware functional probes ---------------------------------------

def test_probe_embedding_validates_vector_shape():
    seen = {}

    def open_request(request, timeout=0):
        seen["url"] = request.full_url
        seen["body"] = json.loads(request.data)
        seen["timeout"] = timeout
        return _JsonResponse({
            "data": [{"embedding": [0.1, 0.2, 0.3]}],
        })

    result = serves.probe_serve(
        {
            "name": "embeddings",
            "stack": "auxiliary",
            "engine": "embedding",
            "port": 30005,
            "served_name": "embed-local",
        },
        text="probe",
        timeout=12,
        _open=open_request,
    )
    assert result["dimensions"] == 3 and result["vectors"] == 1
    assert seen == {
        "url": "http://127.0.0.1:30005/v1/embeddings",
        "body": {"model": "embed-local", "input": ["probe"]},
        "timeout": 12,
    }


def test_probe_reranker_requires_finite_score_for_every_document():
    def open_request(_request, timeout=0):
        return _JsonResponse({
            "results": [
                {"index": 0, "relevance_score": 0.9},
                {"index": 1, "relevance_score": 0.1},
            ],
        })

    result = serves.probe_serve(
        {
            "name": "reranker",
            "engine": "reranker",
            "port": 30006,
            "served_name": "rerank-local",
        },
        _open=open_request,
    )
    assert result["documents"] == 2
    assert result["top_index"] == 0


def test_probe_ocr_sends_data_uri_but_returns_only_bounded_text(tmp_path):
    image = tmp_path / "probe.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\nprobe")
    seen = {}

    def open_request(request, timeout=0):
        seen["body"] = json.loads(request.data)
        return _JsonResponse({
            "choices": [{"message": {"content": "Anvil Serving Dashboard"}}],
        })

    result = serves.probe_serve(
        {
            "name": "ocr",
            "engine": "vllm",
            "port": 30007,
            "served_name": "ocr-local",
        },
        text="Read the text.",
        image_path=str(image),
        _open=open_request,
    )
    image_url = seen["body"]["messages"][0]["content"][1]["image_url"]["url"]
    assert image_url.startswith("data:image/png;base64,")
    assert result["recognized_excerpt"] == "Anvil Serving Dashboard"
    assert "base64" not in json.dumps(result)


def test_probe_comfyui_validates_system_metadata():
    result = serves.probe_serve(
        {
            "name": "comfyui",
            "stack": "comfyui",
            "engine": "image",
            "port": 8188,
            "model": "comfyui",
            "health": "/system_stats",
        },
        _open=lambda *_args, **_kwargs: _JsonResponse({
            "system": {"os": "posix"},
            "devices": [{"name": "cuda:0"}],
        }),
    )
    assert result["devices"] == 1
    assert result["stack"] == "comfyui"


def test_load_manifest_maps_stack_to_compose_project(tmp_path):
    path = _manifest(tmp_path, """
        [[serve]]
        name = "stt"
        stack = "voice-audio"
        container = "anvil-voice-stt"
        runtime = "docker"
        port = 30010
        model = "tdt_ctc-110m"
        engine = "audio"
        up = "docker compose -f {dir}/voice.yml up -d stt"
    """)
    (serve,) = serves.load_manifest(path)
    assert serve["stack"] == "voice-audio"
    assert serves._expected_compose_project(serve) == "anvil-voice-audio"


def test_load_manifest_rejects_stack_project_disagreement(tmp_path):
    path = _manifest(tmp_path, """
        [[serve]]
        name = "stt"
        stack = "voice-audio"
        container = "anvil-voice-stt"
        runtime = "docker"
        port = 30010
        model = "tdt_ctc-110m"
        engine = "audio"
        up = "docker compose --project-name fakoli-dark -f {dir}/voice.yml up -d stt"
    """)
    with pytest.raises(ValueError, match="stack 'voice-audio'.*anvil-voice-audio"):
        serves.load_manifest(path)


@pytest.mark.parametrize("stack", ["Voice Audio", "voice_audio", "-voice", "voice-"])
def test_load_manifest_rejects_invalid_stack_slug(tmp_path, stack):
    path = _manifest(tmp_path, f"""
        [[serve]]
        name = "stt"
        stack = "{stack}"
        container = "anvil-voice-stt"
        runtime = "docker"
        port = 30010
        model = "tdt_ctc-110m"
        engine = "audio"
    """)
    with pytest.raises(ValueError, match="lowercase slug"):
        serves.load_manifest(path)


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
        runtime = "docker"
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
        runtime = "docker"
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
        runtime = "docker"
        port = 30001
        model = "auxiliary-local"
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
        runtime = "docker"
        port = 30001
        model = "auxiliary-local"
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
        runtime = "docker"
        port = 30001
        model = "auxiliary-local"
        engine = "vllm"
        vram_mib = {vram}
    """)
    with pytest.raises(ValueError, match="vram_mib must be a positive integer"):
        serves.load_manifest(path)


@pytest.mark.parametrize("value", ['"true"', "1", "[]"])
def test_load_manifest_rejects_non_boolean_native_kv_offload(tmp_path, value):
    path = _manifest(tmp_path, f"""
        [[serve]]
        name = "candidate"
        container = "vllm-candidate"
        runtime = "docker"
        port = 30001
        model = "candidate-local"
        engine = "vllm"
        native_kv_offload = {value}
    """)
    with pytest.raises(ValueError, match="native_kv_offload must be a boolean"):
        serves.load_manifest(path)


@pytest.mark.parametrize("missing_field", ["router_config", "rollback_router_config"])
def test_load_manifest_rejects_missing_exclusive_router_dependency(
    tmp_path, missing_field,
):
    (tmp_path / "router-target.toml").write_text("[router]\n", encoding="utf-8")
    (tmp_path / "router-rollback.toml").write_text("[router]\n", encoding="utf-8")
    (tmp_path / ("router-target.toml" if missing_field == "router_config" else
                 "router-rollback.toml")).unlink()
    path = _manifest(tmp_path, """
        [[gpu_roles]]
        id = "dark-compute-a"
        vram_mib = 97887

        [[gpu_roles]]
        id = "dark-compute-b"
        vram_mib = 97887

        [[serve]]
        name = "candidate"
        container = "vllm-candidate"
        runtime = "docker"
        port = 30001
        model = "candidate-local"
        engine = "vllm"
        gpu_roles = ["dark-compute-a", "dark-compute-b"]
        operating_mode = "dual-gpu-exclusive"
        tensor_parallel_size = 2
        router_tier = "primary-local"
        router_config = "{dir}/router-target.toml"
        rollback_router_config = "{dir}/router-rollback.toml"
    """)

    with pytest.raises(ValueError, match=rf"{missing_field} does not exist"):
        serves.load_manifest(path)


@pytest.mark.parametrize("gpu_role", ['""', '"   "', "5"])
def test_load_manifest_rejects_empty_or_non_string_gpu_role(tmp_path, gpu_role):
    path = _manifest(tmp_path, f"""
        [[serve]]
        name = "fast"
        container = "vllm-fast"
        runtime = "docker"
        port = 30001
        model = "auxiliary-local"
        engine = "vllm"
        gpu_role = {gpu_role}
    """)
    with pytest.raises(ValueError, match="gpu_role must be a non-empty string"):
        serves.load_manifest(path)


def test_load_manifest_without_reservation_fields_gets_default_stack(tmp_path):
    """A legacy entry gets the explicit serving-stack ownership default."""
    path = _manifest(tmp_path, """
        [[serve]]
        name = "fast"
        container = "vllm-gptoss"
        runtime = "docker"
        port = 30001
        model = "auxiliary-local"
        engine = "vllm"
        up = "bash {dir}/serve.sh"
    """)
    (s,) = serves.load_manifest(path)
    mdir = os.path.dirname(os.path.abspath(path))
    assert s == {
        "name": "fast",
        "container": "vllm-gptoss",
        "runtime": "docker",
        "port": 30001,
        "model": "auxiliary-local",
            "served_name": "auxiliary-local",
            "engine": "vllm",
            "stack": "serving",
            "_manifest_dir": mdir,
        "health": "/health",
        "up": ["bash", mdir + "/serve.sh"],
        }  # stack is normalized; reservation keys still are not invented


# ---- serve groups: field parse/validation --------------------------------

def test_load_manifest_parses_groups_field(tmp_path):
    path = _manifest(tmp_path, """
        [[serve]]
        name = "fast"
        container = "vllm-fast"
        runtime = "docker"
        port = 30001
        model = "auxiliary-local"
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
        runtime = "docker"
        port = 30001
        model = "auxiliary-local"
        engine = "vllm"
    """)
    (s,) = serves.load_manifest(path)
    assert "groups" not in s


def test_load_manifest_groups_dedupes_preserving_order(tmp_path):
    path = _manifest(tmp_path, """
        [[serve]]
        name = "fast"
        container = "vllm-fast"
        runtime = "docker"
        port = 30001
        model = "auxiliary-local"
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
        runtime = "docker"
        port = 30001
        model = "auxiliary-local"
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
        runtime = "docker"
        port = 30001
        model = "auxiliary-local"
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
        runtime = "docker"
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
        runtime = "docker"
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
        runtime = "docker"
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
    assert {"primary", "omni", "omni-small",
            "fast-devstral-small2-llamacpp"} <= names
    by_name = {s["name"]: s for s in serves_list}
    assert by_name["fast-qwen36-35b-a3b"]["engine"] == "vllm"
    assert by_name["fast-glm47-flash-sglang"]["engine"] == "sglang"
    assert by_name["fast-devstral-small2-llamacpp"]["engine"] == "llamacpp"


def test_shipped_fakoli_manifest_purpose_model_serves():
    # gpu-reservations:T009 — the embeddings/reranker serves are resident
    # ADR-0017 reservations on Compute B with truthful engine labels.
    serves_list = serves.load_manifest(serves.EXAMPLE_MANIFEST)
    by_name = {s["name"]: s for s in serves_list}
    emb, rr = by_name["embeddings"], by_name["reranker"]
    assert emb["engine"] == "embedding"
    assert rr["engine"] == "reranker"
    for s in (emb, rr):
        assert s["gpu_role"] == "dark-compute-b"
        assert s["residency"] == "resident"
        assert s["health"] == "/health"
    # HONEST-MEASURED budgets (see the manifest comments): weights + the fixed
    # vLLM/WSL2 runtime floor + the serve's KV window.
    assert emb["vram_mib"] == 3200
    assert rr["vram_mib"] == 3456
    assert emb["port"] == 30005 and emb["model"] == "qwen3-embedding-0.6b"
    assert rr["port"] == 30006 and rr["model"] == "qwen3-reranker-0.6b"
    # The purpose-model stack must fit without the mutually exclusive Omni tier.
    budget = reservations.budgets_of(serves_list)["dark-compute-b"].budget_mib
    purpose_stack = emb["vram_mib"] + rr["vram_mib"]
    assert purpose_stack <= budget, (purpose_stack, budget)


def test_shipped_fakoli_manifest_omni_serve():
    # The large evictable Omni tier replaces the old auxiliary, OCR, and vision
    # containers. Compute B now has enough capacity for it and voice sidecars.
    serves_list = serves.load_manifest(serves.EXAMPLE_MANIFEST)
    by_name = {s["name"]: s for s in serves_list}
    omni = by_name["omni"]
    assert omni["engine"] == "vllm"
    assert omni["gpu_role"] == "dark-compute-b"
    assert omni["residency"] == "evictable"
    assert omni["health"] == "/health"
    assert omni["router_tier"] == "omni-local"
    assert omni["vram_mib"] == 27999
    assert omni["port"] == 30003
    assert omni["model"] == "nemotron3-omni-30b-a3b-nvfp4"
    budget = reservations.budgets_of(serves_list)["dark-compute-b"].budget_mib
    assert omni["vram_mib"] <= budget
    assert omni["vram_mib"] + 4096 <= budget


def test_shipped_fakoli_manifest_small_omni_fits_with_voice():
    serves_set = serves.load_manifest_set(serves.EXAMPLE_MANIFEST)
    by_name = {s["name"]: s for s in serves_set}
    small = by_name["omni-small"]
    assert small["engine"] == "vllm"
    assert small["gpu_role"] == "dark-compute-b"
    assert small["residency"] == "evictable"
    assert small["vram_mib"] == 24576
    assert small["port"] == 30013
    assert small["model"] == "qwen25-omni-3b"
    budget = reservations.budgets_of(serves_set)["dark-compute-b"].budget_mib
    voice = by_name["stt"]["vram_mib"] + by_name["tts"]["vram_mib"]
    assert small["vram_mib"] + voice <= budget


def test_load_manifest_accepts_image_engine(tmp_path):
    # gpu-reservations:T012 — "image" extends the truthful non-chat-LLM engine
    # vocabulary (audio/embedding/reranker precedent) for the ComfyUI tenant.
    path = _manifest(tmp_path, """
        [[serve]]
        name = "comfyui"
        container = "comfyui"
        runtime = "docker"
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
    assert comfyui["gpu_role"] == "dark-compute-b"
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
    # The comfyui manifest re-declares the dark-compute-b ledger (capacity row +
    # reservation mirrors) because ADR-0017 ledgers are derived per manifest.
    # This pin turns the KEEP IN SYNC comment into a checked invariant: a
    # rebalance of serves.toml that forgets the mirrors fails here instead of
    # silently letting `serves up comfyui` admit against stale budgets.
    main_list = serves.load_manifest(serves.EXAMPLE_MANIFEST)
    main = {s["name"]: s for s in main_list}
    comfy_list = serves.load_manifest(COMFYUI_MANIFEST)
    comfy = {s["name"]: s for s in comfy_list}
    main_budget = reservations.budgets_of(main_list)["dark-compute-b"]
    comfy_budget = reservations.budgets_of(comfy_list)["dark-compute-b"]
    assert (comfy_budget.vram_mib, comfy_budget.reserve_mib) == (
        main_budget.vram_mib, main_budget.reserve_mib)
    mirrors = [n for n in comfy if n != "comfyui"]
    # Every serves.toml dark-compute-b reservation must be mirrored — a missing
    # mirror makes comfyui admission blind to that serve's committed VRAM.
    main_reserved = {
        n for n, s in main.items()
        if s.get("gpu_role") == "dark-compute-b"
        and isinstance(s.get("vram_mib"), int)
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
        runtime = "docker"
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
            runtime = "docker"
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

def test_default_manifest_searches_config_home_then_cwd():
    assert serves.DEFAULT_MANIFEST == "./serves.toml"
    candidates = serves.default_manifest_candidates()
    assert candidates[0].endswith(os.path.join(".anvil-serving", "serves.toml"))
    assert candidates[1] == "./serves.toml"
    assert serves.EXAMPLE_MANIFEST.endswith(os.path.join("examples", "fakoli-dark", "serves.toml"))


def test_resolve_manifest_path_prefers_config_home(tmp_path, monkeypatch):
    home = tmp_path / "operator-home"
    home.mkdir()
    manifest = home / "serves.toml"
    manifest.write_text("[[serve]]\nname='x'\ncontainer='x'\nport=1\nmodel='x'\nengine='vllm'\n", encoding="utf-8")
    (tmp_path / "serves.toml").write_text("ignored", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ANVIL_SERVING_HOME", str(home))
    assert serves.resolve_manifest_path() == str(manifest)


def test_resolve_manifest_path_uses_config_home_when_cwd_missing(tmp_path, monkeypatch):
    config_home = tmp_path / "anvil-serving"
    config_home.mkdir()
    manifest = config_home / "serves.toml"
    manifest.write_text("[[serve]]\nname='x'\ncontainer='x'\nport=1\nmodel='x'\nengine='vllm'\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ANVIL_SERVING_HOME", str(config_home))
    assert serves.resolve_manifest_path() == str(manifest)


def test_recipe_registry_prefers_config_home_before_checkout_configs(tmp_path, monkeypatch):
    home = tmp_path / "operator-home"
    home.mkdir()
    home_registry = home / "serve-recipes.toml"
    home_registry.write_text("schema = 'x'\n", encoding="utf-8")
    checkout_registry = tmp_path / "configs" / "serve-recipes.toml"
    checkout_registry.parent.mkdir()
    checkout_registry.write_text("schema = 'x'\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ANVIL_SERVING_HOME", str(home))

    assert serves.resolve_recipe_registry_path() == str(home_registry)


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


def test_docker_states_batches_names_and_marks_missing_absent():
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        return proc(0, '\n'.join([
            '{"Names":"running-one","State":"running"}',
            '{"Names":"stopped-one","State":"exited"}',
        ]))

    assert serves.docker_states(
        ["running-one", "stopped-one", "missing-one"], _run=run
    ) == {
        "running-one": "running",
        "stopped-one": "exited",
        "missing-one": "absent",
    }
    assert calls == [["docker", "ps", "-a", "--format", "{{json .}}"]]


@pytest.mark.parametrize("result", [
    proc(1, "", "daemon unavailable"),
    proc(0, "not-json", ""),
])
def test_docker_states_fails_closed_on_unreliable_inventory(result):
    assert serves.docker_states(
        ["one", "two"], _run=lambda *args, **kwargs: result
    ) == {"one": "error", "two": "error"}


# ---- down -------------------------------------------------------------------

def test_cmd_down_stops_running():
    serv = [{"name": "h", "container": "sglang", "port": 1, "health": "/health"}]
    run = _inspect_returning("running")
    assert serves.cmd_down(serv, [], _run=run) == 0
    assert ["docker", "stop", "sglang"] in run.calls
    assert ["docker", "rm", "-f", "sglang"] in run.calls


def test_cmd_down_stops_paused_container_too():
    # a PAUSED container still holds 100% of its VRAM — `down` must stop it.
    serv = [{"name": "f", "container": "vllm", "port": 1, "health": "/health"}]
    run = _inspect_returning("paused")
    assert serves.cmd_down(serv, [], _run=run) == 0
    assert ["docker", "stop", "vllm"] in run.calls
    assert ["docker", "rm", "-f", "vllm"] in run.calls


def test_cmd_down_removes_already_stopped():
    serv = [{"name": "f", "container": "vllm", "port": 1, "health": "/health"}]
    run = _inspect_returning("exited")
    assert serves.cmd_down(serv, [], _run=run) == 0
    assert not any(c[:2] == ["docker", "stop"] for c in run.calls)
    assert ["docker", "rm", "-f", "vllm"] in run.calls


def test_cmd_down_keep_container_preserves_stopped_container():
    serv = [{"name": "f", "container": "vllm", "port": 1, "health": "/health"}]
    run = _inspect_returning("exited")
    assert serves.cmd_down(serv, [], keep_container=True, _run=run) == 0
    assert not any(c[:2] == ["docker", "stop"] for c in run.calls)
    assert not any(c[:2] == ["docker", "rm"] for c in run.calls)


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
    assert serves.cmd_down(serv, [], keep_container=True, _run=run) == 1


def test_cmd_down_reports_stop_failure():
    serv = [{"name": "h", "container": "sglang", "port": 1, "health": "/health"}]
    run = _inspect_returning("running", stop_rc=1, stop_err="boom")
    assert serves.cmd_down(serv, [], _run=run) == 1


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


def test_cmd_down_force_remove_skips_graceful_stop():
    serv = [{"name": "h", "container": "sglang", "port": 1, "health": "/health"}]
    run = _inspect_returning("running")

    assert serves.cmd_down(serv, [], force_remove=True, _run=run) == 0
    assert ["docker", "stop", "sglang"] not in run.calls
    assert ["docker", "rm", "-f", "sglang"] in run.calls


def test_cmd_down_reclaims_native_offload_mmap_after_force_remove(monkeypatch):
    serv = [{"name": "h", "container": "deepseek", "port": 1, "health": "/health"}]
    run = _inspect_returning("running")
    events = []
    monkeypatch.setattr(
        serves.host_ops, "container_uses_native_kv_offload",
        lambda container, **_kwargs: events.append(("detect", container)) or True,
    )
    monkeypatch.setattr(
        serves.host_ops, "prepare_native_kv_offload_shared_memory",
        lambda **_kwargs: events.append(("reclaim", list(run.calls)))
        or {"outcome": "reclaimed"},
    )
    monkeypatch.setattr(
        serves.host_ops, "render_vllm_offload_shared_memory",
        lambda result: events.append(("render", result["outcome"])),
    )

    assert serves.cmd_down(serv, [], force_remove=True, _run=run) == 0
    reclaim_calls = next(event[1] for event in events if event[0] == "reclaim")
    assert ["docker", "rm", "-f", "deepseek"] in reclaim_calls
    assert events[-1] == ("render", "reclaimed")


def test_cmd_down_preserved_stopped_native_offload_container_still_cleans_mmap(
    monkeypatch,
):
    serv = [{"name": "h", "container": "deepseek", "port": 1, "health": "/health"}]
    run = _inspect_returning("exited")
    reclaimed = []
    monkeypatch.setattr(
        serves.host_ops, "container_uses_native_kv_offload",
        lambda *_args, **_kwargs: True,
    )
    monkeypatch.setattr(
        serves.host_ops, "prepare_native_kv_offload_shared_memory",
        lambda **_kwargs: reclaimed.append(True) or {"outcome": "clean"},
    )
    monkeypatch.setattr(
        serves.host_ops, "render_vllm_offload_shared_memory", lambda _result: None,
    )

    assert serves.cmd_down(serv, [], keep_container=True, _run=run) == 0
    assert reclaimed == [True]
    assert ["docker", "rm", "-f", "deepseek"] not in run.calls


def test_cmd_down_reports_native_offload_cleanup_failure(monkeypatch):
    serv = [{"name": "h", "container": "deepseek", "port": 1, "health": "/health"}]
    run = _inspect_returning("running")
    monkeypatch.setattr(
        serves.host_ops, "container_uses_native_kv_offload",
        lambda *_args, **_kwargs: True,
    )
    monkeypatch.setattr(
        serves.host_ops, "prepare_native_kv_offload_shared_memory",
        lambda **_kwargs: {"outcome": "unavailable"},
    )
    monkeypatch.setattr(
        serves.host_ops, "render_vllm_offload_shared_memory", lambda _result: None,
    )

    assert serves.cmd_down(serv, [], force_remove=True, _run=run) == 1


def test_cmd_down_absent_declared_native_offload_reclaims_orphan_mmap(monkeypatch):
    serv = [{
        "name": "h", "container": "deepseek", "port": 1, "health": "/health",
        "native_kv_offload": True,
    }]
    run = _inspect_returning("absent")
    events = []
    monkeypatch.setattr(
        serves.host_ops, "container_uses_native_kv_offload",
        lambda *_args, **_kwargs: pytest.fail("absent container metadata was queried"),
    )
    monkeypatch.setattr(
        serves.host_ops, "prepare_native_kv_offload_shared_memory",
        lambda **_kwargs: events.append("reclaim") or {"outcome": "reclaimed"},
    )
    monkeypatch.setattr(
        serves.host_ops, "render_vllm_offload_shared_memory",
        lambda result: events.append(("render", result["outcome"])),
    )

    assert serves.cmd_down(serv, [], _run=run) == 0
    assert events == ["reclaim", ("render", "reclaimed")]


def test_cmd_down_absent_ordinary_serve_does_not_scan_shared_memory(monkeypatch):
    serv = [{"name": "h", "container": "ordinary", "port": 1, "health": "/health"}]
    run = _inspect_returning("absent")
    monkeypatch.setattr(
        serves.host_ops, "prepare_native_kv_offload_shared_memory",
        lambda **_kwargs: pytest.fail("ordinary absent serve attempted shared-memory cleanup"),
    )

    assert serves.cmd_down(serv, [], _run=run) == 0


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


def test_cmd_down_absent_declared_native_offload_cleanup_failure(monkeypatch):
    serv = [{
        "name": "h", "container": "deepseek", "port": 1, "health": "/health",
        "native_kv_offload": True,
    }]
    run = _inspect_returning("absent")
    monkeypatch.setattr(
        serves.host_ops, "prepare_native_kv_offload_shared_memory",
        lambda **_kwargs: {"outcome": "changed"},
    )
    monkeypatch.setattr(
        serves.host_ops, "render_vllm_offload_shared_memory", lambda _result: None,
    )

    assert serves.cmd_down(serv, [], _run=run) == 1


def test_cmd_down_reports_remove_failure_after_stop():
    serv = [{"name": "h", "container": "sglang", "port": 1, "health": "/health"}]
    run = _inspect_returning("running", remove_rc=1, remove_err="still referenced")
    assert serves.cmd_down(serv, [], _run=run) == 1
    assert ["docker", "stop", "sglang"] in run.calls
    assert ["docker", "rm", "-f", "sglang"] in run.calls


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
    assert all(c[:2] == ["docker", "inspect"] or c[:3] == ["docker", "ps", "-a"]
               for c in run.calls)  # only inventoried/inspected


def test_cmd_up_error_state_does_not_create():
    serv = [{"name": "f", "container": "vllm", "port": 1, "health": "/health", "up": ["bash", "x.sh"]}]
    run = _inspect_returning("error")
    assert serves.cmd_up(serv, [], _run=run) == 1
    assert all(c[:2] == ["docker", "inspect"] or c[:3] == ["docker", "ps", "-a"]
               for c in run.calls)


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


def test_compose_up_gets_stable_product_project():
    original = ["docker", "compose", "-f", "x.yml", "up", "-d", "heavy"]
    assert serves._compose_up_with_project(original) == [
        "docker", "compose", "--project-name", "anvil-serving",
        "-f", "x.yml", "up", "-d", "heavy",
    ]
    explicit = ["docker", "compose", "-p", "custom", "up", "-d"]
    assert serves._compose_up_with_project(explicit) == explicit
    assert serves._compose_up_with_project(original, "anvil-auxiliary") == [
        "docker", "compose", "--project-name", "anvil-auxiliary",
        "-f", "x.yml", "up", "-d", "heavy",
    ]


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


def test_cmd_up_waits_for_declared_health_and_fails_closed(capsys):
    serve = [{
        "name": "embed",
        "container": "embed",
        "port": 30003,
        "health": "/health",
        "up": ["bash", "start.sh"],
    }]
    run = _inspect_returning("exited")

    def unavailable(_request, timeout=0):
        raise OSError("not ready")

    assert serves.cmd_up(
        serve,
        ["embed"],
        _run=run,
        wait_for_readiness=True,
        readiness_timeout=0,
        _open=unavailable,
        _sleep=lambda _seconds: None,
    ) == 1
    assert "did not become ready" in capsys.readouterr().out


def test_cmd_up_compose_serve_runs_compose_up_not_docker_start():
    # THE fix: an existing (stopped) compose serve is brought up with `docker compose
    # up -d` — which natively recreates on config drift — NOT a blind `docker start`
    # that would resurrect its stale model.
    serv = [{"name": "heavy", "container": "sglang", "port": 1, "health": "/health",
             "model": "qwen35-awq-local",
             "up": ["docker", "compose", "-f", "/x/docker-compose.yml", "up", "-d"]}]
    run = _inspect_returning("exited")
    assert serves.cmd_up(serv, [], _run=run) == 0
    assert [
        "docker", "compose", "--project-name", "anvil-serving",
        "-f", "/x/docker-compose.yml", "up", "-d",
    ] in run.calls
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
    assert [
        "docker", "compose", "--project-name", "anvil-serving",
        "-f", "/x/docker-compose.yml", "up", "-d",
    ] in run.calls
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
    assert [
        "docker", "compose", "--project-name", "anvil-serving", "up", "-d",
    ] in run.calls
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
    assert [
        "docker", "compose", "--project-name", "anvil-serving", "up", "-d",
    ] in run.calls          # the `up` ran
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
        name = "heavy"
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
        target = "heavy"
        rollback = "old-heavy"
        affected_tiers = ["primary-local"]
        router_config = "{dir}/router-promoted.toml"
        rollback_router_config = "{dir}/router-rollback.toml"
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


def test_load_promotions_resolves_direct_router_configs(tmp_path):
    path = _promotion_manifest(tmp_path)
    (plan,) = serves.load_promotions(path)
    assert plan["name"] == "heavy-v2"
    assert plan["target"] == "heavy"
    assert plan["rollback"] == "old-heavy"
    assert plan["affected_tiers"] == ["primary-local"]
    assert plan["router_config"] == str(tmp_path / "router-promoted.toml")
    assert plan["rollback_router_config"] == str(tmp_path / "router-rollback.toml")
    assert [gate["name"] for gate in plan["gate"]] == ["functional", "quality"]
    assert plan["gate"][1]["reasoning_headroom_tokens"] == 4096
    assert plan["rollback_gate"][0]["thinking_mode"] == "unsupported"


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
    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs.get("input")))
        if argv[:4] == ["docker", "inspect", "-f", "{{.Config.Image}}"]:
            return proc(0, "anvil-serving:test\n")
        return proc()

    assert serves._install_router_config(str(config), _run=run) == 0
    assert calls[0][0][:3] == ["docker", "exec", "-i"]
    assert any(
        call[0][:3] == ["docker", "run", "--rm"]
        and "config.toml.new" in call[0][-1]
        for call in calls
    )
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
    assert "start heavy" in out
    assert "eval preflight --tier heavy" in out
    assert "gate functional" in out
    assert "gate quality" in out
    assert "--thinking-mode enabled" in out
    assert "--reasoning-headroom-tokens 4096" in out
    assert "atomically install" in out


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
    monkeypatch.setattr(serves, "_install_router_config", lambda *a, **k: 0)
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


def test_safe_promotion_orders_quiesce_drain_config_restart_before_readiness(
    tmp_path, monkeypatch
):
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
    post_restart = next(i for i, call in enumerate(calls) if "transition-status" in call)
    assert quiesce < drain < first_stop < post_restart
    assert applied == [str(tmp_path / "router-promoted.toml")]
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
        runtime = "docker"
        port = 30003
        model = "auxiliary-local"
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


def test_operating_mode_reports_unreachable_docker_as_unresolved(tmp_path):
    loaded = serves.load_manifest(_manifest(tmp_path, """
        [[gpu_roles]]
        id = "dark-compute-a"
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
    """))
    summary = serves.operating_mode_summary(loaded, lambda _container: "error")
    assert summary["mode"] == "unresolved"
    assert summary["unresolved"] == [{"serve": "split-a", "state": "error"}]


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
        runtime = "docker"
        port = 30003
        model = "auxiliary-local"
        engine = "vllm"
        groups = ["production"]
    """))
    run = _status_states_run({"vllm-fast": "running"})
    data = serves.status_summary(loaded, _run=run, _open=_open_down)
    assert data["reservations"] == {"gpu_roles": []}
    inspects = [c for c in run.calls if c[:2] == ["docker", "inspect"]]
    assert len(inspects) == 1


def test_status_summary_skips_untagged_candidate_until_named(tmp_path):
    loaded = serves.load_manifest(_manifest(tmp_path, """
        [[serve]]
        name = "primary"
        container = "vllm-primary"
        runtime = "docker"
        port = 30002
        model = "primary-local"
        engine = "vllm"
        groups = ["llm-stack"]

        [[serve]]
        name = "candidate"
        container = "vllm-candidate"
        runtime = "docker"
        port = 39002
        model = "candidate-local"
        engine = "vllm"
    """))
    run = _status_states_run({
        "vllm-primary": "running",
        "vllm-candidate": "exited",
    })

    default = serves.status_summary(loaded, _run=run, _open=_open_down)
    assert default["selected"] == ["primary"]
    inspected = [call[-1] for call in run.calls if call[:2] == ["docker", "inspect"]]
    assert inspected == ["vllm-primary"]

    explicit = serves.status_summary(
        loaded, ["candidate"], _run=run, _open=_open_down
    )
    assert explicit["selected"] == ["candidate"]
    inspected = [call[-1] for call in run.calls if call[:2] == ["docker", "inspect"]]
    assert inspected[-1] == "vllm-candidate"


def test_main_status_positional_name_opts_candidate_in(tmp_path, monkeypatch):
    manifest = _manifest(tmp_path, """
        [[serve]]
        name = "primary"
        container = "vllm-primary"
        runtime = "docker"
        port = 30002
        model = "primary-local"
        groups = ["llm-stack"]

        [[serve]]
        name = "candidate"
        container = "vllm-candidate"
        runtime = "docker"
        port = 39002
        model = "candidate-local"
    """)
    received = {}

    def status(loaded, names=None, **kwargs):
        received["names"] = names
        received["ledger"] = [serve["name"] for serve in kwargs["ledger_serves"]]
        return 0

    monkeypatch.setattr(serves, "cmd_status", status)

    assert serves.main(["status", "candidate", "--manifest", manifest]) == 0
    assert received == {
        "names": ["candidate"],
        "ledger": ["primary", "candidate"],
    }


def test_main_status_rejects_unknown_opt_in_before_polling(
    tmp_path, monkeypatch, capsys
):
    manifest = _manifest(tmp_path, """
        [[serve]]
        name = "primary"
        container = "vllm-primary"
        runtime = "docker"
        port = 30002
        model = "primary-local"
        groups = ["llm-stack"]
    """)
    monkeypatch.setattr(
        serves,
        "cmd_status",
        lambda *_args, **_kwargs: pytest.fail("unknown serve reached polling"),
    )

    assert serves.main(["status", "missing", "--manifest", manifest]) == 2
    assert "unknown serve(s): missing" in capsys.readouterr().err


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


@pytest.mark.parametrize(("action", "expected_operation"), [
    (["up", "heavy"], "serves up"),
    (["adopt", "heavy", "--yes"], "serves adopt"),
])
def test_public_up_and_adopt_wait_then_reclaim_exactly_once(
        tmp_path, monkeypatch, action, expected_operation):
    manifest = _lifecycle_manifest(tmp_path)
    policy = _enabled_cache_policy()
    before = {"cached_gb": 10.0}
    events = []
    monkeypatch.setattr(serves.host_ops, "load_cache_reclaim_policy", lambda: policy)
    monkeypatch.setattr(
        serves.host_ops, "capture_cache_before",
        lambda resolved: events.append(("capture", resolved)) or before,
    )
    monkeypatch.setattr(serves, "ensure_router_healthy", lambda **_kwargs: 0)
    if action[0] == "up":
        monkeypatch.setattr(serves, "cmd_up", lambda *_args, **_kwargs: events.append("up") or 0)
    else:
        monkeypatch.setattr(
            serves, "cmd_adopt", lambda *_args, **_kwargs: events.append("adopt") or 0
        )
    monkeypatch.setattr(
        serves, "_await_cache_reclaim_targets",
        lambda targets: events.append(("readiness", [item["name"] for item in targets])) or True,
    )
    monkeypatch.setattr(
        serves.host_ops, "automatic_cache_reclaim",
        lambda resolved, baseline, **kwargs: events.append(
            ("reclaim", resolved, baseline, kwargs)
        ) or {"outcome": "reclaimed"},
    )
    monkeypatch.setattr(
        serves.host_ops, "render_cache_reclaim_result",
        lambda result: events.append(("render", result)),
    )
    assert serves.main([*action, "--manifest", manifest]) == 0
    labels = [event if isinstance(event, str) else event[0] for event in events]
    assert labels.count("reclaim") == 1
    assert labels == ["capture", action[0], "readiness", "reclaim", "render"]
    reclaim = next(event for event in events if not isinstance(event, str) and event[0] == "reclaim")
    assert reclaim[3] == {"operation": expected_operation, "readiness": True}


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
    (["promote", "heavy-v2"], "serves promote"),
    (["promote", "heavy-v2", "--rollback"], "serves promote --rollback"),
    (["switch", "heavy", "org/model"], "serves switch"),
])
def test_switch_and_promote_reclaim_once_without_a_second_readiness_wait(
        tmp_path, monkeypatch, argv, operation):
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
    # This test is about the cache-reclaim postcondition, not the preflight
    # gate (which would otherwise choke on the deliberately incomplete fake
    # promotion plan above); the gate itself has its own dedicated tests.
    monkeypatch.setattr(serves, "_preflight_gate", lambda *_args, **_kwargs: True)
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


def test_invalid_policy_refuses_before_router_or_container_mutation(
        tmp_path, monkeypatch, capsys):
    manifest = _lifecycle_manifest(tmp_path)
    config_home = tmp_path / "config"
    config_home.mkdir()
    (config_home / "host.toml").write_text(
        "schema_version = 1\n[cache_reclaim]\nunknown = true\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("ANVIL_SERVING_HOME", str(config_home))
    monkeypatch.setattr(
        serves, "ensure_router_healthy",
        lambda **_kwargs: pytest.fail("router mutated before host policy validation"),
    )
    monkeypatch.setattr(
        serves, "cmd_up", lambda *_args, **_kwargs: pytest.fail("container mutated")
    )
    assert serves.main(["up", "heavy", "--manifest", manifest]) == 2
    assert "unknown field" in capsys.readouterr().err


def test_ad_hoc_compose_is_excluded_from_automatic_reclaim(monkeypatch):
    monkeypatch.setattr(
        serves.host_ops, "load_cache_reclaim_policy",
        lambda: pytest.fail("ad-hoc Compose loaded the automatic reclaim policy"),
    )
    monkeypatch.setattr(serves, "ensure_router_healthy", lambda **_kwargs: 0)
    monkeypatch.setattr(serves, "cmd_up_compose", lambda *_args, **_kwargs: 0)
    assert serves.main(["up", "--compose", "experiment.yml", "model"]) == 0


def test_reclaim_failure_cannot_change_successful_parent_exit(monkeypatch):
    policy = _enabled_cache_policy()
    monkeypatch.setattr(
        serves.host_ops, "automatic_cache_reclaim",
        lambda *_args, **_kwargs: {"outcome": "failed"},
    )
    rendered = []
    monkeypatch.setattr(
        serves.host_ops, "render_cache_reclaim_result",
        lambda result: rendered.append(result),
    )
    assert serves._finish_cache_reclaim(
        0, policy, {"cached_gb": 10.0}, "serves switch"
    ) == 0
    assert rendered == [{"outcome": "failed"}]
