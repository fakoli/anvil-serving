"""Campaign-safe GPU selection for portable serve recipes."""

from __future__ import annotations

import pytest

from anvil_serving import serve_recipes


def _recipe():
    return {
        "model": "example/model",
        "hardware": {"gpu": "NVIDIA RTX PRO 6000 Blackwell Max-Q"},
        "serve": {
            "image": "vllm/vllm-openai:pinned",
            "port": 30010,
            "env": [
                "CUDA_VISIBLE_DEVICES=GPU-recipe-default",
                "VLLM_WSL2_ENABLE_PIN_MEMORY=1",
            ],
            "flags": ["--served-model-name candidate"],
        },
    }


def test_gpu_override_pins_docker_and_container_environment():
    argv = serve_recipes.docker_run_argv(
        _recipe(),
        container="candidate",
        gpu_device="GPU-runtime-selected",
    )

    assert argv[argv.index("--gpus") + 1] == "device=GPU-runtime-selected"
    assert "CUDA_VISIBLE_DEVICES=GPU-runtime-selected" in argv
    assert "CUDA_VISIBLE_DEVICES=GPU-recipe-default" not in argv
    assert "VLLM_WSL2_ENABLE_PIN_MEMORY=1" in argv


def test_gpu_override_quotes_multi_device_request_for_docker_csv_parser():
    pair = "GPU-runtime-a,GPU-runtime-b"
    argv = serve_recipes.docker_run_argv(
        _recipe(),
        container="candidate",
        gpu_device=pair,
    )

    assert argv[argv.index("--gpus") + 1] == '"device=GPU-runtime-a,GPU-runtime-b"'
    assert "CUDA_VISIBLE_DEVICES=GPU-runtime-a,GPU-runtime-b" in argv


def test_gpu_override_preserves_explicit_container_relative_pair():
    recipe = _recipe()
    recipe["serve"]["allow_cuda_visible_devices_index"] = True
    recipe["serve"]["env"][0] = "CUDA_VISIBLE_DEVICES=0,1"
    pair = "GPU-runtime-a,GPU-runtime-b"

    argv = serve_recipes.docker_run_argv(
        recipe,
        container="candidate",
        gpu_device=pair,
    )

    assert argv[argv.index("--gpus") + 1] == '"device=GPU-runtime-a,GPU-runtime-b"'
    assert "CUDA_VISIBLE_DEVICES=0,1" in argv
    assert f"CUDA_VISIBLE_DEVICES={pair}" not in argv


def test_portable_index_recipe_can_be_reconstructed_before_host_selection():
    recipe = _recipe()
    recipe["serve"]["allow_cuda_visible_devices_index"] = True
    recipe["serve"]["env"][0] = "CUDA_VISIBLE_DEVICES=0,1"

    argv = serve_recipes.docker_run_argv(recipe)

    assert argv[argv.index("--gpus") + 1] == "all"
    assert "CUDA_VISIBLE_DEVICES=0,1" in argv


def test_portable_index_recipe_load_requires_exact_host_selection():
    recipe = _recipe()
    recipe["serve"]["allow_cuda_visible_devices_index"] = True
    recipe["serve"]["env"][0] = "CUDA_VISIBLE_DEVICES=0,1"

    with pytest.raises(serve_recipes.RecipeError, match="selected GPU count"):
        serve_recipes.docker_run_argv(recipe, container="candidate")


def test_complete_cache_recipe_forces_offline_mode_and_declared_volume():
    recipe = _recipe()
    recipe["download"] = {
        "repo": "example/model",
        "revision": "a" * 40,
        "volume": "campaign-cache",
        "require_complete_cache": True,
    }

    argv = serve_recipes.docker_run_argv(recipe, container="candidate")

    assert "HF_HUB_OFFLINE=1" in argv
    assert "TRANSFORMERS_OFFLINE=1" in argv
    assert "campaign-cache:/root/.cache/huggingface" in argv


@pytest.mark.parametrize("value", [0, True, 86401])
def test_recipe_rejects_invalid_startup_timeout(value):
    recipe = _recipe()
    recipe["serve"]["startup_timeout_seconds"] = value

    with pytest.raises(serve_recipes.RecipeError, match="startup_timeout_seconds"):
        serve_recipes.docker_run_argv(recipe)


@pytest.mark.parametrize("visible", ["0", "0,0", "0,GPU-b", "0,1,2"])
def test_gpu_override_rejects_invalid_container_relative_pair(visible):
    recipe = _recipe()
    recipe["serve"]["allow_cuda_visible_devices_index"] = True
    recipe["serve"]["env"][0] = f"CUDA_VISIBLE_DEVICES={visible}"

    with pytest.raises(serve_recipes.RecipeError, match="numeric index count"):
        serve_recipes.docker_run_argv(
            recipe,
            gpu_device="GPU-runtime-a,GPU-runtime-b",
        )


@pytest.mark.parametrize(
    "value",
    ["", "--gpus", "GPU two", "GPU-x\x00bad", "GPU-a,,GPU-b", "GPU-a,GPU-a"],
)
def test_gpu_override_rejects_option_injection_and_ambiguous_values(value):
    with pytest.raises(serve_recipes.RecipeError, match="gpu_device"):
        serve_recipes.docker_run_argv(_recipe(), gpu_device=value)
