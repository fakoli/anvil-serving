"""Offline behavioral grader for the quasar/swift15 coding fixture.

This only consumes retained native `fast-tier-bakeoff/v1` quality evidence; it
never contacts a model or endpoint.
"""

import argparse
import ast
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path


SUITE = "quasar-swift15-coding"
TIMEOUT_SECONDS = 2
MAX_SOURCE_CHARS = 24576

SPECS = {
    "merge_intervals": ("intervals",),
    "stable_toposort": ("nodes", "edges"),
    "parse_csv_line": ("line",),
    "apply_edits": ("text", "edits"),
    "normalize_posix_path": ("path",),
    "lru_trace": ("capacity", "operations"),
    "decode_uvarint": ("data",),
}

CASES = {
    "merge_intervals": [
        ([[[5, 7], [1, 2], [3, 3], [10, 10], [9, 9]]], [[1, 3], [5, 7], [9, 10]]),
        ([[[1, 4], [2, 8], [8, 9], [-3, -1]]], [[-3, -1], [1, 9]]),
        ([[]], []),
    ],
    "stable_toposort": [
        ([['a', 'b', 'c', 'd'], [['a', 'c'], ['b', 'c']]], ['a', 'b', 'c', 'd']),
        ([['d', 'a', 'b', 'c'], [['a', 'c'], ['b', 'c'], ['a', 'c']]], ['d', 'a', 'b', 'c']),
        ([['a', 'b'], [['a', 'b'], ['b', 'a']]], None),
        ([['a'], [['a', 'missing']]], None),
        ([['a', 'b', 'c'], [['b', 'a']]], ['b', 'a', 'c']),
    ],
    "parse_csv_line": [
        (['a,"b,c","d""e",'], ['a', 'b,c', 'd"e', '']),
        (['"",plain," spaced "'], ['', 'plain', ' spaced ']),
        (['a,"unterminated'], None),
        (['a,"x"oops'], None),
        (['a"b,c'], None),
    ],
    "apply_edits": [
        (['abcdef', [[4, 6, 'Y'], [1, 3, 'X']]], 'aXdY'),
        (['abcd', [[0, 0, '<'], [4, 4, '>']]], '<abcd>'),
        (['abcdef', [[1, 4, 'x'], [3, 5, 'y']]], None),
        (['abc', [[2, 1, 'x']]], None),
        (['abc', [[-1, 1, 'x']]], None),
        (['abc', [[0, 4, 'x']]], None),
        (['abc', [[0, 1, 2]]], None),
    ],
    "normalize_posix_path": [
        (['/a//b/./c/../'], '/a/b'),
        (['/../../x'], '/x'),
        (['////'], '/'),
        (['relative/../x'], None),
        ([7], None),
    ],
    "lru_trace": [
        ([2, [['put', 'a', '1'], ['put', 'b', '2'], ['get', 'a', None], ['put', 'c', '3'], ['get', 'b', None]]], [[ '1', None], [['a', '1'], ['c', '3']]]),
        ([0, [['put', 'a', '1'], ['get', 'a', None]]], [[None], []]),
        ([1, [['put', 'a', '1'], ['bad', 'a', None]]], None),
        ([-1, []], None),
        ([1, [['put', 'a', '1'], ['put', 'a', '2'], ['put', 'b', '3'], ['get', 'a', None]]], [[None], [['b', '3']]]),
        ([1, [['get', 'a', 'not-none']]], None),
    ],
    "decode_uvarint": [
        ([[0]], [0, 1]),
        ([[172, 2, 99]], [300, 2]),
        ([[255, 255, 255, 255, 255, 255, 255, 255, 255, 1]], [18446744073709551615, 10]),
        ([[128, 0]], None),
        ([[128]], None),
        ([[256]], None),
        ([[128, 128, 128, 128, 128, 128, 128, 128, 128, 2]], None),
        ([[]], None),
        ([[-1]], None),
        ([[128, 128, 128, 128, 128, 128, 128, 128, 128, 128, 0]], None),
    ],
}

SAFE_BUILTINS = {"len", "range", "enumerate", "sorted", "min", "max", "sum", "str", "int", "list", "tuple", "dict", "set", "all", "any", "isinstance", "type", "abs", "zip", "reversed", "bool", "iter", "next", "TypeError", "ValueError", "KeyError", "IndexError", "AttributeError", "StopIteration"}
SAFE_METHODS = {"append", "pop", "get", "items", "values", "setdefault", "sort", "split", "join", "strip", "startswith", "endswith", "isdigit", "lower", "upper", "replace", "remove", "discard", "add", "copy"}
ALLOWED_NODES = {
    "Module", "FunctionDef", "arguments", "arg", "Return", "Assign", "AugAssign", "Delete", "Expr", "If", "For", "While", "Break", "Continue", "Pass", "Try", "ExceptHandler", "Raise",
    "Name", "Load", "Store", "Del", "Constant", "List", "Tuple", "Dict", "Set", "Subscript", "Slice", "BinOp", "BoolOp", "UnaryOp",
    "Compare", "IfExp", "Call", "Attribute", "keyword", "Lambda", "ListComp", "SetComp", "DictComp", "GeneratorExp", "comprehension", "Add", "Sub", "Mult", "FloorDiv", "Mod", "Pow", "LShift", "RShift",
    "BitOr", "BitAnd", "And", "Or", "Not", "USub", "UAdd", "Invert", "Eq", "NotEq", "Lt", "LtE", "Gt", "GtE", "Is", "IsNot", "In", "NotIn",
}

class SourceRejected(ValueError):
    pass

class HarnessIncompatible(ValueError):
    pass


def extract_source(content):
    if not isinstance(content, str):
        raise ValueError("attempt has no string content")
    match = re.fullmatch(r"\s*```python\s*\n(.*?)\n?```\s*", content, re.DOTALL | re.IGNORECASE)
    if not match:
        raise ValueError("response must be exactly one fenced Python block")
    source = match.group(1)
    if len(source) > MAX_SOURCE_CHARS:
        raise ValueError("source exceeds size limit")
    return source


def validate_source(source, function, expected_args):
    try:
        tree = ast.parse(source, mode="exec")
    except SyntaxError as exc:
        raise SourceRejected("syntax error: %s" % exc.msg) from exc
    if len(tree.body) != 1 or not isinstance(tree.body[0], ast.FunctionDef):
        raise SourceRejected("source must contain exactly one function definition")
    definition = tree.body[0]
    if definition.name != function or definition.decorator_list:
        raise SourceRejected("wrong function name or decorator")
    args = definition.args
    if args.vararg or args.kwarg or args.kwonlyargs or args.defaults or args.kw_defaults or args.posonlyargs:
        raise SourceRejected("function signature must use only the requested positional arguments")
    if tuple(arg.arg for arg in args.args) != expected_args or any(arg.annotation for arg in args.args):
        raise SourceRejected("function signature does not match the prompt")
    if definition.returns:
        raise HarnessIncompatible("annotations are unsupported by this harness")
    parents = {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node is not definition:
            raise SourceRejected("nested function definitions are forbidden")
        if type(node).__name__ not in ALLOWED_NODES:
            raise HarnessIncompatible("unsupported syntax: %s" % type(node).__name__)
        if isinstance(node, ast.Name) and node.id.startswith("__"):
            raise SourceRejected("private names are forbidden")
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store) and node.id in SAFE_BUILTINS:
            raise SourceRejected("allowlisted callable name is shadowed: %s" % node.id)
        if isinstance(node, ast.Attribute):
            if node.attr.startswith("_") or not isinstance(node.ctx, ast.Load):
                raise SourceRejected("private or assigned attributes are forbidden")
            parent = parents.get(node)
            if not isinstance(parent, ast.Call) or parent.func is not node:
                raise SourceRejected("attributes may only be direct method calls")
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                if node.func.id not in SAFE_BUILTINS:
                    raise SourceRejected("call is not allowlisted: %s" % node.func.id)
            elif isinstance(node.func, ast.Attribute):
                if node.func.attr not in SAFE_METHODS:
                    raise SourceRejected("method call is not allowlisted: %s" % node.func.attr)
            else:
                raise SourceRejected("indirect calls are forbidden")


CHILD = r'''
import copy, json, sys
payload = json.loads(sys.stdin.read())
safe = {name: getattr(__builtins__, name) for name in payload["builtins"]}
namespace = {"__builtins__": safe}
exec(compile(payload["source"], "<candidate>", "exec"), namespace, namespace)
func = namespace[payload["function"]]
out = []
for args, expected in payload["cases"]:
    try:
        if payload["function"] in {"apply_edits", "lru_trace"}:
            args[1] = [tuple(item) for item in args[1]]
        before = copy.deepcopy(args) if payload["function"] in {"merge_intervals", "stable_toposort", "apply_edits"} else None
        # JSON-normalize tuples returned by candidates because suite cases cross
        # the subprocess boundary as JSON lists.
        actual = json.loads(json.dumps(func(*args)))
        unchanged = before is None or before == args
        out.append({"actual": actual, "passed": actual == expected and unchanged,
                    "failure_class": None if unchanged else "input_mutation"})
    except BaseException as exc:
        out.append({"error": type(exc).__name__, "passed": False})
print(json.dumps(out, separators=(",", ":")))
'''


def grade_attempt(content, function, native=None):
    native = native or {}
    if native.get("failure_class") == "reasoning_budget_exhausted" and not native.get("content"):
        return {"status": "failed", "failure_class": "reasoning_budget_exhausted", "passed": 0, "total": len(CASES[function])}
    try:
        source = extract_source(content)
        validate_source(source, function, SPECS[function])
    except SourceRejected as exc:
        return {"status": "rejected", "failure_class": "source_rejected", "reason": str(exc), "passed": 0, "total": len(CASES[function])}
    except HarnessIncompatible as exc:
        return {"status": "incompatible", "failure_class": "harness_incompatible", "reason": str(exc), "passed": 0, "total": len(CASES[function])}
    except ValueError as exc:
        return {"status": "failed", "failure_class": native.get("failure_class") or "source_missing", "reason": str(exc), "passed": 0, "total": len(CASES[function])}
    payload = {"source": source, "function": function, "cases": CASES[function], "builtins": sorted(SAFE_BUILTINS)}
    try:
        completed = subprocess.run([sys.executable, "-I", "-c", CHILD], input=json.dumps(payload), text=True, capture_output=True, timeout=TIMEOUT_SECONDS, check=False)
    except subprocess.TimeoutExpired:
        return {"status": "failed", "failure_class": "execution_timeout", "reason": "execution timed out", "passed": 0, "total": len(CASES[function])}
    if completed.returncode:
        return {"status": "failed", "failure_class": "sandbox_error", "reason": "sandbox error: " + completed.stderr.strip()[:300], "passed": 0, "total": len(CASES[function])}
    try:
        results = json.loads(completed.stdout)
    except json.JSONDecodeError:
        return {"status": "failed", "failure_class": "sandbox_error", "reason": "sandbox returned invalid JSON", "passed": 0, "total": len(CASES[function])}
    passed = sum(1 for item in results if item.get("passed"))
    failure = next((item.get("failure_class") for item in results if not item.get("passed") and item.get("failure_class")), "behavioral_failure")
    return {"status": "passed" if passed == len(CASES[function]) else "failed", "failure_class": None if passed == len(CASES[function]) else failure, "passed": passed, "total": len(CASES[function]), "cases": results}


def _unwrap(raw):
    if isinstance(raw, dict) and isinstance(raw.get("data"), str):
        return json.loads(raw["data"])
    return raw


def attempts_from_artifact(raw, suite, repetitions):
    raw = _unwrap(raw)
    if raw.get("schema") != "anvil-serving.fast-tier-bakeoff/v1":
        raise ValueError("expected native anvil-serving.fast-tier-bakeoff/v1 artifact")
    try:
        section = raw["suites"][suite]
        checks = section["checks"]
    except (KeyError, TypeError) as exc:
        raise ValueError("artifact does not contain suite %r" % suite) from exc
    expected_sha = hashlib.sha256(Path(__file__).with_name("suite.json").read_bytes()).hexdigest()
    if section.get("source_sha256") != expected_sha:
        raise ValueError("suite source_sha256 does not match the frozen suite.json")
    found = {}
    for check in checks:
        identifier = check.get("id")
        if identifier in SPECS:
            if identifier in found:
                raise ValueError("artifact has duplicate task id: " + identifier)
            attempts = check.get("attempts")
            if not isinstance(attempts, list) or len(attempts) != repetitions:
                raise ValueError("task %s does not contain exactly %d attempts" % (identifier, repetitions))
            for attempt in attempts:
                exhausted = attempt.get("failure_class") == "reasoning_budget_exhausted" and not attempt.get("content")
                if not exhausted and (attempt.get("status") != "passed" or attempt.get("finish_reason") != "stop" or attempt.get("failure_class") is not None):
                    raise ValueError("task %s has unsuccessful native attempt" % identifier)
                if "content" not in attempt or "reasoning_field" not in attempt:
                    raise ValueError("task %s lacks visible or reasoning evidence" % identifier)
                if not exhausted and not all(item.get("passed") for item in attempt.get("text_checks") or []):
                    raise ValueError("task %s failed native marker checks" % identifier)
                budget = attempt.get("budget") or {}
                if budget.get("visible_answer_tokens") != 4096 or budget.get("reasoning_headroom_tokens") != 8192:
                    raise ValueError("task %s has mismatched per-attempt budget" % identifier)
            found[identifier] = attempts
    missing = sorted(set(SPECS) - set(found))
    if missing:
        raise ValueError("artifact misses fixture evals: " + ", ".join(missing))
    return found


def grade_artifact(raw, suite, repetitions):
    report = {"schema": "anvil-serving.coding-fixture-grade/v1", "suite": suite, "artifact_valid": True, "tasks": {}, "score": {"passed": 0, "total": sum(len(cases) * repetitions for cases in CASES.values()), "whole_solutions_passed": 0, "whole_solutions_total": len(SPECS) * repetitions}}
    try:
        tasks = attempts_from_artifact(raw, suite, repetitions)
    except ValueError as exc:
        report.update({"artifact_valid": False, "error": str(exc)})
        report["score"]["pass_rate"] = 0.0
        report["score"]["whole_solution_pass_rate"] = 0.0
        return report
    for function, attempts in tasks.items():
        graded = [grade_attempt(item.get("content"), function, item) for item in attempts]
        passed = sum(item["passed"] for item in graded)
        total = sum(item["total"] for item in graded)
        report["tasks"][function] = {"attempts": graded, "passed": passed, "total": total, "pass_rate": passed / total if total else 0.0, "whole_solution_passes": sum(item["status"] == "passed" for item in graded)}
        report["score"]["passed"] += passed
        report["score"]["whole_solutions_passed"] += sum(item["status"] == "passed" for item in graded)
    report["score"]["pass_rate"] = report["score"]["passed"] / report["score"]["total"]
    report["score"]["whole_solution_pass_rate"] = report["score"]["whole_solutions_passed"] / report["score"]["whole_solutions_total"]
    return report


def self_check():
    good = "```python\ndef merge_intervals(intervals):\n    out = []\n    for start, end in sorted(intervals):\n        if out and start <= out[-1][1] + 1:\n            out[-1][1] = max(out[-1][1], end)\n        else:\n            out.append([start, end])\n    return out\n```"
    bad = "```python\nimport os\ndef merge_intervals(intervals):\n    return intervals\n```"
    mutate = "```python\ndef merge_intervals(intervals):\n    intervals.sort()\n    return intervals\n```"
    compatible = "```python\ndef merge_intervals(intervals):\n    out = []\n    for pair in sorted([item[:] for item in intervals], key=lambda item: item[0]):\n        if out and pair[0] <= out[-1][1] + 1:\n            out[-1][1] += 0\n            out[-1][1] = max(out[-1][1], pair[1])\n        else:\n            out.append(pair)\n    return out\n```"
    tuples = "```python\ndef apply_edits(text, edits):\n    if not all(isinstance(edit, tuple) and len(edit) == 3 for edit in edits):\n        return None\n    out = []\n    pos = 0\n    for start, end, replacement in sorted(edits):\n        if not isinstance(start, int) or not isinstance(end, int) or not isinstance(replacement, str) or start < pos or start < 0 or end < start or end > len(text):\n            return None\n        out.append(text[pos:start])\n        out.append(replacement)\n        pos = end\n    out.append(text[pos:])\n    return ''.join(out)\n```"
    assert grade_attempt(good, "merge_intervals")["status"] == "passed"
    assert grade_attempt(bad, "merge_intervals")["status"] == "rejected"
    assert grade_attempt(mutate, "merge_intervals")["failure_class"] == "input_mutation"
    assert grade_attempt(compatible, "merge_intervals")["status"] == "passed"
    assert grade_attempt(tuples, "apply_edits")["status"] == "passed"
    assert grade_attempt("", "parse_csv_line", {"failure_class": "reasoning_budget_exhausted"})["failure_class"] == "reasoning_budget_exhausted"


def main():
    parser = argparse.ArgumentParser(description="Grade retained coding responses without model calls.")
    parser.add_argument("artifact", nargs="?", help="native fast-tier-bakeoff/v1 evidence JSON")
    parser.add_argument("--suite", default=SUITE)
    parser.add_argument("--self-check", action="store_true")
    parser.add_argument("--reviewed", action="store_true", help="confirm retained source was reviewed for safe execution")
    parser.add_argument("--expected-repetitions", type=int, default=3)
    args = parser.parse_args()
    if args.self_check:
        self_check()
        print("self-check passed")
        return 0
    if not args.artifact:
        parser.error("artifact is required unless --self-check is used")
    if not args.reviewed:
        parser.error("--reviewed is required before executing retained model source")
    if args.expected_repetitions < 1:
        parser.error("--expected-repetitions must be positive")
    artifact_bytes = Path(args.artifact).read_bytes()
    report = grade_artifact(json.loads(artifact_bytes), args.suite, args.expected_repetitions)
    report["artifact_sha256"] = hashlib.sha256(artifact_bytes).hexdigest()
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["artifact_valid"] and report["score"]["pass_rate"] == 1.0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
