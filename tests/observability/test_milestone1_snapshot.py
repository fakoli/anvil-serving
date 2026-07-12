"""Integrated exit gate for Milestone 1 trustworthy local telemetry."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from anvil_serving.observability.probes.containers import collect_containers
from anvil_serving.observability.probes.gpu_nvidia import collect_nvidia_gpus
from anvil_serving.observability.probes.gpu_shared_memory import (
    collect_windows_shared_gpu_memory,
)
from anvil_serving.observability.probes.windows_host import collect_windows_host
from anvil_serving.observability.probes.wsl_docker import (
    collect_wsl_docker_boundaries,
)
from anvil_serving.observability.redaction import redact_record
from anvil_serving.observability.schema import CapabilityStatus, TelemetrySample
from anvil_serving.observability.status import prepare_sample


NOW = datetime(2026, 7, 11, 20, 0, tzinfo=timezone.utc)
SECRET = "milestone-controller-secret"
GPU_XML = """<nvidia_smi_log><gpu>
<product_name>NVIDIA Test GPU</product_name><uuid>GPU-1</uuid>
<fb_memory_usage><total>32768 MiB</total><used>8192 MiB</used><free>24576 MiB</free></fb_memory_usage>
<utilization><gpu_util>75 %</gpu_util></utilization>
<temperature><gpu_temp>65 C</gpu_temp></temperature>
<gpu_power_readings><instant_power_draw>200 W</instant_power_draw></gpu_power_readings>
<processes><process_info><pid>42</pid><process_name>Bearer sk-proj-abcdefgh</process_name><used_memory>4096 MiB</used_memory></process_info></processes>
</gpu></nvidia_smi_log>"""


def _snapshot() -> list[TelemetrySample]:
    windows = collect_windows_host(
        provider=lambda: {
            "cpu_percent": 25,
            "memory_total_bytes": 64 * 1024**3,
            "memory_available_bytes": 16 * 1024**3,
            "paging_pages_per_second": 5,
            "disk_bytes_per_second": 10_000,
            "network_bytes_per_second": 20_000,
        },
        host_id="dark",
        collected_at=NOW,
    )
    boundaries = collect_wsl_docker_boundaries(
        wsl_provider=lambda: [
            {"name": "vmmemWSL", "cpu_percent": 10, "memory_used_bytes": 8 * 1024**3}
        ],
        docker_provider=lambda: [{"CPUPerc": "5%", "MemUsage": "4GiB / 32GiB"}],
        host_id="dark",
        collected_at=NOW,
    )
    gpu = collect_nvidia_gpus(provider=lambda: GPU_XML, host_id="dark", collected_at=NOW)
    shared = collect_windows_shared_gpu_memory(
        provider=lambda: [{"adapter": "luid-1", "shared_used_bytes": 2 * 1024**3}],
        host_id="dark",
        collected_at=NOW,
    )
    containers = collect_containers(
        provider=lambda: (
            [
                {
                    "Id": "container-1",
                    "Name": f"/router-{SECRET}",
                    "State": {"Status": "running"},
                    "HostConfig": {
                        "DeviceRequests": [
                            {"DeviceIDs": ["GPU-1"], "Capabilities": [["gpu"]]}
                        ]
                    },
                }
            ],
            [
                {
                    "ID": "container-1",
                    "CPUPerc": "15%",
                    "MemUsage": "6GiB / 32GiB",
                    "NetIO": "1MB / 2MB",
                    "BlockIO": "3MB / 4MB",
                }
            ],
        ),
        host_id="dark",
        collected_at=NOW,
    )
    return windows + boundaries + gpu + shared + containers


def test_integrated_snapshot_contains_every_milestone_signal() -> None:
    snapshot = _snapshot()
    metrics = {sample.metric for sample in snapshot}

    assert {
        "host.cpu.utilization",
        "host.memory.used",
        "host.memory.pressure",
        "host.paging.rate",
        "host.disk.throughput",
        "host.network.throughput",
        "boundary.cpu.utilization",
        "boundary.memory.used",
        "gpu.utilization",
        "gpu.memory.used",
        "gpu.memory.shared.used",
        "container.cpu.utilization",
        "container.memory.used",
        "container.network.received",
        "container.storage.read",
    } <= metrics
    boundaries = {
        dict(sample.labels).get("boundary")
        for sample in snapshot
        if sample.metric == "boundary.memory.used"
    }
    assert boundaries == {"wsl-vm", "docker-engine"}
    assert all(TelemetrySample.from_json(sample.to_json()) == sample for sample in snapshot)


def test_degraded_states_remain_distinct_from_healthy_zero() -> None:
    healthy_zero = collect_windows_host(
        provider=lambda: {
            "cpu_percent": 0,
            "memory_total_bytes": 1,
            "memory_available_bytes": 1,
            "paging_pages_per_second": 0,
            "disk_bytes_per_second": 0,
            "network_bytes_per_second": 0,
        },
        host_id="dark",
        collected_at=NOW,
    )[0]
    permission_denied = collect_windows_host(
        provider=lambda: (_ for _ in ()).throw(PermissionError("denied")),
        host_id="dark",
        collected_at=NOW,
    )[0]
    unsupported = collect_windows_shared_gpu_memory(
        provider=lambda: [], host_id="dark", collected_at=NOW
    )[0]
    failed = collect_nvidia_gpus(
        provider=lambda: "malformed", host_id="dark", collected_at=NOW
    )[0]
    stale = prepare_sample(
        TelemetrySample(
            metric="host.memory.used",
            source_timestamp=NOW - timedelta(seconds=10),
            collection_timestamp=NOW,
            host_id="dark",
            collector_id="fixture",
            capability="memory",
            capability_status=CapabilityStatus.OK,
            value=1,
            unit="bytes",
            stale_after_seconds=5,
        )
    )
    ambiguous = next(
        sample
        for sample in _snapshot()
        if dict(sample.labels).get("attribution") == "ambiguous"
    )

    assert healthy_zero.capability_status is CapabilityStatus.OK
    assert healthy_zero.value == 0
    assert permission_denied.capability_status is CapabilityStatus.PERMISSION_DENIED
    assert unsupported.capability_status is CapabilityStatus.UNSUPPORTED
    assert failed.capability_status is CapabilityStatus.FAILED
    assert stale.capability_status is CapabilityStatus.STALE
    assert dict(ambiguous.labels)["attribution"] == "ambiguous"
    assert all(
        sample.value is None
        for sample in (permission_denied, unsupported, failed)
    )


def test_integrated_snapshot_and_logs_are_secret_free() -> None:
    serialized = "\n".join(
        prepare_sample(sample, secrets=(SECRET,)).to_json() for sample in _snapshot()
    )
    log = str(
        redact_record(
            {
                "message": f"authorization: Bearer {SECRET}",
                "password": "hunter2",
            },
            secrets=(SECRET,),
        )
    )

    for leaked in (SECRET, "sk-proj-abcdefgh", "hunter2"):
        assert leaked not in serialized
        assert leaked not in log
