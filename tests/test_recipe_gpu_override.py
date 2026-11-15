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


@pytest.mark.parametrize("value", [0, True, 86401])
def test_recipe_rejects_invalid_startup_timeout(value):
    recipe = _recipe()
    recipe["serve"]["startup_timeout_seconds"] = value

    with pytest.raises(serve_recipes.RecipeError, match="startup_timeout_seconds"):
        serve_recipes.docker_run_argv(recipe)
