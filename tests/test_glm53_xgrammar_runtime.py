"""Contracts for the corrected GLM-5.3-Flash runtime and matched 524K lanes."""

from __future__ import annotations

import json
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


def test_runtime_patch_is_hash_gated_before_and_after_application() -> None:
    dockerfile = (PATCH_ROOT / "Dockerfile").read_text(encoding="utf-8")

    for digest in (
        "001a45bd71bcf908a8c07459570bdb8c5e0a205d085f29ac7f3201529fa3eb75",
        "355f6f1193c15d5d6901a0f567e2e16005e3681f04f70079c6ba11e020b4d33a",
        "3fd606dc2b8e950fe9b49f28cf1c030be78beaaf7c78b457b5942a0909d3457f",
        "003b090b3182e377dff48561050ba86b6f671e0e5c60cc35984e96df8699c386",
        "906b24eae8ca3cdd9425a87f9e2dfae9ef9840cfd2f3be647d7b1f4ba72cbab4",
        "12f64b39d29282437e35be9aa5db432fb2a1a6e6",
        "c6e19b3be24338759a443e03c8325d76da9ee202",
    ):
        assert digest in dockerfile
    assert "sha256sum -c -" in dockerfile
    assert "patch -p1" in dockerfile


def test_runtime_patch_contains_both_merged_structured_output_fixes() -> None:
    patch = (PATCH_ROOT / "xgrammar-spec-reasoning-end.patch").read_text(
        encoding="utf-8"
    )

    assert "if self._is_terminated:" in patch
    assert "if self.matcher.is_terminated():" in patch
    assert "self._is_terminated = False" in patch
    assert "accepted = bool(grammar.validate_tokens([token]))" in patch
    assert "accepted = grammar.accept_tokens(req_id, [token])" in patch


def test_524k_recipes_are_loadable_and_pin_the_qualified_image() -> None:
    for path in (NO_SPEC, SPEC):
        recipe = _recipe(path)
        serve_recipes.validate_recipe(recipe, require_loadable=True)
        assert recipe["serve"]["image"] == IMAGE
        assert recipe["serve"]["context_tokens"] == 524_288
        assert "--max-model-len 524288" in recipe["serve"]["flags"]
        assert "--max-num-seqs 16" in recipe["serve"]["flags"]


def test_no_spec_and_dflash2_are_otherwise_matched() -> None:
    no_spec = _recipe(NO_SPEC)
    spec = _recipe(SPEC)
    assert no_spec["download"] == spec["download"]
    assert no_spec["hardware"] == spec["hardware"]

    def normalized_serve(recipe: dict) -> dict:
        serve = dict(recipe["serve"])
        for key in ("engine", "note", "quantization", "served_model_name"):
            serve.pop(key)
        serve["flags"] = [
            flag
            for flag in serve["flags"]
            if not flag.startswith("--served-model-name ")
            and not flag.startswith("--speculative-config ")
        ]
        return serve

    assert normalized_serve(no_spec) == normalized_serve(spec)
    no_spec_flags = no_spec["serve"]["flags"]
    spec_flags = spec["serve"]["flags"]
    assert not any(flag.startswith("--speculative-config ") for flag in no_spec_flags)
    speculative = [
        flag for flag in spec_flags if flag.startswith("--speculative-config ")
    ]
    assert len(speculative) == 1
    assert '"method":"dflash"' in speculative[0]
    assert '"num_speculative_tokens":5' in speculative[0]


def test_feasibility_contract_encodes_250k_plus_output_at_c2() -> None:
    payload = json.loads(
        (
            ROOT
            / "docs"
            / "findings"
            / "2026-08-31-glm53-xgrammar-524k-qualification-evidence"
            / "feasibility-input.json"
        ).read_text(encoding="utf-8")
    )
    requirements = payload["requirements"]

    assert requirements["tokens"]["prompt"]["value"] == 250_000
    assert requirements["tokens"]["output_reserve"]["value"] == 8_192
    assert requirements["concurrency"]["value"] == 2
    assert all(
        candidate["runtime_context_limit_tokens"]["value"] == 524_288
        for candidate in payload["candidates"]
    )
