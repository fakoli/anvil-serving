"""Tests for `anvil_serving.serve_recipes` — the shared GENERATE + READ helpers.

Every test is HERMETIC: `docker inspect` / `nvidia-smi` are injected as FAKE `_run`
callables, so CI never touches real docker, a real GPU, or the network. The write path
is proven round-trip-safe by parsing its output back through `tomllib`.
"""
import json
from datetime import date
import tomllib
from types import SimpleNamespace

import pytest

from anvil_serving import serve_recipes as sr

# A recipe exercising every value kind: str, int, float, bool, arrays, nested tables.
_RECIPE = {
    "model": "openai/gpt-oss-120b",
    "status": "verified",
    "source": 'measured on fakoli-dark — preflight "ALL PASS"\ttab\\backslash',
    "total_params_b": 116.8,
    "active_params_b": 5.1,
    "hardware": {
        "gpu": "NVIDIA RTX PRO 6000 Blackwell Max-Q",
        "gpu_uuid": "GPU-d0f446cf-1771-414c-e116-a39138798a8c",
        "vram_total_gb": 96,
    },
    "serve": {
        "engine": "vllm",
        "image": "vllm/vllm-openai:nightly",
        "port": 30002,
        "context_tokens": 131072,
        "streaming": True,
        "env": [
            "FLASHINFER_CUDA_ARCH_LIST=12.0f",
            "VLLM_USE_V2_MODEL_RUNNER=0",
            "CUDA_DEVICE_ORDER=PCI_BUS_ID",
        ],
        "flags": [
            "--kv-cache-dtype fp8",
            "--tool-call-parser openai",
            "--enable-auto-tool-choice",
            "--gpu-memory-utilization 0.88",
            "--max-model-len 131072",
        ],
    },
    "measured": {"throughput_single_tok_s": 183.2},
    "intent": {
        "suited": ["flexibility", "quality"],
        "not_suited": ["agentic-interactive"],
        "mode": "flexibility",
    },
    "download": {"note": "single copy", "command": "anvil-serving models pull openai/gpt-oss-120b"},
}


# ---- WRITE: format_recipe round-trips through tomllib -------------------------------

def test_format_recipe_round_trips_through_tomllib():
    block = sr.format_recipe(_RECIPE)
    parsed = tomllib.loads("schema='x'\n" + block)
    assert parsed["schema"] == "x"
    assert parsed["recipe"] == [_RECIPE]  # exact round-trip, types preserved


def test_recipe_rejects_ignored_serve_args_with_flags_recovery():
    recipe = {"model": "org/model", "serve": {"image": "example/image", "args": ["--x"]}}
    with pytest.raises(sr.RecipeError, match=r"serve\.flags"):
        sr.validate_recipe(recipe)


def test_format_recipe_emits_recipe_array_header_and_nested_tables():
    block = sr.format_recipe(_RECIPE)
    assert block.startswith("[[recipe]]\n")
    assert "[recipe.hardware]" in block
    assert "[recipe.serve]" in block
    assert "[recipe.intent]" in block
    # bool stays a TOML bool (not the Python "True"); float keeps its point.
    assert "streaming = true" in block
    assert "total_params_b = 116.8" in block


def test_format_recipe_preserves_int_vs_float():
    r = {"model": "m", "a_int": 32, "a_float": 32.0}
    parsed = tomllib.loads("s='x'\n" + sr.format_recipe(r))["recipe"][0]
    assert isinstance(parsed["a_int"], int) and parsed["a_int"] == 32
    assert isinstance(parsed["a_float"], float) and parsed["a_float"] == 32.0


def test_format_recipe_escapes_control_and_quote_chars():
    r = {"model": "m", "s": 'a"b\\c\nd\te'}
    parsed = tomllib.loads("s='x'\n" + sr.format_recipe(r))["recipe"][0]
    assert parsed["s"] == 'a"b\\c\nd\te'


def test_append_recipe_stays_parseable(tmp_path):
    reg = tmp_path / "serve-recipes.toml"
    reg.write_text('schema = "v1"\n', encoding="utf-8")
    sr.append_recipe(str(reg), {"model": "a/one", "status": "verified"})
    sr.append_recipe(str(reg), _RECIPE)
    data = tomllib.loads(reg.read_text(encoding="utf-8"))
    assert data["schema"] == "v1"
    assert [r["model"] for r in data["recipe"]] == ["a/one", "openai/gpt-oss-120b"]
    assert data["recipe"][1] == _RECIPE


def test_registry_lock_refuses_concurrent_writer(tmp_path):
    path = tmp_path / "serve-recipes.toml"
    with sr.registry_lock(path):
        with pytest.raises(sr.RecipeError, match="another process"):
            with sr.registry_lock(path):
                pass


def test_registry_writer_quotes_non_bare_keys_and_rejects_unsupported_scalars():
    registry = {
        "schema": sr.REGISTRY_SCHEMA,
        "recipe": [{"model": "org/model", "custom key": {"value.with.dot": "ok"}}],
    }
    rendered = sr.format_registry(registry)
    assert tomllib.loads(rendered) == registry

    with pytest.raises(sr.RecipeError, match="unsupported TOML scalar type"):
        sr.format_registry({"recipe": [{"model": "org/model", "published": date.today()}]})


def test_mutable_registry_create_update_delete_and_atomic_write(tmp_path):
    path = tmp_path / "serve-recipes.toml"
    registry = {"schema": sr.REGISTRY_SCHEMA, "recipe": []}
    created = sr.create_recipe(registry, {"model": "org/one", "status": "unverified"})
    sr.write_registry(path, created)
    loaded = sr.load_registry(path)
    assert loaded["recipe"][0]["model"] == "org/one"

    updated, previous = sr.update_recipe(
        loaded,
        "one",
        {"model": "org/two", "status": "verified", "serve": {"image": "example/image"}},
    )
    assert previous["model"] == "org/one"
    remaining, deleted = sr.delete_recipe(updated, "org/two")
    assert deleted["status"] == "verified"
    assert remaining["recipe"] == []


def test_create_recipe_rejects_duplicate_or_ambiguous_selectors():
    registry = {
        "recipe": [
            {"model": "org-a/model", "status": "verified"},
            {"model": "org-b/model", "status": "verified"},
        ]
    }
    with pytest.raises(sr.RecipeError, match="ambiguous"):
        sr.find_recipe_index(registry, "model")
    with pytest.raises(sr.RecipeError, match="already exists"):
        sr.create_recipe(registry, {"model": "org-a/model"})


def test_load_recipe_file_requires_one_recipe(tmp_path):
    path = tmp_path / "recipes.toml"
    path.write_text('schema = "x"\n', encoding="utf-8")
    with pytest.raises(sr.RecipeError, match="exactly one"):
        sr.load_recipe_file(path)


def test_docker_run_argv_uses_named_container_and_loopback_port():
    argv = sr.docker_run_argv(_RECIPE, container="heavy-candidate")
    assert argv[:6] == ["docker", "run", "-d", "--name", "heavy-candidate", "--gpus"]
    assert ["-p", "127.0.0.1:30002:30002"] == argv[argv.index("-p"):argv.index("-p") + 2]
    assert argv[argv.index("vllm/vllm-openai:nightly") + 1] == "openai/gpt-oss-120b"


def test_load_recipe_runs_once_with_argv_seam():
    calls = []

    def fake_run(argv, **kwargs):
        calls.append((argv, kwargs))
        return SimpleNamespace(returncode=0)

    argv, rc = sr.load_recipe(_RECIPE, "heavy-candidate", _run=fake_run)
    assert rc == 0
    assert calls == [(argv, {"check": False})]


def test_docker_run_argv_refuses_unsafe_container_and_env():
    with pytest.raises(sr.RecipeError, match="container name"):
        sr.docker_run_argv(_RECIPE, container="bad name")
    unsafe = {**_RECIPE, "serve": {**_RECIPE["serve"], "env": ["BAD\nVALUE=1"]}}
    with pytest.raises(sr.RecipeError, match="NAME=value"):
        sr.docker_run_argv(unsafe, container="safe")
    option_image = {**_RECIPE, "serve": {**_RECIPE["serve"], "image": "--privileged"}}
    with pytest.raises(sr.RecipeError, match="not an option"):
        sr.docker_run_argv(option_image, container="safe")


# ---- READ: find_recipe (exact + basename) ------------------------------------------

def test_find_recipe_exact_and_basename():
    registry = {"recipe": [_RECIPE]}
    assert sr.find_recipe(registry, "openai/gpt-oss-120b") is _RECIPE
    assert sr.find_recipe(registry, "gpt-oss-120b") is _RECIPE  # basename match
    assert sr.find_recipe(registry, "no-such-model") is None


# ---- READ: reconstruct_docker_run --------------------------------------------------

def test_reconstruct_docker_run_model_is_positional_after_image():
    cmd = sr.reconstruct_docker_run(_RECIPE)
    img_i = cmd.index("vllm/vllm-openai:nightly")
    model_i = cmd.index("openai/gpt-oss-120b")
    assert img_i < model_i, "model must be a POSITIONAL after the image"
    # no accidental extra 'serve' verb (entrypoint already is `vllm serve`).
    assert " serve " not in cmd


def test_reconstruct_docker_run_includes_env_volume_and_flags():
    cmd = sr.reconstruct_docker_run(_RECIPE)
    assert "-e FLASHINFER_CUDA_ARCH_LIST=12.0f" in cmd
    assert "-v vllm-hfcache:/root/.cache/huggingface" in cmd
    assert "--gpus device=GPU-d0f446cf-1771-414c-e116-a39138798a8c" in cmd
    assert "-p 127.0.0.1:30002:30002" in cmd
    for flag in _RECIPE["serve"]["flags"]:
        assert flag in cmd


def test_reconstruct_docker_run_falls_back_without_gpu_uuid_or_port():
    minimal = {"model": "m/x", "serve": {"image": "img", "flags": ["--foo bar"]}}
    cmd = sr.reconstruct_docker_run(minimal)
    assert "--gpus all" in cmd
    assert "-p " not in cmd
    assert cmd.rstrip().endswith("--foo bar")


# ---- CAPTURE: capture_from_container (fake docker inspect) --------------------------

_FAKE_INSPECT = [{
    "Config": {
        "Image": "vllm/vllm-openai:nightly",
        "Env": [
            "PATH=/usr/local/bin",
            "FLASHINFER_CUDA_ARCH_LIST=12.0f",
            "VLLM_USE_V2_MODEL_RUNNER=0",
            "CUDA_DEVICE_ORDER=PCI_BUS_ID",
            "HOME=/root",
        ],
    },
    "Args": [
        "openai/gpt-oss-120b",
        "--kv-cache-dtype", "fp8",
        "--tool-call-parser", "openai",
        "--enable-auto-tool-choice",
        "--gpu-memory-utilization", "0.88",
        "--max-model-len", "131072",
    ],
    "HostConfig": {
        "DeviceRequests": [
            {"Driver": "nvidia",
             "DeviceIDs": ["GPU-d0f446cf-1771-414c-e116-a39138798a8c"],
             "Capabilities": [["gpu"]]},
        ],
        "PortBindings": {"30002/tcp": [{"HostIp": "127.0.0.1", "HostPort": "30002"}]},
    },
}]


def _fake_docker(inspect_json):
    calls = []

    def _run(cmd, **kw):
        calls.append(cmd)
        return SimpleNamespace(stdout=json.dumps(inspect_json))

    return _run, calls


def test_capture_from_container_parses_inspect_json():
    run, calls = _fake_docker(_FAKE_INSPECT)
    cap = sr.capture_from_container("heavy-serve", _run=run)
    assert calls == [["docker", "inspect", "heavy-serve"]]

    serve = cap["serve"]
    assert serve["engine"] == "vllm"
    assert serve["image"] == "vllm/vllm-openai:nightly"
    assert serve["port"] == 30002
    # only VLLM_/FLASHINFER_/CUDA_ env survives, in order; PATH/HOME dropped.
    assert serve["env"] == [
        "FLASHINFER_CUDA_ARCH_LIST=12.0f",
        "VLLM_USE_V2_MODEL_RUNNER=0",
        "CUDA_DEVICE_ORDER=PCI_BUS_ID",
    ]
    # positional model stripped; flag/value token pairs grouped.
    assert serve["flags"] == [
        "--kv-cache-dtype fp8",
        "--tool-call-parser openai",
        "--enable-auto-tool-choice",
        "--gpu-memory-utilization 0.88",
        "--max-model-len 131072",
    ]
    assert cap["hardware"]["gpu_uuid"] == "GPU-d0f446cf-1771-414c-e116-a39138798a8c"


def test_capture_from_container_gpu_uuid_falls_back_to_cuda_visible_devices():
    inspect = [{
        "Config": {
            "Image": "lmsysorg/sglang:latest",
            "Env": ["CUDA_VISIBLE_DEVICES=GPU-abc123,GPU-def456"],
        },
        "Args": ["--foo", "bar"],
        "HostConfig": {},
    }]
    run, _ = _fake_docker(inspect)
    cap = sr.capture_from_container("s", _run=run)
    assert cap["serve"]["engine"] == "sglang"
    assert cap["hardware"]["gpu_uuid"] == "GPU-abc123"  # first of CUDA_VISIBLE_DEVICES


def test_capture_from_container_round_trips_into_a_reconstructable_recipe():
    run, _ = _fake_docker(_FAKE_INSPECT)
    cap = sr.capture_from_container("s", _run=run)
    recipe = {"model": "openai/gpt-oss-120b", **cap}
    cmd = sr.reconstruct_docker_run(recipe)
    assert "vllm/vllm-openai:nightly openai/gpt-oss-120b --kv-cache-dtype fp8" in cmd


# ---- CAPTURE: capture_hardware (fake nvidia-smi) -----------------------------------

_SMI = (
    "NVIDIA RTX PRO 6000 Blackwell Max-Q, 98304 MiB, GPU-d0f446cf-1771-414c-e116-a39138798a8c\n"
    "NVIDIA GeForce RTX 5090, 32768 MiB, GPU-04d3b6e7-0000-0000-0000-000000000000\n"
)


def _fake_smi(text):
    def _run(cmd, **kw):
        return SimpleNamespace(stdout=text)

    return _run


def test_capture_hardware_matches_uuid_and_converts_mib_to_gb():
    run = _fake_smi(_SMI)
    hw = sr.capture_hardware("GPU-d0f446cf-1771-414c-e116-a39138798a8c", _run=run)
    assert hw == {"gpu": "NVIDIA RTX PRO 6000 Blackwell Max-Q", "vram_total_gb": 96}

    hw2 = sr.capture_hardware("GPU-04d3b6e7-0000-0000-0000-000000000000", _run=run)
    assert hw2 == {"gpu": "NVIDIA GeForce RTX 5090", "vram_total_gb": 32}


def test_capture_hardware_first_row_when_no_uuid_given():
    hw = sr.capture_hardware(_run=_fake_smi(_SMI))
    assert hw["gpu"] == "NVIDIA RTX PRO 6000 Blackwell Max-Q"


def test_capture_hardware_empty_when_uuid_not_present():
    assert sr.capture_hardware("GPU-nope", _run=_fake_smi(_SMI)) == {}


# ---- the shipped registry loads and reconstructs -----------------------------------

def test_shipped_registry_reconstructs_gpt_oss(request):
    root = request.config.rootpath
    registry = sr.load_registry(str(root / "configs" / "serve-recipes.toml"))
    recipe = sr.find_recipe(registry, "gpt-oss-120b")
    assert recipe is not None
    assert recipe["measured"]["throughput_single_tok_s"] == pytest.approx(183.2)
    cmd = sr.reconstruct_docker_run(recipe)
    assert cmd.startswith("docker run -d --gpus device=GPU-d0f446cf")
    assert "vllm/vllm-openai@sha256:e4f88a835143cd22aee2397a26ec6bb80b3a4a6fe0c882bcbc63822904766089 openai/gpt-oss-120b" in cmd


def test_shipped_stable_vllm_recipes_pin_0251_and_enable_wsl2_memory(request):
    root = request.config.rootpath
    registry = sr.load_registry(str(root / "configs" / "serve-recipes.toml"))
    stable = (
        "vllm/vllm-openai@sha256:"
        "e4f88a835143cd22aee2397a26ec6bb80b3a4a6fe0c882bcbc63822904766089"
    )
    recipes = [
        recipe for recipe in registry["recipe"]
        if recipe.get("serve", {}).get("image") == stable
    ]
    assert recipes
    for recipe in recipes:
        assert "VLLM_WSL2_ENABLE_PIN_MEMORY=1" in recipe["serve"].get("env", []), recipe["model"]

    managed = {
        recipe["serve"].get("managed_serve"): recipe["model"]
        for recipe in recipes
    }
    assert managed["fast"] == "leon-se/gemma-4-E4B-it-FP8-Dynamic"
    assert managed["heavy"] == "google/gemma-4-12B-it-qat-w4a16-ct"
