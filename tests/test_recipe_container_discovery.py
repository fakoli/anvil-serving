from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from anvil_serving import cli, models, reservations, serve_recipes, serves
from anvil_serving.control_plane.mcp.tools import models as models_tools
from anvil_serving.controller_diagnostics import ChildCapture
from anvil_serving.observability.workloads import (
    ResultStatus,
    WorkloadErrorCode,
    WorkloadState,
)


def _completed(argv, *, stdout="", stderr="", returncode=0):
    return subprocess.CompletedProcess(argv, returncode, stdout, stderr)


def _container_id(container: str) -> str:
    return (container.encode("utf-8").hex() * 64)[:64]


def _row(
    container: str,
    *,
    container_id: str | None = None,
    model: str = "org/model",
    state: str = "running",
    running: bool = True,
    labels: dict | None = None,
) -> dict:
    resolved_labels = {
        serve_recipes.RECIPE_MANAGED_LABEL: serve_recipes.RECIPE_MANAGED_VALUE,
        serve_recipes.RECIPE_MODEL_LABEL: model,
        serve_recipes.RECIPE_REVISION_LABEL: "a" * 40,
        serve_recipes.RECIPE_DIGEST_LABEL: "b" * 64,
        serve_recipes.RECIPE_REGISTRY_DIGEST_LABEL: "c" * 64,
        serve_recipes.RECIPE_NATIVE_KV_OFFLOAD_LABEL: "false",
    }
    if labels:
        resolved_labels.update(labels)
    return {
        "Id": container_id or _container_id(container),
        "Name": "/" + container,
        "Image": "sha256:" + "d" * 64,
        "Args": [
            "/weights/model",
            "--served-model-name",
            "served-model",
            "--api-key",
            "must-not-leak",
        ],
        "Config": {
            "Labels": resolved_labels,
            "Env": ["HF_TOKEN=must-not-leak"],
            "Image": "registry.invalid/private:tag",
        },
        "HostConfig": {
            "PortBindings": {
                "8000/tcp": [{"HostIp": "127.0.0.1", "HostPort": "39077"}],
            },
            "DeviceRequests": [
                {"DeviceIDs": ["GPU-a", "GPU-b"], "Count": 0},
            ],
        },
        "State": {
            "Status": state,
            "Running": running,
            "Health": {"Status": "healthy" if running else "unhealthy"},
        },
    }


class _DiscoveryRun:
    def __init__(self, rows: list[dict]) -> None:
        self.rows = rows
        self.calls: list[list[str]] = []

    def __call__(self, argv, **_kwargs):
        self.calls.append(list(argv))
        if argv[1] == "ps":
            ids = [format(index + 1, "012x") for index in range(len(self.rows))]
            return _completed(argv, stdout="\n".join(ids) + ("\n" if ids else ""))
        assert argv[1] == "inspect"
        return _completed(argv, stdout=json.dumps(self.rows))


def test_recipe_container_discovery_with_no_containers_is_bounded() -> None:
    run = _DiscoveryRun([])

    inventory = serve_recipes.discover_recipe_containers(_run=run)

    assert inventory == {
        "schema": serve_recipes.RECIPE_CONTAINER_INVENTORY_SCHEMA,
        "containers": [],
    }
    assert len(run.calls) == 1
    assert run.calls[0][1:3] == ["ps", "-a"]


def test_recipe_container_discovery_returns_only_safe_typed_fields() -> None:
    inventory = serve_recipes.discover_recipe_containers(
        _run=_DiscoveryRun([_row("candidate")])
    )

    assert inventory["containers"] == [
        {
            "container": "candidate",
            "container_id": _container_id("candidate"),
            "model": "org/model",
            "revision": "a" * 40,
            "recipe_digest": "b" * 64,
            "registry_digest": "c" * 64,
            "image_digest": "sha256:" + "d" * 64,
            "served_identity": "served-model",
            "bound_port": 39077,
            "bound_ports": [39077],
            "gpu_selection": ["GPU-a", "GPU-b"],
            "state": "running",
            "running": True,
            "health": "healthy",
            "native_kv_offload": False,
        }
    ]
    encoded = json.dumps(inventory)
    assert "must-not-leak" not in encoded
    assert "HF_TOKEN" not in encoded
    assert "registry.invalid" not in encoded
    assert "127.0.0.1" not in encoded


def test_recipe_container_discovery_includes_exited_owned_container() -> None:
    inventory = serve_recipes.discover_recipe_containers(
        _run=_DiscoveryRun([_row("stopped", state="exited", running=False)])
    )

    assert inventory["containers"][0]["state"] == "exited"
    assert inventory["containers"][0]["running"] is False


def test_recipe_container_discovery_skips_missing_malformed_and_non_anvil_labels() -> None:
    missing_id = _row("missing-id")
    del missing_id["Id"]
    missing_model = _row("missing")
    del missing_model["Config"]["Labels"][serve_recipes.RECIPE_MODEL_LABEL]
    malformed_digest = _row(
        "malformed",
        labels={serve_recipes.RECIPE_DIGEST_LABEL: "not-a-digest"},
    )
    non_anvil = _row(
        "foreign",
        labels={serve_recipes.RECIPE_MANAGED_LABEL: "someone-else"},
    )

    inventory = serve_recipes.discover_recipe_containers(
        _run=_DiscoveryRun([missing_id, missing_model, malformed_digest, non_anvil])
    )

    assert inventory["containers"] == []


def test_recipe_container_selection_refuses_two_containers_for_same_model() -> None:
    inventory = serve_recipes.discover_recipe_containers(
        _run=_DiscoveryRun([_row("one"), _row("two")])
    )

    with pytest.raises(serve_recipes.RecipeError, match="ambiguous"):
        serve_recipes.select_recipe_container(inventory, model="org/model")

    selected = serve_recipes.select_recipe_container(
        inventory,
        model="org/model",
        container="two",
    )
    assert selected["container"] == "two"


def test_recipe_load_labels_canonical_recipe_and_registry_digests() -> None:
    recipe = {
        "model": "org/model",
        "serve": {"image": "image@sha256:" + "a" * 64},
    }
    argv = serve_recipes.docker_run_argv(
        recipe,
        container="candidate",
        registry_digest_value="f" * 64,
    )
    labels = [argv[index + 1] for index, token in enumerate(argv[:-1]) if token == "--label"]

    assert "%s=%s" % (
        serve_recipes.RECIPE_DIGEST_LABEL,
        serve_recipes.recipe_digest(recipe),
    ) in labels
    assert "%s=%s" % (
        serve_recipes.RECIPE_REGISTRY_DIGEST_LABEL,
        "f" * 64,
    ) in labels
    assert "%s=false" % serve_recipes.RECIPE_NATIVE_KV_OFFLOAD_LABEL in labels


def test_models_recipes_running_json_uses_typed_inventory(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    inventory = {
        "schema": serve_recipes.RECIPE_CONTAINER_INVENTORY_SCHEMA,
        "containers": [serve_recipes._recipe_container_record(_row("candidate"))],
    }
    monkeypatch.setattr(serve_recipes, "discover_recipe_containers", lambda: inventory)

    assert models._recipe_main(["running", "--json"]) == 0

    assert json.loads(capsys.readouterr().out) == inventory


def test_canonical_recipe_running_json_envelope_keeps_typed_inventory(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    inventory = {
        "schema": serve_recipes.RECIPE_CONTAINER_INVENTORY_SCHEMA,
        "containers": [serve_recipes._recipe_container_record(_row("candidate"))],
    }
    monkeypatch.setattr(serve_recipes, "discover_recipe_containers", lambda: inventory)

    assert cli.main(["models", "recipes", "running", "--json"]) == 0

    envelope = json.loads(capsys.readouterr().out)
    assert envelope["ok"] is True
    assert envelope["data"] == inventory


def test_recipe_status_survives_missing_origin_registry(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    identity = serve_recipes._recipe_container_record(_row("candidate"))
    inventory = {
        "schema": serve_recipes.RECIPE_CONTAINER_INVENTORY_SCHEMA,
        "containers": [identity],
    }
    missing_registry = tmp_path / "removed-registry.toml"
    monkeypatch.setattr(serve_recipes, "discover_recipe_containers", lambda: inventory)

    assert models._recipe_main([
        "status",
        "org/model",
        "--registry",
        str(missing_registry),
    ]) == 0

    assert json.loads(capsys.readouterr().out) == identity


def test_discovered_recipe_unload_removes_revalidated_immutable_id(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    identity = serve_recipes._recipe_container_record(_row("candidate"))
    assert identity is not None
    monkeypatch.setattr(
        serve_recipes,
        "discover_recipe_containers",
        lambda **_kwargs: {
            "schema": serve_recipes.RECIPE_CONTAINER_INVENTORY_SCHEMA,
            "containers": [identity],
        },
    )
    calls = []

    assert models._discovered_recipe_container_unload(
        identity,
        confirm=True,
        _run=lambda argv, **_kwargs: calls.append(argv) or _completed(argv),
    ) == 0

    assert calls == [["docker", "rm", "-f", identity["container_id"]]]
    assert "candidate" in capsys.readouterr().out


def test_discovered_recipe_logs_read_revalidated_immutable_id(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    identity = serve_recipes._recipe_container_record(_row("candidate"))
    assert identity is not None
    monkeypatch.setattr(
        serve_recipes,
        "discover_recipe_containers",
        lambda **_kwargs: {
            "schema": serve_recipes.RECIPE_CONTAINER_INVENTORY_SCHEMA,
            "containers": [identity],
        },
    )
    calls = []

    def run(argv, **_kwargs):
        calls.append(argv)
        return _completed(argv, stdout="candidate ready\n")

    assert models._discovered_recipe_container_logs(
        identity,
        tail=17,
        _run=run,
    ) == 0

    assert calls == [
        ["docker", "logs", "--tail", "17", identity["container_id"]]
    ]
    assert capsys.readouterr().out == "candidate ready\n"


def test_discovered_recipe_unload_refuses_same_name_replacement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    selected = serve_recipes._recipe_container_record(
        _row("candidate", container_id="a" * 64)
    )
    replacement = serve_recipes._recipe_container_record(
        _row("candidate", container_id="f" * 64)
    )
    assert selected is not None and replacement is not None
    monkeypatch.setattr(
        serve_recipes,
        "discover_recipe_containers",
        lambda **_kwargs: {
            "schema": serve_recipes.RECIPE_CONTAINER_INVENTORY_SCHEMA,
            "containers": [replacement],
        },
    )
    calls = []

    with pytest.raises(serve_recipes.RecipeError, match="identity changed"):
        models._discovered_recipe_container_unload(
            selected,
            confirm=True,
            _run=lambda argv, **_kwargs: calls.append(argv) or _completed(argv),
        )

    assert calls == []


def test_recipe_containers_mcp_returns_the_same_typed_inventory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inventory = {
        "schema": serve_recipes.RECIPE_CONTAINER_INVENTORY_SCHEMA,
        "containers": [],
    }
    monkeypatch.setattr(serve_recipes, "discover_recipe_containers", lambda: inventory)

    result = models_tools.tool_recipe_containers({})

    assert result["ok"] is True
    assert result["data"]["inventory"] == inventory


def _recipe_snapshot_capture(rows: list[str], calls: list[tuple[str, ...]]):
    def capture(argv):
        calls.append(tuple(argv))
        if tuple(argv) == serve_recipes._RECIPE_LIST_ARGV:
            return ChildCapture("ok", ("\n".join(rows) + "\n").encode(), b"", False)
        return ChildCapture("ok", b"", b"", False)

    return capture


def _write_snapshot_registry(path, text='[[recipe]]\nmodel = "org/model"\n') -> None:
    path.write_text(text, encoding="utf-8")
    timestamp_ns = int(datetime(2026, 9, 5, 12, tzinfo=timezone.utc).timestamp() * 1_000_000_000)
    os.utime(path, ns=(timestamp_ns, timestamp_ns))


def test_recipe_workload_snapshot_uses_fixed_metadata_only_docker_capture(tmp_path) -> None:
    registry = tmp_path / "recipes.toml"
    _write_snapshot_registry(registry)
    container_id = "a" * 64
    capture_calls: list[tuple[str, ...]] = []
    capture = _recipe_snapshot_capture([container_id], capture_calls)
    row = {
        "id": container_id,
        "managed_by": serve_recipes.RECIPE_MANAGED_VALUE,
        "recipe_digest": "b" * 64,
        "created_at": "2026-09-05T12:00:00.123456789Z",
        "status": "running",
        "running": True,
        "started_at": "2026-09-05T12:00:01.987654321Z",
        "finished_at": "0001-01-01T00:00:00Z",
    }

    def captured(argv):
        result = capture(argv)
        if tuple(argv) != serve_recipes._RECIPE_LIST_ARGV:
            return ChildCapture("ok", (json.dumps(row) + "\n").encode(), b"", False)
        return result

    snapshot = serve_recipes.capture_recipe_workload_snapshot(
        registry,
        clock=lambda: datetime(2026, 9, 5, 12, 0, 2, tzinfo=timezone.utc),
        _capture=captured,
    )

    assert snapshot.configuration.status is ResultStatus.COMPLETE
    assert len(snapshot.configuration.records) == 1
    assert snapshot.runtime.status is ResultStatus.COMPLETE
    assert snapshot.runtime.records[0].state is WorkloadState.RUNNING
    assert snapshot.runtime.records[0].updated_at.microsecond == 987654
    assert capture_calls == [
        serve_recipes._RECIPE_LIST_ARGV,
        (
            "docker",
            "inspect",
            "--type",
            "container",
            "--format",
            serve_recipes._RECIPE_INSPECT_TEMPLATE,
            container_id,
        ),
    ]
    assert "org/model" not in repr(snapshot)


def test_recipe_workload_snapshot_retains_valid_peer_after_invalid_metadata(tmp_path) -> None:
    registry = tmp_path / "recipes.toml"
    _write_snapshot_registry(registry)
    valid_id, invalid_id = "a" * 64, "b" * 64
    calls: list[tuple[str, ...]] = []
    capture = _recipe_snapshot_capture([valid_id, invalid_id], calls)
    rows = [
        {
            "id": valid_id,
            "managed_by": serve_recipes.RECIPE_MANAGED_VALUE,
            "recipe_digest": None,
            "created_at": "2026-09-05T12:00:00Z",
            "status": "exited",
            "running": False,
            "started_at": "2026-09-05T12:00:02Z",
            "finished_at": "2026-09-05T12:00:03Z",
        },
        {
            "id": invalid_id,
            "managed_by": serve_recipes.RECIPE_MANAGED_VALUE,
            "recipe_digest": "not-a-digest",
            "created_at": "2026-09-05T12:00:00Z",
            "status": "running",
            "running": "not-bool",
            "started_at": "2026-09-05T12:00:01Z",
            "finished_at": "0001-01-01T00:00:00Z",
        },
    ]

    def captured(argv):
        listed = capture(argv)
        if tuple(argv) == serve_recipes._RECIPE_LIST_ARGV:
            return listed
        return ChildCapture(
            "ok", ("\n".join(json.dumps(row) for row in rows) + "\n").encode(), b"", False
        )

    snapshot = serve_recipes.capture_recipe_workload_snapshot(
        registry,
        clock=lambda: datetime(2026, 9, 5, 12, 0, 4, tzinfo=timezone.utc),
        _capture=captured,
    )

    assert snapshot.runtime.status is ResultStatus.PARTIAL
    assert snapshot.runtime.error is WorkloadErrorCode.INVALID
    assert [(record.container_id, record.state, record.updated_at) for record in snapshot.runtime.records] == [
        (valid_id, WorkloadState.ABSENT, datetime(2026, 9, 5, 12, 0, 3, tzinfo=timezone.utc))
    ]


def test_recipe_workload_snapshot_reports_bounded_source_failures_without_raw_output(tmp_path) -> None:
    registry = tmp_path / "invalid.toml"
    _write_snapshot_registry(registry, "not = [valid")

    snapshot = serve_recipes.capture_recipe_workload_snapshot(
        registry,
        clock=lambda: datetime(2026, 9, 5, 12, 0, tzinfo=timezone.utc),
        _capture=lambda _argv: ChildCapture("output-limit", b"secret-token", b"secret-token", True),
    )

    assert snapshot.configuration.status is ResultStatus.PARTIAL
    assert snapshot.configuration.error is WorkloadErrorCode.INVALID
    assert snapshot.runtime.status is ResultStatus.UNAVAILABLE
    assert snapshot.runtime.error is WorkloadErrorCode.UNAVAILABLE
    assert "secret-token" not in repr(snapshot)


def test_recipe_workload_configuration_retains_valid_recipe_when_peer_is_malformed(tmp_path) -> None:
    registry = tmp_path / "mixed.toml"
    _write_snapshot_registry(
        registry,
        '[[recipe]]\nmodel = "org/good"\n\n[[recipe]]\nmodel = ""\n',
    )

    snapshot = serve_recipes.capture_recipe_workload_snapshot(
        registry,
        clock=lambda: datetime(2026, 9, 5, 12, 0, 1, tzinfo=timezone.utc),
        _capture=lambda _argv: ChildCapture("ok", b"", b"", False),
    )

    assert snapshot.configuration.status is ResultStatus.PARTIAL
    assert len(snapshot.configuration.records) == 1
    assert snapshot.configuration.error is WorkloadErrorCode.INVALID


def test_recipe_workload_configuration_checks_stable_metadata_not_access_time(tmp_path, monkeypatch) -> None:
    registry = tmp_path / "recipes.toml"
    _write_snapshot_registry(registry)
    original_fstat = serve_recipes.os.fstat
    calls = 0

    def access_time_only(fd):
        nonlocal calls
        calls += 1
        value = original_fstat(fd)
        if calls == 2:
            return SimpleNamespace(
                st_dev=value.st_dev,
                st_ino=value.st_ino,
                st_size=value.st_size,
                st_mtime_ns=value.st_mtime_ns,
                st_ctime_ns=value.st_ctime_ns,
                st_atime_ns=value.st_atime_ns + 1,
                st_mode=value.st_mode,
            )
        return value

    monkeypatch.setattr(serve_recipes.os, "fstat", access_time_only)
    snapshot = serve_recipes.capture_recipe_workload_snapshot(
        registry,
        clock=lambda: datetime(2026, 9, 5, 12, 0, 1, tzinfo=timezone.utc),
        _capture=lambda _argv: ChildCapture("ok", b"", b"", False),
    )

    assert snapshot.configuration.status is ResultStatus.COMPLETE


def test_recipe_workload_configuration_refuses_mutation_and_preparse_overflow(tmp_path, monkeypatch) -> None:
    registry = tmp_path / "recipes.toml"
    _write_snapshot_registry(registry)
    original_fstat = serve_recipes.os.fstat
    calls = 0

    def changed_metadata(fd):
        nonlocal calls
        calls += 1
        value = original_fstat(fd)
        if calls == 2:
            return SimpleNamespace(
                st_dev=value.st_dev,
                st_ino=value.st_ino,
                st_size=value.st_size + 1,
                st_mtime_ns=value.st_mtime_ns,
                st_ctime_ns=value.st_ctime_ns,
                st_mode=value.st_mode,
            )
        return value

    monkeypatch.setattr(serve_recipes.os, "fstat", changed_metadata)
    changed = serve_recipes.capture_recipe_workload_snapshot(
        registry,
        clock=lambda: datetime(2026, 9, 5, 12, 0, 1, tzinfo=timezone.utc),
        _capture=lambda _argv: ChildCapture("ok", b"", b"", False),
    )
    assert changed.configuration.status is ResultStatus.PARTIAL

    exact = tmp_path / "exact.toml"
    exact.write_bytes(b"#" * serve_recipes.MAX_RECIPE_REGISTRY_BYTES)
    timestamp_ns = int(datetime(2026, 9, 5, 12, tzinfo=timezone.utc).timestamp() * 1_000_000_000)
    os.utime(exact, ns=(timestamp_ns, timestamp_ns))
    exact_snapshot = serve_recipes.capture_recipe_workload_snapshot(
        exact,
        clock=lambda: datetime(2026, 9, 5, 12, 0, 1, tzinfo=timezone.utc),
        _capture=lambda _argv: ChildCapture("ok", b"", b"", False),
    )
    assert exact_snapshot.configuration.status is ResultStatus.COMPLETE

    oversized = tmp_path / "oversized.toml"
    oversized.write_bytes(b"#" * (serve_recipes.MAX_RECIPE_REGISTRY_BYTES + 1))
    monkeypatch.setattr(
        serve_recipes.tomllib,
        "loads",
        lambda _value: pytest.fail("overflow must not reach the parser"),
    )
    overflow = serve_recipes.capture_recipe_workload_snapshot(
        oversized,
        clock=lambda: datetime(2026, 9, 5, 12, 0, 1, tzinfo=timezone.utc),
        _capture=lambda _argv: ChildCapture("ok", b"", b"", False),
    )
    assert overflow.configuration.status is ResultStatus.PARTIAL


def test_recipe_workload_runtime_empty_list_and_future_peer_are_distinct(tmp_path) -> None:
    registry = tmp_path / "recipes.toml"
    _write_snapshot_registry(registry)
    calls: list[tuple[str, ...]] = []
    empty = serve_recipes.capture_recipe_workload_snapshot(
        registry,
        clock=lambda: datetime(2026, 9, 5, 12, 0, 1, tzinfo=timezone.utc),
        _capture=_recipe_snapshot_capture([], calls),
    )
    assert empty.runtime.status is ResultStatus.COMPLETE
    assert empty.runtime.records == ()
    assert calls == [serve_recipes._RECIPE_LIST_ARGV]

    container_id = "a" * 64
    calls.clear()
    capture = _recipe_snapshot_capture([container_id], calls)
    future_row = {
        "id": container_id,
        "managed_by": serve_recipes.RECIPE_MANAGED_VALUE,
        "recipe_digest": None,
        "created_at": "2026-09-05T12:01:00Z",
        "status": "running",
        "running": True,
        "started_at": "2026-09-05T12:01:00Z",
        "finished_at": "0001-01-01T00:00:00Z",
    }

    def captured(argv):
        listed = capture(argv)
        if tuple(argv) == serve_recipes._RECIPE_LIST_ARGV:
            return listed
        return ChildCapture("ok", (json.dumps(future_row) + "\n").encode(), b"", False)

    future = serve_recipes.capture_recipe_workload_snapshot(
        registry,
        clock=lambda: datetime(2026, 9, 5, 12, 0, 1, tzinfo=timezone.utc),
        _capture=captured,
    )
    assert future.runtime.status is ResultStatus.PARTIAL
    assert future.runtime.error is WorkloadErrorCode.FUTURE


def test_recipe_workload_runtime_caps_ids_and_rejects_inconsistent_known_state(tmp_path) -> None:
    registry = tmp_path / "recipes.toml"
    _write_snapshot_registry(registry)
    identifiers = [format(index, "064x") for index in range(257)]
    calls: list[tuple[str, ...]] = []
    capture = _recipe_snapshot_capture(identifiers, calls)

    def captured(argv):
        listed = capture(argv)
        if tuple(argv) == serve_recipes._RECIPE_LIST_ARGV:
            return listed
        return ChildCapture("ok", b"", b"", False)

    capped = serve_recipes.capture_recipe_workload_snapshot(
        registry,
        clock=lambda: datetime(2026, 9, 5, 12, 0, 2, tzinfo=timezone.utc),
        _capture=captured,
    )
    assert capped.runtime.status is ResultStatus.PARTIAL
    assert len(calls[1]) == 6 + 256
    assert identifiers[-1] not in calls[1]

    container_id = "a" * 64
    bad_pair = {
        "id": container_id,
        "managed_by": serve_recipes.RECIPE_MANAGED_VALUE,
        "recipe_digest": None,
        "created_at": "2026-09-05T12:00:00Z",
        "status": "running",
        "running": False,
        "started_at": "malformed",
        "finished_at": "0001-01-01T00:00:00Z",
    }
    capture = _recipe_snapshot_capture([container_id], [])

    def invalid_pair(argv):
        listed = capture(argv)
        if tuple(argv) == serve_recipes._RECIPE_LIST_ARGV:
            return listed
        return ChildCapture("ok", (json.dumps(bad_pair) + "\n").encode(), b"", False)

    malformed = serve_recipes.capture_recipe_workload_snapshot(
        registry,
        clock=lambda: datetime(2026, 9, 5, 12, 0, 2, tzinfo=timezone.utc),
        _capture=invalid_pair,
    )
    assert malformed.runtime.status is ResultStatus.PARTIAL
    assert malformed.runtime.error is WorkloadErrorCode.INVALID


def test_recipe_workload_runtime_rejects_bad_json_peers_and_exactly_uses_unmerged_capture(tmp_path, monkeypatch) -> None:
    registry = tmp_path / "recipes.toml"
    _write_snapshot_registry(registry)
    container_id = "a" * 64
    raw = (
        '{"id":"' + container_id + '","id":"' + container_id + '",'
        '"managed_by":"models-recipes","recipe_digest":null,'
        '"created_at":"2026-09-05T12:00:00Z","status":"running",'
        '"running":true,"started_at":"2026-09-05T12:00:01Z",'
        '"finished_at":"0001-01-01T00:00:00Z"}\n'
    ).encode()
    list_capture = _recipe_snapshot_capture([container_id], [])

    def captured(argv):
        if tuple(argv) == serve_recipes._RECIPE_LIST_ARGV:
            return list_capture(argv)
        return ChildCapture("ok", raw, b"forbidden-stderr", False)

    snapshot = serve_recipes.capture_recipe_workload_snapshot(
        registry,
        clock=lambda: datetime(2026, 9, 5, 12, 0, 2, tzinfo=timezone.utc),
        _capture=captured,
    )
    assert snapshot.runtime.status is ResultStatus.PARTIAL
    assert "forbidden-stderr" not in repr(snapshot)

    captured_args = []
    monkeypatch.setattr(
        "anvil_serving.controller_diagnostics._capture_fixed_child",
        lambda argv, **kwargs: captured_args.append((argv, kwargs)) or ChildCapture("ok", b"", b"", False),
    )
    assert serve_recipes._recipe_capture(None, serve_recipes._RECIPE_LIST_ARGV).state == "ok"
    assert captured_args == [(serve_recipes._RECIPE_LIST_ARGV, {"merged": False})]
    assert serve_recipes._RECIPE_LIST_ARGV == (
        "docker", "ps", "-a", "--no-trunc", "--filter",
        "label=io.anvil-serving.managed-by=models-recipes", "--format", "{{.ID}}",
    )
    assert serve_recipes._RECIPE_INSPECT_TEMPLATE == (
        '{"id":{{json .Id}},"managed_by":{{json (index .Config.Labels "io.anvil-serving.managed-by")}},'
        '"recipe_digest":{{json (index .Config.Labels "io.anvil-serving.recipe.digest")}},'
        '"created_at":{{json .Created}},"status":{{json .State.Status}},'
        '"running":{{json .State.Running}},"started_at":{{json .State.StartedAt}},'
        '"finished_at":{{json .State.FinishedAt}}}'
    )


def test_recipe_workload_snapshot_keeps_components_independent_on_missing_source_and_clock_failure(tmp_path) -> None:
    missing = tmp_path / "missing.toml"
    missing_config = serve_recipes.capture_recipe_workload_snapshot(
        missing,
        clock=lambda: datetime(2026, 9, 5, 12, 0, 1, tzinfo=timezone.utc),
        _capture=lambda _argv: ChildCapture("ok", b"", b"", False),
    )
    assert missing_config.configuration.status is ResultStatus.UNAVAILABLE
    assert missing_config.runtime.status is ResultStatus.COMPLETE

    registry = tmp_path / "recipes.toml"
    _write_snapshot_registry(registry)
    failed_clock = serve_recipes.capture_recipe_workload_snapshot(
        registry,
        clock=lambda: (_ for _ in ()).throw(RuntimeError("must-not-leak")),
        _capture=lambda _argv: ChildCapture("ok", b"", b"", False),
    )
    assert failed_clock.configuration.status is ResultStatus.UNAVAILABLE
    assert failed_clock.runtime.status is ResultStatus.UNAVAILABLE
    assert "must-not-leak" not in repr(failed_clock)


def test_recipe_workload_configuration_marks_future_mtime_without_using_wall_clock(tmp_path) -> None:
    registry = tmp_path / "future.toml"
    _write_snapshot_registry(registry)
    future_ns = int(datetime(2026, 9, 5, 13, tzinfo=timezone.utc).timestamp() * 1_000_000_000)
    os.utime(registry, ns=(future_ns, future_ns))
    snapshot = serve_recipes.capture_recipe_workload_snapshot(
        registry,
        clock=lambda: datetime(2026, 9, 5, 12, 0, 1, tzinfo=timezone.utc),
        _capture=lambda _argv: ChildCapture("ok", b"", b"", False),
    )
    assert snapshot.configuration.status is ResultStatus.PARTIAL
    assert snapshot.configuration.error is WorkloadErrorCode.FUTURE
    assert snapshot.configuration.records == ()


@pytest.mark.parametrize(
    "created_at",
    [
        "2026-09-05T12:00:00+00:00",
        "2026-09-05T12:00:00.1234567890Z",
        "not-a-timestamp",
    ],
)
def test_recipe_workload_runtime_rejects_noncanonical_timestamps(tmp_path, created_at) -> None:
    registry = tmp_path / "recipes.toml"
    _write_snapshot_registry(registry)
    container_id = "a" * 64
    capture = _recipe_snapshot_capture([container_id], [])
    row = {
        "id": container_id,
        "managed_by": serve_recipes.RECIPE_MANAGED_VALUE,
        "recipe_digest": None,
        "created_at": created_at,
        "status": "created",
        "running": False,
        "started_at": "0001-01-01T00:00:00Z",
        "finished_at": "0001-01-01T00:00:00Z",
    }

    def captured(argv):
        listed = capture(argv)
        if tuple(argv) == serve_recipes._RECIPE_LIST_ARGV:
            return listed
        return ChildCapture("ok", (json.dumps(row) + "\n").encode(), b"", False)

    snapshot = serve_recipes.capture_recipe_workload_snapshot(
        registry,
        clock=lambda: datetime(2026, 9, 5, 12, 0, 1, tzinfo=timezone.utc),
        _capture=captured,
    )
    assert snapshot.runtime.status is ResultStatus.PARTIAL
    assert snapshot.runtime.error is WorkloadErrorCode.INVALID


def test_recipe_workload_runtime_quarantines_duplicate_and_unsolicited_ids(tmp_path) -> None:
    registry = tmp_path / "recipes.toml"
    _write_snapshot_registry(registry)
    requested, unsolicited = "a" * 64, "b" * 64
    capture = _recipe_snapshot_capture([requested, requested], [])
    row = {
        "id": unsolicited,
        "managed_by": serve_recipes.RECIPE_MANAGED_VALUE,
        "recipe_digest": None,
        "created_at": "2026-09-05T12:00:00Z",
        "status": "created",
        "running": False,
        "started_at": "0001-01-01T00:00:00Z",
        "finished_at": "0001-01-01T00:00:00Z",
    }

    def captured(argv):
        listed = capture(argv)
        if tuple(argv) == serve_recipes._RECIPE_LIST_ARGV:
            return listed
        return ChildCapture("ok", (json.dumps(row) + "\n").encode(), b"", False)

    snapshot = serve_recipes.capture_recipe_workload_snapshot(
        registry,
        clock=lambda: datetime(2026, 9, 5, 12, 0, 1, tzinfo=timezone.utc),
        _capture=captured,
    )
    assert snapshot.runtime.status is ResultStatus.PARTIAL
    assert snapshot.runtime.records == ()


def _mode_serves() -> list[dict]:
    budgets = {
        "gpu-a": reservations.GpuRoleBudget("gpu-a", 96_000),
        "gpu-b": reservations.GpuRoleBudget("gpu-b", 96_000),
    }
    return [
        {
            "name": "split-a",
            "container": "split-a",
            "gpu_role": "gpu-a",
            "groups": ["split"],
            "vram_mib": 90_000,
            "gpu_inference": True,
            reservations.GPU_ROLES_KEY: budgets,
        },
        {
            "name": "split-b",
            "container": "split-b",
            "gpu_role": "gpu-b",
            "groups": ["split"],
            "vram_mib": 90_000,
            "gpu_inference": True,
            reservations.GPU_ROLES_KEY: budgets,
        },
        {
            "name": "tp2",
            "container": "tp2",
            "gpu_roles": ["gpu-a", "gpu-b"],
            "vram_mib": 90_000,
            "gpu_inference": True,
            "operating_mode": serves.DUAL_GPU_EXCLUSIVE_MODE,
            "tensor_parallel_size": 2,
            reservations.GPU_ROLES_KEY: budgets,
        },
    ]


def _recipe_owner() -> dict:
    return {
        "owner": "recipe:candidate",
        "classification": "unmanaged-by-manifest",
        "container": "candidate",
        "model": "org/model",
        "state": "running",
        "gpu_selection": ["GPU-a", "GPU-b"],
        "gpu_roles": ["gpu-a", "gpu-b"],
        "unresolved_gpu_selection": [],
    }


def test_operating_mode_marks_recipe_owned_roles_unresolved_not_free() -> None:
    summary = serves.operating_mode_summary(
        _mode_serves(),
        lambda _container: "absent",
        {
            "owners": [_recipe_owner()],
            "discovery_error": None,
            "topology_resolved": True,
        },
    )

    assert summary["mode"] == "unresolved"
    assert summary["recipe_owners"] == [_recipe_owner()]
    assert summary["gpu_ownership"] == [
        {"gpu_role": "gpu-a", "owners": ["recipe:candidate"]},
        {"gpu_role": "gpu-b", "owners": ["recipe:candidate"]},
    ]


def test_all_gpu_recipe_selection_maps_to_every_declared_local_role(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from anvil_serving import topology

    monkeypatch.setattr(
        serve_recipes,
        "discover_recipe_containers",
        lambda **_kwargs: {
            "schema": serve_recipes.RECIPE_CONTAINER_INVENTORY_SCHEMA,
            "containers": [
                {
                    "container": "candidate",
                    "model": "org/model",
                    "state": "running",
                    "gpu_selection": ["all"],
                }
            ],
        },
    )
    monkeypatch.setattr(serves, "resolve_topology_path", lambda _path: "topology.toml")
    monkeypatch.setattr(
        topology,
        "load_topology",
        lambda _path: SimpleNamespace(
            gpu_roles=(
                SimpleNamespace(id="gpu-a", uuid="GPU-a"),
                SimpleNamespace(id="gpu-b", uuid="GPU-b"),
            )
        ),
    )

    ownership = serves._unmanaged_recipe_ownership(_mode_serves())

    assert ownership["owners"][0]["gpu_roles"] == ["gpu-a", "gpu-b"]
    assert ownership["owners"][0]["unresolved_gpu_selection"] == []


def test_mode_transition_refuses_unmanaged_recipe_owner_before_mutation(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    calls = []
    monkeypatch.setattr(
        serves,
        "docker_states",
        lambda containers, _run=None: {container: "absent" for container in containers},
    )

    rc = serves.cmd_mode(
        _mode_serves(),
        "preview",
        "tp2",
        "split",
        _run=lambda argv, **_kwargs: calls.append(argv),
        _recipe_ownership={
            "owners": [_recipe_owner()],
            "discovery_error": None,
            "topology_resolved": True,
        },
    )

    assert rc == 1
    assert calls == []
    assert "unmanaged or unresolved recipe-loaded GPU ownership" in capsys.readouterr().err


def test_mode_status_reports_unresolved_recipe_owner_successfully(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        serves,
        "docker_states",
        lambda containers, _run=None: {container: "absent" for container in containers},
    )

    rc = serves.cmd_mode(
        _mode_serves(),
        "status",
        None,
        None,
        _recipe_ownership={
            "owners": [_recipe_owner()],
            "discovery_error": None,
            "topology_resolved": True,
        },
    )

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "unresolved"
    assert payload["unresolved"] == [
        {"serve": "recipe:candidate", "state": "unmanaged-recipe-owner"}
    ]
