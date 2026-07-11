#!/usr/bin/env python3
"""Audit active CLI references and validate manifest-generated documentation.

The check path is read-only and hermetic. ``--update`` is the deliberate
maintenance path for the generated CLI tables and numeric reference inventory.
"""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import asdict, dataclass
import json
from pathlib import Path
import re
import subprocess
import sys
from typing import Iterable


SCHEMA_VERSION = 1
ROOT = Path(__file__).resolve().parents[1]
MANIFEST_REL = Path("docs/CLI-COMMAND-MANIFEST.json")
CLI_DOC_REL = Path("docs/CLI.md")
INVENTORY_REL = Path("docs/CLI-REFERENCE-AUDIT.json")
FIXTURE_REL = Path("tests/fixtures/cli_reference_audit")

INDEX_START = "<!-- BEGIN GENERATED CLI MANIFEST INDEX -->"
INDEX_END = "<!-- END GENERATED CLI MANIFEST INDEX -->"
MIGRATION_START = "<!-- BEGIN GENERATED CLI TOMBSTONES -->"
MIGRATION_END = "<!-- END GENERATED CLI TOMBSTONES -->"

TEXT_SUFFIXES = frozenset({".md", ".py", ".toml", ".json", ".yml", ".yaml", ".sh", ".ps1"})
SKILL_ROOT_LABELS = (".agents/skills", ".claude/skills", "skills", "examples/*/skills")

_PREFIX = r"(?:\banvil-serving\b|\bpython\s+-m\s+anvil_serving\.cli\b)\s+"
LEGACY_PATTERNS = {
    "serve": r"serve\b",
    "deploy": r"deploy\b",
    "multiplexer": r"multiplexer\b",
    "cache-prune": r"cache-prune\b",
    "score": r"score\b",
    "profile": r"profile\b",
    "preflight": r"preflight\b",
    "benchmark": r"benchmark\b",
    "external-bench": r"external-bench\b",
    "calibrate": r"calibrate\b",
    "gpus": r"gpus\b",
    "voice-sidecar": r"voice-sidecar\b",
    "onboard": r"onboard\b",
    "models-recipe": r"models\s+recipe\b",
    "voice-up-down": r"voice\s+(?:up|down)\b",
    "voice-run-bridge": r"voice\s+(?:run|bridge)\b",
    "voice-start-stop": r"voice\s+(?:start|stop)\b",
    "mcp-list-tools": r"mcp\s+list-tools\b",
    "mcp-bare": r"mcp(?=\s*(?:`|$|[.,;:]))",
}
CANONICAL_PATTERNS = {
    "router-run": r"router\s+run\b",
    "serves-render": r"serves\s+render\b",
    "serves-multiplex": r"serves\s+multiplex\b",
    "models-cache-prune": r"models\s+cache\s+prune\b",
    "models-score": r"models\s+score\b",
    "eval-usage": r"eval\s+usage\b",
    "eval-preflight": r"eval\s+preflight\b",
    "eval-benchmark-run": r"eval\s+benchmark\s+run\b",
    "eval-benchmark-external": r"eval\s+benchmark\s+external\b",
    "eval-calibrate": r"eval\s+calibrate\b",
    "host-gpus": r"host\s+gpus\b",
    "voice-sidecar": r"voice\s+sidecar\b",
    "init": r"init\b",
    "models-recipes": r"models\s+recipes\b",
    "voice-audio": r"voice\s+audio\s+(?:up|down|status|logs)\b",
    "voice-proxy": r"voice\s+proxy\s+(?:run|up|down|restart|status|logs|bridge)\b",
    "mcp-serve": r"mcp\s+serve\b",
    "mcp-tools": r"mcp\s+tools\b",
}

_LEGACY_RE = {
    name: re.compile(_PREFIX + pattern, re.IGNORECASE)
    for name, pattern in LEGACY_PATTERNS.items()
}
_CANONICAL_RE = {
    name: re.compile(_PREFIX + pattern, re.IGNORECASE)
    for name, pattern in CANONICAL_PATTERNS.items()
}
_BARE_LEGACY_RE = {
    name: re.compile(r"`" + pattern + r"(?=\s|`|$)", re.IGNORECASE)
    for name, pattern in LEGACY_PATTERNS.items()
    if name
    in {
        "models-recipe",
        "voice-up-down",
        "voice-run-bridge",
        "voice-start-stop",
        "mcp-list-tools",
        "onboard",
    }
}
_BARE_LEGACY_RE["benchmark"] = re.compile(r"`benchmark\s+external(?=\s|`|$)", re.IGNORECASE)
_SKILL_BARE_RE = {
    name: re.compile(r"`" + LEGACY_PATTERNS[name] + r"(?=\s|`|$)", re.IGNORECASE)
    for name in {"serve", "multiplexer", "profile", "preflight", "score"}
}


@dataclass(frozen=True)
class Hit:
    kind: str
    name: str
    path: str
    line: int
    category: str
    allowed: bool
    text: str


@dataclass(frozen=True)
class ScanResult:
    scope: str
    files: tuple[str, ...]
    skill_roots: tuple[str, ...]
    hits: tuple[Hit, ...]

    @property
    def violations(self) -> tuple[Hit, ...]:
        return tuple(hit for hit in self.hits if hit.kind == "legacy" and not hit.allowed)


def _is_excluded(relative: Path) -> bool:
    value = relative.as_posix()
    parts = set(relative.parts)
    if parts & {".git", ".venv", "build", "dist", "site", "__pycache__"}:
        return True
    return (
        value.startswith("docs/findings/")
        or value.startswith("specs/archive/")
        or value.startswith(".anvil/")
        or value.startswith("tests/fixtures/cli_reference_audit/")
        or value.startswith("tests/fixtures/eval-data/")
        or value == "scripts/audit_cli_references.py"
        or value == INVENTORY_REL.as_posix()
    )


def _text_files(base: Path, relative_root: Path) -> Iterable[Path]:
    root = base / relative_root
    if not root.exists():
        return ()
    return (
        path
        for path in root.rglob("*")
        if path.is_file()
        and path.suffix.lower() in TEXT_SUFFIXES
        and not _is_excluded(path.relative_to(base))
    )


def _docs_files(base: Path) -> set[Path]:
    paths = {
        path
        for path in base.glob("*.md")
        if path.is_file() and not _is_excluded(path.relative_to(base))
    }
    paths.update(_text_files(base, Path("docs")))
    return paths


def _root_product_files(base: Path) -> set[Path]:
    return {
        path
        for path in base.iterdir()
        if path.is_file()
        and (path.suffix.lower() in TEXT_SUFFIXES or path.name == "Dockerfile")
        and not _is_excluded(path.relative_to(base))
    }


def _skill_files(base: Path) -> set[Path]:
    paths: set[Path] = set()
    for root in (Path(".agents/skills"), Path(".claude/skills"), Path("skills")):
        skill_root = base / root
        if skill_root.exists():
            paths.update(skill_root.rglob("SKILL.md"))
    examples = base / "examples"
    if examples.exists():
        paths.update(
            path
            for path in examples.rglob("SKILL.md")
            if "skills" in path.relative_to(base).parts
        )
    return {path for path in paths if path.is_file()}


def _tracked_paths(root: Path) -> set[str]:
    completed = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=root,
        text=False,
        capture_output=True,
        check=False,
        shell=False,
    )
    if completed.returncode != 0:
        raise ValueError("production reference audit requires a readable Git index")
    return {
        value.decode("utf-8").replace("\\", "/")
        for value in completed.stdout.split(b"\0")
        if value
    }


def discover_files(root: Path, scope: str) -> tuple[Path, tuple[Path, ...]]:
    scan_root = root
    fixture_scope = scope == "fixtures"
    if scope == "fixtures":
        scan_root = root / FIXTURE_REL / "input"
        if not scan_root.is_dir():
            raise FileNotFoundError(f"fixture input directory not found: {scan_root}")
        scope = "full"

    paths: set[Path]
    if scope == "docs":
        paths = _docs_files(scan_root)
    elif scope == "skills":
        paths = _skill_files(scan_root)
    elif scope == "full":
        paths = _root_product_files(scan_root) | _docs_files(scan_root) | _skill_files(scan_root)
        for relative_root in (
            Path("examples"),
            Path("tests"),
            Path("anvil_serving"),
            Path("scripts"),
            Path(".github"),
            Path("configs"),
            Path("templates"),
            Path("plugins"),
            Path("specs"),
        ):
            paths.update(_text_files(scan_root, relative_root))
    else:
        raise ValueError(f"unsupported scope: {scope}")
    if not fixture_scope:
        tracked = _tracked_paths(scan_root)
        paths = {path for path in paths if path.relative_to(scan_root).as_posix() in tracked}
    return scan_root, tuple(sorted(paths, key=lambda item: item.relative_to(scan_root).as_posix()))


def _category(relative: Path) -> str:
    value = relative.as_posix()
    if value == "CHANGELOG.md" or value == "README.md" or value.startswith("docs/"):
        return "docs"
    if (
        value.startswith(".agents/skills/")
        or value.startswith(".claude/skills/")
        or value.startswith("skills/")
        or (value.startswith("examples/") and "/skills/" in value)
    ):
        return "skills"
    if value.startswith(("examples/", "configs/", "templates/")):
        return "examples"
    if value.startswith("tests/"):
        return "tests"
    if value.startswith(("anvil_serving/", "plugins/")):
        return "implementation"
    return "tooling"


def _skill_root(relative: Path) -> str | None:
    value = relative.as_posix()
    for root in SKILL_ROOT_LABELS[:3]:
        if value.startswith(root + "/"):
            return root
    if value.startswith("examples/") and "/skills/" in value:
        return SKILL_ROOT_LABELS[3]
    return None


def _legacy_allowed(relative: Path, category: str, heading: str) -> bool:
    value = relative.as_posix()
    if value == "CHANGELOG.md":
        return True
    if value in {"docs/CLI-CONSOLIDATION-INVENTORY.md", "docs/CLI-LEGACY-DISPOSITIONS.md"}:
        return True
    if value.startswith("docs/adr/"):
        return True
    if value == "docs/CLI.md" and heading == "migration from legacy commands":
        return True
    if value == "docs/VOICE.md" and heading == "removed module-level paths":
        return True
    if value == "README.md" and heading == "cli compatibility notes":
        return True
    if category == "tests":
        return True
    if value in {"anvil_serving/cli.py", "anvil_serving/command_tree.py"}:
        return True
    return False


def scan(root: Path, scope: str) -> ScanResult:
    scan_root, files = discover_files(root, scope)
    hits: list[Hit] = []
    skill_roots: set[str] = set()
    for path in files:
        relative = path.relative_to(scan_root)
        category = _category(relative)
        root_label = _skill_root(relative)
        if root_label:
            skill_roots.add(root_label)
        heading = ""
        text = path.read_text(encoding="utf-8")
        for line_number, line in enumerate(text.splitlines(), start=1):
            heading_match = re.match(r"^#{1,6}\s+(.+?)\s*$", line)
            if heading_match:
                heading = heading_match.group(1).strip().casefold()
            allowed = _legacy_allowed(relative, category, heading)
            for name, pattern in _LEGACY_RE.items():
                if pattern.search(line):
                    hits.append(
                        Hit("legacy", name, relative.as_posix(), line_number, category, allowed, line.strip())
                    )
            for name, pattern in _BARE_LEGACY_RE.items():
                if pattern.search(line):
                    hits.append(
                        Hit("legacy", name, relative.as_posix(), line_number, category, allowed, line.strip())
                    )
            if category == "skills":
                for name, pattern in _SKILL_BARE_RE.items():
                    if pattern.search(line):
                        hits.append(
                            Hit("legacy", name, relative.as_posix(), line_number, category, allowed, line.strip())
                        )
            for name, pattern in _CANONICAL_RE.items():
                if pattern.search(line):
                    hits.append(
                        Hit("canonical", name, relative.as_posix(), line_number, category, True, line.strip())
                    )
    ordered_hits = tuple(sorted(hits, key=lambda hit: (hit.path, hit.line, hit.kind, hit.name)))
    return ScanResult(
        scope=scope,
        files=tuple(path.relative_to(scan_root).as_posix() for path in files),
        skill_roots=tuple(sorted(skill_roots)),
        hits=ordered_hits,
    )


def inventory_record(result: ScanResult) -> dict[str, object]:
    canonical = Counter(hit.name for hit in result.hits if hit.kind == "canonical")
    allowed = Counter(hit.name for hit in result.hits if hit.kind == "legacy" and hit.allowed)
    violations = Counter(hit.name for hit in result.violations)
    categories = Counter(_category(Path(path)) for path in result.files)
    return {
        "files_scanned": len(result.files),
        "files_by_category": dict(sorted(categories.items())),
        "skill_roots": list(result.skill_roots),
        "canonical_counts": {name: canonical[name] for name in CANONICAL_PATTERNS},
        "allowed_legacy_counts": {name: allowed[name] for name in LEGACY_PATTERNS},
        "violation_counts": {name: violations[name] for name in LEGACY_PATTERNS},
    }


def _load_json(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, value: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _manifest(root: Path) -> dict[str, object]:
    value = _load_json(root / MANIFEST_REL)
    if not isinstance(value.get("commands"), list):
        raise ValueError("command manifest must contain a commands array")
    return value


def _markdown(value: object) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


def render_manifest_index(manifest: dict[str, object]) -> str:
    commands = [record for record in manifest["commands"] if record["visible"]]
    option_sets = [
        {tuple(option["flags"]) for option in record["options"]}
        for record in commands
    ]
    global_options = set.intersection(*option_sets) if option_sets else set()
    lines = [
        "| Command path | Purpose | Class / output | Declared command options |",
        "|---|---|---|---|",
    ]
    seen: set[str] = set()
    for record in commands:
        path = str(record["path"])
        if path in seen:
            raise ValueError(f"duplicate visible manifest path: {path}")
        seen.add(path)
        options = [
            ", ".join(f"`{flag}`" for flag in option["flags"])
            for option in record["options"]
            if tuple(option["flags"]) not in global_options
        ]
        lines.append(
            "| `{}` | {} | `{}` / `{}` | {} |".format(
                _markdown(path),
                _markdown(record["summary"]),
                _markdown(record["mutation_class"]),
                _markdown(record["output_policy"]),
                "<br>".join(options) if options else "-",
            )
        )
    return "\n".join(lines)


def render_tombstones(manifest: dict[str, object]) -> str:
    rows: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for record in manifest["commands"]:
        tombstone = record.get("tombstone")
        if tombstone:
            row = (str(record["path"]), str(tombstone["replacement"]))
            if row not in seen:
                rows.append(row)
                seen.add(row)
        for option in record["options"]:
            option_tombstone = option.get("tombstone")
            if not option_tombstone:
                continue
            removed = f"{record['path']} {' / '.join(option['flags'])}"
            row = (removed, str(option_tombstone["replacement"]))
            if row not in seen:
                rows.append(row)
                seen.add(row)
    lines = ["| Removed path | Replacement |", "|---|---|"]
    lines.extend(f"| `{_markdown(old)}` | `{_markdown(new)}` |" for old, new in rows)
    return "\n".join(lines)


def _block(text: str, start: str, end: str) -> str:
    pattern = re.compile(re.escape(start) + r"\n(.*?)\n" + re.escape(end), re.DOTALL)
    match = pattern.search(text)
    if not match:
        raise ValueError(f"generated block markers missing: {start}")
    return match.group(1)


def _replace_block(text: str, start: str, end: str, body: str) -> str:
    pattern = re.compile(re.escape(start) + r"\n.*?\n" + re.escape(end), re.DOTALL)
    replacement = f"{start}\n{body}\n{end}"
    updated, count = pattern.subn(lambda _match: replacement, text, count=1)
    if count != 1:
        raise ValueError(f"generated block markers missing or duplicated: {start}")
    return updated


def generated_docs_match(root: Path) -> bool:
    manifest = _manifest(root)
    text = (root / CLI_DOC_REL).read_text(encoding="utf-8")
    return (
        _block(text, INDEX_START, INDEX_END) == render_manifest_index(manifest)
        and _block(text, MIGRATION_START, MIGRATION_END) == render_tombstones(manifest)
    )


def update_generated_docs(root: Path) -> None:
    manifest = _manifest(root)
    path = root / CLI_DOC_REL
    text = path.read_text(encoding="utf-8")
    text = _replace_block(text, INDEX_START, INDEX_END, render_manifest_index(manifest))
    text = _replace_block(text, MIGRATION_START, MIGRATION_END, render_tombstones(manifest))
    path.write_text(text, encoding="utf-8")


def _inventory_path(root: Path, scope: str) -> Path:
    if scope == "fixtures":
        return root / FIXTURE_REL / "expected.json"
    return root / INVENTORY_REL


def inventory_matches(root: Path, scope: str, record: dict[str, object]) -> bool:
    path = _inventory_path(root, scope)
    if not path.exists():
        return False
    value = _load_json(path)
    if scope == "fixtures":
        return value == {"schema_version": SCHEMA_VERSION, "record": record}
    return value.get("schema_version") == SCHEMA_VERSION and value.get("scopes", {}).get(scope) == record


def update_inventories(root: Path) -> None:
    scopes = {scope: inventory_record(scan(root, scope)) for scope in ("docs", "skills", "full")}
    _write_json(root / INVENTORY_REL, {"schema_version": SCHEMA_VERSION, "scopes": scopes})
    fixture = inventory_record(scan(root, "fixtures"))
    _write_json(
        root / FIXTURE_REL / "expected.json",
        {"schema_version": SCHEMA_VERSION, "record": fixture},
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT, help="Repository root (default: checkout root).")
    parser.add_argument("--scope", choices=("fixtures", "docs", "skills", "full"), default="full")
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--check", action="store_true", help="Read-only validation against checked-in state.")
    action.add_argument("--update", action="store_true", help="Regenerate docs tables and all inventories.")
    parser.add_argument("--json", action="store_true", help="Emit a structured JSON report.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    root = args.root.resolve()
    try:
        result = scan(root, args.scope)
        record = inventory_record(result)
        if args.update:
            if result.violations:
                raise ValueError("refusing to update while active legacy-reference violations exist")
            update_generated_docs(root)
            update_inventories(root)
        inventory_ok = inventory_matches(root, args.scope, record)
        generated_ok = True if args.scope == "fixtures" else generated_docs_match(root)
    except (FileNotFoundError, OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        payload = {"ok": False, "scope": args.scope, "error": str(exc)}
        if args.json:
            print(json.dumps(payload, sort_keys=True))
        else:
            print(f"CLI reference audit failed: {exc}", file=sys.stderr)
        return 1

    ok = not result.violations and inventory_ok and generated_ok
    payload = {
        "ok": ok,
        "scope": args.scope,
        "record": record,
        "inventory_match": inventory_ok,
        "generated_docs_match": generated_ok,
        "files": list(result.files),
        "violations": [asdict(hit) for hit in result.violations],
    }
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(
            f"scope={args.scope} files={len(result.files)} "
            f"violations={len(result.violations)} inventory={'ok' if inventory_ok else 'stale'} "
            f"generated={'ok' if generated_ok else 'stale'}"
        )
        for hit in result.violations:
            print(f"{hit.path}:{hit.line}: stale {hit.name}: {hit.text}", file=sys.stderr)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
