"""Bounded, metadata-only observations of declared ``serves*.toml`` slots.

This module deliberately does not load lifecycle manifests, resolve referenced
files, or execute a declaration.  Configuration and runtime observations stay
separate so a declaration can never become a health or launch claim.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import stat
import tomllib
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Callable

from .observability.workloads import (
    DEFAULT_STALE_AFTER_SECONDS,
    MAX_FUTURE_SECONDS,
    ObservationQuality,
    ResultStatus,
    SourceAuthority,
    SourceResult,
    Truncation,
    WorkloadError,
    WorkloadErrorCode,
    WorkloadKind,
    WorkloadOutcome,
    WorkloadOwner,
    WorkloadPhase,
    WorkloadQuery,
    WorkloadRecord,
    WorkloadState,
    normalize_workload_timestamp,
    select_managed_records,
    validate_source_records,
    workload_id,
)
from .serves import DEFAULT_STACK, _STACK_RE, _stack_project

MAX_MANIFEST_BYTES = 8 * 1024 * 1024
MAX_MANIFEST_FILES = 64
MAX_DIRECTORY_ENTRIES = 4096
MAX_MANIFEST_ROWS = 256
MAX_CAPTURE_BYTES = 256 * 1024
_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", re.ASCII)
_ID = re.compile(r"[0-9a-f]{64}", re.ASCII)
_ZERO_TIME = "0001-01-01T00:00:00Z"
_INSPECT_TEMPLATE = (
    '{"id":{{json .Id}},"name":{{json .Name}},"created_at":{{json .Created}},'
    '"status":{{json .State.Status}},"running":{{json .State.Running}},'
    '"started_at":{{json .State.StartedAt}},"finished_at":{{json .State.FinishedAt}},'
    '"project":{{json (index .Config.Labels "com.docker.compose.project")}},'
    '"service":{{json (index .Config.Labels "com.docker.compose.service")}}}'
)


class ManifestRuntimeKind(str, Enum):
    DOCKER_COMPOSE = "docker-compose"
    DOCKER_GENERIC = "docker-generic"
    NATIVE = "native"


class _FutureTime(ValueError):
    pass


@dataclass(frozen=True)
class ManifestConfiguredObservation:
    config_digest: str
    runtime: ManifestRuntimeKind
    configured_at: datetime
    observed_at: datetime


@dataclass(frozen=True)
class ManifestRuntimeObservation:
    config_digest: str
    container_id: str | None
    state: WorkloadState
    created_at: datetime
    updated_at: datetime
    observed_at: datetime


@dataclass(frozen=True)
class ManifestComponentResult:
    status: ResultStatus
    observed_at: datetime | None
    records: tuple[ManifestConfiguredObservation | ManifestRuntimeObservation, ...]
    omitted: int | None
    error: WorkloadErrorCode | None


@dataclass(frozen=True)
class ManifestWorkloadSnapshot:
    configuration: ManifestComponentResult
    runtime: ManifestComponentResult


@dataclass(frozen=True)
class _Declared:
    name: str
    runtime: ManifestRuntimeKind
    container: str
    digest: str
    configured_at: datetime
    observed_at: datetime
    project: str
    service: str
    command: tuple[str, ...]
    supported: bool


def _component(status, observed_at, records=(), omitted=0, error=None):
    return ManifestComponentResult(status, observed_at, tuple(records), omitted, error)


def _now(clock: Callable[[], datetime]) -> datetime:
    value = clock()
    if type(value) is not datetime or value.tzinfo is None:
        raise ValueError
    return value.astimezone(timezone.utc)


def _mtime(info: os.stat_result) -> datetime:
    seconds, remainder = divmod(info.st_mtime_ns, 1_000_000_000)
    return datetime.fromtimestamp(seconds, tz=timezone.utc).replace(microsecond=remainder // 1_000)


def _same_file(before: os.stat_result, after: os.stat_result) -> bool:
    return (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns) == (
        after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns,
    )


def _read_regular(path: Path, budget: list[int]) -> tuple[bytes, os.stat_result]:
    if budget[0] <= 0:
        raise ValueError
    listed = os.lstat(path)
    reparse = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    attributes = getattr(listed, "st_file_attributes", 0)
    if (
        not stat.S_ISREG(listed.st_mode)
        or stat.S_ISLNK(listed.st_mode)
        or bool(reparse and attributes & reparse)
    ):
        raise ValueError
    with open(path, "rb") as handle:
        before = os.fstat(handle.fileno())
        if (
            not stat.S_ISREG(before.st_mode)
            or (listed.st_dev, listed.st_ino) != (before.st_dev, before.st_ino)
        ):
            raise ValueError
        reservation = budget[0]
        if reservation <= 0:
            raise ValueError
        budget[0] = 0
        raw = handle.read(reservation)
        # Only a completed read can safely refund known unread capacity.
        budget[0] = reservation - len(raw)
        after = os.fstat(handle.fileno())
    if not _same_file(before, after):
        raise ValueError
    return raw, before


def _paths(manifest_path: object) -> tuple[Path, ...]:
    if type(manifest_path) is not str or not manifest_path:
        raise ValueError
    explicit = Path(manifest_path)
    entries = []
    with os.scandir(explicit.parent) as scanner:
        for entry in scanner:
            entries.append(entry)
            if len(entries) > MAX_DIRECTORY_ENTRIES:
                raise ValueError
    selected = {explicit}
    for entry in entries:
        if entry.name.startswith("serves") and entry.name.endswith(".toml"):
            try:
                info = os.lstat(entry.path)
                reparse = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
                if stat.S_ISREG(info.st_mode) and not stat.S_ISLNK(info.st_mode) and not (
                    reparse and getattr(info, "st_file_attributes", 0) & reparse
                ):
                    selected.add(Path(entry.path))
            except OSError:
                continue
    if len(selected) > MAX_MANIFEST_FILES:
        raise ValueError
    return tuple(sorted(selected, key=lambda value: str(value)))


def _kind(value: object) -> ManifestRuntimeKind:
    if type(value) is not str:
        raise ValueError
    normalized = value.strip().lower()
    if normalized == "native":
        return ManifestRuntimeKind.NATIVE
    if normalized == "docker":
        return ManifestRuntimeKind.DOCKER_GENERIC
    raise ValueError


def _digest(runtime: ManifestRuntimeKind, name: str, container: str) -> str:
    declared_runtime = "docker" if runtime in {
        ManifestRuntimeKind.DOCKER_COMPOSE, ManifestRuntimeKind.DOCKER_GENERIC,
    } else runtime.value
    raw = json.dumps(
        ["manifest-config/v1", declared_runtime, name, container],
        ensure_ascii=True, separators=(",", ":"),
    ).encode("ascii")
    return hashlib.sha256(raw).hexdigest()


def _safe_token(value: object) -> str:
    if type(value) is not str or not _IDENTIFIER.fullmatch(value):
        raise ValueError
    return value


def _compose_up(value: object, stack: object) -> tuple[bool, str, str, tuple[str, ...]]:
    """Return whether an explicit command is one supported Compose owner."""
    if value is None or value == "":
        return False, _stack_project(DEFAULT_STACK if stack is None else _safe_stack(stack)), "", ()
    if type(value) is not str or len(value.encode("utf-8")) > 8192:
        return False, "", "", ()
    try:
        tokens = shlex.split(value, posix=True)
    except ValueError:
        return False, "", "", ()
    if not 1 <= len(tokens) <= 128:
        return False, "", "", ()
    stack_name = DEFAULT_STACK if stack is None else _safe_stack(stack)
    project = _stack_project(stack_name)
    if tokens[:2] == ["docker", "compose"]:
        index = 2
    elif tokens[:1] == ["docker-compose"]:
        index = 1
    else:
        return False, project, "", tuple(tokens)
    files = 0
    explicit_project = None
    while index < len(tokens) and tokens[index] != "up":
        token = tokens[index]
        if token in {"-f", "--file", "--profile", "-p", "--project-name"}:
            if index + 1 >= len(tokens):
                return False, project, "", tuple(tokens)
            candidate = tokens[index + 1]
            if (
                not candidate
                or candidate.startswith("-")
                or len(candidate.encode("utf-8")) > 1024
                or any(ord(char) < 32 or ord(char) == 127 for char in candidate)
            ):
                return False, project, "", tuple(tokens)
            if token in {"-f", "--file"}:
                files += 1
            elif token in {"-p", "--project-name"}:
                if explicit_project is not None or not _IDENTIFIER.fullmatch(candidate):
                    return False, project, "", tuple(tokens)
                explicit_project = candidate
            elif not _IDENTIFIER.fullmatch(candidate):
                return False, project, "", tuple(tokens)
            index += 2
        elif token.startswith(("--file=", "--profile=", "--project-name=")):
            option, candidate = token.split("=", 1)
            if (
                not candidate
                or candidate.startswith("-")
                or len(candidate.encode("utf-8")) > 1024
                or any(ord(char) < 32 or ord(char) == 127 for char in candidate)
            ):
                return False, project, "", tuple(tokens)
            if option == "--file":
                files += 1
            elif option == "--profile":
                if _IDENTIFIER.fullmatch(candidate) is None:
                    return False, project, "", tuple(tokens)
            else:
                if explicit_project is not None or _IDENTIFIER.fullmatch(candidate) is None:
                    return False, project, "", tuple(tokens)
                explicit_project = candidate
            index += 1
        else:
            return False, project, "", tuple(tokens)
    if files < 1 or index >= len(tokens) or explicit_project not in {None, project}:
        return False, project, "", tuple(tokens)
    rest = tokens[index + 1:]
    if rest[:1] in (["-d"], ["--detach"]):
        rest = rest[1:]
    if len(rest) != 1 or not _IDENTIFIER.fullmatch(rest[0]):
        return False, project, "", tuple(tokens)
    return True, project, rest[0], tuple(tokens)


def _safe_stack(value: object) -> str:
    if type(value) is not str or len(value) > 64 or _STACK_RE.fullmatch(value) is None:
        raise ValueError
    return value


def _declarations(paths: tuple[Path, ...], clock) -> tuple[list[_Declared], ManifestComponentResult]:
    records = []
    declared: list[_Declared] = []
    budget = [MAX_MANIFEST_BYTES + 1]
    invalid = False
    future_config = False
    declaration_count = 0
    observed: datetime | None = None
    for path in paths:
        if budget[0] <= 0:
            invalid = True
            break
        try:
            raw, info = _read_regular(path, budget)
            # The sentinel is aggregate, including failed prior file reads.
            if budget[0] == 0:
                raise ValueError
            observed = _now(clock)
            configured = _mtime(info)
            document = tomllib.loads(raw.decode("utf-8"))
            rows = document.get("serve", []) if type(document) is dict else None
            if type(rows) is not list:
                raise ValueError
            for row in rows:
                declaration_count += 1
                if declaration_count > MAX_MANIFEST_ROWS:
                    invalid = True
                    break
                if type(row) is not dict:
                    invalid = True
                    continue
                try:
                    name = _safe_token(row.get("name"))
                    runtime = _kind(row.get("runtime"))
                    container = "" if runtime is ManifestRuntimeKind.NATIVE else _safe_token(row.get("container"))
                    if runtime is ManifestRuntimeKind.NATIVE and "container" in row:
                        raise ValueError
                    supported, project, service, command = _compose_up(row.get("up"), row.get("stack"))
                    if runtime is not ManifestRuntimeKind.DOCKER_GENERIC:
                        supported = False
                    if supported:
                        runtime = ManifestRuntimeKind.DOCKER_COMPOSE
                    digest = _digest(runtime, name, container)
                    if configured - observed > timedelta(seconds=MAX_FUTURE_SECONDS):
                        future_config = True
                        continue
                    declared.append(_Declared(name, runtime, container, digest, configured, observed, project, service, command, supported))
                    records.append(ManifestConfiguredObservation(digest, runtime, configured, observed))
                except Exception:
                    invalid = True
        except Exception:
            invalid = True
    if observed is None:
        return [], _component(ResultStatus.UNAVAILABLE, None, (), None, WorkloadErrorCode.UNAVAILABLE)
    # Exact declared identity mirrors are one observation.  A different stack
    # or ownership mode for the same identity is ambiguous evidence, so keep
    # neither rather than selecting a lifecycle winner.
    by_name: dict[str, str] = {}
    by_container: dict[str, str] = {}
    conflicted: set[_Declared] = set()
    for item in declared:
        if item.runtime is ManifestRuntimeKind.NATIVE:
            continue
        if item.name in by_name and by_name[item.name] != item.container:
            conflicted.update(candidate for candidate in declared if candidate.name == item.name)
        if item.container in by_container and by_container[item.container] != item.name:
            conflicted.update(candidate for candidate in declared if candidate.container == item.container)
        by_name[item.name] = item.container
        by_container[item.container] = item.name
    if conflicted:
        invalid = True
        declared = [item for item in declared if item not in conflicted]
    grouped: dict[tuple[str, str, str], list[_Declared]] = {}
    for item in declared:
        runtime = "docker" if item.runtime in {ManifestRuntimeKind.DOCKER_COMPOSE, ManifestRuntimeKind.DOCKER_GENERIC} else item.runtime.value
        grouped.setdefault((runtime, item.name, item.container), []).append(item)
    collapsed = []
    for group in grouped.values():
        first = group[0]
        nonempty = [item for item in group if item.command]
        supported = [item for item in group if item.supported]
        if (
            len({(item.project, item.command) for item in nonempty}) > 1
            or len({item.project for item in group}) != 1
            or len(supported) > 1 and len({item.command for item in supported}) != 1
        ):
            invalid = True
            continue
        collapsed.append(supported[0] if supported else first)
    declared = collapsed
    records = [
        ManifestConfiguredObservation(item.digest, item.runtime, item.configured_at, item.observed_at)
        for item in declared
    ]
    status = ResultStatus.PARTIAL if invalid or future_config else ResultStatus.COMPLETE
    return declared, _component(status, observed, records, None if status is ResultStatus.PARTIAL else 0, WorkloadErrorCode.FUTURE if future_config else WorkloadErrorCode.INVALID if invalid else None)


def _time(value: object, *, optional=False) -> datetime | None:
    if optional and (value is None or value == _ZERO_TIME):
        return None
    if type(value) is not str or not value.endswith("Z"):
        raise ValueError
    match = re.fullmatch(r"(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)(?:\.(\d{1,9}))?Z", value)
    if match is None:
        raise ValueError
    base = datetime.fromisoformat(match.group(1)).replace(tzinfo=timezone.utc)
    return base.replace(microsecond=int((match.group(2) or "").ljust(9, "0")[:6]))


def _runtime_row(raw: object, owners: dict[str, _Declared], observed: datetime) -> ManifestRuntimeObservation:
    keys = {"id", "name", "created_at", "status", "running", "started_at", "finished_at", "project", "service"}
    if type(raw) is not dict or set(raw) != keys:
        raise ValueError
    name = raw["name"]
    if type(name) is not str or name.removeprefix("/") not in owners:
        raise ValueError
    owner = owners[name.removeprefix("/")]
    if raw["project"] != owner.project or raw["service"] != owner.service or not _ID.fullmatch(raw["id"] if type(raw["id"]) is str else ""):
        raise ValueError
    created = _time(raw["created_at"])
    started = _time(raw["started_at"], optional=True)
    finished = _time(raw["finished_at"], optional=True)
    status, running = raw["status"], raw["running"]
    if type(status) is not str or type(running) is not bool:
        raise ValueError
    if status == "running" and running:
        if started is None:
            raise ValueError
        state, updated = WorkloadState.RUNNING, started
    elif status in {"created", "exited", "dead"} and not running:
        state, updated = WorkloadState.ABSENT, max(created, started or created, finished or created)
    elif status in {"paused", "restarting", "removing"} and not running:
        state, updated = WorkloadState.UNSUPPORTED, created
    elif status in {"running", "created", "exited", "dead"}:
        raise ValueError
    else:
        state, updated = WorkloadState.UNSUPPORTED, created
    if any(
        value is not None and value - observed > timedelta(seconds=MAX_FUTURE_SECONDS)
        for value in (created, started, finished)
    ):
        raise _FutureTime
    if (
        created > updated
        or (started is not None and started < created)
        or (finished is not None and finished < created)
    ):
        raise ValueError
    return ManifestRuntimeObservation(owner.digest, raw["id"], state, created, updated, observed)


def _compose_owners(declared: list[_Declared]) -> tuple[dict[str, _Declared], bool]:
    """Keep only unambiguous supported declarations; do not pick a winner."""
    by_name: dict[str, _Declared] = {}
    by_container: dict[str, _Declared] = {}
    conflicted: set[_Declared] = set()
    for item in declared:
        if not item.supported:
            continue
        name_owner = by_name.get(item.name)
        container_owner = by_container.get(item.container)
        if name_owner is not None and name_owner.container != item.container:
            conflicted.update((name_owner, item))
        if container_owner is not None and container_owner.name != item.name:
            conflicted.update((container_owner, item))
        by_name.setdefault(item.name, item)
        by_container.setdefault(item.container, item)
    return (
        {container: item for container, item in by_container.items() if item not in conflicted},
        bool(conflicted),
    )


def capture_manifest_workload_snapshot(manifest_path, *, clock, _capture=None) -> ManifestWorkloadSnapshot:
    """Capture only bounded declared-slot and declared-container observations."""
    from .controller_diagnostics import ChildCapture, _capture_fixed_child

    try:
        declared, configuration = _declarations(_paths(manifest_path), clock)
    except Exception:
        configuration = _component(ResultStatus.UNAVAILABLE, None, (), None, WorkloadErrorCode.UNAVAILABLE)
        declared = []
    owners, owner_conflict = _compose_owners(declared)
    unsupported = [item for item in declared if item.runtime is ManifestRuntimeKind.NATIVE or not item.supported]
    runtime_records = [
        ManifestRuntimeObservation(item.digest, None, WorkloadState.UNSUPPORTED, item.configured_at, item.configured_at, item.observed_at)
        for item in unsupported
    ]

    def _failed_runtime(observed_at: datetime | None) -> ManifestComponentResult:
        if runtime_records:
            return _component(
                ResultStatus.PARTIAL,
                observed_at if observed_at is not None else configuration.observed_at,
                runtime_records, None, WorkloadErrorCode.UNAVAILABLE,
            )
        return _component(ResultStatus.UNAVAILABLE, observed_at, (), None, WorkloadErrorCode.UNAVAILABLE)

    if not owners:
        observed = configuration.observed_at
        if configuration.status is ResultStatus.UNAVAILABLE:
            return ManifestWorkloadSnapshot(
                configuration,
                _failed_runtime(observed),
            )
        return ManifestWorkloadSnapshot(
            configuration,
            _component(
                ResultStatus.PARTIAL if owner_conflict else ResultStatus.COMPLETE,
                observed, runtime_records, None if owner_conflict else 0,
                WorkloadErrorCode.INVALID if owner_conflict else None,
            ),
        )
    argv = ("docker", "inspect", "--type", "container", "--format", _INSPECT_TEMPLATE, *sorted(owners))
    try:
        capture = (_capture(argv) if _capture is not None else _capture_fixed_child(argv, merged=False, retain_stdout_on_error=True))
        if (
            type(capture) is not ChildCapture
            or type(capture.state) is not str
            or type(capture.stdout) is not bytes
            or type(capture.stderr) is not bytes
            or type(capture.truncated) is not bool
            or len(capture.stdout) > MAX_CAPTURE_BYTES
            or len(capture.stderr) > MAX_CAPTURE_BYTES
        ):
            raise ValueError
        observed = _now(clock)
        if capture.truncated or capture.state not in {"ok", "unavailable"}:
            raise ValueError
        if capture.stdout and not capture.stdout.endswith(b"\n"):
            raise ValueError
        rows = []
        seen = set()
        responded = set()
        invalid = False
        future = False
        for row_index, line in enumerate(capture.stdout.splitlines()):
            if row_index >= MAX_MANIFEST_ROWS:
                invalid = True
                break
            try:
                row = json.loads(line, object_pairs_hook=lambda pairs: _no_duplicates(pairs))
                record = _runtime_row(row, owners, observed)
                responded.add(row["name"].removeprefix("/"))
                if record.container_id in seen:
                    raise ValueError
                seen.add(record.container_id)
                rows.append(record)
            except _FutureTime:
                future = True
            except Exception:
                invalid = True
        missing = responded != set(owners)
        if capture.state != "ok" and not rows:
            return ManifestWorkloadSnapshot(
                configuration,
                _failed_runtime(observed),
            )
        status = ResultStatus.PARTIAL if invalid or future or missing or owner_conflict or capture.state != "ok" else ResultStatus.COMPLETE
        error = WorkloadErrorCode.UNAVAILABLE if capture.state != "ok" else WorkloadErrorCode.FUTURE if future else WorkloadErrorCode.INVALID if invalid or missing or owner_conflict else None
        return ManifestWorkloadSnapshot(configuration, _component(status, observed, runtime_records + rows, None if status is ResultStatus.PARTIAL else 0, error))
    except Exception:
        return ManifestWorkloadSnapshot(configuration, _failed_runtime(None))


def _no_duplicates(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError
        result[key] = value
    return result


_PROJECTION_ERRORS = (
    WorkloadErrorCode.INVALID, WorkloadErrorCode.FUTURE, WorkloadErrorCode.UNAVAILABLE,
)


def _projection_error(exc: Exception) -> WorkloadErrorCode:
    return exc.code if isinstance(exc, WorkloadError) and exc.code in _PROJECTION_ERRORS else WorkloadErrorCode.INVALID


def _projection_component(value: object, now: datetime) -> ManifestComponentResult:
    """Validate component metadata without discarding independently valid peers."""
    if type(value) is not ManifestComponentResult:
        raise ValueError
    if (
        type(value.status) is not ResultStatus
        or type(value.records) is not tuple
        or len(value.records) > MAX_MANIFEST_ROWS
        or value.omitted is not None and (type(value.omitted) is not int or not 0 <= value.omitted <= 1_000_000_000)
        or value.error is not None and (type(value.error) is not WorkloadErrorCode or value.error not in _PROJECTION_ERRORS)
    ):
        raise ValueError
    if value.observed_at is not None:
        if type(value.observed_at) is not datetime:
            raise ValueError
        observed = normalize_workload_timestamp(value.observed_at)
        if observed - now > timedelta(seconds=MAX_FUTURE_SECONDS):
            raise WorkloadError(WorkloadErrorCode.FUTURE, "manifest observation is in the future")
    elif value.status is not ResultStatus.UNAVAILABLE:
        raise ValueError
    if value.status is ResultStatus.COMPLETE and (value.error is not None or value.omitted != 0):
        raise ValueError
    if value.status is ResultStatus.PARTIAL and value.error is None and value.omitted == 0:
        raise ValueError
    if value.status is ResultStatus.UNAVAILABLE and (value.records or value.error is None):
        raise ValueError
    return value


def _project_observation(observation, *, configured: bool, host: str, now: datetime) -> WorkloadRecord:
    expected = ManifestConfiguredObservation if configured else ManifestRuntimeObservation
    if type(observation) is not expected:
        raise ValueError
    digest = observation.config_digest
    if type(digest) is not str or _ID.fullmatch(digest) is None:
        raise ValueError
    if configured:
        if type(observation.runtime) is not ManifestRuntimeKind:
            raise ValueError
        state = WorkloadState.CONFIGURED
        native_id = "manifest-config:" + digest
        created = updated = observation.configured_at
    else:
        state = observation.state
        if type(state) is not WorkloadState or state not in {WorkloadState.RUNNING, WorkloadState.ABSENT, WorkloadState.UNSUPPORTED}:
            raise ValueError
        container = observation.container_id
        if container is None:
            if state is not WorkloadState.UNSUPPORTED:
                raise ValueError
            native_id = "manifest-config:" + digest
        else:
            if type(container) is not str or _ID.fullmatch(container) is None:
                raise ValueError
            native_id = "manifest-container:" + container
        created, updated = observation.created_at, observation.updated_at
    if any(type(value) is not datetime for value in (created, updated, observation.observed_at)):
        raise ValueError
    source = normalize_workload_timestamp(observation.observed_at)
    # Mirror the recipe state/quality contract, not its native identity namespace.
    phase, quality = {
        WorkloadState.CONFIGURED: (WorkloadPhase.CONFIGURED, ObservationQuality.CONFIGURED),
        WorkloadState.RUNNING: (WorkloadPhase.RUNNING, ObservationQuality.OBSERVED_RUNNING),
        WorkloadState.ABSENT: (WorkloadPhase.ABSENT, ObservationQuality.ABSENT),
        WorkloadState.UNSUPPORTED: (WorkloadPhase.UNSUPPORTED, ObservationQuality.INSPECTION_ERROR),
    }[state]
    if state is not WorkloadState.UNSUPPORTED and now - source > timedelta(seconds=DEFAULT_STALE_AFTER_SECONDS):
        quality = ObservationQuality.STALE
    record = WorkloadRecord(
        id=workload_id(host, WorkloadKind.RECIPE_SERVE, WorkloadOwner.MANIFEST, native_id),
        kind=WorkloadKind.RECIPE_SERVE, owner=WorkloadOwner.MANIFEST, host=host,
        state=state, phase=phase,
        outcome=WorkloadOutcome.UNKNOWN if state is WorkloadState.UNSUPPORTED else None,
        created_at=created, updated_at=updated, source_timestamp=source,
        source_authority=SourceAuthority.MANAGED_STATUS, observation_quality=quality,
    )
    # Check source-relative lifecycle skew as well as collection-relative skew.
    for collected in (source, now):
        validate_source_records((record,), owner=WorkloadOwner.MANIFEST, host=host, collection_timestamp=collected)
    return record


def list_manifest_workloads(
    manifest_path, host: str, query: WorkloadQuery, now: datetime,
    *, snapshot_reader=capture_manifest_workload_snapshot,
) -> SourceResult:
    """Project one bounded snapshot; never reopen config or inspect a container."""
    now = normalize_workload_timestamp(now)
    validate_source_records((), owner=WorkloadOwner.MANIFEST, host=host, collection_timestamp=now)
    if type(query) is not WorkloadQuery:
        raise ValueError("manifest workload query is invalid")
    # Revalidate the frozen input rather than trusting a tampered query instance.
    query = WorkloadQuery(query.owner, query.kind, query.state, query.host, query.active_only, query.recent_seconds, query.limit)
    try:
        snapshot = snapshot_reader(manifest_path, clock=lambda: datetime.now(timezone.utc))
    except Exception:
        return SourceResult(WorkloadOwner.MANIFEST, ResultStatus.UNAVAILABLE, now, (), Truncation(0, None), WorkloadErrorCode.UNAVAILABLE)
    if type(snapshot) is not ManifestWorkloadSnapshot:
        return SourceResult(WorkloadOwner.MANIFEST, ResultStatus.UNAVAILABLE, now, (), Truncation(0, None), WorkloadErrorCode.INVALID)
    try:
        components = ((True, snapshot.configuration), (False, snapshot.runtime))
    except AttributeError:
        return SourceResult(WorkloadOwner.MANIFEST, ResultStatus.UNAVAILABLE, now, (), Truncation(0, None), WorkloadErrorCode.INVALID)

    errors = set()
    incomplete = False
    usable_components = 0
    configured_records = {}
    runtime_records = {}
    observed_digests = set()
    for configured, raw_component in components:
        try:
            component = _projection_component(raw_component, now)
        except Exception as exc:
            errors.add(_projection_error(exc))
            incomplete = True
            continue
        if component.status is not ResultStatus.COMPLETE:
            incomplete = True
        if component.error is not None:
            errors.add(component.error)
        if component.status is not ResultStatus.UNAVAILABLE:
            usable_components += 1
        for observation in component.records:
            try:
                record = _project_observation(observation, configured=configured, host=host, now=now)
                records = configured_records if configured else runtime_records
                if record.id in records:
                    raise ValueError
                records[record.id] = record
                # Invalid/future/duplicate runtime peers never suppress a valid declaration.
                if not configured:
                    observed_digests.add(observation.config_digest)
            except Exception as exc:
                errors.add(_projection_error(exc))
                incomplete = True

    suppressed = {
        workload_id(host, WorkloadKind.RECIPE_SERVE, WorkloadOwner.MANIFEST, "manifest-config:" + digest)
        for digest in observed_digests
    }
    candidates = tuple(runtime_records.values()) + tuple(
        record for identity, record in configured_records.items() if identity not in suppressed
    )
    selected, truncation = select_managed_records(candidates, query, now=now)
    selected = validate_source_records(selected, owner=WorkloadOwner.MANIFEST, host=host, collection_timestamp=now)
    if incomplete:
        status = ResultStatus.PARTIAL if usable_components or candidates else ResultStatus.UNAVAILABLE
        error = next((code for code in _PROJECTION_ERRORS if code in errors), WorkloadErrorCode.INVALID)
        return SourceResult(WorkloadOwner.MANIFEST, status, now, selected, Truncation(len(selected), None), error)
    status = ResultStatus.PARTIAL if truncation.omitted else ResultStatus.COMPLETE
    return SourceResult(WorkloadOwner.MANIFEST, status, now, selected, truncation)
