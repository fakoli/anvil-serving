"""Keep the static host declaration aligned without importing VM-only code."""
import ast
import json
from pathlib import Path


def test_static_declaration_matches_only_the_guest_declarative_functions():
    root = Path(__file__).resolve().parents[2]
    tree = ast.parse((root / "connect/test/vm/guest.py").read_text(encoding="utf-8"))
    names = {"_limits", "_rule", "build_manifest"}
    functions = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names]
    assert {node.name for node in functions} == names and len(functions) == 3
    declaration = ast.Module(body=functions, type_ignores=[])
    # This small declarative subset admits no imports, loops, attribute access,
    # indirect calls, or effects from the rest of the guest program.
    allowed = {ast.Module, ast.FunctionDef, ast.arguments, ast.arg, ast.Return,
               ast.Dict, ast.List, ast.Tuple, ast.Name, ast.Load, ast.Store,
               ast.Constant, ast.Subscript, ast.Assign, ast.Call, ast.Expr}
    for node in ast.walk(declaration):
        assert type(node) in allowed
        if isinstance(node, ast.Call):
            assert isinstance(node.func, ast.Name) and node.func.id in {"_limits", "_rule"}
    namespace = {"__builtins__": {}, "Any": object, "dict": dict, "list": list, "str": str, "int": int}
    exec(compile(declaration, "<synthetic-declaration>", "exec"), namespace)
    expected = namespace["build_manifest"]()
    actual = json.loads((root / "connect/test/vm/deployment.json").read_text(encoding="utf-8"))
    assert actual == expected
