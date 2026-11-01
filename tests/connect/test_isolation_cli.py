import json
import sys
from types import SimpleNamespace


from anvil_serving import cli


def test_isolation_interrupt_returns_safe_cancelled_envelope_with_runner_counts(monkeypatch, capsys):
    interrupted = KeyboardInterrupt("private-child-sentinel")
    interrupted.execution_started = True
    interrupted.case_counts = {"passed": 0, "failed": 0, "skipped": 0, "not_run": 0, "unavailable": 8}
    interrupted.stage = "execution"

    def qualify(_config):
        raise interrupted

    monkeypatch.setitem(
        sys.modules,
        "anvil_serving.connect.qualification_vm_run",
        SimpleNamespace(qualify=qualify, _CASES=tuple(range(8))),
    )

    assert cli.main(["connect", "qualify", "--lane", "isolation", "--json"]) != 0

    output = capsys.readouterr().out
    assert "private-child-sentinel" not in output
    data = json.loads(output)["data"]
    assert data == {
        "schema": "anvil-connect.qualification/v1",
        "ok": False,
        "state": "failed",
        "error_code": "runner-interrupted",
        "counts": interrupted.case_counts,
        "stage": "execution",
    }


def test_isolation_interrupt_without_runner_metadata_uses_unavailable_fallback(monkeypatch, capsys):
    def qualify(_config):
        raise KeyboardInterrupt("private-child-sentinel")

    monkeypatch.setitem(
        sys.modules,
        "anvil_serving.connect.qualification_vm_run",
        SimpleNamespace(qualify=qualify, _CASES=tuple(range(8))),
    )

    assert cli.main(["connect", "qualify", "--lane", "isolation", "--json"]) != 0

    output = capsys.readouterr().out
    assert "private-child-sentinel" not in output
    data = json.loads(output)["data"]
    assert data["state"] == "failed" and data["error_code"] == "runner-interrupted"
    assert data["counts"] == {"passed": 0, "failed": 0, "skipped": 0, "not_run": 0, "unavailable": 8}
