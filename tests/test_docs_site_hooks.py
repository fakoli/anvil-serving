"""The documentation site's generated views stay tied to their sources."""

import importlib.util
import json
import shlex
from pathlib import Path

from anvil_serving.product_families import PRODUCT_FAMILIES

REPO = Path(__file__).resolve().parents[1]


def _hook(name):
    spec = importlib.util.spec_from_file_location(name, REPO / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_every_journey_step_is_a_command_in_the_cli_manifest():
    manifest = json.loads((REPO / "docs" / "CLI-COMMAND-MANIFEST.json").read_text())
    commands = {command["path"] for command in manifest["commands"]}
    for family in PRODUCT_FAMILIES:
        assert (REPO / "docs" / "journeys" / f"{family.id}.md").is_file(), family.id
        for step in family.journey:
            words = shlex.split(step.cli)
            assert words[0] == "anvil-serving", step.cli
            path = []
            for word in words[1:]:
                if " ".join([*path, word]) not in commands:
                    break
                path.append(word)
            assert path, f"{family.id}/{step.stage}: {step.cli!r} names no CLI command"


def test_journey_views_render_every_family_and_step():
    hook = _hook("mkdocs_product_journeys")
    href = "../journeys/{}/".format
    cards, sheet = hook.render_cards(href), hook.render_cheatsheet(href)
    for family in PRODUCT_FAMILIES:
        page = hook.render(family)
        assert f'href="../journeys/{family.id}/"' in cards and family.name.replace("&", "&amp;") in cards
        assert page.count('class="av-step"') == len(family.journey)
        for step in family.journey:
            assert step.cli in page
            assert step.cli.replace("<", "&lt;").replace(">", "&gt;") in sheet


def test_record_views_escape_values_and_show_unrecorded_fields():
    hook = _hook("mkdocs_benchmark_records")
    record = {
        "id": "2026-01-02-x", "date": "2026-01-02", "title": "A <b>result</b>",
        "categories": ["capacity"], "decision": {"labels": "`challenger`, `no-promotion`"},
        "subject": {"model": ["m<1>", "m2"], "engine": "not recorded", "quantization": "fp8", "hardware": "RTX 5090"},
        "workload": {"context_tokens": [8192], "concurrency": "not recorded"},
        "headline": [{"metric": "TTFT p50", "value": 120, "unit": "ms", "condition": "C1"}],
        "limitations": ["one <task>"], "gaps": ["engine", "concurrency"],
        "artifacts": {"capacity": 2, "quality_suites": 0},
        "links": {"finding": "2026-01-02-x.md", "evidence_index": "2026-01-02-x-evidence/README.md"},
    }
    hook._index = {"not_recorded": "not recorded", "records": [record, {**record, "links": {"finding": None, "evidence_index": None}}]}
    href = "../findings/{}".format

    header = hook.render_record(record, href)
    assert "m&lt;1&gt;, m2" in header and "one &lt;task&gt;" in header
    assert header.count('class="is-gap"') == 2
    assert 'av-badge--info">challenger<' in header and 'av-badge--warn">no-promotion<' in header
    assert "\n\n" not in header  # one raw HTML block inside Markdown

    latest = hook.render_latest(5, href)
    assert latest.count('class="av-card"') == 1 and "A &lt;b&gt;result&lt;/b&gt;" in latest
    assert hook.render_table(href).count("<tr>") == 3
