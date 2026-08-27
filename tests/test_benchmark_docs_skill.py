import csv
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills" / "anvil-serving-benchmark-docs" / "SKILL.md"
REFERENCE = SKILL.parent / "references" / "publication-contract.md"
WRAPPER = ROOT / ".agents" / "skills" / "anvil-serving-benchmark-docs" / "SKILL.md"
OPENAI_YAML = SKILL.parent / "agents" / "openai.yaml"
FINDING_TEMPLATE = SKILL.parent / "templates" / "finding.md"
PUBLICATION_TEMPLATE = SKILL.parent / "templates" / "publication-summary.md"
RUNS = ROOT / "docs" / "benchmarks" / "runs.md"
AUDIT = ROOT / "docs" / "benchmarks" / "rtx-pro-6000-audit.md"
PORTAL = ROOT / "docs" / "benchmarks" / "index.md"
FINDING_FORMAT = ROOT / "docs" / "benchmarks" / "finding-format.md"
PRO = ROOT / "docs" / "benchmarks" / "hardware" / "rtx-pro-6000.md"
ARCHIVE = ROOT / "docs" / "BENCHMARKS.md"
DOSSIERS = ROOT / "docs" / "benchmarks" / "models"
REFERENCE_FINDING = (
    ROOT / "docs" / "findings" / "2026-08-26-qwen38-flash-next-vision-promotion.md"
)
REFERENCE_PUBLICATION = (
    ROOT
    / "docs"
    / "findings"
    / "2026-08-26-qwen38-flash-next-vision-promotion-evidence"
    / "publication-summary.md"
)
REFERENCE_SUMMARY = REFERENCE_PUBLICATION.parent / "summary.json"
REFERENCE_THROUGHPUT = REFERENCE_PUBLICATION.parent / "throughput-summary.csv"


def _section(text: str, heading: str) -> str:
    start = text.index(heading) + len(heading)
    next_heading = text.find("\n## ", start)
    return text[start:] if next_heading == -1 else text[start:next_heading]


def _fenced_block(section: str, language: str) -> str:
    fence = f"```{language}\n"
    start = section.index(fence) + len(fence)
    end = section.index("\n```", start)
    return section[start:end]


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
    assert (
        "do not load a model, restart a serve, or rerun a benchmark"
        in " ".join(text.split())
    )
    assert "Repository-only format migration" in reference


def test_benchmark_docs_wrapper_is_thin_and_ui_metadata_points_to_skill():
    wrapper = WRAPPER.read_text(encoding="utf-8")
    assert "../../../skills/anvil-serving-benchmark-docs/SKILL.md" in wrapper
    assert len(wrapper.splitlines()) < 20
    assert "$anvil-serving-benchmark-docs" in OPENAI_YAML.read_text(encoding="utf-8")


def test_publication_ready_contract_has_guide_and_copyable_templates():
    skill = SKILL.read_text(encoding="utf-8")
    reference = REFERENCE.read_text(encoding="utf-8")
    guide = FINDING_FORMAT.read_text(encoding="utf-8")
    finding_template = FINDING_TEMPLATE.read_text(encoding="utf-8")
    publication_template = PUBLICATION_TEMPLATE.read_text(encoding="utf-8")

    for text in (skill, reference, guide, finding_template):
        assert "benchmark-result-card/v1" in text
    for text in (skill, reference, guide, publication_template):
        assert "benchmark-publication-summary/v1" in text
    for template_path in (
        "templates/finding.md",
        "templates/publication-summary.md",
    ):
        assert template_path in skill
        assert template_path in guide

    for heading in (
        "## Result card",
        "**Why it matters:**",
        "**Important caveat:**",
        "## Evidence boundary",
    ):
        assert heading in finding_template
    for heading in (
        "## Canonical facts",
        "## X / short post",
        "## Reddit",
        "## Screenshot alt text",
        "## Claim ledger",
    ):
        assert heading in publication_template
    assert "**Model identity:**" in publication_template
    assert "**Runtime identity:**" in publication_template
    assert "280" in guide
    assert "120-character title" in guide
    assert "effective prefill" in guide.casefold()


def test_publication_surfaces_use_neutral_motivation_language():
    forbidden_motivation = "so" + "cial"
    paths = (
        SKILL,
        REFERENCE,
        OPENAI_YAML,
        FINDING_TEMPLATE,
        PUBLICATION_TEMPLATE,
        FINDING_FORMAT,
        REFERENCE_FINDING,
        REFERENCE_PUBLICATION,
        ROOT / "docs" / "benchmarks" / "index.md",
        ROOT / "docs" / "findings" / "README.md",
    )
    for path in paths:
        assert forbidden_motivation not in path.read_text(encoding="utf-8").casefold(), path


def test_reference_finding_uses_publication_ready_result_card():
    text = REFERENCE_FINDING.read_text(encoding="utf-8")
    card = _section(text, "## Result card")

    assert "<!-- benchmark-result-card/v1 -->" in text
    for required in (
        "| Model |",
        "| Hardware |",
        "| Runtime |",
        "| Recipe |",
        "| Measurement path |",
        "| Contract |",
        "| Evidence |",
        "| Decision |",
        "| Headline measurement | Local result | Conditions |",
        "**Why it matters:**",
        "**Important caveat:**",
        "[Evidence manifest]",
        "[Publication summary]",
    ):
        assert required in card
    assert "not a c2 result" in card.casefold()


def test_reference_publication_summary_is_bounded_and_platform_ready():
    text = REFERENCE_PUBLICATION.read_text(encoding="utf-8")
    assert "<!-- benchmark-publication-summary/v1 -->" in text
    assert "**Model identity:**" in text
    assert "**Runtime identity:**" in text
    assert "**Recipe:**" in text
    assert "**Measurement path:**" in text

    x_post = _fenced_block(_section(text, "## X / short post"), "text")
    reddit = _section(text, "## Reddit")
    reddit_title = _fenced_block(reddit, "text")
    reddit_body = _fenced_block(reddit, "markdown")
    reddit_body_single_line = " ".join(reddit_body.split())

    assert len(x_post) <= 260
    assert x_post.startswith("Local ")
    assert "TP=2/c1" in x_post
    assert "misses retained" in x_post.casefold()
    assert "https://fakoli.github.io/anvil-serving/findings/" in x_post

    assert len(reddit_title) <= 120
    for required in (
        "p50 of five",
        "bounded local c1 result",
        "not a universal model ranking",
        "Full methodology, failures, and raw artifacts",
    ):
        assert required in reddit_body_single_line

    ledger = _section(text, "## Claim ledger")
    assert ledger.count(".json`](") >= 7
    assert "three retained literal-rubric misses" in ledger
    assert "## Screenshot alt text" in text


def test_reference_publication_metrics_reconcile_with_retained_artifacts():
    summary = json.loads(REFERENCE_SUMMARY.read_text(encoding="utf-8"))
    with REFERENCE_THROUGHPUT.open(encoding="utf-8", newline="") as handle:
        csv_rows = {
            int(row["target_context"]): row
            for row in csv.DictReader(handle)
        }

    summary_rows = {
        row["target_context"]: row
        for row in summary["context_sweep"]["rows"]
    }
    assert csv_rows.keys() == summary_rows.keys()
    for target, row in summary_rows.items():
        csv_row = csv_rows[target]
        assert int(csv_row["actual_prompt_p50"]) == row["actual_prompt_p50"]
        assert int(csv_row["repetitions"]) == row["repetitions"]
        for csv_name, summary_name in (
            ("ttft_p50_s", "ttft_p50_s"),
            ("effective_prefill_p50_tok_s", "effective_prefill_p50_tok_s"),
            ("decode_p50_tok_s", "decode_p50_tok_s"),
            ("e2e_p50_s", "e2e_p50_s"),
        ):
            assert float(csv_row[csv_name]) == row[summary_name]

    finding = REFERENCE_FINDING.read_text(encoding="utf-8")
    publication = REFERENCE_PUBLICATION.read_text(encoding="utf-8")
    identity = summary["identity"]
    decision = summary["decision"]
    memory = summary["memory"]
    multimodal = summary["multimodal"]

    exact_model = f"{identity['model']}@{identity['model_revision']}"
    assert exact_model in publication
    assert identity["served_name"] in publication
    assert identity["engine_revision"] in publication
    assert identity["runtime_image_digest"] in publication

    for text in (finding, publication):
        assert f"{decision['context_window']:,}" in text
        assert f"{decision['max_output_tokens']:,}" in text
        assert f"{memory['maximum_server_tokens']:,}" in text
        assert f"{memory['two_full_windows_token_shortfall']:,}" in text
        assert (
            f"{multimodal['direct']['passed']}/{multimodal['direct']['attempts']}"
            in text
        )
        assert (
            f"{multimodal['live_strict_total']['passed']}/"
            f"{multimodal['live_strict_total']['attempts']}"
            in text
        )

    for target in (4096, 131072, 253952):
        row = summary_rows[target]
        assert f"{row['decode_p50_tok_s']:.1f}" in finding
        assert f"{row['decode_p50_tok_s']:.1f}" in publication

    for modality in ("image", "video", "mixed"):
        latency = multimodal["direct_latency_seconds"][modality]["p50"]
        assert f"{latency:.3f}" in finding
        assert f"{latency:.3f}" in publication

    reserve = summary["context_sweep"]["full_reserve_separate_gate"]
    for text in (finding, publication):
        assert f"{reserve['actual_prompt_tokens']:,}" in text
        assert f"{reserve['decode_tok_s']:.1f}" in text

    x_post = _fenced_block(_section(publication, "## X / short post"), "text")
    assert f"{summary_rows[4096]['decode_p50_tok_s']:.1f}" in x_post
    assert "30/30 direct" in x_post
    assert "57/60 live routed" in x_post


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
            text.index("Qwen3.8"),
            text.index("DeepSeek V4 Flash"),
            text.index("Qwen3.5"),
            text.index("Agents-A1"),
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
            part.startswith((".scratch", ".pytest")) for part in path.parts
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


def test_benchmark_producing_skills_delegate_publication_ready_format():
    skill_paths = (
        ROOT / "skills" / "anvil-serving-llm-qualification" / "SKILL.md",
        ROOT / "skills" / "anvil-serving-stt-benchmark" / "SKILL.md",
        ROOT / "skills" / "anvil-serving-voice-ops" / "SKILL.md",
        ROOT / "skills" / "anvil-serving-kernel-tuning" / "SKILL.md",
        ROOT / ".agents" / "skills" / "anvil-serving-workbench" / "SKILL.md",
    )
    for path in skill_paths:
        text = path.read_text(encoding="utf-8")
        assert "skills/anvil-serving-benchmark-docs/SKILL.md" in text, path
        assert "publication-ready" in text, path
        assert "format-only" in text, path
        assert "retained" in text, path
    stt = skill_paths[1].read_text(encoding="utf-8")
    assert "protected/co-resident topology" in stt
    assert "non-promotion decision" in stt
