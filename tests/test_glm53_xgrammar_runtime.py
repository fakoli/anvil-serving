"""Contracts for the corrected GLM-5.3-Flash runtime and matched 524K lanes."""

from __future__ import annotations

from pathlib import Path

from anvil_serving import serve_recipes


ROOT = Path(__file__).resolve().parents[1]
PATCH_ROOT = (
    ROOT
    / "configs"
    / "runtime-patches"
    / "vllm"
    / "487ecf187-xgrammar-spec-reasoning-end"
)
NO_SPEC = (
    ROOT
    / "configs"
    / "glm53-flash-purtell-k3-nospec-fp8-524k-vision-xgrammar-sm120-tp2-wsl2-recipe.toml"
)
SPEC = (
    ROOT
    / "configs"
    / "glm53-flash-purtell-k3-dflash2-k5-fp8-524k-vision-xgrammar-sm120-tp2-wsl2-recipe.toml"
)
IMAGE = (
    "anvil-glm53-xgrammar@sha256:"
    "4909e318ba1348a179824e210f90c268d6fc68e8b4e514af4782e26e6a1e5939"
)


def _recipe(path: Path) -> dict:
    return serve_recipes.load_registry(path)["recipe"][0]
