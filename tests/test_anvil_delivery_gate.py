from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "check_anvil_delivery_gate.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("check_anvil_delivery_gate", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


gate = _load_module()
COMMIT = "a" * 40


def _task(task_id: str, *, status: str = "done") -> dict[str, object]:
    command = f"python -m pytest tests/{task_id.rsplit(':', 1)[-1].lower()}.py -q"
    return {
        "id": task_id,
        "status": status,
        "verification": {
            "required_proofs": [
                {"kind": "command", "command": command, "passing_exit_codes": [0]}
            ]
        },
    }


def _entry(task: dict[str, object]) -> dict[str, object]:
    proof = task["verification"]["required_proofs"][0]
    return {
        "id": task["id"],
        "evidence_status": "complete",
        "proofs": [
            {
                "kind": "command",
                "command": proof["command"],
                "exit_code": 0,
                "observed_at": "2026-07-11T00:00:00Z",
                "commit_sha": COMMIT,
            }
        ],
        "human_disposition": {
            "decision": "approved",
            "reviewer": "reviewer",
            "reason": "Acceptance criteria and observed proofs passed.",
            "observed_at": "2026-07-11T00:01:00Z",
            "commit_sha": COMMIT,
        },
    }


def _manifest(tasks: list[dict[str, object]]) -> dict[str, object]:
    return {
        "schema_version": gate.SCHEMA_VERSION,
        "reviewed_commit": COMMIT,
        "author": "implementer",
        "reviewer": "reviewer",
        "tasks": [_entry(task) for task in tasks],
        "final_reviews": {
            kind: {
                "disposition": "passed",
                "reviewer": f"{kind}-reviewer",
                "observed_at": "2026-07-11T00:02:00Z",
                "commit_sha": COMMIT,
                "summary": f"{kind} review passed.",
            }
            for kind in gate.FINAL_REVIEW_KINDS
        },
    }


def _write(path: Path, value: dict[str, object]) -> Path:
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def _runner(tasks: list[dict[str, object]], calls: list[tuple[str, ...]]):
    by_id = {task["id"]: task for task in tasks}

    def run(argv, **kwargs):
        command = tuple(argv)
        calls.append(command)
        assert kwargs["timeout"] == 9
        assert command[-1] == "--json"
        if "list" in command:
            payload = {"ok": True, "data": {"tasks": list(by_id.values())}}
        else:
            task_id = command[-2]
            payload = {"ok": True, "data": {"task": by_id[task_id]}}
        return subprocess.CompletedProcess(argv, 0, json.dumps(payload), "")

    return run
