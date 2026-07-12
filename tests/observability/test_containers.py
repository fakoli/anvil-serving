from __future__ import annotations

import json
from datetime import datetime, timezone

from anvil_serving.observability.probes.containers import collect_containers
from anvil_serving.observability.schema import CapabilityStatus, TelemetrySample


NOW = datetime(2026, 7, 11, 20, 0, tzinfo=timezone.utc)
INSPECT = {
    "Id": "abc123",
    "Name": "/anvil-router",
    "State": {"Status": "running"},
    "HostConfig": {
        "DeviceRequests": [
            {"Count": -1, "DeviceIDs": ["GPU-1234"], "Capabilities": [["gpu"]]}
        ]
    },
}
STATS = {
    "ID": "abc123",
    "Name": "anvil-router",
    "CPUPerc": "12.50%",
    "MemUsage": "1.5GiB / 8GiB",
    "NetIO": "12.5kB / 2MB",
    "BlockIO": "3MiB / 4.5MiB",
}


def _by_metric(samples: list[TelemetrySample]) -> dict[str, TelemetrySample]:
    return {sample.metric: sample for sample in samples}


def test_each_container_reports_identity_state_and_resources() -> None:
    samples = collect_containers(
        provider=lambda: ([INSPECT], [STATS]), host_id="dark", collected_at=NOW
    )
    metrics = _by_metric(samples)

    assert metrics["container.identity"].value == "anvil-router"
    assert metrics["container.status"].value == "running"
    assert metrics["container.cpu.utilization"].value == 12.5
    assert metrics["container.memory.used"].value == 1.5 * 1024**3
    assert metrics["container.network.received"].value == 12_500
    assert metrics["container.network.sent"].value == 2_000_000
    assert metrics["container.storage.read"].value == 3 * 1024**2
    assert metrics["container.storage.written"].value == 4.5 * 1024**2
    assert all(TelemetrySample.from_json(sample.to_json()) == sample for sample in samples)


def test_gpu_assignment_and_unavailable_consumption_are_not_conflated() -> None:
    metrics = _by_metric(
        collect_containers(
            provider=lambda: ([INSPECT], [STATS]), host_id="dark", collected_at=NOW
        )
    )

    assignment = metrics["container.gpu.assignment"]
    consumption = metrics["container.gpu.memory.used"]
    assert assignment.value == "GPU-1234"
    assert dict(assignment.labels)["attribution"] == "configured"
    assert consumption.capability_status is CapabilityStatus.UNSUPPORTED
    assert consumption.value is None
    assert dict(consumption.labels)["attribution"] == "unavailable"


def test_stopped_container_without_stats_has_explicit_missing_resources() -> None:
    inspect = dict(INSPECT, State={"Status": "exited"})

    metrics = _by_metric(
        collect_containers(
            provider=lambda: ([inspect], []), host_id="dark", collected_at=NOW
        )
    )

    assert metrics["container.status"].value == "exited"
    for metric in (
        "container.cpu.utilization",
        "container.memory.used",
        "container.network.received",
        "container.network.sent",
        "container.storage.read",
        "container.storage.written",
    ):
        assert metrics[metric].capability_status is CapabilityStatus.MISSING
        assert metrics[metric].value is None


def test_malformed_stats_are_missing_not_zero() -> None:
    stats = dict(STATS, CPUPerc="unknown", NetIO="bad")

    metrics = _by_metric(
        collect_containers(
            provider=lambda: ([INSPECT], [stats]), host_id="dark", collected_at=NOW
        )
    )

    assert metrics["container.cpu.utilization"].capability_status is CapabilityStatus.MISSING
    assert metrics["container.network.received"].capability_status is CapabilityStatus.MISSING
    assert metrics["container.network.sent"].capability_status is CapabilityStatus.MISSING


def test_permission_failure_is_explicit() -> None:
    def denied():
        raise PermissionError("Docker socket access denied")

    sample = collect_containers(provider=denied, host_id="dark", collected_at=NOW)[0]

    assert sample.capability_status is CapabilityStatus.PERMISSION_DENIED
    assert sample.value is None


def test_empty_engine_is_a_valid_empty_result() -> None:
    assert (
        collect_containers(
            provider=lambda: ([], []), host_id="dark", collected_at=NOW
        )
        == []
    )


def test_default_provider_issues_only_read_only_docker_calls(monkeypatch) -> None:
    calls: list[list[str]] = []

    def fake_run(arguments: list[str]) -> str:
        calls.append(arguments)
        if arguments[:3] == ["container", "ls", "-aq"]:
            return "abc123\n"
        if arguments[0] == "inspect":
            return json.dumps([INSPECT])
        if arguments[0] == "stats":
            return json.dumps(STATS)
        raise AssertionError(f"unexpected Docker call: {arguments}")

    monkeypatch.setattr(
        "anvil_serving.observability.probes.containers._run_docker", fake_run
    )

    samples = collect_containers(host_id="dark", collected_at=NOW)

    assert samples
    assert [call[0] for call in calls] == ["container", "inspect", "stats"]
    assert calls[0] == ["container", "ls", "-aq"]
    assert "--no-stream" in calls[2]
