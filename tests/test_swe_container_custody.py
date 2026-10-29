"""Offline custody regression coverage; no Docker daemon is contacted."""
from __future__ import annotations

import json
import hashlib
import os
import subprocess
import sys
from pathlib import Path

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


def test_generated_agent_wrapper_parses_metadata_before_delimiter_and_forwards_docker(tmp_path):
    calls_path = tmp_path / "fake-docker-calls.jsonl"
    fake_docker = tmp_path / "fake-docker"
    fake_docker.write_text(
        "#!%s\nimport json, sys\nfrom pathlib import Path\n"
        "calls = Path(%r)\nargs = sys.argv[1:]\n"
        "with calls.open('a', encoding='utf-8') as stream:\n"
        "    stream.write(json.dumps(args) + '\\n')\n"
        "if args[:2] == ['image', 'inspect']:\n"
        "    print(json.dumps([{'Id': 'sha256:' + 'a' * 64}]))\n"
        "elif args[:1] == ['run']:\n    print('b' * 64)\n"
        "elif args[:1] == ['version']:\n    print('fake-version')\n"
        "else:\n    raise SystemExit(9)\n" % (sys.executable, str(calls_path)),
        encoding="utf-8",
    )
    os.chmod(fake_docker, 0o700)
    ledger = custody.initialize(tmp_path, "owner", "run", str(fake_docker))
    source_sha256 = hashlib.sha256(Path(custody.__file__).read_bytes()).hexdigest()
    wrapper = custody.write_agent_wrapper(
        tmp_path / "agent-wrapper", python_executable=sys.executable, ledger=ledger,
        owner="owner", run_id="run", binary=str(fake_docker), source_sha256=source_sha256,
    )

    version = subprocess.run([wrapper, "version", "--format", "{{.Client.Version}}"], text=True,
                             capture_output=True, check=False)
    run = subprocess.run([wrapper, "run", "-d", "--name", "minisweagent-random", "-w", "/",
                          "--rm", "example:latest", "sleep", "2h"], text=True,
                         capture_output=True, check=False)

    assert version.returncode == 0 and version.stdout == "fake-version\n"
    assert run.returncode == 0 and run.stdout == "b" * 64 + "\n"
    calls = [json.loads(line) for line in calls_path.read_text(encoding="utf-8").splitlines()]
    record = custody._read_json(next(path for path in (tmp_path / "swe-containers").glob("*.json")
                                      if path.name != "contract.json"))
    assert record["state"] == "created" and record["container_id"] == "b" * 64
    assert calls == [
        ["version", "--format", "{{.Client.Version}}"],
        ["image", "inspect", "example:latest"],
        ["run", "-d", "--name", record["name"],
         "--label", "anvil.swe.ownership_id=owner",
         "--label", "anvil.swe.run_id=run",
         "--label", "anvil.swe.token=" + record["token"],
         "-w", "/", "--rm", "sha256:" + "a" * 64, "sleep", "2h"],
    ]
