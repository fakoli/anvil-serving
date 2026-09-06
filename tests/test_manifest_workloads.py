"""Hermetic bounded manifest workload observation coverage."""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from types import SimpleNamespace
from pathlib import Path
import subprocess
import sys

import pytest

from anvil_serving import manifest_workloads
from anvil_serving.controller_diagnostics import ChildCapture
from anvil_serving.manifest_workloads import (
    MAX_CAPTURE_BYTES,
    MAX_MANIFEST_BYTES,
    ManifestComponentResult,
    ManifestConfiguredObservation,
    ManifestRuntimeKind,
    ManifestRuntimeObservation,
    ManifestWorkloadSnapshot,
    _INSPECT_TEMPLATE,
    _compose_up,
    _paths,
    _same_file,
    _time,
    capture_manifest_workload_snapshot,
    list_manifest_workloads,
)
from anvil_serving.observability.workloads import (
    ObservationQuality, ResultStatus, WorkloadErrorCode, WorkloadKind,
    WorkloadOwner, WorkloadPhase, WorkloadQuery, WorkloadState,
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


def test_manifest_fixture_mtime_is_fixed_independently_of_wall_clock(tmp_path):
    for path in (
        _manifest(tmp_path, ""),
        _write_manifest(tmp_path / "serves-sibling.toml", ""),
    ):
        measured = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
        assert measured == datetime(2026, 9, 5, 22, tzinfo=timezone.utc)


def _row(name="slot-a", container="container-a", *, status="running", running=True):
    return (
        '{"id":"' + "a" * 64 + '","name":"/' + container +
        '","created_at":"2026-09-05T11:00:00.123456789Z","status":"' + status +
        '","running":' + str(running).lower() +
        ',"started_at":"2026-09-05T11:01:00Z","finished_at":"0001-01-01T00:00:00Z",'
        '"project":"anvil-serving","service":"' + name + '"}\n'
    ).encode("ascii")


def test_compose_capture_is_declared_name_only_and_keeps_configuration_distinct(tmp_path):
    path = _manifest(tmp_path, '''
[[serve]]
name = "slot-a"
runtime = "docker"
container = "container-a"
up = "docker compose -f ignored.yml up -d slot-a"
''')
    seen = []

    def capture(argv):
        seen.append(argv)
        return ChildCapture("ok", _row(), b"private-stderr", False)

    snapshot = capture_manifest_workload_snapshot(str(path), clock=_clock, _capture=capture)
    assert snapshot.configuration.status is ResultStatus.COMPLETE
    assert snapshot.configuration.records[0].runtime is ManifestRuntimeKind.DOCKER_COMPOSE
    assert snapshot.runtime.status is ResultStatus.COMPLETE
    assert snapshot.runtime.records[0].state is WorkloadState.RUNNING
    assert seen == [("docker", "inspect", "--type", "container", "--format", _INSPECT_TEMPLATE, "container-a")]
    assert "ignored.yml" not in repr(snapshot) and "private-stderr" not in repr(snapshot)


def test_native_and_unsupported_declarations_never_capture_or_claim_running(tmp_path):
    path = _manifest(tmp_path, '''
[[serve]]
name = "native-slot"
runtime = "native"

[[serve]]
name = "generic-slot"
runtime = "docker"
container = "generic-container"
up = "docker run private-image"
''')
    snapshot = capture_manifest_workload_snapshot(
        str(path), clock=_clock, _capture=lambda _argv: (_ for _ in ()).throw(AssertionError()),
    )
    assert snapshot.runtime.status is ResultStatus.COMPLETE
    assert all(row.state is WorkloadState.UNSUPPORTED for row in snapshot.runtime.records)
    assert all(row.container_id is None for row in snapshot.runtime.records)


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


def test_explicit_missing_input_is_unavailable_not_complete_empty(tmp_path):
    snapshot = capture_manifest_workload_snapshot(str(tmp_path / "missing.toml"), clock=_clock)
    assert snapshot.configuration.status is ResultStatus.UNAVAILABLE
    assert snapshot.runtime.status is ResultStatus.UNAVAILABLE


def test_invalid_or_duplicate_capture_rows_are_partial_without_private_data(tmp_path):
    path = _manifest(tmp_path, '''
[[serve]]
name = "slot-a"
runtime = "docker"
container = "container-a"
up = "docker compose -f x.yml up slot-a"
''')
    duplicate = _row() + _row()
    snapshot = capture_manifest_workload_snapshot(
        str(path), clock=_clock, _capture=lambda _argv: ChildCapture("ok", duplicate, b"secret", False),
    )
    assert snapshot.runtime.status is ResultStatus.PARTIAL
    assert len(snapshot.runtime.records) == 1
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


def test_compose_grammar_requires_file_and_single_service(tmp_path):
    path = _manifest(tmp_path, '''
[[serve]]
name = "slot-a"
runtime = "docker"
container = "container-a"
up = "docker compose up slot-a slot-b"
''')
    snapshot = capture_manifest_workload_snapshot(
        str(path), clock=_clock, _capture=lambda _argv: (_ for _ in ()).throw(AssertionError()),
    )
    assert snapshot.configuration.records[0].runtime is ManifestRuntimeKind.DOCKER_GENERIC
    assert snapshot.runtime.records[0].state is WorkloadState.UNSUPPORTED


def test_capture_overflow_or_wrong_capture_shape_is_unavailable(tmp_path):
    path = _manifest(tmp_path, '''
[[serve]]
name = "slot-a"
runtime = "docker"
container = "container-a"
up = "docker-compose -f x.yml up slot-a"
''')
    for capture in (
        lambda _argv: ChildCapture("ok", b"x" * (MAX_CAPTURE_BYTES + 1), b"", False),
        lambda _argv: object(),
        lambda _argv: ChildCapture("timeout", b"private", b"", True),
    ):
        snapshot = capture_manifest_workload_snapshot(str(path), clock=_clock, _capture=capture)
        assert snapshot.runtime.status is ResultStatus.UNAVAILABLE
        assert "private" not in repr(snapshot)


@pytest.mark.parametrize("up", [
    "docker compose -f x.yml up slot-a",
    "docker-compose --file x.yml up slot-a",
    "docker compose --file=x.yml up -d slot-a",
    "docker-compose -f a.yml --file=b.yml --profile gpu --profile=fast up --detach slot-a",
    "docker compose -f x.yml -p anvil-serving up slot-a",
    "docker-compose --file=x.yml --project-name=anvil-serving up slot-a",
])
def test_compose_grammar_accepts_every_supported_option_form(up):
    assert _compose_up(up, None)[:2] == (True, "anvil-serving")


@pytest.mark.parametrize("up", [
    None, "",
])
def test_compose_absent_or_empty_command_is_a_mirror(up):
    assert _compose_up(up, None)[:2] == (False, "anvil-serving")


@pytest.mark.parametrize("up", [
    "docker compose up slot-a",
    "docker compose -f x.yml -p anvil-serving --project-name anvil-serving up slot-a",
    "docker compose -f x.yml -p wrong-project up slot-a",
    "docker compose -f up slot-a",
    "docker compose -f '' up slot-a",
    "docker compose -f --profile gpu up slot-a",
    "docker compose --unknown x -f y up slot-a",
    "docker compose -f x.yml down slot-a",
    "docker compose -f x.yml up --profile gpu slot-a",
    "docker compose -f x.yml up slot-a slot-b",
    "docker compose -f x.yml up slot-a trailing",
    "docker compose -f 'bad\x01' up slot-a",
    "docker compose -f x.yml --profile bad/name up slot-a",
    "docker compose -f x.yml -p bad/name up slot-a",
    "docker compose -f x.yml up bad/name",
    "docker compose --file= up slot-a",
    "docker compose -f " + "x" * 1025 + " up slot-a",
    "docker compose -f x.yml up " + "slot " * 129,
    "docker compose -f " + "x" * 8200 + " up slot-a",
])
def test_compose_grammar_rejects_unsupported_or_unsafe_forms(up):
    assert _compose_up(up, None)[0] is False


@pytest.mark.parametrize("value", [1, [], {"up": "docker compose -f x up a"}])
def test_compose_grammar_rejects_nonstring_up(value):
    assert _compose_up(value, None)[0] is False


def test_regular_serves_siblings_are_lexically_ordered_and_bad_peer_is_partial(tmp_path):
    explicit = _manifest(tmp_path, '''
[[serve]]
name = "z-slot"
runtime = "native"
''')
    _write_manifest(tmp_path / "serves-a.toml", '''
[[serve]]
name = "a-slot"
runtime = "native"
''')
    _write_manifest(tmp_path / "not-serves.toml", "not = [valid")
    _write_manifest(tmp_path / "serves-b.toml", "not = [valid")
    snapshot = capture_manifest_workload_snapshot(str(explicit), clock=_clock)
    assert snapshot.configuration.status is ResultStatus.PARTIAL
    assert len(snapshot.configuration.records) == 2


@pytest.mark.parametrize("text", [
    "serve = {}",
    "serve = [1]",
    "[[serve]]\nname = 1\nruntime = 'native'",
    "[[serve]]\nname = 'native'\nruntime = 1",
    "[[serve]]\nname = 'native'\nruntime = 'native'\ncontainer = 'nope'",
    "[[serve]]\nname = 'native'\nruntime = 'docker'",
    "[[serve]]\nname = 'bad/name'\nruntime = 'native'",
])
def test_invalid_top_level_or_minimal_field_shapes_are_not_successful_configuration(tmp_path, text):
    snapshot = capture_manifest_workload_snapshot(str(_manifest(tmp_path, text)), clock=_clock)
    assert snapshot.configuration.status is not ResultStatus.COMPLETE


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


@pytest.mark.parametrize("count,accepted", [(4096, True), (4097, False)])
def test_directory_entry_cap_has_no_arbitrary_subset(monkeypatch, tmp_path, count, accepted):
    explicit = tmp_path / "input.toml"
    entries = [_Entry(tmp_path, f"other-{index}.txt") for index in range(count)]
    monkeypatch.setattr(manifest_workloads.os, "scandir", lambda _path: _Scanner(entries))
    if accepted:
        assert _paths(str(explicit)) == (explicit,)
    else:
        with pytest.raises(ValueError):
            _paths(str(explicit))


@pytest.mark.parametrize("siblings,accepted", [(63, True), (64, False)])
def test_selected_file_cap_has_no_arbitrary_subset(monkeypatch, tmp_path, siblings, accepted):
    explicit = tmp_path / "input.toml"
    entries = [_Entry(tmp_path, f"serves-{index}.toml") for index in range(siblings)]
    for entry in entries:
        Path(entry.path).touch()
    monkeypatch.setattr(manifest_workloads.os, "scandir", lambda _path: _Scanner(entries))
    if accepted:
        assert len(_paths(str(explicit))) == 64
    else:
        with pytest.raises(ValueError):
            _paths(str(explicit))


def test_same_file_rejects_identity_or_content_mutation_but_not_atime_change():
    base = dict(st_dev=1, st_ino=2, st_size=3, st_mtime_ns=4, st_ctime_ns=5, st_atime_ns=6)
    before = SimpleNamespace(**base)
    assert _same_file(before, SimpleNamespace(**{**base, "st_atime_ns": 999}))
    for name in ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns"):
        changed = dict(base)
        changed[name] += 1
        assert not _same_file(before, SimpleNamespace(**changed))


@pytest.mark.parametrize("status,running,expected", [
    ("running", True, WorkloadState.RUNNING),
    ("created", False, WorkloadState.ABSENT),
    ("exited", False, WorkloadState.ABSENT),
    ("dead", False, WorkloadState.ABSENT),
    ("paused", False, WorkloadState.UNSUPPORTED),
    ("restarting", False, WorkloadState.UNSUPPORTED),
    ("removing", False, WorkloadState.UNSUPPORTED),
    ("unknown-private", False, WorkloadState.UNSUPPORTED),
])
def test_runtime_lifecycle_mapping_is_strict_and_metadata_only(tmp_path, status, running, expected):
    path = _manifest(tmp_path, '''
[[serve]]
name = "slot-a"
runtime = "docker"
container = "container-a"
up = "docker compose -f x.yml up slot-a"
''')
    snapshot = capture_manifest_workload_snapshot(
        str(path), clock=_clock,
        _capture=lambda _argv: ChildCapture("ok", _row(status=status, running=running), b"raw-stderr", False),
    )
    assert snapshot.runtime.records[0].state is expected
    assert "raw-stderr" not in repr(snapshot)


@pytest.mark.parametrize("status,running", [("running", False), ("created", True), ("exited", True), ("dead", True)])
def test_contradictory_known_runtime_pairs_are_quarantined(tmp_path, status, running):
    path = _manifest(tmp_path, '''
[[serve]]
name = "slot-a"
runtime = "docker"
container = "container-a"
up = "docker compose -f x.yml up slot-a"
''')
    snapshot = capture_manifest_workload_snapshot(
        str(path), clock=_clock, _capture=lambda _argv: ChildCapture("ok", _row(status=status, running=running), b"", False),
    )
    assert snapshot.runtime.status is ResultStatus.PARTIAL


@pytest.mark.parametrize("raw,microsecond", [
    ("2026-09-05T11:00:00Z", 0),
    ("2026-09-05T11:00:00.1Z", 100000),
    ("2026-09-05T11:00:00.123456789Z", 123456),
])
def test_rfc3339_nano_flooring_and_optional_zero_none(raw, microsecond):
    assert _time(raw).microsecond == microsecond
    assert _time("0001-01-01T00:00:00Z", optional=True) is None
    assert _time(None, optional=True) is None
    for invalid in ("2026-09-05T11:00:00+00:00", "bad", "2026-09-05T11:00:00.1234567890Z"):
        with pytest.raises(ValueError):
            _time(invalid)


@pytest.mark.parametrize("first,second,kept", [
    ("", "", 1),
    ("", "docker compose -f x.yml up service-a", 1),
    ("docker compose -f x.yml up service-a", "docker compose -f x.yml up service-a", 1),
    ("docker compose -f x.yml up service-a", "docker compose -f y.yml up service-a", 0),
])
def test_manifest_mirrors_collapse_or_quarantine_by_preserved_command(tmp_path, first, second, kept):
    path = _manifest(tmp_path, f'''\
[[serve]]
name = "slot-a"
runtime = "docker"
container = "container-a"
up = "{first}"

[[serve]]
name = "slot-a"
runtime = "docker"
container = "container-a"
up = "{second}"
''')
    snapshot = capture_manifest_workload_snapshot(
        str(path), clock=_clock, _capture=lambda argv: ChildCapture("unavailable", b"", b"", False),
    )
    assert len(snapshot.configuration.records) == kept


@pytest.mark.parametrize("second_name,second_container,second_up", [
    ("slot-a", "container-b", "docker compose -f x.yml up service-a"),
    ("slot-b", "container-a", "docker compose -f x.yml up service-b"),
    ("slot-a", "container-a", "docker run ignored"),
])
def test_manifest_collision_quarantines_only_conflicted_identity(tmp_path, second_name, second_container, second_up):
    path = _manifest(tmp_path, f'''\
[[serve]]
name = "slot-a"
runtime = "docker"
container = "container-a"
up = "docker compose -f x.yml up service-a"

[[serve]]
name = "{second_name}"
runtime = "docker"
container = "{second_container}"
up = "{second_up}"

[[serve]]
name = "unrelated"
runtime = "native"
''')
    snapshot = capture_manifest_workload_snapshot(str(path), clock=_clock)
    assert len(snapshot.configuration.records) == 1
    assert len(snapshot.runtime.records) == 1


@pytest.mark.parametrize("timestamp", ["0001-01-01T00:00:00Z", "2026-09-05T10:00:00Z"])
def test_running_requires_started_and_lifecycle_times_follow_created(tmp_path, timestamp):
    path = _manifest(tmp_path, '''
[[serve]]
name = "slot-a"
runtime = "docker"
container = "container-a"
up = "docker compose -f x.yml up service-a"
''')
    raw = _row(name="service-a").replace(
        b'"started_at":"2026-09-05T11:01:00Z"',
        ('"started_at":"' + timestamp + '"').encode("ascii"),
    )
    snapshot = capture_manifest_workload_snapshot(str(path), clock=_clock, _capture=lambda _argv: ChildCapture("ok", raw, b"", False))
    assert snapshot.runtime.status is ResultStatus.PARTIAL


def test_runtime_row_budget_counts_malformed_rows_before_valid_trailer(tmp_path):
    path = _manifest(tmp_path, '''
[[serve]]
name = "slot-a"
runtime = "docker"
container = "container-a"
up = "docker compose -f x.yml up service-a"
''')
    valid = _row().replace(b'"service":"slot-a"', b'"service":"service-a"')
    raw = valid + (b"{}\n" * 256) + valid
    snapshot = capture_manifest_workload_snapshot(
        str(path), clock=_clock, _capture=lambda _argv: ChildCapture("ok", raw, b"", False),
    )
    assert snapshot.runtime.status is ResultStatus.PARTIAL
    assert snapshot.runtime.omitted is None
    assert len(snapshot.runtime.records) == 1


def test_invalid_declaration_rows_consume_aggregate_budget_before_valid_row(tmp_path):
    path = _manifest(tmp_path, "serve = [\n" + ",\n".join("1" for _ in range(257)) + "]")
    snapshot = capture_manifest_workload_snapshot(str(path), clock=_clock)
    assert snapshot.configuration.status is ResultStatus.PARTIAL
    assert snapshot.configuration.records == ()


def test_matching_nonregular_siblings_do_not_consume_regular_selection_cap(tmp_path):
    explicit = _manifest(tmp_path, "")
    target = tmp_path / "target"
    target.write_text("", encoding="utf-8")
    for index in range(65):
        try:
            (tmp_path / f"serves-link-{index}.toml").symlink_to(target)
        except OSError:
            pytest.skip("symlink creation unavailable")
    snapshot = capture_manifest_workload_snapshot(str(explicit), clock=_clock)
    assert snapshot.configuration.status is ResultStatus.COMPLETE


def test_manifest_module_import_does_not_eagerly_import_diagnostics():
    source = (
        "import builtins; original=builtins.__import__; "
        "builtins.__import__=lambda name,*a,**k: (_ for _ in ()).throw(RuntimeError()) "
        "if name.endswith('controller_diagnostics') else original(name,*a,**k); "
        "import anvil_serving.manifest_workloads"
    )
    result = subprocess.run([sys.executable, "-c", source], capture_output=True, text=True, check=False)
    assert result.returncode == 0


def test_future_runtime_peer_uses_future_error_and_preserves_valid_peer(tmp_path):
    path = _manifest(tmp_path, '''
[[serve]]
name = "slot-a"
runtime = "docker"
container = "container-a"
up = "docker compose -f x.yml up service-a"
''')
    valid = _row().replace(b'"service":"slot-a"', b'"service":"service-a"')
    future = valid.replace(b"2026-09-05T11", b"2027-09-05T11")
    snapshot = capture_manifest_workload_snapshot(str(path), clock=_clock, _capture=lambda _argv: ChildCapture("ok", valid + future, b"", False))
    assert snapshot.runtime.error.value == "future-workload-timestamp"
    assert snapshot.runtime.status is ResultStatus.PARTIAL
    assert len(snapshot.runtime.records) == 1


def test_unused_runtime_lifecycle_timestamp_in_future_is_quarantined(tmp_path):
    path = _manifest(tmp_path, '''
[[serve]]
name = "slot-a"
runtime = "docker"
container = "container-a"
up = "docker compose -f x.yml up slot-a"
''')
    raw = _row().replace(b'"finished_at":"0001-01-01T00:00:00Z"', b'"finished_at":"2027-09-05T11:00:00Z"')
    snapshot = capture_manifest_workload_snapshot(
        str(path), clock=_clock, _capture=lambda argv: ChildCapture("ok", raw, b"", False),
    )
    assert snapshot.runtime.status is ResultStatus.PARTIAL
    assert snapshot.runtime.error.value == "future-workload-timestamp"
    assert snapshot.runtime.records == ()


def test_same_identity_different_stack_is_quarantined_with_unrelated_survivor(tmp_path):
    path = _manifest(tmp_path, '''
[[serve]]
name = "slot-a"
runtime = "docker"
container = "container-a"
stack = "serving"

[[serve]]
name = "slot-a"
runtime = "docker"
container = "container-a"
stack = "other"

[[serve]]
name = "unrelated"
runtime = "native"
''')
    snapshot = capture_manifest_workload_snapshot(str(path), clock=_clock)
    assert len(snapshot.configuration.records) == 1
    assert len(snapshot.runtime.records) == 1


def test_future_configuration_mtime_is_future_with_valid_sibling_retained(tmp_path):
    future = _manifest(tmp_path, '''
[[serve]]
name = "future"
runtime = "native"
''')
    sibling = tmp_path / "serves-valid.toml"
    _write_manifest(sibling, '''
[[serve]]
name = "valid"
runtime = "native"
''')
    os.utime(future, (1924992000, 1924992000))  # 2031-01-01 UTC
    snapshot = capture_manifest_workload_snapshot(
        str(future), clock=lambda: datetime(2030, 1, 1, tzinfo=timezone.utc),
    )
    assert snapshot.configuration.error.value == "future-workload-timestamp"
    assert len(snapshot.configuration.records) == 1


@pytest.mark.parametrize("seconds,expected_count,error", [
    (30, 2, None),
    (31, 1, WorkloadErrorCode.FUTURE),
])
def test_configuration_future_mtime_boundary_preserves_valid_sibling(
    tmp_path, seconds, expected_count, error,
):
    selected = _manifest(tmp_path, "[[serve]]\nname='selected'\nruntime='native'\n")
    sibling = _write_manifest(
        tmp_path / "serves-valid.toml", "[[serve]]\nname='valid'\nruntime='native'\n",
    )
    timestamp = (_clock() + timedelta(seconds=seconds)).timestamp()
    os.utime(selected, (timestamp, timestamp))
    snapshot = capture_manifest_workload_snapshot(str(selected), clock=_clock)
    expected_status = ResultStatus.COMPLETE if error is None else ResultStatus.PARTIAL
    assert snapshot.configuration.status is expected_status
    assert snapshot.configuration.error is error
    assert len(snapshot.configuration.records) == expected_count
    sibling_time = datetime.fromtimestamp(sibling.stat().st_mtime, timezone.utc)
    assert any(row.configured_at == sibling_time for row in snapshot.configuration.records)


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


@pytest.mark.parametrize("throws", [False, True])
def test_failed_compose_capture_without_peers_remains_unavailable(throws, tmp_path):
    path = _manifest(tmp_path, '''[[serve]]
name = "compose"
runtime = "docker"
container = "compose"
up = "docker compose -f ignored.yml up compose"
''')

    def capture(_argv):
        if throws:
            raise OSError("private failure text")
        return ChildCapture("unavailable", b"", b"", False)

    snapshot = capture_manifest_workload_snapshot(str(path), clock=_clock, _capture=capture)
    assert snapshot.runtime.status is ResultStatus.UNAVAILABLE
    assert snapshot.runtime.records == ()
    assert snapshot.runtime.error is WorkloadErrorCode.UNAVAILABLE
    assert snapshot.runtime.omitted is None
    result = list_manifest_workloads(
        "ignored", "node-a", WorkloadQuery(), _clock(), snapshot_reader=lambda *_args, **_kwargs: snapshot,
    )
    assert result.status is ResultStatus.PARTIAL
    assert [record.state for record in result.records] == [WorkloadState.CONFIGURED]
    assert result.error is WorkloadErrorCode.UNAVAILABLE


def test_native_only_projection_keeps_unsupported_without_subprocess(tmp_path):
    path = _manifest(tmp_path, '[[serve]]\nname = "native"\nruntime = "native"\n')

    def forbidden(_argv):
        pytest.fail("native-only capture invoked a subprocess")

    snapshot = capture_manifest_workload_snapshot(str(path), clock=_clock, _capture=forbidden)
    assert snapshot.runtime.status is ResultStatus.COMPLETE
    result = list_manifest_workloads(
        "ignored", "node-a", WorkloadQuery(), _clock(), snapshot_reader=lambda *_args, **_kwargs: snapshot,
    )
    assert result.status is ResultStatus.COMPLETE and result.error is None
    assert result.truncation.omitted == 0
    assert [record.state for record in result.records] == [WorkloadState.UNSUPPORTED]
    assert result.records[0].observation_quality is ObservationQuality.INSPECTION_ERROR


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


def test_multiple_unstable_files_share_one_aggregate_read_budget(monkeypatch, tmp_path):
    valid = b"[[serve]]\nname='retained'\nruntime='native'\n"
    paths = _budget_files(tmp_path, [valid, b"#" * 20, b"#" * 30, valid])
    limit = len(valid) + 30
    monkeypatch.setattr(manifest_workloads, "MAX_MANIFEST_BYTES", limit)
    stability = iter([True, False, False])
    monkeypatch.setattr(manifest_workloads, "_same_file", lambda before, after: next(stability))
    opened, reads = _record_manifest_reads(monkeypatch)
    snapshot = capture_manifest_workload_snapshot(str(paths[0]), clock=_clock)
    assert opened == paths[:3]
    assert [count for _, count, _ in reads] == [limit + 1, 31, 11]
    assert sum(size for _, _, size in reads) == limit + 1
    assert snapshot.configuration.status is ResultStatus.PARTIAL
    assert snapshot.configuration.omitted is None
    assert len(snapshot.configuration.records) == 1


def test_oversized_first_manifest_stops_before_opening_later_file(monkeypatch, tmp_path):
    paths = _budget_files(tmp_path, [b"#" * 100, b"[[serve]]\nname='later'\nruntime='native'\n"])
    monkeypatch.setattr(manifest_workloads, "MAX_MANIFEST_BYTES", 32)
    opened, reads = _record_manifest_reads(monkeypatch)
    snapshot = capture_manifest_workload_snapshot(str(paths[0]), clock=_clock)
    assert opened == paths[:1] and reads == [(paths[0], 33, 33)]
    assert snapshot.configuration.status is ResultStatus.UNAVAILABLE
    assert snapshot.configuration.records == ()


def test_exact_aggregate_manifest_limit_retains_all_records(monkeypatch, tmp_path):
    contents = [b"[[serve]]\nname='first'\nruntime='native'\n", b"[[serve]]\nname='second'\nruntime='native'\n", b""]
    paths = _budget_files(tmp_path, contents)
    limit = sum(map(len, contents))
    monkeypatch.setattr(manifest_workloads, "MAX_MANIFEST_BYTES", limit)
    opened, reads = _record_manifest_reads(monkeypatch)
    snapshot = capture_manifest_workload_snapshot(str(paths[0]), clock=_clock)
    assert opened == paths
    assert sum(size for _, _, size in reads) == limit
    assert reads[-1] == (paths[-1], 1, 0)
    assert snapshot.configuration.status is ResultStatus.COMPLETE
    assert len(snapshot.configuration.records) == 2


@pytest.mark.parametrize("failure", ["read", "fstat"])
def test_failed_read_or_post_read_stat_cannot_reset_aggregate_budget(monkeypatch, tmp_path, failure):
    valid = b"[[serve]]\nname='retained'\nruntime='native'\n"
    paths = _budget_files(tmp_path, [valid, b"#" * 20, b"#" * 100, valid])
    limit = len(valid) + 30
    monkeypatch.setattr(manifest_workloads, "MAX_MANIFEST_BYTES", limit)
    opened, reads = _record_manifest_reads(
        monkeypatch, read_failure=paths[1] if failure == "read" else None,
        stat_failure=paths[1] if failure == "fstat" else None,
    )
    snapshot = capture_manifest_workload_snapshot(str(paths[0]), clock=_clock)
    assert opened == paths[:2] if failure == "read" else opened == paths[:3]
    assert sum(size for _, _, size in reads) <= limit + 1
    if failure == "fstat":
        assert reads[-1] == (paths[2], 11, 11)
    assert snapshot.configuration.status is ResultStatus.PARTIAL
    assert len(snapshot.configuration.records) == 1


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


@pytest.mark.parametrize("state,phase,quality", [
    (WorkloadState.RUNNING, WorkloadPhase.RUNNING, ObservationQuality.OBSERVED_RUNNING),
    (WorkloadState.ABSENT, WorkloadPhase.ABSENT, ObservationQuality.ABSENT),
    (WorkloadState.UNSUPPORTED, WorkloadPhase.UNSUPPORTED, ObservationQuality.INSPECTION_ERROR),
])
def test_projection_runtime_states_and_provenance_are_not_health_claims(state, phase, quality):
    result = _project(_snapshot((_configured(),), (_runtime(state=state),)))
    row, = result.records
    assert (row.state, row.phase, row.observation_quality) == (state, phase, quality)
    assert row.source_timestamp == _clock()
    assert row.updated_at == _clock() - timedelta(seconds=1)


def test_projection_configured_and_native_unsupported_use_one_slot_identity():
    configured = _configured(runtime=ManifestRuntimeKind.NATIVE)
    declared = _project(_snapshot((configured,)))
    assert declared.records[0].observation_quality is ObservationQuality.CONFIGURED
    observed = _project(_snapshot((configured,), (_runtime(container=None, state=WorkloadState.UNSUPPORTED),)))
    row, = observed.records
    assert row.id == declared.records[0].id == _expected_id("manifest-config:" + "a" * 64)
    assert row.state is WorkloadState.UNSUPPORTED
    assert row.observation_quality is ObservationQuality.INSPECTION_ERROR


@pytest.mark.parametrize("age,stale", [(30, False), (30.000001, True)])
@pytest.mark.parametrize("configured", [False, True])
def test_projection_freshness_uses_observation_not_old_lifecycle(age, stale, configured):
    snapshot = _snapshot((_configured(age=age),)) if configured else _snapshot(runtime=(_runtime(age=age),))
    result = _project(snapshot)
    row, = result.records
    assert (row.observation_quality is ObservationQuality.STALE) is stale
    assert row.freshness(_clock()).is_stale is stale
    active = _project(snapshot, WorkloadQuery(active_only=True))
    assert bool(active.records) is (not configured and not stale)


@pytest.mark.parametrize("bad", [
    replace(_runtime(), container_id="bad/path"),
    replace(_runtime(), container_id=None),
    replace(_runtime(), state="running"),
    replace(_runtime(), state=WorkloadState.QUEUED),
    replace(_runtime(), observed_at=_clock().replace(tzinfo=None)),
    replace(_runtime(), created_at=_clock(), updated_at=_clock() - timedelta(seconds=1)),
    replace(_runtime(), config_digest=None),
    object(),
])
def test_invalid_runtime_peer_never_suppresses_valid_configuration(bad):
    result = _project(_snapshot((_configured(),), (bad,)))
    assert result.status is ResultStatus.PARTIAL and result.error is WorkloadErrorCode.INVALID
    assert result.truncation.omitted is None
    assert [row.state for row in result.records] == [WorkloadState.CONFIGURED]


def test_future_runtime_cannot_suppress_configured_peer_or_hide_invalid_error():
    future = replace(_runtime(), observed_at=_clock() + timedelta(seconds=31))
    snapshot = _snapshot((_configured(),), (future,))
    result = _project(snapshot)
    assert result.status is ResultStatus.PARTIAL and result.error is WorkloadErrorCode.FUTURE
    assert result.records[0].state is WorkloadState.CONFIGURED
    mixed = _project(replace(snapshot, runtime=replace(snapshot.runtime, records=(future, object()))))
    assert mixed.error is WorkloadErrorCode.INVALID and len(mixed.records) == 1


@pytest.mark.parametrize("bad_component", [
    object(),
    ManifestComponentResult(ResultStatus.UNAVAILABLE, None, (), None, WorkloadErrorCode.UNAVAILABLE),
    ManifestComponentResult(ResultStatus.COMPLETE, _clock(), (), None, None),
    ManifestComponentResult(ResultStatus.COMPLETE, _clock(), [], 0, None),
    ManifestComponentResult(ResultStatus.COMPLETE, _clock(), (_runtime(),) * 257, 0, None),
    ManifestComponentResult(ResultStatus.COMPLETE, _clock() + timedelta(seconds=31), (), 0, None),
])
def test_projection_component_failure_preserves_valid_other_component(bad_component):
    result = _project(replace(_snapshot((_configured(),)), runtime=bad_component))
    assert result.status is ResultStatus.PARTIAL and result.truncation.omitted is None
    assert result.records[0].state is WorkloadState.CONFIGURED


def test_projection_duplicate_runtime_is_invalid_and_cannot_suppress_another_digest():
    runtime = _runtime()
    result = _project(_snapshot((_configured(), _configured("c" * 64)), (runtime, replace(runtime, config_digest="c" * 64))))
    assert result.status is ResultStatus.PARTIAL and result.error is WorkloadErrorCode.INVALID
    assert {row.id for row in result.records} == {
        _expected_id("manifest-container:" + "b" * 64), _expected_id("manifest-config:" + "c" * 64),
    }


def test_projection_empty_success_and_all_failed_are_distinct():
    empty = _project(_snapshot())
    assert empty.status is ResultStatus.COMPLETE and empty.truncation.omitted == 0
    failed = ManifestComponentResult(ResultStatus.UNAVAILABLE, None, (), None, WorkloadErrorCode.UNAVAILABLE)
    result = _project(ManifestWorkloadSnapshot(failed, failed))
    assert result.status is ResultStatus.UNAVAILABLE and result.records == ()
    assert result.error is WorkloadErrorCode.UNAVAILABLE


def test_projection_applies_all_filters_before_source_cap_and_reports_exact_omissions():
    observations = tuple(_runtime(f"{index:064x}", f"{index + 1000:064x}", state=WorkloadState.ABSENT if index < 220 else WorkloadState.RUNNING) for index in range(256))
    snapshot = _snapshot(runtime=observations)
    limited = _project(snapshot, WorkloadQuery(limit=1000))
    assert len(limited.records) == 200 and limited.truncation.omitted == 56
    assert limited.status is ResultStatus.PARTIAL and limited.error is None
    assert source_result_from_json(source_result_to_json(limited)) == limited
    filtered = _project(snapshot, WorkloadQuery(state=WorkloadState.RUNNING, limit=1000))
    assert len(filtered.records) == 36 and filtered.truncation.omitted == 0
    assert filtered.status is ResultStatus.COMPLETE
    assert filtered.records == tuple(sorted(filtered.records, key=lambda row: row.id))
    for query in (WorkloadQuery(owner=WorkloadOwner.RECIPE), WorkloadQuery(host="node-b"), WorkloadQuery(kind=WorkloadKind.ROUTER_REQUEST)):
        result = _project(snapshot, query)
        assert result.records == () and result.truncation.omitted == 0


def test_projection_partial_producer_keeps_unknown_omissions_even_after_excluding_query():
    snapshot = _snapshot((_configured(),))
    snapshot = replace(snapshot, configuration=replace(snapshot.configuration, status=ResultStatus.PARTIAL, omitted=None, error=WorkloadErrorCode.INVALID))
    result = _project(snapshot, WorkloadQuery(owner=WorkloadOwner.RECIPE))
    assert result.status is ResultStatus.PARTIAL and result.truncation.omitted is None


def test_projection_only_calls_snapshot_reader_and_never_reopens_or_probes(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("projection attempted file, lifecycle or process I/O")

    monkeypatch.setattr(manifest_workloads, "open", forbidden, raising=False)
    monkeypatch.setattr(manifest_workloads, "_paths", forbidden)
    monkeypatch.setattr("anvil_serving.serves.load_manifest", forbidden)
    monkeypatch.setattr("anvil_serving.controller_diagnostics._capture_fixed_child", forbidden)
    seen = []

    def reader(path, *, clock):
        seen.append(path)
        assert clock().tzinfo is timezone.utc
        return _snapshot((_configured(),))

    result = list_manifest_workloads("synthetic-private-path", "node-a", WorkloadQuery(), _clock(), snapshot_reader=reader)
    assert seen == ["synthetic-private-path"] and len(result.records) == 1
    assert "synthetic-private-path" not in source_result_to_json(result)


def test_projection_reader_exception_is_fixed_unavailable_without_exception_text():
    def reader(path, *, clock):
        raise ValueError("private-token http://100.64.0.10:8000 private/path")

    result = list_manifest_workloads("private/path", "node-a", WorkloadQuery(), _clock(), snapshot_reader=reader)
    assert result.status is ResultStatus.UNAVAILABLE and result.error is WorkloadErrorCode.UNAVAILABLE
    assert "private" not in source_result_to_json(result)


def test_projection_malformed_frozen_snapshot_is_not_trusted():
    result = _project(object.__new__(ManifestWorkloadSnapshot))
    assert result.status is ResultStatus.UNAVAILABLE and result.error is WorkloadErrorCode.INVALID
