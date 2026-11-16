from __future__ import annotations

import json
from datetime import datetime, timezone

from anvil_serving.observability.probes.containers import collect_containers
from anvil_serving.observability.schema import CapabilityStatus


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


def test_permission_failure_is_explicit() -> None:
    def denied():
        raise PermissionError("Docker socket access denied")

    sample = collect_containers(provider=denied, host_id="dark", collected_at=NOW)[0]

    assert sample.capability_status is CapabilityStatus.PERMISSION_DENIED
    assert sample.value is None


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
