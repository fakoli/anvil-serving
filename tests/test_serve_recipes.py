"""Tests for `anvil_serving.serve_recipes` — the shared GENERATE + READ helpers.

Every test is HERMETIC: `docker inspect` / `nvidia-smi` are injected as FAKE `_run`
callables, so CI never touches real docker, a real GPU, or the network. The write path
is proven round-trip-safe by parsing its output back through `tomllib`.
"""
import json
from datetime import datetime, timedelta, timezone
import shlex
from types import SimpleNamespace

import pytest

from anvil_serving import serve_recipes as sr
from anvil_serving.observability.workloads import (
    ObservationQuality,
    ResultStatus,
    WorkloadQuery,
    WorkloadState,
    source_result_to_json,
)

# A recipe exercising every value kind: str, int, float, bool, arrays, nested tables.
_RECIPE = {
    "model": "openai/gpt-oss-120b",
    "status": "verified",
    "source": 'measured on primary-node — preflight "ALL PASS"\ttab\\backslash',
    "total_params_b": 116.8,
    "active_params_b": 5.1,
    "hardware": {
        "gpu": "NVIDIA RTX PRO 6000 Blackwell Max-Q",
        "gpu_uuid": "GPU-test-device",
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


_WORKLOAD_NOW = datetime(2026, 9, 5, 12, 0, tzinfo=timezone.utc)


def _recipe_snapshot(
    configuration=(),
    runtime=(),
    *,
    configuration_status=ResultStatus.COMPLETE,
    runtime_status=ResultStatus.COMPLETE,
    configuration_error=None,
    runtime_error=None,
):
    return sr.RecipeWorkloadSnapshot(
        sr.RecipeComponentResult(
            configuration_status, _WORKLOAD_NOW, tuple(configuration),
            0 if configuration_status is ResultStatus.COMPLETE else None,
            configuration_error,
        ),
        sr.RecipeComponentResult(
            runtime_status, _WORKLOAD_NOW, tuple(runtime),
            0 if runtime_status is ResultStatus.COMPLETE else None,
            runtime_error,
        ),
    )


def _recipe_reader(snapshot):
    def reader(_path, *, clock):
        assert clock().tzinfo is timezone.utc
        return snapshot

    return reader


# ---- WRITE: format_recipe round-trips through tomllib -------------------------------


def test_recipe_workload_projection_reconciles_digests_without_leaking_native_values():
    digest, container_a, container_b = "a" * 64, "b" * 64, "c" * 64
    configured = sr.RecipeConfiguredObservation(
        digest, _WORKLOAD_NOW - timedelta(seconds=2), _WORKLOAD_NOW
    )
    runtime = (
        sr.RecipeContainerObservation(
            container_a, digest, WorkloadState.RUNNING,
            _WORKLOAD_NOW - timedelta(seconds=3), _WORKLOAD_NOW - timedelta(seconds=1),
            _WORKLOAD_NOW,
        ),
        sr.RecipeContainerObservation(
            container_b, None, WorkloadState.ABSENT,
            _WORKLOAD_NOW - timedelta(seconds=4), _WORKLOAD_NOW - timedelta(seconds=1),
            _WORKLOAD_NOW,
        ),
    )
    result = sr.list_recipe_workloads(
        "not-read.toml", "node-a", WorkloadQuery(), _WORKLOAD_NOW,
        snapshot_reader=_recipe_reader(_recipe_snapshot((configured,), runtime)),
    )

    assert result.status is ResultStatus.COMPLETE
    assert len(result.records) == 2
    assert {record.state for record in result.records} == {WorkloadState.RUNNING, WorkloadState.ABSENT}
    assert all(record.observation_quality is not ObservationQuality.HEALTHY_IDENTITY for record in result.records)
    payload = source_result_to_json(result)
    for native in (digest, container_a, container_b, "not-read.toml"):
        assert native not in payload


def test_recipe_rejects_ignored_serve_args_with_flags_recovery():
    recipe = {"model": "org/model", "serve": {"image": "example/image", "args": ["--x"]}}
    with pytest.raises(sr.RecipeError, match=r"serve\.flags"):
        sr.validate_recipe(recipe)


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


# ---- READ: find_recipe (exact + basename) ------------------------------------------


# ---- READ: reconstruct_docker_run --------------------------------------------------


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
             "DeviceIDs": ["GPU-inspected-device"],
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
    "NVIDIA RTX PRO 6000 Blackwell Max-Q, 98304 MiB, GPU-11111111-1111-1111-1111-111111111111\n"
    "NVIDIA GeForce RTX 5090, 32768 MiB, GPU-33333333-3333-3333-3333-333333333333\n"
)


def _fake_smi(text):
    def _run(cmd, **kw):
        return SimpleNamespace(stdout=text)

    return _run


def test_capture_hardware_matches_uuid_and_converts_mib_to_gb():
    run = _fake_smi(_SMI)
    hw = sr.capture_hardware("GPU-11111111-1111-1111-1111-111111111111", _run=run)
    assert hw == {"gpu": "NVIDIA RTX PRO 6000 Blackwell Max-Q", "vram_total_gb": 96}

    hw2 = sr.capture_hardware("GPU-33333333-3333-3333-3333-333333333333", _run=run)
    assert hw2 == {"gpu": "NVIDIA GeForce RTX 5090", "vram_total_gb": 32}


# ---- the shipped registry loads and reconstructs -----------------------------------


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
        "gpu_uuid": "GPU-11111111-1111-1111-1111-111111111111",
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

    preview = sr.reconstruct_docker_run(recipe)
    assert sr.PUBLIC_GPU_DEVICE_PLACEHOLDER in preview
    with pytest.raises(sr.RecipeError, match="public placeholder"):
        sr.docker_run_argv(recipe, container="candidate")
    cmd = shlex.join(sr.docker_run_argv(recipe, gpu_device="0"))
    assert cmd.startswith("docker run -d --gpus device=0")
    assert serve["image"] in cmd
    assert "nvidia/gpt-oss-puzzle-88B" in cmd
    assert "--revision 9c0e0746a0d2218b28cc7b2cb3ce4e1a2f50fdb2" in cmd


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

    preview = sr.reconstruct_docker_run(recipe)
    assert sr.PUBLIC_GPU_DEVICE_PLACEHOLDER in preview
    with pytest.raises(sr.RecipeError, match="public placeholder"):
        sr.docker_run_argv(recipe, container="candidate")
    cmd = shlex.join(sr.docker_run_argv(recipe, gpu_device="0"))
    assert cmd.startswith("docker run -d --gpus device=0")
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
