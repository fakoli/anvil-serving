"""Keep the public dossier inventory complete without policing its prose."""

from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
INVENTORY = ROOT / "docs/benchmarks/models/index.md"
DOSSIERS = ROOT / "docs/benchmarks/models"
HEADERS = "| Model / configuration | Measured result | Limitation / decision | Evidence date | Dossier |"


def test_inventory_covers_every_dossier_with_one_table_schema():
    text = INVENTORY.read_text(encoding="utf-8")
    tables = re.findall(r"(?ms)^## .+?\n\n(\| Model / configuration.*?)(?=^## |\Z)", text)
    assert len(tables) >= 3
    assert all(table.splitlines()[0] == HEADERS for table in tables)
    for table in tables:
        rows = [line for line in table.splitlines() if line.startswith("|")]
        assert all(line.count("|") == 6 for line in rows)

    links = set(re.findall(r"\]\(([^)]+\.md)\)", text))
    missing = [path.name for path in DOSSIERS.glob("*.md")
               if path.name != "index.md" and path.name not in links]
    assert not missing, f"dossiers absent from inventory: {missing}"
