"""MkDocs hook: render product-family journeys from the canonical catalog.

A page that contains ``<!-- product-journey:<family-id> -->`` gets that family's
promise, boundary, and ordered journey steps from
``anvil_serving.product_families`` -- the same catalog ``anvil-serving product``
prints -- so the site and the CLI cannot drift apart.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from anvil_serving.product_families import PRODUCT_FAMILIES  # noqa: E402

MARKER = re.compile(r"<!-- product-journey:([a-z0-9-]+) -->")
_FAMILIES = {family.id: family for family in PRODUCT_FAMILIES}


def render(family) -> str:
    lines = [
        f"**{family.promise}**",
        "",
        '!!! info "Boundary"',
        f"    {family.boundary}",
        "",
        "## The journey",
        "",
        f"Each step is a supported command. Run `anvil-serving product journey {family.id}` "
        "to print the same journey from the CLI.",
    ]
    for number, step in enumerate(family.journey, start=1):
        lines += [
            "",
            f"### {number}. {step.stage.replace('-', ' ').capitalize()}",
            "",
            step.intent,
            "",
            "```bash",
            step.cli,
            "```",
            "",
            f"**Result:** {step.outcome}",
        ]
    return "\n".join(lines)


def on_page_markdown(markdown, page, config, files):
    def replace(match):
        family = _FAMILIES.get(match.group(1))
        if family is None:
            raise ValueError(f"{page.file.src_uri}: unknown product family {match.group(1)!r}")
        return render(family)

    return MARKER.sub(replace, markdown)
