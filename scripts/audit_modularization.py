#!/usr/bin/env python
"""Report reproducible structure metrics for the milestone-6 modularization."""

from __future__ import annotations

import argparse
import ast
import json
import subprocess
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any


SCOPES = {
    "benchmark": (
        "anvil_serving/benchmark.py",
        "anvil_serving/benchmarking",
    ),
    "controller": (
        "anvil_serving/controller.py",
        "anvil_serving/control_plane/controller",
    ),
    "mcp": (
        "anvil_serving/mcp.py",
        "anvil_serving/control_plane/mcp",
    ),
}
FACADES = {
    "benchmark": "anvil_serving/benchmark.py",
    "controller": "anvil_serving/controller.py",
    "mcp": "anvil_serving/mcp.py",
}
WORKTREE = "WORKTREE"


def _git(*args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return result.stdout


def _in_scope(path: str) -> bool:
    return any(
        path == root or path.startswith(root.rstrip("/") + "/")
        for roots in SCOPES.values()
        for root in roots
    )


def load_sources(ref: str = WORKTREE, root: Path = Path(".")) -> dict[str, str]:
    """Load scoped Python sources from the worktree or an immutable Git ref."""

    if ref == WORKTREE:
        sources: dict[str, str] = {}
        for roots in SCOPES.values():
            for item in roots:
                path = root / item
                paths = [path] if path.is_file() else sorted(path.rglob("*.py"))
                for candidate in paths:
                    relative = candidate.relative_to(root).as_posix()
                    sources[relative] = candidate.read_text(encoding="utf-8")
        return sources

    paths = [
        path
        for path in _git("ls-tree", "-r", "--name-only", ref, "--", "anvil_serving").splitlines()
        if path.endswith(".py") and _in_scope(path)
    ]
    return {path: _git("show", f"{ref}:{path}") for path in paths}


def _source_loc(text: str) -> int:
    """Count nonblank, non-comment physical source lines."""

    return sum(
        1
        for line in text.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    )


def _logical_counts(text: str) -> tuple[int, int]:
    tree = ast.parse(text)
    functions = sum(
        isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        for node in ast.walk(tree)
    )
    branches = 0
    for node in ast.walk(tree):
        if isinstance(node, (ast.If, ast.IfExp, ast.For, ast.AsyncFor, ast.While)):
            branches += 1
        elif isinstance(node, ast.BoolOp):
            branches += max(0, len(node.values) - 1)
        elif isinstance(node, ast.Match):
            branches += len(node.cases)
        elif isinstance(node, (ast.Try, ast.TryStar)):
            branches += len(node.handlers) + bool(node.orelse) + bool(node.finalbody)
        elif isinstance(node, ast.comprehension):
            branches += len(node.ifs)
    return functions, branches


def _module_name(path: str) -> str:
    parts = list(Path(path).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _resolve_from(module: str, path: str, node: ast.ImportFrom) -> list[str]:
    if node.level == 0:
        return [node.module] if node.module else []

    package = module if path.endswith("/__init__.py") else module.rsplit(".", 1)[0]
    parts = package.split(".")
    keep = len(parts) - (node.level - 1)
    if keep <= 0:
        return []
    prefix = parts[:keep]
    if node.module:
        return [".".join([*prefix, node.module])]
    package_target = ".".join(prefix)
    return [
        package_target,
        *[".".join([*prefix, alias.name]) for alias in node.names],
    ]


def import_graph(sources: Mapping[str, str]) -> dict[str, set[str]]:
    modules = {_module_name(path): path for path in sources}
    graph = {module: set() for module in modules}
    for module, path in modules.items():
        tree = ast.parse(sources[path], filename=path)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                targets = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                targets = _resolve_from(module, path, node)
            else:
                continue
            for target in targets:
                if target in modules:
                    graph[module].add(target)
    return graph


def find_cycles(graph: Mapping[str, Iterable[str]]) -> list[list[str]]:
    """Return deterministic directed cycles from a module dependency graph."""

    visited: set[str] = set()
    active: set[str] = set()
    stack: list[str] = []
    cycles: set[tuple[str, ...]] = set()

    def visit(module: str) -> None:
        if module in active:
            start = stack.index(module)
            cycle = stack[start:]
            rotations = [tuple(cycle[index:] + cycle[:index]) for index in range(len(cycle))]
            cycles.add(min(rotations))
            return
        if module in visited:
            return
        visited.add(module)
        active.add(module)
        stack.append(module)
        for dependency in sorted(graph[module]):
            visit(dependency)
        stack.pop()
        active.remove(module)

    for module in sorted(graph):
        visit(module)
    return [list(cycle) + [cycle[0]] for cycle in sorted(cycles)]


def audit_sources(sources: Mapping[str, str], ref: str) -> dict[str, Any]:
    groups: dict[str, Any] = {}
    for name, roots in SCOPES.items():
        paths = sorted(
            path
            for path in sources
            if any(path == item or path.startswith(item.rstrip("/") + "/") for item in roots)
        )
        functions = branches = source_loc = 0
        for path in paths:
            text = sources[path]
            file_functions, file_branches = _logical_counts(text)
            functions += file_functions
            branches += file_branches
            source_loc += _source_loc(text)
        facade_text = sources.get(FACADES[name], "")
        facade_functions, facade_branches = (
            _logical_counts(facade_text) if facade_text else (0, 0)
        )
        groups[name] = {
            "files": len(paths),
            "source_loc": source_loc,
            "functions": functions,
            "branches": branches,
            "facade_source_loc": _source_loc(facade_text),
            "facade_functions": facade_functions,
            "facade_branches": facade_branches,
        }
    graph = import_graph(sources)
    return {
        "ref": ref,
        "method": {
            "source_loc": "nonblank, non-comment physical lines",
            "functions": "AST FunctionDef and AsyncFunctionDef nodes",
            "branches": (
                "AST if/conditional/loop nodes, extra boolean operands, match cases, "
                "try handlers/else/finally, and comprehension filters"
            ),
            "imports": "directed AST imports among the scoped production modules",
        },
        "groups": groups,
        "import_modules": len(graph),
        "import_edges": sum(len(edges) for edges in graph.values()),
        "import_cycles": find_cycles(graph),
    }


def audit_ref(ref: str, root: Path = Path(".")) -> dict[str, Any]:
    return audit_sources(load_sources(ref, root), ref)


def _render(audits: list[dict[str, Any]]) -> str:
    lines = [
        "| ref | area | files | source LOC | functions | branches | facade LOC |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for audit in audits:
        for name, values in audit["groups"].items():
            lines.append(
                "| {ref} | {name} | {files} | {source_loc} | {functions} | "
                "{branches} | {facade_source_loc} |".format(
                    ref=audit["ref"], name=name, **values
                )
            )
        cycles = audit["import_cycles"]
        lines.append(
            f"\n{audit['ref']}: {audit['import_modules']} modules, "
            f"{audit['import_edges']} directed import edges, {len(cycles)} cycles."
        )
        lines.extend(" - " + " -> ".join(cycle) for cycle in cycles)
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Audit milestone-6 facade/package structure with reproducible AST metrics."
    )
    parser.add_argument("--before-ref", default="c3af271")
    parser.add_argument("--after-ref", default=WORKTREE)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    audits = [audit_ref(args.before_ref), audit_ref(args.after_ref)]
    print(json.dumps(audits, indent=2, sort_keys=True) if args.json else _render(audits))
    return 1 if audits[-1]["import_cycles"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
