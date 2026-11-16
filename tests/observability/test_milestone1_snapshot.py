"""Integrated exit gate for Milestone 1 trustworthy local telemetry."""

from __future__ import annotations

from datetime import datetime, timezone

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
from anvil_serving.observability.schema import TelemetrySample
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
