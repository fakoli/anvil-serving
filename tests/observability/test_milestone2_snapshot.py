from __future__ import annotations

import json
import urllib.request
from datetime import datetime, timezone

from anvil_serving.observability.api import (
    ProbeRegistration,
    TelemetryRegistry,
    create_server,
    run_server_in_thread,
)
from anvil_serving.observability.probes.remote_controller import (
    collect_remote_telemetry,
)
from anvil_serving.observability.schema import CapabilityStatus, TelemetrySample


NOW = datetime(2026, 7, 11, 20, 0, tzinfo=timezone.utc)
TOKEN = "milestone-two-controller-secret"


class _Response:
    def __init__(self, payload: dict[str, object]) -> None:
        self._payload = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *_args: object) -> bool:
        return False

    def read(self, size: int = -1) -> bytes:
        return self._payload if size < 0 else self._payload[:size]


def _sample(
    metric: str,
    *,
    host: str,
    collector: str,
    capability: str,
    value: int | None = 1,
    status: CapabilityStatus = CapabilityStatus.OK,
    labels: tuple[tuple[str, str], ...] = (),
) -> TelemetrySample:
    return TelemetrySample(
        metric=metric,
        source_timestamp=NOW,
        collection_timestamp=NOW,
        host_id=host,
        collector_id=collector,
        capability=capability,
        capability_status=status,
        value=value,
        stale_after_seconds=10,
        labels=labels,
    )


def test_milestone_two_serves_one_authenticated_whole_topology_snapshot() -> None:
    seen: dict[str, object] = {}
    mini_sample = _sample(
        "host.memory.used",
        host="fakoli-mini",
        collector="macos-host",
        capability="host-resources",
        value=8_000_000_000,
    )

    def controller_opener(request, timeout):
        seen["url"] = request.full_url
        seen["authorization"] = request.get_header("Authorization")
        seen["request"] = json.loads(request.data)
        seen["timeout"] = timeout
        return _Response({"ok": True, "data": {"samples": [mini_sample.to_dict()]}})

    def collect_mini():
        return collect_remote_telemetry(
            endpoint="http://100.64.0.20:8765",
            auth_env="ANVIL_CONTROLLER_TOKEN",
            expected_host_id="fakoli-mini",
            capabilities=("host-resources",),
            environment={"ANVIL_CONTROLLER_TOKEN": TOKEN},
            opener=controller_opener,
            received_at=NOW,
        )

    registrations = (
        ProbeRegistration(
            "host-resources",
            lambda: [
                _sample(
                    "host.memory.used",
                    host="fakoli-dark",
                    collector="windows-host",
                    capability="host-resources",
                )
            ],
            "fakoli-dark",
            "windows-host",
        ),
        ProbeRegistration(
            "boundary-resources",
            lambda: [
                _sample(
                    "boundary.memory.used",
                    host="fakoli-dark",
                    collector="wsl-docker",
                    capability="boundary-resources",
                    labels=(("boundary", "docker-desktop"),),
                )
            ],
            "fakoli-dark",
            "wsl-docker",
        ),
        ProbeRegistration(
            "nvidia-gpu",
            lambda: [
                _sample(
                    "gpu.memory.used",
                    host="fakoli-dark",
                    collector="nvidia-smi",
                    capability="nvidia-gpu",
                    labels=(("gpu", "0"),),
                ),
                _sample(
                    "gpu.memory.used",
                    host="fakoli-dark",
                    collector="nvidia-smi",
                    capability="nvidia-gpu",
                    labels=(("gpu", "1"),),
                ),
            ],
            "fakoli-dark",
            "nvidia-smi",
        ),
        ProbeRegistration(
            "containers",
            lambda: [
                _sample(
                    "container.memory.used",
                    host="fakoli-dark",
                    collector="docker-engine",
                    capability="containers",
                    labels=(("container", "router"),),
                )
            ],
            "fakoli-dark",
            "docker-engine",
        ),
        ProbeRegistration(
            "service-health",
            lambda: [
                _sample(
                    "service.health",
                    host="fakoli-dark",
                    collector="service-health",
                    capability="service-health",
                    labels=(("service", "router"),),
                )
            ],
            "fakoli-dark",
            "service-health",
        ),
        ProbeRegistration("mini-remote", collect_mini, "fakoli-mini", "authenticated-controller"),
        ProbeRegistration(
            "optional-adapter",
            lambda: [
                _sample(
                    "collector.status",
                    host="fakoli-mini",
                    collector="optional-adapter",
                    capability="optional-adapter",
                    value=None,
                    status=CapabilityStatus.UNSUPPORTED,
                )
            ],
            "fakoli-mini",
            "optional-adapter",
        ),
    )
    server = create_server(
        TelemetryRegistry(registrations),
        auth_env="ANVIL_TELEMETRY_TOKEN",
        environment={"ANVIL_TELEMETRY_TOKEN": TOKEN},
    )
    thread = run_server_in_thread(server)
    try:
        request = urllib.request.Request(
            f"http://127.0.0.1:{server.server_address[1]}/v1/metrics",
            headers={"Authorization": f"Bearer {TOKEN}"},
        )
        with urllib.request.urlopen(request, timeout=2) as response:
            payload = json.loads(response.read())["data"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    samples = payload["samples"]
    assert {sample["host_id"] for sample in samples} == {"fakoli-dark", "fakoli-mini"}
    assert {
        sample["labels"].get("gpu") for sample in samples if sample["metric"] == "gpu.memory.used"
    } == {"0", "1"}
    assert {sample["capability"] for sample in samples} >= {
        "host-resources",
        "boundary-resources",
        "nvidia-gpu",
        "containers",
        "service-health",
        "optional-adapter",
    }
    assert (
        next(sample for sample in samples if sample["collector_id"] == "optional-adapter")[
            "capability_status"
        ]
        == "unsupported"
    )
    assert seen["url"] == "http://100.64.0.20:8765/tools/call"
    assert seen["authorization"] == f"Bearer {TOKEN}"
    assert seen["request"] == {
        "name": "observability_collect",
        "arguments": {"capabilities": ["host-resources"]},
    }
    assert TOKEN not in json.dumps(payload)
