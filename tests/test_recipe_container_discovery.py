from __future__ import annotations

import json
import io
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


@pytest.mark.parametrize("tokens,expected", [
    (["--model-id=alias", "--served-model-name=override"], "override"),
    (["--served-model-name", "override", "--model-id", "alias"], "override"),
    (["--model-id=alias", "--served-model-name="], "org/model"),
    (["--model-id=alias", "--served-model-name"], "org/model"),
    (["--model-id=first", "--model-id=second"], "first"),
    ([None, "--model-id", "x" * 512], "x" * 512),
    (["--model-id="], "org/model"),
    (["--model-id=" + "x" * 513], "org/model"),
    (["--model-id=bad\nname"], "org/model"),
    (["--model-identifier=other"], "org/model"),
    ([], "org/model"),
])
def test_recipe_discovery_served_identity_preserves_precedence_and_fallback(tokens, expected):
    row = _row("candidate")
    row["Args"] = tokens
    # An explicit Args list, including empty, still takes precedence over Cmd.
    row["Config"]["Cmd"] = ["--model-id=ignored-cmd"]
    inventory = serve_recipes.discover_recipe_containers(_run=_DiscoveryRun([row]))
    assert inventory["containers"][0]["served_identity"] == expected


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


def test_discovered_status_fails_closed_on_unknown_containment(tmp_path, monkeypatch, capsys):
    row = _row("candidate")
    row["HostConfig"].update(Memory=64 * 1024**2, MemorySwap=64 * 1024**2 + 1)
    inventory = serve_recipes.discover_recipe_containers(_run=_DiscoveryRun([row]))
    monkeypatch.setattr(serve_recipes, "discover_recipe_containers", lambda: inventory)
    assert models._recipe_main([
        "status", "org/model", "--registry", str(tmp_path / "missing.toml"),
    ]) == 1
    result = json.loads(capsys.readouterr().out)
    assert "containment failed" in result["containment_error"]
    assert result["health"] == "healthy"  # Docker health is separate evidence.


@pytest.mark.parametrize("registered", [False, True])
def test_recipe_logs_emit_redacted_stderr_only_container_output_as_data(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    registered: bool,
) -> None:
    identity = serve_recipes._recipe_container_record(_row("candidate"))
    assert identity is not None
    monkeypatch.setattr(models, "_recipe_container_identity", lambda *args, **kwargs: identity)
    monkeypatch.setattr(models, "_recheck_discovered_recipe_container", lambda *args, **kwargs: identity)

    def run(argv, **_kwargs):
        assert argv[:2] == ["docker", "logs"]
        return _completed(
            argv,
            stderr="error: token=private-test-token\n",
        )

    if registered:
        returncode = models._recipe_container_logs(
            {"model": "org/model"}, "candidate", _run=run,
        )
    else:
        returncode = models._discovered_recipe_container_logs(identity, _run=run)

    captured = capsys.readouterr()
    assert returncode == 0
    assert captured.out == "error: token=<redacted>\n"
    assert captured.err == ""
    assert "private-test-token" not in captured.out


def test_recipe_log_metadata_rechecks_discovered_identity_and_uses_interval_flags(monkeypatch: pytest.MonkeyPatch) -> None:
    identity = serve_recipes._recipe_container_record(_row("candidate"))
    assert identity is not None
    monkeypatch.setattr(
        serve_recipes, "discover_recipe_containers",
        lambda **_kwargs: {"schema": serve_recipes.RECIPE_CONTAINER_INVENTORY_SCHEMA, "containers": [identity]},
    )
    calls = []
    result = models.collect_discovered_recipe_container_logs(
        "candidate", tail=3, since="2026-10-16T12:00:00Z", until="2026-10-16T12:01:00Z", timestamps=True,
        _run=lambda argv, **_kwargs: calls.append(argv) or _completed(
            argv, stdout='2026-10-16T12:00:02Z event=admission token=private-test-token\n'
        ),
    )
    assert calls == [[
        "docker", "logs", "--tail", "3", "--since", "2026-10-16T12:00:00Z",
        "--until", "2026-10-16T12:01:00Z", "--timestamps", identity["container_id"],
    ]]
    assert result["observed_runtime"]["container_id"] == identity["container_id"]
    assert result["lines"] == ["2026-10-16T12:00:02Z event=admission token=<redacted>"]


def test_default_recipe_log_capture_uses_bounded_popen_path(monkeypatch: pytest.MonkeyPatch) -> None:
    class Process:
        returncode = 0
        def __init__(self, argv, **kwargs):
            self.argv, self.kwargs = argv, kwargs
            self.stdout = io.BytesIO(b'{"event":"admission"}\n')
        def wait(self, timeout=None):
            return 0
        def poll(self):
            return self.returncode
        def kill(self):
            raise AssertionError("small output must not be killed")

    captured = {}
    monkeypatch.setattr(models.subprocess, "Popen", lambda argv, **kwargs: captured.setdefault("process", Process(argv, **kwargs)))
    code, stdout, stderr = models._bounded_recipe_log_capture(["docker", "logs", "id"])
    assert code == 0 and stdout == '{"event":"admission"}\n' and stderr == ""
    assert captured["process"].kwargs["stderr"] is models.subprocess.STDOUT


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


@pytest.mark.parametrize("registered", [False, True])
@pytest.mark.parametrize("stderr,stdout,expected", [
    ("daemon: removal already in progress", "unused", "daemon: removal already in progress"),
    (" \n", "daemon: removal failed", "daemon: removal failed"),
    ("", "", "Docker returned a non-zero exit status"),
    ("daemon: token=private-test-token", "", "daemon: token=<redacted>"),
])
def test_recipe_unload_failure_retains_bounded_redacted_docker_detail(
    monkeypatch, capsys, registered, stderr, stdout, expected,
):
    identity = serve_recipes._recipe_container_record(_row("candidate"))
    monkeypatch.setattr(models, "_recipe_container_identity", lambda *a, **k: identity)
    monkeypatch.setattr(models, "_recheck_discovered_recipe_container", lambda *a, **k: identity)
    def unexpected_cleanup():
        pytest.fail("a failed removal must not run cleanup")
    monkeypatch.setattr(models.host_ops, "prepare_native_kv_offload_shared_memory", unexpected_cleanup)
    calls = []
    def run(argv, **kwargs):
        calls.append(argv)
        return _completed(argv, returncode=17, stderr=stderr, stdout=stdout)
    if registered:
        code = models._recipe_container_unload({"model": "org/model"}, "candidate", confirm=True, _run=run)
    else:
        code = models._discovered_recipe_container_unload(identity, confirm=True, _run=run)
    captured = capsys.readouterr()
    assert code == 17
    assert calls == [["docker", "rm", "-f", identity["container_id"]]]
    assert captured.err == "recipe unload failed: " + expected + "\n"
    assert "unloaded" not in captured.out
    assert "private-test-token" not in captured.err


def test_recipe_unload_failure_redacts_before_truncation():
    detail = models._recipe_unload_failure_detail(_completed([], returncode=1,
        stderr="denied token=" + "sensitive" * 500 + " " + "x" * 3000))
    assert "sensitive" not in detail
    assert "token=<redacted>" in detail
    assert detail.endswith(" [truncated]\n")
    assert len(detail) <= len("recipe unload failed: ") + 2048 + len(" [truncated]\n")
