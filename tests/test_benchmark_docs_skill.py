from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills" / "anvil-serving-benchmark-docs" / "SKILL.md"
REFERENCE = SKILL.parent / "references" / "publication-contract.md"
WRAPPER = ROOT / ".agents" / "skills" / "anvil-serving-benchmark-docs" / "SKILL.md"
OPENAI_YAML = SKILL.parent / "agents" / "openai.yaml"
RUNS = ROOT / "docs" / "benchmarks" / "runs.md"
AUDIT = ROOT / "docs" / "benchmarks" / "rtx-pro-6000-audit.md"
PORTAL = ROOT / "docs" / "benchmarks" / "index.md"
PRO = ROOT / "docs" / "benchmarks" / "hardware" / "rtx-pro-6000.md"
ARCHIVE = ROOT / "docs" / "BENCHMARKS.md"
DOSSIERS = ROOT / "docs" / "benchmarks" / "models"


def test_benchmark_docs_skill_has_publication_matrix_and_boundaries():
    text = SKILL.read_text(encoding="utf-8")
    reference = REFERENCE.read_text(encoding="utf-8")
    for path in (
        "docs/findings/README.md",
        "docs/benchmarks/runs.md",
        "docs/benchmarks/models/",
        "docs/BENCHMARKS.md",
        "docs/benchmarks/methodology.md",
    ):
        assert path in text
    for label in (
        "external-prior",
        "compatibility-only",
        "functional",
        "capacity",
        "quality",
        "historical-invalid",
        "current",
        "rollback",
        "challenger",
        "no-promotion",
        "rejected",
    ):
        assert label in text
    assert "never authorizes" in text
    assert "C:/Users/" not in text
    assert "never permission to mutate a serve" in reference
    assert "| Trigger | Finding + index | Run catalog | Dossier |" in reference


def test_benchmark_docs_wrapper_is_thin_and_ui_metadata_points_to_skill():
    wrapper = WRAPPER.read_text(encoding="utf-8")
    assert "../../../skills/anvil-serving-benchmark-docs/SKILL.md" in wrapper
    assert len(wrapper.splitlines()) < 20
    assert "$anvil-serving-benchmark-docs" in OPENAI_YAML.read_text(encoding="utf-8")


def test_every_dossier_uses_the_common_contract():
    headings = (
        "## Current status and review date",
        "## Immutable identity",
        "## Tested hardware and topology",
        "## Engine, quantization, KV, context, and concurrency recipe",
        "## Evidence by measurement class",
        "## Decision and promotion state",
        "## Failures and gotchas",
        "## Dated run history",
    )
    dossier_files = sorted(path for path in DOSSIERS.glob("*.md") if path.name != "index.md")
    assert len(dossier_files) >= 20
    for path in dossier_files:
        text = path.read_text(encoding="utf-8")
        missing = [heading for heading in headings if heading not in text]
        assert not missing, f"{path.relative_to(ROOT)} missing {missing}"


def test_current_pro_decision_chain_is_consistent_in_maintained_views():
    for path in (PORTAL, PRO, ARCHIVE):
        text = path.read_text(encoding="utf-8")
        positions = [
            text.index("Agents-A1"),
            text.index("Qwen3.5"),
            text.index("Laguna S 2.1"),
            text.index("GPT-OSS Puzzle"),
            text.index("Gemma 4"),
        ]
        assert positions == sorted(positions), path


def test_every_pro_6000_markdown_mention_is_classified_in_audit():
    audit = AUDIT.read_text(encoding="utf-8").replace("\\", "/")
    ignored_parts = {".git", ".venv", "site", "build"}
    mentioning = []
    for path in ROOT.rglob("*.md"):
        if ignored_parts.intersection(path.parts) or any(
            part.startswith(".scratch") for part in path.parts
        ):
            continue
        text = path.read_text(encoding="utf-8")
        folded = text.casefold()
        if (
            "rtx pro 6000" in folded
            or "rtx 6000 pro" in folded
            or "pro 6000" in folded
        ):
            mentioning.append(path.relative_to(ROOT).as_posix())
    missing = [path for path in mentioning if path not in audit]
    assert not missing, f"unclassified RTX PRO 6000 mentions: {missing}"


def test_every_run_catalog_row_links_a_dossier_and_finding():
    rows = [
        line
        for line in RUNS.read_text(encoding="utf-8").splitlines()
        if line.startswith("| 20")
    ]
    assert len(rows) >= 30
    for row in rows:
        assert "(models/" in row, row
        assert "(../findings/" in row, row


def test_benchmark_skills_delegate_publication_without_promotion():
    skill_paths = (
        ROOT / "skills" / "anvil-serving-stt-benchmark" / "SKILL.md",
        ROOT / "skills" / "anvil-serving-voice-ops" / "SKILL.md",
        ROOT / ".agents" / "skills" / "anvil-serving-workbench" / "SKILL.md",
    )
    for path in skill_paths:
        text = path.read_text(encoding="utf-8")
        assert "skills/anvil-serving-benchmark-docs/SKILL.md" in text, path
    stt = skill_paths[0].read_text(encoding="utf-8")
    assert "protected/co-resident topology" in stt
    assert "non-promotion decision" in stt
