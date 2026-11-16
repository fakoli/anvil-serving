from pathlib import Path

import pytest

from anvil_serving import recipe_catalog

REPO = Path(__file__).resolve().parents[1]

RECIPE = """schema = "anvil-serving.serve-recipes/v1"

[[recipe]]
model = "example/Model-FP8"
status = "candidate"
source = "test"

[recipe.hardware]
gpu = "2x NVIDIA RTX PRO 6000 Blackwell Max-Q"

[recipe.serve]
engine = "vllm"
image = "vllm/vllm-openai:v0.0.0"
port = 30001
served_model_name = "example-served"
quantization = "fp8"
context_tokens = 8192
flags = ["--max-model-len", "8192"]

[recipe.sources]
official = ["https://example.com/model"]
local = ["docs/guide.md", "docs/missing.md", "configs/other-recipe.toml"]
"""


def _tree(tmp_path: Path) -> list[Path]:
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "guide.md").write_text("# Guide\n")
    configs = tmp_path / "configs"
    configs.mkdir()
    (configs / "example-recipe.toml").write_text(RECIPE)
    (configs / "other-recipe.toml").write_text(RECIPE.replace("example/Model-FP8", "example/Other"))
    (configs / "router.toml").write_text('[router]\nname = "not a recipe"\n')
    return list(configs.glob("*.toml"))


def test_catalog_skips_non_recipes_links_findings_and_resolves_sources(tmp_path):
    index = {
        "records": [{"id": "2026-11-01-x", "date": "2026-11-01", "title": "X result",
                     "links": {"finding": "2026-11-01-x.md"}}],
        "capacity_points": [{"record": "2026-11-01-x", "model": "example-served"}],
        "quality_suites": [],
    }
    catalog = recipe_catalog.build_catalog(_tree(tmp_path), index, repo_root=tmp_path)

    assert [item["name"] for item in catalog["files"]] == ["example-recipe.toml", "other-recipe.toml"]
    recipe = catalog["files"][0]["recipes"][0]
    assert recipe["benchmarks"] == [{"id": "2026-11-01-x", "date": "2026-11-01",
                                     "title": "X result", "finding": "2026-11-01-x.md"}]
    assert [source["href"] for source in recipe["sources"]] == [
        "https://example.com/model", "docs:guide.md", None, "other-recipe.md"]

    page = recipe_catalog.render_file_page(catalog["files"][0])
    assert "[Download example-recipe.toml](files/example-recipe.toml)" in page
    assert "[X result](../findings/2026-11-01-x.md)" in page
    assert "- local: `docs/missing.md`" in page
    assert "models recipes load 'example/Model-FP8' --container my-serve --registry ./example-recipe.toml --dry-run" in page
    assert "### 2× RTX PRO 6000" in recipe_catalog.render_index_table(catalog)


# Assembled at runtime so the repository's own hygiene scan does not flag the fixtures.
@pytest.mark.parametrize("text", [
    'CUDA_VISIBLE_DEVICES = "' + "-".join(("GPU", "3f2a9c1e", "7b4d", "4e8a", "9c2b", "1d5e6f7a8b9c")) + '"',
    'model_path = "' + "/".join(("", "home", "someone", "models", "x")) + '"',
])
def test_publication_guard_refuses_host_identifiers(text):
    with pytest.raises(recipe_catalog.PublicationError):
        recipe_catalog.check_publishable("x.toml", text)


def test_publication_guard_allows_public_placeholders():
    recipe_catalog.check_publishable("x.toml", 'gpu_uuid = "GPU-11111111-1111-1111-1111-111111111111"')


def test_every_repository_recipe_file_is_publishable():
    catalog = recipe_catalog.build_catalog((REPO / "configs").glob("*.toml"), repo_root=REPO)
    assert len(catalog["files"]) > 100
