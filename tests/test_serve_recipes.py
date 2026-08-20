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
    "fit": {
        "suited": ["benchmark-quality", "quality"],
        "not_suited": ["agentic-interactive"],
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
    assert "[recipe.fit]" in block
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
    assert argv[:5] == ["docker", "run", "-d", "--name", "heavy-candidate"]
    assert "io.anvil-serving.managed-by=models-recipes" in argv
    assert "io.anvil-serving.recipe.model=openai/gpt-oss-120b" in argv
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


def test_native_kv_offload_recipe_detection_accepts_env_and_flag_forms():
    env_recipe = {
        **_RECIPE,
        "serve": {**_RECIPE["serve"], "env": ["KV_OFFLOADING_SIZE=8"]},
    }
    flag_recipe = {
        **_RECIPE,
        "serve": {**_RECIPE["serve"], "flags": ["--kv-offloading-size 8"]},
    }
    disabled = {
        **_RECIPE,
        "serve": {**_RECIPE["serve"], "env": ["KV_OFFLOADING_SIZE=0"]},
    }
    assert sr.uses_native_kv_offload(env_recipe) is True
    assert sr.uses_native_kv_offload(flag_recipe) is True
    assert sr.uses_native_kv_offload(disabled) is False


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
    model_i = cmd.rindex("openai/gpt-oss-120b")
    assert img_i < model_i, "model must be a POSITIONAL after the image"
    # no accidental extra 'serve' verb (entrypoint already is `vllm serve`).
    assert " serve " not in cmd


def test_reconstruct_docker_run_supports_immutable_container_model_path():
    snapshot = (
        "/root/.cache/huggingface/hub/models--Qwen--Qwen3.8-27B/"
        "snapshots/1d4bf0f2ff6012fd82039f2fa52739d0dd7c60c0"
    )
    recipe = {
        **_RECIPE,
        "serve": {**_RECIPE["serve"], "model_path": snapshot},
    }

    argv = sr.docker_run_argv(recipe)
    image_i = argv.index("vllm/vllm-openai:nightly")
    assert argv[image_i + 1] == snapshot
    assert "io.anvil-serving.recipe.model=openai/gpt-oss-120b" in argv
    assert "openai/gpt-oss-120b" not in argv[image_i + 1:]


@pytest.mark.parametrize("model_path", ["relative/model", "/bad/../model", "C:\\bad"])
def test_docker_run_argv_rejects_unsafe_container_model_path(model_path):
    recipe = {
        **_RECIPE,
        "serve": {**_RECIPE["serve"], "model_path": model_path},
    }
    with pytest.raises(sr.RecipeError, match="normalized absolute POSIX path"):
        sr.docker_run_argv(recipe)


def test_docker_run_argv_rejects_model_path_with_model_env():
    recipe = {
        **_RECIPE,
        "serve": {
            **_RECIPE["serve"],
            "model_path": "/models/pinned",
            "model_env": "MODEL",
        },
    }
    with pytest.raises(sr.RecipeError, match="incompatible"):
        sr.docker_run_argv(recipe)


def test_reconstruct_docker_run_supports_entrypoint_and_model_flag():
    recipe = {
        "model": "nvidia/Qwen3.5-122B-A10B-NVFP4",
        "serve": {
            "image": "nvcr.io/nvidia/vllm:26.06-py3",
            "entrypoint": ["python3", "-m", "vllm.entrypoints.openai.api_server"],
            "model_flag": "--model",
            "flags": ["--max-model-len 131072"],
        },
    }

    argv = sr.docker_run_argv(recipe)
    image_i = argv.index("nvcr.io/nvidia/vllm:26.06-py3")
    assert argv[image_i - 2:image_i] == ["--entrypoint", "python3"]
    assert argv[image_i + 1:image_i + 5] == [
        "-m", "vllm.entrypoints.openai.api_server", "--model",
        "nvidia/Qwen3.5-122B-A10B-NVFP4",
    ]


def test_reconstruct_docker_run_supports_environment_owned_model_and_named_volumes():
    recipe = {
        "model": "deepseek-ai/DeepSeek-V4-Flash-0731",
        "download": {"volume": "deepseek-r16-models"},
        "serve": {
            "image": "voipmonitor/vllm@sha256:abc",
            "entrypoint": ["/usr/local/bin/serve-ds4-flash.sh"],
            "model_env": "MODEL",
            "named_volumes": [
                "deepseek-r16-jit:/cache",
                "deepseek-r16-tmp:/container-tmp",
                "deepseek-r16-readonly:/opt/reference:ro",
            ],
            "env": ["MODE=dspark"],
        },
    }

    argv = sr.docker_run_argv(recipe)
    image_i = argv.index("voipmonitor/vllm@sha256:abc")
    assert argv[image_i - 2:image_i] == [
        "--entrypoint", "/usr/local/bin/serve-ds4-flash.sh",
    ]
    assert argv[image_i + 1:] == []
    assert "MODEL=deepseek-ai/DeepSeek-V4-Flash-0731" in argv
    assert "deepseek-ai/DeepSeek-V4-Flash-0731" not in argv[image_i + 1:]
    assert "deepseek-r16-models:/root/.cache/huggingface" in argv
    mounts = [argv[index + 1] for index, item in enumerate(argv) if item == "--mount"]
    assert mounts == [
        "type=volume,source=deepseek-r16-jit,target=/cache",
        "type=volume,source=deepseek-r16-tmp,target=/container-tmp",
        "type=volume,source=deepseek-r16-readonly,target=/opt/reference,readonly",
    ]


def test_docker_run_argv_supports_recipe_ipc_shared_memory_and_ulimits():
    recipe = {
        "model": "m/x",
        "serve": {
            "image": "img",
            "ipc": "host",
            "shm_size": "16gb",
            "ulimits": ["memlock=-1", "stack=67108864"],
        },
    }

    argv = sr.docker_run_argv(recipe)
    assert argv[argv.index("--ipc"):argv.index("--ipc") + 2] == ["--ipc", "host"]
    assert argv[argv.index("--shm-size"):argv.index("--shm-size") + 2] == ["--shm-size", "16gb"]
    assert argv.count("--ulimit") == 2
    assert "memlock=-1" in argv
    assert "stack=67108864" in argv


def test_recipe_entrypoint_and_model_flag_reject_unsafe_or_incomplete_values():
    recipe = {"model": "m/x", "serve": {"image": "img", "entrypoint": []}}
    with pytest.raises(sr.RecipeError, match="entrypoint"):
        sr.validate_recipe(recipe, require_loadable=True)

    recipe = {"model": "m/x", "serve": {"image": "img", "model_flag": "--model"}}
    with pytest.raises(sr.RecipeError, match="model_flag"):
        sr.validate_recipe(recipe, require_loadable=True)

    recipe = {"model": "m/x", "serve": {"image": "img", "model_env": "MODEL"}}
    with pytest.raises(sr.RecipeError, match="model_env"):
        sr.validate_recipe(recipe, require_loadable=True)

    recipe = {
        "model": "m/x",
        "serve": {
            "image": "img",
            "entrypoint": ["launcher"],
            "model_flag": "--model",
            "model_env": "MODEL",
        },
    }
    with pytest.raises(sr.RecipeError, match="model_env"):
        sr.validate_recipe(recipe, require_loadable=True)

    recipe = {"model": "m/x", "serve": {"image": "img", "ipc": "container:other"}}
    with pytest.raises(sr.RecipeError, match="ipc"):
        sr.validate_recipe(recipe, require_loadable=True)

    recipe = {"model": "m/x", "serve": {"image": "img", "shm_size": "0gb"}}
    with pytest.raises(sr.RecipeError, match="shm_size"):
        sr.validate_recipe(recipe, require_loadable=True)

    recipe = {
        "model": "m/x",
        "serve": {"image": "img", "entrypoint": ["python3"], "model_flag": "--model value"},
    }
    with pytest.raises(sr.RecipeError, match="model_flag"):
        sr.validate_recipe(recipe, require_loadable=True)


@pytest.mark.parametrize(
    "named_volumes,match",
    [
        (["C:/host:/cache"], "named Docker volumes"),
        (["cache:relative"], "absolute POSIX"),
        (["cache:/bad target"], "absolute POSIX"),
        (["cache:/a/../b"], "absolute POSIX"),
        (["cache:/root/.cache/huggingface"], "cannot shadow"),
        (["cache:/one", "cache:/two"], "repeat"),
        (["one:/cache", "two:/cache"], "repeat"),
    ],
)
def test_recipe_named_volumes_reject_unsafe_or_ambiguous_mounts(
    named_volumes, match,
):
    recipe = {
        "model": "m/x",
        "serve": {"image": "img", "named_volumes": named_volumes},
    }
    with pytest.raises(sr.RecipeError, match=match):
        sr.validate_recipe(recipe, require_loadable=True)


def test_environment_owned_model_rejects_declared_env_override():
    recipe = {
        "model": "m/x",
        "serve": {
            "image": "img",
            "entrypoint": ["launcher"],
            "model_env": "MODEL",
            "env": ["MODEL=other/model"],
        },
    }
    with pytest.raises(sr.RecipeError, match="must not override"):
        sr.docker_run_argv(recipe)


def test_reconstruct_docker_run_includes_env_volume_and_flags():
    cmd = sr.reconstruct_docker_run(_RECIPE)
    assert "-e FLASHINFER_CUDA_ARCH_LIST=12.0f" in cmd
    assert (
        "-e CUDA_VISIBLE_DEVICES=GPU-d0f446cf-1771-414c-e116-a39138798a8c"
        in cmd
    )
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


def test_reconstruct_docker_run_rejects_gpu_visibility_mismatch():
    recipe = {
        "model": "m/x",
        "hardware": {"gpu_uuid": "GPU-selected"},
        "serve": {
            "image": "img",
            "env": ["CUDA_VISIBLE_DEVICES=GPU-other"],
        },
    }
    with pytest.raises(sr.RecipeError, match="must match"):
        sr.reconstruct_docker_run(recipe)


def test_reconstruct_docker_run_allows_explicit_numeric_gpu_visibility():
    recipe = {
        "model": "m/x",
        "hardware": {"gpu_uuid": "GPU-selected"},
        "serve": {
            "image": "img",
            "allow_cuda_visible_devices_index": True,
            "env": ["CUDA_VISIBLE_DEVICES=1"],
        },
    }

    cmd = sr.reconstruct_docker_run(recipe)
    assert "--gpus device=GPU-selected" in cmd
    assert "-e CUDA_VISIBLE_DEVICES=1" in cmd


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


def test_capture_from_container_preserves_nvidia_entrypoint_and_model_flag():
    inspect = [{
        "Config": {
            "Image": "nvcr.io/nvidia/vllm:26.06-py3",
            "Entrypoint": ["python3", "-m", "vllm.entrypoints.openai.api_server"],
            "Env": [],
        },
        "Args": [
            "--model", "nvidia/Qwen3.5-122B-A10B-NVFP4",
            "--max-model-len", "131072",
        ],
        "HostConfig": {},
    }]
    run, _ = _fake_docker(inspect)

    cap = sr.capture_from_container("nvidia-vllm", _run=run)

    assert cap["serve"]["entrypoint"] == [
        "python3", "-m", "vllm.entrypoints.openai.api_server",
    ]
    assert cap["serve"]["model_flag"] == "--model"
    assert cap["serve"]["flags"] == ["--max-model-len 131072"]

    recipe = {"model": "nvidia/Qwen3.5-122B-A10B-NVFP4", **cap}
    cmd = sr.reconstruct_docker_run(recipe)
    assert "--entrypoint python3 nvcr.io/nvidia/vllm:26.06-py3 -m vllm.entrypoints.openai.api_server --model nvidia/Qwen3.5-122B-A10B-NVFP4" in cmd


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
    assert "vllm/vllm-openai@sha256:907377dddef392f6b679d9c071e1c33c3935b4dc993b61d0352e391a5319ff3e openai/gpt-oss-120b" in cmd


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
    assert managed["heavy-gemma4-rollback"] == "google/gemma-4-12B-it-qat-w4a16-ct"


def test_deepseek_r16_recipe_pins_environment_launcher_and_named_data(request):
    root = request.config.rootpath
    registry = sr.load_registry(
        str(
            root
            / "configs"
            / "deepseek-v4-flash-0731-r16-b12x-dspark5-128k-recipe.toml"
        )
    )
    recipe = sr.find_recipe(registry, "deepseek-ai/DeepSeek-V4-Flash-0731")

    assert recipe is not None
    assert recipe["download"] == {
        "repo": "deepseek-ai/DeepSeek-V4-Flash-0731",
        "revision": "9e165c30e2704aec5d9d593cce3eebd58bbef1cb",
        "volume": "deepseek-v4-0731-r16-hfcache",
        "require_complete_cache": True,
    }
    serve = recipe["serve"]
    assert serve["image"] == (
        "voipmonitor/vllm@sha256:"
        "48518e91cf87dd0c0483c76ff86e81dfc0f46de7e364b46f7a82c481ce08188f"
    )
    assert serve["entrypoint"] == ["/usr/local/bin/serve-ds4-flash.sh"]
    assert serve["model_env"] == "MODEL"
    assert serve["named_volumes"] == [
        "deepseek-v4-0731-r16-jit:/cache",
        "deepseek-v4-0731-r16-tmp:/container-tmp",
    ]
    assert "BACKEND=b12x-a8" in serve["env"]
    assert "GPU_MEMORY_UTILIZATION=0.975" in serve["env"]
    assert "MAX_MODEL_LEN=131072" in serve["env"]
    snapshot = (
        "/root/.cache/huggingface/hub/"
        "models--deepseek-ai--DeepSeek-V4-Flash-0731/snapshots/"
        "9e165c30e2704aec5d9d593cce3eebd58bbef1cb"
    )
    assert "MODEL_PATH=%s" % snapshot in serve["env"]
    assert "SPEC_MODEL_PATH=%s" % snapshot in serve["env"]
    assert not any(env.startswith("MODEL_REVISION=") for env in serve["env"])

    argv = sr.docker_run_argv(recipe)
    image_i = argv.index(serve["image"])
    assert argv[image_i + 1:] == []
    assert "MODEL=deepseek-ai/DeepSeek-V4-Flash-0731" in argv
    assert "deepseek-ai/DeepSeek-V4-Flash-0731" not in argv[image_i + 1:]


def test_shipped_gpt_oss_puzzle_recipe_is_verified_heavy_rollback(request):
    root = request.config.rootpath
    registry = sr.load_registry(str(root / "configs" / "serve-recipes.toml"))
    recipe = sr.find_recipe(registry, "nvidia/gpt-oss-puzzle-88B")

    assert recipe is not None
    assert recipe["status"] == "verified"
    assert recipe["hardware"] == {
        "gpu": "NVIDIA RTX PRO 6000 Blackwell Max-Q Workstation Edition",
        "arch": "sm_120",
        "gpu_uuid": "GPU-d0f446cf-1771-414c-e116-a39138798a8c",
        "vram_total_gb": 96,
    }
    serve = recipe["serve"]
    assert serve["image"] == ("anvil-vllm:gpt-oss-puzzle-485463b3498ed3ffcf0c8fcb52c1670a21be5d82")
    assert serve["managed_serve"] == "primary-gptoss-puzzle-rollback"
    assert serve["served_model_name"] == "gpt-oss-puzzle-88b"
    assert serve["port"] == 30002
    assert "--revision 9c0e0746a0d2218b28cc7b2cb3ce4e1a2f50fdb2" in serve["flags"]
    assert "--tensor-parallel-size 1" in serve["flags"]
    assert "--moe-backend marlin" in serve["flags"]
    assert "--max-model-len 131072" in serve["flags"]
    assert "--override-generation-config '{\"eos_token_id\":[200002,199999,200012]}'" in serve["flags"]

    cmd = sr.reconstruct_docker_run(recipe)
    assert cmd.startswith("docker run -d --gpus device=GPU-d0f446cf-1771-414c-e116-a39138798a8c")
    assert serve["image"] in cmd
    assert "nvidia/gpt-oss-puzzle-88B" in cmd
    assert "--revision 9c0e0746a0d2218b28cc7b2cb3ce4e1a2f50fdb2" in cmd


def test_shipped_agents_a1_recipe_is_verified_primary_target(request):
    root = request.config.rootpath
    registry = sr.load_registry(str(root / "configs" / "serve-recipes.toml"))
    recipe = sr.find_recipe(registry, "InternScience/Agents-A1-FP8")

    assert recipe is not None
    assert recipe["status"] == "verified"
    serve = recipe["serve"]
    assert serve["image"] == (
        "vllm/vllm-openai:nightly-f25953cc59f9b4ba9b04b16228d2b86dcfbcbdb1"
    )
    assert serve["managed_serve"] == "primary"
    assert serve["served_model_name"] == "agents-a1-fp8-mm-262k"
    assert serve["port"] == 30002
    assert serve["context_tokens"] == 262144
    assert "--revision 4d7d59380f327b76e73bc71f40e0c589ad0ca1d5" in serve["flags"]
    assert "--kv-cache-dtype fp8" in serve["flags"]
    assert "--max-model-len 262144" in serve["flags"]
    assert "--limit-mm-per-prompt '{\"image\":4,\"video\":1}'" in serve["flags"]
    assert recipe["activation"]["primary"] == {
        "plan": "agents-a1-fp8-primary",
        "direction": "promote",
        "compose_service": "primary",
    }


def test_shipped_qwen35_recipe_is_verified_primary_rollback(request):
    root = request.config.rootpath
    registry = sr.load_registry(str(root / "configs" / "serve-recipes.toml"))
    recipe = sr.find_recipe(registry, "nvidia/Qwen3.5-122B-A10B-NVFP4")

    assert recipe is not None
    assert recipe["status"] == "verified"
    serve = recipe["serve"]
    assert serve["image"] == "nvcr.io/nvidia/vllm:26.06-py3"
    assert serve["managed_serve"] == "primary-qwen35-rollback"
    assert serve["served_model_name"] == "qwen35-122b-a10b-nvfp4"
    assert serve["port"] == 30002
    assert serve["context_tokens"] == 262144
    assert serve["allow_cuda_visible_devices_index"] is True
    assert "CUDA_VISIBLE_DEVICES=1" in serve["env"]
    assert "--revision 98915d837c4e7c87ac8296d02e89de19b3207e6d" in serve["flags"]
    assert "--kv-cache-dtype bfloat16" in serve["flags"]
    assert "--max-model-len 262144" in serve["flags"]
    assert "--limit-mm-per-prompt '{\"image\":1,\"video\":0}'" in serve["flags"]
    assert "--language-model-only" not in serve["flags"]
    assert not any("enable_thinking" in flag for flag in serve["flags"])
    assert recipe["activation"]["primary"] == {
        "plan": "agents-a1-fp8-primary",
        "direction": "rollback",
        "compose_service": "primary-qwen35-rollback",
    }

    cmd = sr.reconstruct_docker_run(recipe)
    assert cmd.startswith(
        "docker run -d --gpus device=GPU-d0f446cf-1771-414c-e116-a39138798a8c"
    )
    assert serve["image"] in cmd
    assert "nvidia/Qwen3.5-122B-A10B-NVFP4" in cmd
    assert "--max-model-len 262144" in cmd


def test_shipped_laguna_recipe_is_verified_primary_rollback(request):
    root = request.config.rootpath
    registry = sr.load_registry(str(root / "configs" / "serve-recipes.toml"))
    recipe = sr.find_recipe(registry, "poolside/Laguna-S-2.1-NVFP4")

    assert recipe is not None
    assert recipe["status"] == "verified"
    serve = recipe["serve"]
    assert serve["managed_serve"] == "primary-laguna-rollback"
    assert serve["served_model_name"] == "laguna-s-2.1-nvfp4"
    assert serve["port"] == 30002
    assert "--revision 07614121b31898586430f189d27a25a0be310843" in serve["flags"]
    assert (
        "--default-chat-template-kwargs '{\"enable_thinking\":false}'"
        in serve["flags"]
    )
    assert recipe["activation"]["primary"] == {
        "plan": "agents-a1-fp8-primary",
        "direction": "rollback",
        "compose_service": "primary-laguna-rollback",
    }
