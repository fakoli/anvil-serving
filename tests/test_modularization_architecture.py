from scripts.audit_modularization import (
    WORKTREE,
    audit_ref,
    find_cycles,
    import_graph,
)


def test_modularized_production_packages_have_directed_acyclic_imports():
    audit = audit_ref(WORKTREE)

    assert audit["import_cycles"] == []


def test_import_audit_detects_function_local_package_back_edges():
    sources = {
        "example/tools/__init__.py": "from .operations import FAMILY\nTOOLS = {}\n",
        "example/tools/operations.py": (
            "FAMILY = object()\n"
            "def operation_declarations():\n"
            "    from . import TOOLS\n"
            "    return TOOLS\n"
        ),
    }

    assert find_cycles(import_graph(sources)) == [
        [
            "example.tools",
            "example.tools.operations",
            "example.tools",
        ]
    ]
