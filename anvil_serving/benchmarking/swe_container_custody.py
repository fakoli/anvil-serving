"""Run-scoped SWE Docker custody; intentionally independent of Docker SDKs."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile
import uuid

SCHEMA = "anvil-serving.swe-container-custody/v1"
CONTRACT_SCHEMA = "anvil-serving.swe-container-custody-contract/v1"
CLEANUP_SCHEMA = "anvil-serving.swe-container-cleanup/v1"
LABEL_PREFIX = "anvil.swe."
MAX_RECORD_BYTES = 65_536
MAX_RECORDS = 512
_HEX64 = re.compile(r"[0-9a-f]{64}")
_TOKEN = re.compile(r"[0-9a-f]{32}")
_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}")


def _require(value, message):
    if not value:
        raise ValueError(message)


def _no_symlinks(path):
    path = Path(path)
    _require(path.is_absolute(), "custody path must be absolute")
    _require(not any(item.is_symlink() for item in (path, *path.parents)), "custody path contains a symlink")
    return path


def _read_json(path):
    path = Path(path)
    descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
    with os.fdopen(descriptor, "rb") as stream:
        before = os.fstat(stream.fileno())
        _require(stat.S_ISREG(before.st_mode), "custody record is not regular")
        raw = stream.read(MAX_RECORD_BYTES + 1)
        after = os.fstat(stream.fileno())
    current = os.stat(path, follow_symlinks=False)
    def identity(item):
        return (item.st_dev, item.st_ino, item.st_mode, item.st_size, item.st_mtime_ns, item.st_ctime_ns)
    _require(len(raw) <= MAX_RECORD_BYTES and identity(before) == identity(after) == identity(current),
             "custody record changed or exceeds its bound")
    try:
        return json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError("custody record is invalid JSON") from exc


def _write_json(path, value):
    path = Path(path)
    data = (json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(",", ":")) + "\n").encode()
    _require(len(data) <= MAX_RECORD_BYTES, "custody record exceeds its bound")
    _no_symlinks(path.parent)
    descriptor, temporary = tempfile.mkstemp(prefix=".swe-custody-", dir=path.parent)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _validate_identity(owner, run_id, binary):
    _require(isinstance(owner, str) and _IDENTIFIER.fullmatch(owner), "invalid custody owner")
    _require(isinstance(run_id, str) and _IDENTIFIER.fullmatch(run_id), "invalid custody run")
    _require(isinstance(binary, str) and os.path.isabs(binary) and "\x00" not in binary, "invalid Docker executable")


def verify_source_hash(expected):
    _require(isinstance(expected, str) and _HEX64.fullmatch(expected), "invalid custody source hash")
    actual = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    _require(actual == expected, "SWE container custody source changed after planning")
    return actual


def ledger_path(run_path):
    return _no_symlinks(Path(run_path)) / "swe-containers"


def initialize(run_path, owner, run_id, binary):
    """Durably establish the per-run ledger before either harness can create."""
    _validate_identity(owner, run_id, binary)
    ledger = ledger_path(run_path)
    ledger.mkdir(mode=0o700, parents=True, exist_ok=True)
    _no_symlinks(ledger)
    contract = {"schema": CONTRACT_SCHEMA, "ownership_id": owner, "run_id": run_id,
                "container_binary": binary}
    path = ledger / "contract.json"
    if path.exists():
        _require(_read_json(path) == contract, "custody contract identity drift")
    else:
        _write_json(path, contract)
    return str(ledger)


def _contract(ledger, owner, run_id):
    ledger = _no_symlinks(ledger)
    _require(ledger.is_dir(), "custody ledger is missing")
    value = _read_json(ledger / "contract.json")
    binary = value.get("container_binary") if isinstance(value, dict) else None
    _validate_identity(owner, run_id, binary)
    _require(value == {"schema": CONTRACT_SCHEMA, "ownership_id": owner, "run_id": run_id,
                       "container_binary": binary}, "custody contract is invalid")
    return ledger, binary


def _record_path(ledger, token):
    _require(isinstance(token, str) and _TOKEN.fullmatch(token), "invalid custody token")
    return ledger / f"{token}.json"


def _validate_record(record, path, owner, run_id):
    token = record.get("token") if isinstance(record, dict) else None
    _require(isinstance(record, dict) and set(record) == {"schema", "ownership_id", "run_id", "token", "name", "image_id", "container_binary", "stage", "state", "container_id"},
             "custody record fields are invalid")
    _require(record["schema"] == SCHEMA and record["ownership_id"] == owner and record["run_id"] == run_id,
             "custody record ownership mismatch")
    _require(isinstance(token, str) and _TOKEN.fullmatch(token) and path.name == f"{token}.json"
             and record["name"] == f"anvil-swe-{token}", "custody record token mismatch")
    _require(isinstance(record["image_id"], str) and re.fullmatch(r"sha256:" + _HEX64.pattern, record["image_id"]),
             "custody image identity is invalid")
    _validate_identity(owner, run_id, record["container_binary"])
    _require(record["stage"] in {"agent", "grader"} and record["state"] in {"creating", "created", "removed"},
             "custody record state is invalid")
    container_id = record["container_id"]
    _require((record["state"] == "creating" and container_id is None)
             or (record["state"] != "creating" and isinstance(container_id, str) and _HEX64.fullmatch(container_id)),
             "custody container identity is invalid")
    return record


def begin(ledger, owner, run_id, image_id, binary, stage):
    """Write and fsync an unpredictable intent before a Docker create request."""
    ledger, contract_binary = _contract(ledger, owner, run_id)
    _require(binary == contract_binary and isinstance(image_id, str)
             and re.fullmatch(r"sha256:" + _HEX64.pattern, image_id) and stage in {"agent", "grader"},
             "invalid custody create identity")
    _require(all(record["state"] == "removed" for record, _path in _records(ledger, owner, run_id)),
             "previous SWE container cleanup is unresolved")
    token = uuid.uuid4().hex
    path = _record_path(ledger, token)
    _require(not path.exists(), "duplicate custody token")
    record = {"schema": SCHEMA, "ownership_id": owner, "run_id": run_id, "token": token,
              "name": f"anvil-swe-{token}", "image_id": image_id, "container_binary": binary,
              "stage": stage, "state": "creating", "container_id": None}
    _write_json(path, record)
    return str(path), record


def labels(record):
    return {LABEL_PREFIX + key: record[key] for key in ("ownership_id", "run_id", "token")}


def mark_created(path, record, container_id):
    path = Path(path)
    _require(_read_json(path) == record and record.get("state") == "creating"
             and isinstance(container_id, str) and _HEX64.fullmatch(container_id),
             "container creation identity drift")
    value = {**record, "container_id": container_id, "state": "created"}
    _write_json(path, value)
    return value


def _default_runner(argv, cwd, timeout):
    return subprocess.run(argv, cwd=cwd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, env={"PATH": os.defpath}, timeout=timeout, check=False)


def _text(value):
    raw = value if isinstance(value, bytes) else str(value or "").encode()
    _require(len(raw) <= MAX_RECORD_BYTES, "Docker output exceeds its custody bound")
    return raw.decode("utf-8", "replace")


def _inspect(runner, binary, target, *, image=False, missing=False):
    result = runner((binary, "image" if image else "container", "inspect", target), None, 15)
    if missing and result.returncode and "No such container" in _text(result.stderr):
        return None
    _require(result.returncode == 0, "Docker inspection failed")
    try:
        rows = json.loads(_text(result.stdout))
    except (TypeError, ValueError) as exc:
        raise ValueError("Docker inspection JSON is invalid") from exc
    _require(isinstance(rows, list) and len(rows) == 1 and isinstance(rows[0], dict),
             "Docker inspection is ambiguous")
    return rows[0]


def _owned(observed, record):
    _require(observed.get("Id") == record["container_id"] and observed.get("Name") == "/" + record["name"]
             and observed.get("Image") == record["image_id"], "container identity drift")
    observed_labels = observed.get("Config", {}).get("Labels") or {}
    _require(all(observed_labels.get(key) == value for key, value in labels(record).items()),
             "container ownership labels drift")


def _records(ledger, owner, run_id):
    entries = []
    with os.scandir(ledger) as directory:
        for entry in directory:
            if entry.name == "contract.json":
                continue
            _require(entry.name.endswith(".json") and entry.is_file(follow_symlinks=False),
                     "custody ledger contains an unsafe entry")
            entries.append(Path(entry.path))
    _require(len(entries) <= MAX_RECORDS, "custody ledger exceeds its count bound")
    return [(_validate_record(_read_json(path), path, owner, run_id), path) for path in sorted(entries)]


def cleanup(ledger, owner, run_id, *, runner=_default_runner, receipt_path=None):
    """Remove only re-inspected owned containers; uncertainty remains incomplete."""
    result = {"schema": CLEANUP_SCHEMA, "ownership_id": owner, "run_id": run_id,
              "status": "completed", "containers": [], "errors": []}
    try:
        ledger, contract_binary = _contract(ledger, owner, run_id)
        for record, path in _records(ledger, owner, run_id):
            _require(record["container_binary"] == contract_binary, "container binary drift")
            if record["state"] == "creating":
                raise ValueError("container creation outcome remains unresolved")
            observed = _inspect(runner, contract_binary, record["container_id"], missing=True)
            if observed is not None:
                _owned(observed, record)
                removed = runner((contract_binary, "container", "rm", "--force", record["container_id"]), None, 15)
                _require(removed.returncode == 0, "owned container removal failed")
                _require(_inspect(runner, contract_binary, record["container_id"], missing=True) is None,
                         "owned container removal was not verified")
                outcome = "removed"
            else:
                outcome = "already_absent"
            if record["state"] != "removed":
                _write_json(path, {**record, "state": "removed"})
            result["containers"].append({"id": record["container_id"], "stage": record["stage"], "status": outcome})
    except Exception as exc:
        result["status"] = "incomplete"
        result["errors"].append({"error": type(exc).__name__, "reason": str(exc)[:256]})
    if receipt_path is not None:
        _write_json(receipt_path, result)
    return result


def _agent_run(argv, *, ledger, owner, run_id, binary):
    _require(len(argv) >= 9 and argv[:3] == ["run", "-d", "--name"] and argv[3]
             and argv[4] == "-w" and argv[5] and argv[-2] == "sleep", "unexpected pinned mini-SWE Docker invocation")
    prior = cleanup(ledger, owner, run_id)
    _require(prior["status"] == "completed", "previous SWE container cleanup is unresolved")
    image = argv[-3]
    _require(isinstance(image, str) and image and "\x00" not in image, "invalid mini-SWE image")
    run_args = argv[6:-3]
    _require("--name" not in run_args and "--label" not in run_args and "--label-add" not in run_args,
             "unexpected mini-SWE Docker run options")
    image_observed = _inspect(_default_runner, binary, image, image=True)
    image_id = image_observed.get("Id")
    _require(isinstance(image_id, str) and re.fullmatch(r"sha256:" + _HEX64.pattern, image_id),
             "mini-SWE image identity is unavailable")
    record_path, record = begin(ledger, owner, run_id, image_id, binary, "agent")
    rewritten = ["run", "-d", "--name", record["name"]]
    for key, value in labels(record).items():
        rewritten.extend(("--label", key + "=" + value))
    rewritten.extend(["-w", argv[5], *run_args, image_id, "sleep", argv[-1]])
    result = _default_runner((binary, *rewritten), None, 120)
    if result.returncode == 0:
        mark_created(record_path, record, _text(result.stdout).strip())
    sys.stdout.write(_text(result.stdout)); sys.stderr.write(_text(result.stderr))
    return result.returncode


def agent_wrapper(argv, *, ledger, owner, run_id, binary, source_sha256=None):
    """The pinned mini-SWE source is verified by its adapter before this wrapper runs."""
    _validate_identity(owner, run_id, binary)
    if source_sha256 is not None:
        verify_source_hash(source_sha256)
    _contract(ledger, owner, run_id)
    if argv and argv[0] == "run":
        return _agent_run(list(argv), ledger=ledger, owner=owner, run_id=run_id, binary=binary)
    return subprocess.call((binary, *argv), stdin=subprocess.DEVNULL, env={"PATH": os.defpath})


def write_agent_wrapper(path, *, python_executable, ledger, owner, run_id, binary, source_sha256):
    """Write one private executable which re-enters this source-verified module."""
    _validate_identity(owner, run_id, binary)
    path = Path(path)
    _no_symlinks(path.parent)
    _require(os.path.isabs(python_executable) and "\n" not in python_executable, "invalid wrapper Python")
    source = Path(__file__).resolve()
    verify_source_hash(source_sha256)
    content = "#!%s\nimport os, sys\nos.execv(%r, [%r, %r, 'agent-wrapper', '--ledger', %r, '--owner', %r, '--run-id', %r, '--binary', %r, '--source-sha256', %r, '--', *sys.argv[1:]])\n" % (
        python_executable, python_executable, python_executable, str(source), str(ledger), owner, run_id, binary, source_sha256)
    descriptor, temporary = tempfile.mkstemp(prefix=".swe-wrapper-", dir=path.parent)
    try:
        os.fchmod(descriptor, 0o700)
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(content); stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return str(path)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    try:
        delimiter = argv.index("--")
    except ValueError:
        delimiter = None
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("agent-wrapper",))
    parser.add_argument("--ledger", required=True)
    parser.add_argument("--owner", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--binary", required=True)
    parser.add_argument("--source-sha256", required=True)
    if delimiter is None:
        parser.error("Docker arguments must follow --")
    args = parser.parse_args(argv[:delimiter])
    docker_args = argv[delimiter + 1:]
    if not docker_args:
        parser.error("Docker arguments must follow --")
    return agent_wrapper(docker_args, ledger=args.ledger, owner=args.owner,
                         run_id=args.run_id, binary=args.binary, source_sha256=args.source_sha256)


if __name__ == "__main__":
    raise SystemExit(main())
