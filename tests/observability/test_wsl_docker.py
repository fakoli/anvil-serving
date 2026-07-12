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


def test_wsl_and_docker_cpu_memory_are_distinct_labeled_values() -> None:
    samples = collect_wsl_docker_boundaries(
        wsl_provider=lambda: WSL_ROWS,
        docker_provider=lambda: DOCKER_ROWS,
        host_id="dark",
        collected_at=NOW,
    )
    metrics = _by_boundary_metric(samples)

    assert metrics[("wsl-vm", "boundary.cpu.utilization")].value == 5
    assert metrics[("wsl-vm", "boundary.memory.used")].value == 3 * 1024**3
    assert metrics[("docker-engine", "boundary.cpu.utilization")].value == 10
    assert metrics[("docker-engine", "boundary.memory.used")].value == 2 * 1024**3
    assert all(TelemetrySample.from_json(sample.to_json()) == sample for sample in samples)


def test_attribution_quality_is_explicit_at_each_boundary() -> None:
    samples = collect_wsl_docker_boundaries(
        wsl_provider=lambda: WSL_ROWS,
        docker_provider=lambda: DOCKER_ROWS,
        host_id="dark",
        collected_at=NOW,
    )
    labels = {dict(sample.labels)["boundary"]: dict(sample.labels) for sample in samples}

    assert labels["wsl-vm"]["attribution"] == "ambiguous"
    assert labels["wsl-vm"]["source"] == "windows-perfproc-vmmem"
    assert labels["docker-engine"]["attribution"] == "inferred"
    assert labels["docker-engine"]["source"] == "docker-stats-container-aggregate"


def test_no_running_containers_is_a_valid_inferred_zero() -> None:
    metrics = _by_boundary_metric(
        collect_wsl_docker_boundaries(
            wsl_provider=lambda: WSL_ROWS,
            docker_provider=lambda: [],
            host_id="dark",
            collected_at=NOW,
        )
    )

    assert metrics[("docker-engine", "boundary.cpu.utilization")].value == 0
    assert metrics[("docker-engine", "boundary.memory.used")].value == 0
    assert (
        dict(metrics[("docker-engine", "boundary.memory.used")].labels)["attribution"]
        == "inferred"
    )


def test_missing_wsl_boundary_does_not_fabricate_zero_or_hide_docker() -> None:
    metrics = _by_boundary_metric(
        collect_wsl_docker_boundaries(
            wsl_provider=lambda: [],
            docker_provider=lambda: DOCKER_ROWS,
            host_id="dark",
            collected_at=NOW,
        )
    )

    assert metrics[("wsl-vm", "boundary.memory.used")].capability_status is CapabilityStatus.MISSING
    assert metrics[("wsl-vm", "boundary.memory.used")].value is None
    assert metrics[("docker-engine", "boundary.memory.used")].capability_status is CapabilityStatus.OK


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


def test_non_windows_default_path_is_unsupported(monkeypatch) -> None:
    monkeypatch.setattr("platform.system", lambda: "Darwin")

    samples = collect_wsl_docker_boundaries(host_id="mini", collected_at=NOW)

    assert len(samples) == 4
    assert all(sample.capability_status is CapabilityStatus.UNSUPPORTED for sample in samples)
    assert all(sample.value is None for sample in samples)


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

    samples = collect_wsl_docker_boundaries(host_id="dark", collected_at=NOW)

    assert samples
    assert calls[0][0] == "powershell.exe"
    assert "Get-CimInstance" in calls[0][-1]
    assert calls[1][:3] == ["docker", "stats", "--no-stream"]


def test_boundary_rows_are_bounded() -> None:
    rows = [
        {"name": str(index), "cpu_percent": 0, "memory_used_bytes": 0}
        for index in range(65)
    ]

    metrics = _by_boundary_metric(
        collect_wsl_docker_boundaries(
            wsl_provider=lambda: rows,
            docker_provider=lambda: [],
            host_id="dark",
            collected_at=NOW,
        )
    )

    assert metrics[("wsl-vm", "boundary.memory.used")].capability_status is CapabilityStatus.FAILED
