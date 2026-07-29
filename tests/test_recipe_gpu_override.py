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


@pytest.mark.parametrize("value", ["", "--gpus", "GPU two", "GPU-x\x00bad"])
def test_gpu_override_rejects_option_injection_and_ambiguous_values(value):
    with pytest.raises(serve_recipes.RecipeError, match="gpu_device"):
        serve_recipes.docker_run_argv(_recipe(), gpu_device=value)
