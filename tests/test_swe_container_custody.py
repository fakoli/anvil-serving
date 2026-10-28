"""Offline custody regression coverage; no Docker daemon is contacted."""
from __future__ import annotations

import json
import subprocess

from anvil_serving.benchmarking import swe_container_custody as custody


def _runner(observed, operations):
    def run(argv, cwd, timeout):
        operations.append(tuple(argv))
        if argv[1:3] == ("container", "inspect"):
            if observed["value"] is None:
                return subprocess.CompletedProcess(argv, 1, b"", b"No such container")
            return subprocess.CompletedProcess(argv, 0, json.dumps([observed["value"]]).encode(), b"")
        if argv[1:4] == ("container", "rm", "--force"):
            observed["value"] = None
            return subprocess.CompletedProcess(argv, 0, b"", b"")
        raise AssertionError(argv)
    return run


def test_inflight_create_is_unresolved_even_when_listing_would_be_empty(tmp_path):
    ledger = custody.initialize(tmp_path, "owner", "run", "/usr/bin/docker")
    path, record = custody.begin(ledger, "owner", "run", "sha256:" + "a" * 64, "/usr/bin/docker", "agent")
    result = custody.cleanup(ledger, "owner", "run", runner=lambda *_: (_ for _ in ()).throw(AssertionError("must not list")))
    assert result["status"] == "incomplete"
    assert custody._read_json(path) == record


def test_cleanup_removes_only_reinspected_owned_identity_and_is_repeatable(tmp_path):
    ledger = custody.initialize(tmp_path, "owner", "run", "/usr/bin/docker")
    path, record = custody.begin(ledger, "owner", "run", "sha256:" + "a" * 64, "/usr/bin/docker", "grader")
    record = custody.mark_created(path, record, "b" * 64)
    observed = {"value": {"Id": record["container_id"], "Name": "/" + record["name"],
                            "Image": record["image_id"], "Config": {"Labels": custody.labels(record)}}}
    operations = []
    first = custody.cleanup(ledger, "owner", "run", runner=_runner(observed, operations))
    assert first["status"] == "completed" and first["containers"][0]["status"] == "removed"
    assert any(call[1:4] == ("container", "rm", "--force") for call in operations)
    second = custody.cleanup(ledger, "owner", "run", runner=_runner(observed, []))
    assert second["status"] == "completed" and second["containers"][0]["status"] == "already_absent"


def test_identity_drift_never_removes_foreign_sibling(tmp_path):
    ledger = custody.initialize(tmp_path, "owner", "run", "/usr/bin/docker")
    path, record = custody.begin(ledger, "owner", "run", "sha256:" + "a" * 64, "/usr/bin/docker", "grader")
    record = custody.mark_created(path, record, "b" * 64)
    observed = {"value": {"Id": record["container_id"], "Name": "/" + record["name"],
                            "Image": record["image_id"], "Config": {"Labels": {**custody.labels(record), "anvil.swe.run_id": "foreign"}}}}
    operations = []
    result = custody.cleanup(ledger, "owner", "run", runner=_runner(observed, operations))
    assert result["status"] == "incomplete"
    assert not any("rm" in call for call in operations)


def test_agent_wrapper_records_intent_before_create_and_replaces_random_name(tmp_path, monkeypatch):
    ledger = custody.initialize(tmp_path, "owner", "run", "/usr/bin/docker")
    created = []

    def runner(argv, cwd, timeout):
        if argv[1:3] == ("image", "inspect"):
            return subprocess.CompletedProcess(argv, 0, json.dumps([{"Id": "sha256:" + "a" * 64}]).encode(), b"")
        if argv[1] == "run":
            created.append(tuple(argv))
            records = [path for path in (tmp_path / "swe-containers").glob("*.json") if path.name != "contract.json"]
            assert len(records) == 1
            assert custody._read_json(records[0])["state"] == "creating"
            return subprocess.CompletedProcess(argv, 0, ("b" * 64).encode(), b"")
        raise AssertionError(argv)

    monkeypatch.setattr(custody, "_default_runner", runner)
    assert custody.agent_wrapper(
        ["run", "-d", "--name", "minisweagent-random", "-w", "/", "--rm", "example:latest", "sleep", "2h"],
        ledger=ledger, owner="owner", run_id="run", binary="/usr/bin/docker",
    ) == 0
    record = custody._read_json(next(path for path in (tmp_path / "swe-containers").glob("*.json") if path.name != "contract.json"))
    assert record["state"] == "created" and record["container_id"] == "b" * 64
    assert created[0][4] == record["name"]
    assert any(value.startswith(custody.LABEL_PREFIX + "token=") for value in created[0])
