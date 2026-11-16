"""Hermetic bounded manifest workload observation coverage."""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from types import SimpleNamespace
from pathlib import Path

import pytest

from anvil_serving import manifest_workloads
from anvil_serving.controller_diagnostics import ChildCapture
from anvil_serving.manifest_workloads import (
    MAX_MANIFEST_BYTES,
    ManifestComponentResult,
    ManifestConfiguredObservation,
    ManifestRuntimeKind,
    ManifestRuntimeObservation,
    ManifestWorkloadSnapshot,
    _INSPECT_TEMPLATE,
    _compose_up,
    _same_file,
    capture_manifest_workload_snapshot,
    list_manifest_workloads,
)
from anvil_serving.observability.workloads import (
    ObservationQuality, ResultStatus, WorkloadErrorCode, WorkloadKind,
    WorkloadOwner, WorkloadQuery, WorkloadState,
    source_result_from_json, source_result_to_json,
)


def _clock():
    return datetime(2026, 9, 5, 23, tzinfo=timezone.utc)


def _write_manifest(path, text):
    path.write_text(text, encoding="utf-8")
    # The observer reads real filesystem mtimes alongside its injected clock.
    timestamp = (_clock() - timedelta(hours=1)).timestamp()
    os.utime(path, (timestamp, timestamp))
    return path


def _manifest(tmp_path, text):
    return _write_manifest(tmp_path / "serves.toml", text)


def _row(name="slot-a", container="container-a", *, status="running", running=True):
    return (
        '{"id":"' + "a" * 64 + '","name":"/' + container +
        '","created_at":"2026-09-05T11:00:00.123456789Z","status":"' + status +
        '","running":' + str(running).lower() +
        ',"started_at":"2026-09-05T11:01:00Z","finished_at":"0001-01-01T00:00:00Z",'
        '"project":"anvil-serving","service":"' + name + '"}\n'
    ).encode("ascii")


def test_missing_or_incomplete_runtime_rows_are_partial_and_preserve_valid_peers(tmp_path):
    path = _manifest(tmp_path, '''
[[serve]]
name = "slot-a"
runtime = "docker"
container = "container-a"
up = "docker compose -f x.yml up slot-a"

[[serve]]
name = "slot-b"
runtime = "docker"
container = "container-b"
up = "docker compose -f x.yml up slot-b"
''')
    snapshot = capture_manifest_workload_snapshot(
        str(path), clock=_clock, _capture=lambda _argv: ChildCapture("unavailable", _row(), b"secret", False),
    )
    assert snapshot.runtime.status is ResultStatus.PARTIAL
    assert len(snapshot.runtime.records) == 1
    assert snapshot.runtime.records[0].container_id == "a" * 64
    assert "secret" not in repr(snapshot)


def test_bounded_overflow_and_symlink_configuration_are_never_complete(tmp_path):
    oversized = _manifest(tmp_path, "#" + "x" * (MAX_MANIFEST_BYTES + 1))
    snapshot = capture_manifest_workload_snapshot(str(oversized), clock=_clock)
    assert snapshot.configuration.status is ResultStatus.UNAVAILABLE
    target = tmp_path / "target.toml"
    target.write_text("", encoding="utf-8")
    linked = tmp_path / "linked.toml"
    try:
        linked.symlink_to(target)
    except OSError:
        return
    snapshot = capture_manifest_workload_snapshot(str(linked), clock=_clock)
    assert snapshot.configuration.status is ResultStatus.UNAVAILABLE


@pytest.mark.parametrize("up", [
    "docker-compose -f a.yml --file=b.yml --profile gpu --profile=fast up --detach slot-a",
])
def test_compose_grammar_accepts_every_supported_option_form(up):
    assert _compose_up(up, None)[:2] == (True, "anvil-serving")


class _Scanner:
    def __init__(self, entries):
        self._entries = entries

    def __enter__(self):
        return iter(self._entries)

    def __exit__(self, *_args):
        return False


class _Entry:
    def __init__(self, parent, name):
        self.name = name
        self.path = str(parent / name)


def test_same_file_rejects_identity_or_content_mutation_but_not_atime_change():
    base = dict(st_dev=1, st_ino=2, st_size=3, st_mtime_ns=4, st_ctime_ns=5, st_atime_ns=6)
    before = SimpleNamespace(**base)
    assert _same_file(before, SimpleNamespace(**{**base, "st_atime_ns": 999}))
    for name in ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns"):
        changed = dict(base)
        changed[name] += 1
        assert not _same_file(before, SimpleNamespace(**changed))


@pytest.mark.parametrize("runtime", ["native", "docker"])
@pytest.mark.parametrize("failure", ["nonzero", "throws", "truncated", "wrong-type", "incomplete-line", "empty", "malformed"])
def test_failed_compose_capture_keeps_unsupported_peer_as_partial(runtime, failure, tmp_path):
    extra = "" if runtime == "native" else 'container = "generic"\nup = "docker run ignored"'
    path = _manifest(tmp_path, f'''\
[[serve]]
name = "peer"
runtime = "{runtime}"
{extra}

[[serve]]
name = "compose"
runtime = "docker"
container = "compose"
up = "docker compose -f x.yml up compose"
''')
    configured_time = _clock() - timedelta(hours=1)
    os.utime(path, (configured_time.timestamp(), configured_time.timestamp()))
    observed_time = _clock() - timedelta(seconds=10)
    calls = []

    def capture(argv):
        calls.append(argv)
        if failure == "throws":
            raise OSError("private failure text")
        if failure == "wrong-type":
            return {"private": "not a capture"}
        raw = b"{}\n" if failure == "malformed" else b"private" if failure == "incomplete-line" else b""
        return ChildCapture("unavailable" if failure == "nonzero" else "ok", raw, b"private stderr", failure == "truncated")

    times = iter((observed_time, _clock()))
    snapshot = capture_manifest_workload_snapshot(
        str(path), clock=lambda: next(times), _capture=capture,
    )
    assert calls == [("docker", "inspect", "--type", "container", "--format", _INSPECT_TEMPLATE, "compose")]
    assert snapshot.runtime.status is ResultStatus.PARTIAL
    expected_error = WorkloadErrorCode.INVALID if failure in {"empty", "malformed"} else WorkloadErrorCode.UNAVAILABLE
    assert snapshot.runtime.error is expected_error
    assert snapshot.runtime.omitted is None
    assert len(snapshot.runtime.records) == 1
    peer = snapshot.runtime.records[0]
    assert peer.state is WorkloadState.UNSUPPORTED and peer.container_id is None
    assert peer.created_at == peer.updated_at == configured_time
    assert peer.observed_at == observed_time
    expected_observed = observed_time if failure in {"throws", "wrong-type", "truncated", "incomplete-line"} else _clock()
    assert snapshot.runtime.observed_at == expected_observed

    result = list_manifest_workloads(
        "ignored", "node-a", WorkloadQuery(), _clock(), snapshot_reader=lambda *_args, **_kwargs: snapshot,
    )
    assert result.status is ResultStatus.PARTIAL and result.error is expected_error
    assert result.truncation.omitted is None
    assert {record.state for record in result.records} == {WorkloadState.CONFIGURED, WorkloadState.UNSUPPORTED}
    assert len(result.records) == 2
    assert {record.observation_quality for record in result.records} == {
        ObservationQuality.CONFIGURED, ObservationQuality.INSPECTION_ERROR,
    }
    assert all(record.source_timestamp == observed_time for record in result.records)
    wire = source_result_to_json(result)
    assert source_result_from_json(wire) == result
    assert "private" not in wire and str(path) not in wire


def _budget_files(tmp_path, contents):
    paths = []
    for index, raw in enumerate(contents):
        path = tmp_path / f"serves-{index}.toml"
        path.write_bytes(raw)
        os.utime(path, (1750000000, 1750000000))
        paths.append(path)
    return paths


def _record_manifest_reads(monkeypatch, *, read_failure=None, stat_failure=None):
    """Keep real file reads and descriptors; inject only the selected failure."""
    real_open = open
    real_fstat = os.fstat
    opened, reads, read_fds = [], [], {}

    class Handle:
        def __init__(self, path, mode):
            self.path = Path(path)
            self.inner = real_open(path, mode)
            opened.append(self.path)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            read_fds.pop(self.inner.fileno(), None)
            self.inner.close()

        def fileno(self):
            return self.inner.fileno()

        def read(self, count):
            raw = self.inner.read(count)
            reads.append((self.path, count, len(raw)))
            read_fds[self.fileno()] = self.path
            if self.path == read_failure:
                raise OSError("injected read failure after consumption")
            return raw

    def fstat(fd):
        if fd in read_fds and read_fds[fd] == stat_failure:
            raise OSError("injected post-read fstat failure")
        return real_fstat(fd)

    monkeypatch.setattr(manifest_workloads, "open", Handle, raising=False)
    monkeypatch.setattr(manifest_workloads.os, "fstat", fstat)
    return opened, reads


def _configured(digest="a" * 64, *, age=0, runtime=ManifestRuntimeKind.DOCKER_COMPOSE):
    observed = _clock() - timedelta(seconds=age)
    return ManifestConfiguredObservation(digest, runtime, observed - timedelta(days=100), observed)


def _runtime(digest="a" * 64, container="b" * 64, *, state=WorkloadState.RUNNING, age=0):
    observed = _clock() - timedelta(seconds=age)
    return ManifestRuntimeObservation(digest, container, state, observed - timedelta(days=2), observed - timedelta(seconds=1), observed)


def _snapshot(configured=(), runtime=()):
    return ManifestWorkloadSnapshot(
        ManifestComponentResult(ResultStatus.COMPLETE, _clock(), tuple(configured), 0, None),
        ManifestComponentResult(ResultStatus.COMPLETE, _clock(), tuple(runtime), 0, None),
    )


def _project(snapshot, query=None):
    return list_manifest_workloads(
        "not-read.toml", "node-a", query or WorkloadQuery(), _clock(),
        snapshot_reader=lambda path, *, clock: snapshot,
    )


def _expected_id(native, owner="manifest"):
    material = json.dumps(["node-a", "recipe-serve", owner, native], separators=(",", ":")).encode()
    return hashlib.sha256(material).hexdigest()


def test_projection_uses_exact_manifest_id_namespace_and_valid_digest_reconciliation():
    snapshot = _snapshot(
        (_configured(), _configured("d" * 64)),
        (_runtime(), _runtime(container="c" * 64), _runtime("e" * 64, "f" * 64, state=WorkloadState.ABSENT)),
    )
    result = _project(snapshot)
    assert result.status is ResultStatus.COMPLETE
    assert {row.id for row in result.records} == {
        _expected_id("manifest-config:" + "d" * 64),
        *(_expected_id("manifest-container:" + value * 64) for value in "bcf"),
    }
    assert all(row.kind is WorkloadKind.RECIPE_SERVE and row.owner is WorkloadOwner.MANIFEST for row in result.records)
    assert _expected_id("manifest-config:" + "d" * 64, "recipe") not in {row.id for row in result.records}
    payload = source_result_to_json(result)
    assert source_result_from_json(payload) == result
    for private in ["not-read.toml", *(value * 64 for value in "abcdef"), "healthy-identity"]:
        assert private not in payload


def test_projection_duplicate_runtime_is_invalid_and_cannot_suppress_another_digest():
    runtime = _runtime()
    result = _project(_snapshot((_configured(), _configured("c" * 64)), (runtime, replace(runtime, config_digest="c" * 64))))
    assert result.status is ResultStatus.PARTIAL and result.error is WorkloadErrorCode.INVALID
    assert {row.id for row in result.records} == {
        _expected_id("manifest-container:" + "b" * 64), _expected_id("manifest-config:" + "c" * 64),
    }
