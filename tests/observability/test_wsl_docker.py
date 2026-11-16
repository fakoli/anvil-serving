from __future__ import annotations

import json
from datetime import datetime, timezone

from anvil_serving.observability.probes.wsl_docker import (
    collect_wsl_docker_boundaries,
)
from anvil_serving.observability.schema import CapabilityStatus, TelemetrySample


NOW = datetime(2026, 7, 11, 20, 0, tzinfo=timezone.utc)
WSL_ROWS = [
    {"name": "vmmem", "cpu_percent": 2, "memory_used_bytes": 1024**3},
    {"name": "vmmemWSL", "cpu_percent": 3, "memory_used_bytes": 2 * 1024**3},
]
DOCKER_ROWS = [
    {"CPUPerc": "4.5%", "MemUsage": "1.5GiB / 8GiB"},
    {"CPUPerc": "5.5%", "MemUsage": "512MiB / 8GiB"},
]


def _by_boundary_metric(samples: list[TelemetrySample]):
    return {(dict(sample.labels)["boundary"], sample.metric): sample for sample in samples}


def test_one_boundary_permission_failure_does_not_hide_the_other() -> None:
    def denied():
        raise PermissionError("counter access denied")

    metrics = _by_boundary_metric(
        collect_wsl_docker_boundaries(
            wsl_provider=denied,
            docker_provider=lambda: DOCKER_ROWS,
            host_id="dark",
            collected_at=NOW,
        )
    )

    assert metrics[("wsl-vm", "boundary.cpu.utilization")].capability_status is CapabilityStatus.PERMISSION_DENIED
    assert metrics[("docker-engine", "boundary.cpu.utilization")].capability_status is CapabilityStatus.OK


def test_default_collectors_issue_only_read_only_queries(monkeypatch) -> None:
    calls: list[list[str]] = []

    def fake_run(command: list[str], source: str) -> str:
        calls.append(command)
        if command[0] == "powershell.exe":
            return json.dumps(WSL_ROWS)
        if command[:2] == ["docker", "stats"]:
            return "\n".join(json.dumps(row) for row in DOCKER_ROWS)
        raise AssertionError(f"unexpected command for {source}: {command}")

    monkeypatch.setattr(
        "anvil_serving.observability.probes.wsl_docker._run", fake_run
    )
    monkeypatch.setattr("platform.system", lambda: "Windows")

    samples = collect_wsl_docker_boundaries(host_id="dark", collected_at=NOW)

    assert samples
    assert calls[0][0] == "powershell.exe"
    assert "Get-CimInstance" in calls[0][-1]
    assert calls[1][:3] == ["docker", "stats", "--no-stream"]
