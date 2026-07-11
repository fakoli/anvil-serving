from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest


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


def test_complete_gate_uses_only_supported_read_only_json_commands(tmp_path):
    tasks = [_task("operator-cli-v2:T024"), _task("operator-cli-v2:T025")]
    manifest = _write(tmp_path / "delivery.json", _manifest(tasks))
    calls = []

    result = gate.run_gate(
        manifest,
        include_prds=["operator-cli-v2"],
        anvil_prefix=("anvil",),
        timeout=9,
        runner=_runner(tasks, calls),
        expected_commit=COMMIT,
    )

    assert result.task_ids == ("operator-cli-v2:T024", "operator-cli-v2:T025")
    assert calls[0] == ("anvil", "list", "--prd", "operator-cli-v2", "--json")
    assert all(command[1] in {"list", "show"} for command in calls)
    assert all("state.db" not in token and "events.jsonl" not in token for call in calls for token in call)


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda tasks, manifest: tasks[0].update(status="needs_review"), "is not done"),
        (
            lambda tasks, manifest: manifest["tasks"][0].update(evidence_status="partial"),
            "evidence_status must be complete",
        ),
        (lambda tasks, manifest: manifest["tasks"][0].update(proofs=[]), "missing observed proof"),
        (
            lambda tasks, manifest: manifest["tasks"][0]["proofs"][0].update(exit_code=1),
            "proof failed",
        ),
        (
            lambda tasks, manifest: manifest["tasks"][0].pop("human_disposition"),
            "approved human disposition",
        ),
        (lambda tasks, manifest: manifest.update(reviewer="implementer"), "differ from author"),
        (
            lambda tasks, manifest: manifest["final_reviews"]["adversarial"].update(
                disposition="failed"
            ),
            "must be passed",
        ),
        (
            lambda tasks, manifest: manifest["final_reviews"]["adversarial"].update(
                commit_sha="b" * 40
            ),
            "commit_sha is stale",
        ),
    ],
)
def test_gate_fails_closed_on_incomplete_state_proof_or_review(tmp_path, mutate, message):
    tasks = [_task("operator-cli-v2:T024")]
    value = _manifest(tasks)
    mutate(tasks, value)
    manifest = _write(tmp_path / "delivery.json", value)

    with pytest.raises(gate.DeliveryGateError, match=message):
        gate.run_gate(
            manifest, timeout=9, runner=_runner(tasks, []), expected_commit=COMMIT
        )


def test_include_prd_requires_every_listed_task_in_manifest(tmp_path):
    tasks = [_task("cli-consolidation:T001"), _task("cli-consolidation:T002")]
    value = _manifest(tasks[:1])
    manifest = _write(tmp_path / "delivery.json", value)

    with pytest.raises(gate.DeliveryGateError, match="missing task cli-consolidation:T002"):
        gate.run_gate(
            manifest,
            include_prds=["cli-consolidation"],
            timeout=9,
            runner=_runner(tasks, []),
            expected_commit=COMMIT,
        )


def test_bad_anvil_subprocess_envelopes_fail_closed(tmp_path):
    task = _task("operator-cli-v2:T024")
    manifest = _write(tmp_path / "delivery.json", _manifest([task]))

    for completed, message in (
        (subprocess.CompletedProcess([], 2, "", "bad command"), "command failed"),
        (subprocess.CompletedProcess([], 0, "not-json", ""), "malformed JSON"),
        (subprocess.CompletedProcess([], 0, '{"ok":false}', ""), "unsuccessful"),
    ):
        with pytest.raises(gate.DeliveryGateError, match=message):
            gate.run_gate(
                manifest,
                runner=lambda *args, completed=completed, **kwargs: completed,
                expected_commit=COMMIT,
            )


def test_link_contains_requirement_matches_one_observed_url(tmp_path):
    task = _task("operator-cli-v2:T024")
    value = _manifest([task])
    task["verification"]["required_proofs"] = [
        {"kind": "link", "link_contains": "github.com/fakoli/anvil-serving/pull/"}
    ]
    value["tasks"][0]["proofs"] = [
        {
            "kind": "link",
            "url": "https://github.com/fakoli/anvil-serving/pull/206",
            "observed_at": "2026-07-11T00:00:00Z",
            "commit_sha": COMMIT,
        }
    ]
    manifest = _write(tmp_path / "delivery.json", value)

    result = gate.run_gate(
        manifest, timeout=9, runner=_runner([task], []), expected_commit=COMMIT
    )

    assert result.task_ids == ("operator-cli-v2:T024",)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda value: value["tasks"][0]["proofs"][0].update(observed_at="not-a-date"),
        lambda value: value["tasks"][0]["human_disposition"].update(
            observed_at="2026-07-11T00:01:00"
        ),
        lambda value: value["final_reviews"]["documentation"].update(
            observed_at="2026-07-11"
        ),
    ],
)
def test_gate_requires_zoned_iso_timestamps(tmp_path, mutate):
    task = _task("operator-cli-v2:T024")
    value = _manifest([task])
    mutate(value)
    manifest = _write(tmp_path / "delivery.json", value)

    with pytest.raises(gate.DeliveryGateError, match="ISO-8601|include a timezone"):
        gate.run_gate(
            manifest, timeout=9, runner=_runner([task], []), expected_commit=COMMIT
        )


def test_gate_rejects_oversized_manifest_before_parsing(tmp_path):
    manifest = tmp_path / "delivery.json"
    manifest.write_bytes(b"{" + b" " * gate.MAX_MANIFEST_BYTES)

    with pytest.raises(gate.DeliveryGateError, match="exceeds the size limit"):
        gate.run_gate(manifest, expected_commit=COMMIT)


def test_gate_rejects_oversized_runner_output(tmp_path, monkeypatch):
    task = _task("operator-cli-v2:T024")
    manifest = _write(tmp_path / "delivery.json", _manifest([task]))
    monkeypatch.setattr(gate, "MAX_ANVIL_OUTPUT_BYTES", 8)

    with pytest.raises(gate.DeliveryGateError, match="output exceeded"):
        gate.run_gate(
            manifest,
            runner=lambda *args, **kwargs: subprocess.CompletedProcess(args, 0, "{}" * 5, ""),
            expected_commit=COMMIT,
        )


def test_gate_binds_manifest_and_review_dispositions_to_reviewed_commit(tmp_path):
    task = _task("operator-cli-v2:T024")
    value = _manifest([task])
    manifest = _write(tmp_path / "delivery.json", value)

    with pytest.raises(gate.DeliveryGateError, match="does not match Git HEAD"):
        gate.run_gate(manifest, expected_commit="b" * 40)

    value["tasks"][0]["human_disposition"]["commit_sha"] = "b" * 40
    manifest = _write(tmp_path / "delivery.json", value)
    with pytest.raises(gate.DeliveryGateError, match="disposition commit_sha is stale"):
        gate.run_gate(
            manifest, timeout=9, runner=_runner([task], []), expected_commit=COMMIT
        )


def test_proof_commits_must_be_full_hashes(tmp_path):
    task = _task("operator-cli-v2:T024")
    value = _manifest([task])
    value["tasks"][0]["proofs"][0]["commit_sha"] = "abcdef1"
    manifest = _write(tmp_path / "delivery.json", value)

    with pytest.raises(gate.DeliveryGateError, match="full 40-character"):
        gate.run_gate(
            manifest, timeout=9, runner=_runner([task], []), expected_commit=COMMIT
        )


def test_gate_rejects_proof_commits_outside_reviewed_history(tmp_path, monkeypatch):
    task = _task("operator-cli-v2:T024")
    value = _manifest([task])
    value["tasks"][0]["proofs"][0]["commit_sha"] = "b" * 40
    manifest = _write(tmp_path / "delivery.json", value)
    monkeypatch.setattr(gate, "_git_head", lambda **kwargs: COMMIT)
    monkeypatch.setattr(gate, "_git_is_ancestor", lambda *args, **kwargs: False)

    with pytest.raises(gate.DeliveryGateError, match="not an ancestor"):
        gate.run_gate(manifest, timeout=9, runner=_runner([task], []))


def test_include_prd_rejects_malformed_task_entries(tmp_path):
    task = _task("operator-cli-v2:T024")
    manifest = _write(tmp_path / "delivery.json", _manifest([task]))

    def runner(argv, **kwargs):
        payload = {"ok": True, "data": {"tasks": [task, "malformed"]}}
        return subprocess.CompletedProcess(argv, 0, json.dumps(payload), "")

    with pytest.raises(gate.DeliveryGateError, match=r"tasks\[1\] must be an object"):
        gate.run_gate(
            manifest,
            include_prds=["operator-cli-v2"],
            runner=runner,
            expected_commit=COMMIT,
        )


def test_link_proofs_require_absolute_http_urls(tmp_path):
    task = _task("operator-cli-v2:T024")
    task["verification"]["required_proofs"] = [
        {"kind": "link", "link_contains": "github.com/fakoli/anvil-serving/pull/"}
    ]
    value = _manifest([_task("operator-cli-v2:T024")])
    value["tasks"][0]["proofs"] = [
        {
            "kind": "link",
            "url": "javascript:github.com/fakoli/anvil-serving/pull/207",
            "observed_at": "2026-07-11T00:00:00Z",
            "commit_sha": COMMIT,
        }
    ]
    manifest = _write(tmp_path / "delivery.json", value)

    with pytest.raises(gate.DeliveryGateError, match="absolute HTTP"):
        gate.run_gate(
            manifest, timeout=9, runner=_runner([task], []), expected_commit=COMMIT
        )


def test_passing_exit_codes_reject_booleans(tmp_path):
    task = _task("operator-cli-v2:T024")
    task["verification"]["required_proofs"][0]["passing_exit_codes"] = [False]
    manifest = _write(tmp_path / "delivery.json", _manifest([task]))

    with pytest.raises(gate.DeliveryGateError, match="non-empty integer array"):
        gate.run_gate(
            manifest, timeout=9, runner=_runner([task], []), expected_commit=COMMIT
        )
