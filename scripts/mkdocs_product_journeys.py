"""MkDocs hook: render product-family journeys from the canonical catalog.

Markers on any page are replaced at build time from
``anvil_serving.product_families`` -- the same catalog ``anvil-serving product``
prints -- so the site and the CLI cannot drift apart:

- ``<!-- product-journey:<family-id> -->``: that family's promise, boundary, and
  ordered journey steps;
- ``<!-- product-journey:cards -->``: one linked card per family;
- ``<!-- product-journey:cheatsheet -->``: every journey's commands on one page.
"""

from __future__ import annotations

import re
import sys
from html import escape
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from anvil_serving.product_families import PRODUCT_FAMILIES  # noqa: E402

MARKER = re.compile(r"<!-- product-journey:([a-z0-9-]+) -->")
_FAMILIES = {family.id: family for family in PRODUCT_FAMILIES}


def _stage(step) -> str:
    return step.stage.replace("-", " ").capitalize()


def render(family) -> str:
    lines = [
        f'<p class="av-lead">{escape(family.promise)}</p>',
        "",
        '!!! info "Boundary"',
        f"    {family.boundary}",
        "",
        "## The journey",
        "",
        f"Each step is a supported command. Run `anvil-serving product journey {family.id}` "
        "to print the same journey from the CLI.",
        "",
        '<div class="av-steps" markdown="1">',
    ]
    for number, step in enumerate(family.journey, start=1):
        lines += [
            "",
            '<div class="av-step" markdown="1">',
            f'<div class="av-step__num" aria-hidden="true">{number}</div>',
            '<div class="av-step__body" markdown="1">',
            "",
            f"### {_stage(step)}",
            "",
            step.intent,
            "",
            "```bash",
            step.cli,
            "```",
            "",
            f'<p class="av-step__result"><strong>Result</strong> {escape(step.outcome)}</p>',
            "",
            "</div>",
            "</div>",
        ]
    lines += ["", "</div>"]
    return "\n".join(lines)


def render_cards(href) -> str:
    """One card per family; ``href(family_id)`` returns the page-relative URL."""
    cards = []
    for number, family in enumerate(PRODUCT_FAMILIES, start=1):
        cards.append(
            f'<a class="av-card" href="{escape(href(family.id))}">'
            f'<span class="av-card__kicker">Journey {number:02d}</span>'
            f'<span class="av-card__title">{escape(family.name)}</span>'
            f'<span class="av-card__body">{escape(family.promise)}</span>'
            f'<span class="av-card__go">{len(family.journey)} steps →</span>'
            "</a>"
        )
    return '<div class="av-grid">' + "".join(cards) + "</div>"


def render_cheatsheet(href) -> str:
    blocks = []
    for family in PRODUCT_FAMILIES:
        steps = "".join(
            f"<li><span>{escape(_stage(step))}</span><code>{escape(step.cli)}</code></li>"
            for step in family.journey
        )
        blocks.append(
            '<section class="av-sheet__family">'
            f'<h3 id="{family.id}"><a href="{escape(href(family.id))}">{escape(family.name)}</a></h3>'
            f"<ol>{steps}</ol></section>"
        )
    return '<div class="av-sheet">' + "".join(blocks) + "</div>"


def on_page_markdown(markdown, page, config, files):
    def href(family_id: str) -> str:
        target = files.get_file_from_path(f"journeys/{family_id}.md")
        if target is None:
            raise ValueError(f"no journey page for product family {family_id!r}")
        return target.url_relative_to(page.file)

    def replace(match):
        key = match.group(1)
        if key == "cards":
            return render_cards(href)
        if key == "cheatsheet":
            return render_cheatsheet(href)
        family = _FAMILIES.get(key)
        if family is None:
            raise ValueError(f"{page.file.src_uri}: unknown product family {key!r}")
        return render(family)

    return MARKER.sub(replace, markdown)
