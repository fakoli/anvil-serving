"""Hermetic bounded manifest workload observation coverage."""
from __future__ import annotations

from datetime import datetime, timezone
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
    ManifestRuntimeKind,
    _INSPECT_TEMPLATE,
    _compose_up,
    _paths,
    _same_file,
    _time,
    capture_manifest_workload_snapshot,
)
from anvil_serving.observability.workloads import ResultStatus, WorkloadState


def _clock():
    return datetime(2026, 9, 5, 23, tzinfo=timezone.utc)


def _manifest(tmp_path, text):
    path = tmp_path / "serves.toml"
    path.write_text(text, encoding="utf-8")
    return path


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
    (tmp_path / "serves-a.toml").write_text('''
[[serve]]
name = "a-slot"
runtime = "native"
''', encoding="utf-8")
    (tmp_path / "not-serves.toml").write_text("not = [valid", encoding="utf-8")
    (tmp_path / "serves-b.toml").write_text("not = [valid", encoding="utf-8")
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
    sibling.write_text('''
[[serve]]
name = "valid"
runtime = "native"
''', encoding="utf-8")
    os.utime(future, (1924992000, 1924992000))  # 2031-01-01 UTC
    snapshot = capture_manifest_workload_snapshot(
        str(future), clock=lambda: datetime(2030, 1, 1, tzinfo=timezone.utc),
    )
    assert snapshot.configuration.error.value == "future-workload-timestamp"
    assert len(snapshot.configuration.records) == 1


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
