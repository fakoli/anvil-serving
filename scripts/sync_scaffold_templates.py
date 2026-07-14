#!/usr/bin/env python3
"""Sync anvil_serving/_scaffold_templates/ from canonical configs and examples.

`anvil-serving init` (default: full home scaffold) ships its reference config
set as PACKAGE DATA under `anvil_serving/_scaffold_templates/` so an installed
tool can resolve the files via importlib.resources (fixes #252 — the source
tree's `configs/` and `examples/` are NOT shipped in the wheel). The canonical
copies remain under `configs/`, `examples/fakoli-dark/`, and `examples/voice/`;
this package dir is a byte-for-byte MIRROR of the subset `init` needs.

Run this whenever a mirrored source file under configs/ or examples/ changes:

    python scripts/sync_scaffold_templates.py            # rewrite the mirror
    python scripts/sync_scaffold_templates.py --check    # CI/test: nonzero if stale

The mapping is the single source of truth in `anvil_serving/init.py`
(`_SCAFFOLD_TEMPLATES`); this script imports it so the two never diverge.
`tests/test_init.py::test_scaffold_templates_match_examples` enforces the mirror
is fresh on every test run.
"""
import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from anvil_serving.init import _SCAFFOLD_TEMPLATES  # noqa: E402

TEMPLATES_DIR = REPO_ROOT / "anvil_serving" / "_scaffold_templates"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true",
                    help="do not write; exit nonzero if any mirror copy is stale")
    args = ap.parse_args(argv)

    stale = []
    for _dest, template_name, source_rel in _SCAFFOLD_TEMPLATES:
        source = (REPO_ROOT / source_rel).read_bytes()
        mirror_path = TEMPLATES_DIR / template_name
        current = mirror_path.read_bytes() if mirror_path.exists() else None
        if current == source:
            continue
        stale.append(template_name)
        if not args.check:
            mirror_path.write_bytes(source)

    if args.check:
        if stale:
            print("stale scaffold templates (run scripts/sync_scaffold_templates.py): "
                  + ", ".join(sorted(stale)), file=sys.stderr)
            return 1
        print("scaffold templates are in sync with examples/")
        return 0

    if stale:
        print("synced: " + ", ".join(sorted(stale)))
    else:
        print("already in sync — nothing to do")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
