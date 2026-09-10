import json
import sys
from types import SimpleNamespace

import pytest

from anvil_serving import cli
from anvil_serving.connect.cli import dispatch
from anvil_serving.connect.qualification import QualificationError


def test_isolation_uses_saved_config_without_preparation_or_deployment(monkeypatch, capsys):
    calls = []
    def qualify(config):
        calls.append(config)
        return {"ok": True, "counts": {"passed": 8, "failed": 0, "skipped": 0, "not_run": 0}}
    monkeypatch.setitem(sys.modules, "anvil_serving.connect.qualification_vm_run",
                        SimpleNamespace(qualify=qualify, _CASES=tuple(range(8))))
    def forbidden(*args, **kwargs):
        pytest.fail("qualification implicitly prepared an image")
    monkeypatch.setitem(sys.modules, "anvil_serving.connect.qualification_vm", SimpleNamespace(prepare=forbidden))
    assert cli.main(["connect", "qualify", "--lane", "isolation", "--json"]) == 0
    assert calls == [None]
    assert json.loads(capsys.readouterr().out)["data"]["counts"]["passed"] == 8
    assert dispatch(["qualify", "--lane", "isolation", "--prepare-vm"]).error is not None
    assert calls == [None]


@pytest.mark.parametrize("started", [False, True])
def test_isolation_errors_preserve_execution_state_and_hide_child_text(monkeypatch, capsys, started):
    def qualify(config):
        raise QualificationError("runner-timeout", "private-child-sentinel", execution_started=started,
                                 stage="execution" if started else "preflight")
    monkeypatch.setitem(sys.modules, "anvil_serving.connect.qualification_vm_run",
                        SimpleNamespace(qualify=qualify, _CASES=tuple(range(8))))
    assert cli.main(["connect", "qualify", "--lane", "isolation", "--json"]) != 0
    output = capsys.readouterr().out
    assert "private-child-sentinel" not in output
    data = json.loads(output)["data"]
    assert data["state"] == ("failed" if started else "not-run")
    assert data["counts"] == (
        {"passed": 0, "failed": 0, "skipped": 0, "not_run": 0, "unavailable": 8}
        if started else {"passed": 0, "failed": 0, "skipped": 0, "not_run": 8}
    )


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
