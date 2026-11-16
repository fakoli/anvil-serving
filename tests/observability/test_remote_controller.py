from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from anvil_serving.observability.probes.remote_controller import (
    collect_remote_telemetry,
)
from anvil_serving.observability.schema import CapabilityStatus, TelemetrySample
from tests.conftest import Response


TOKEN = "controller-secret-token"
NOW = datetime(2026, 7, 11, 20, 0, tzinfo=timezone.utc)


def _remote_sample(*, age_seconds: float = 0) -> TelemetrySample:
    collected = NOW - timedelta(seconds=age_seconds)
    return TelemetrySample(
        metric="host.memory.used",
        source_timestamp=collected,
        collection_timestamp=collected,
        host_id="generic-macos-host",
        collector_id="macos-host",
        capability="unified-memory",
        capability_status=CapabilityStatus.OK,
        value=1024,
        unit="bytes",
        stale_after_seconds=5,
    )


def _opener_for(samples, seen):
    def opener(request, timeout):
        seen["authorization"] = request.get_header("Authorization")
        seen["url"] = request.full_url
        seen["body"] = json.loads(request.data)
        seen["timeout"] = timeout
        payload = {"ok": True, "data": {"samples": [sample.to_dict() for sample in samples]}}
        return Response(json.dumps(payload).encode("utf-8"))

    return opener


def test_remote_samples_use_authenticated_controller_and_preserve_identity() -> None:
    seen = {}

    samples = collect_remote_telemetry(
        endpoint="http://100.64.0.10:8765",
        auth_env="ANVIL_CONTROLLER_TOKEN",
        expected_host_id="generic-macos-host",
        capabilities=("host-resources", "process-resources"),
        environment={"ANVIL_CONTROLLER_TOKEN": TOKEN},
        opener=_opener_for([_remote_sample()], seen),
        received_at=NOW,
    )

    assert samples[0].host_id == "generic-macos-host"
    assert samples[0].collector_id == "macos-host"
    assert samples[0].capability_status is CapabilityStatus.OK
    assert seen["authorization"] == f"Bearer {TOKEN}"
    assert seen["url"] == "http://100.64.0.10:8765/tools/call"
    assert seen["body"] == {
        "name": "observability_collect",
        "arguments": {"capabilities": ["host-resources", "process-resources"]},
    }
    assert TOKEN not in "".join(sample.to_json() for sample in samples)


def test_missing_controller_token_is_permission_denied_before_network() -> None:
    opened = False

    def opener(*_args, **_kwargs):
        nonlocal opened
        opened = True
        raise AssertionError("network must not open without authentication")

    sample = collect_remote_telemetry(
        endpoint="http://127.0.0.1:8765",
        auth_env="ANVIL_CONTROLLER_TOKEN",
        expected_host_id="generic-macos-host",
        capabilities=("host-resources",),
        environment={},
        opener=opener,
        received_at=NOW,
    )[0]

    assert sample.capability_status is CapabilityStatus.PERMISSION_DENIED
    assert sample.value is None
    assert opened is False


def test_empty_authenticated_response_is_missing_not_silent_success() -> None:
    sample = collect_remote_telemetry(
        endpoint="http://127.0.0.1:8765",
        auth_env="ANVIL_CONTROLLER_TOKEN",
        expected_host_id="generic-macos-host",
        capabilities=("host-resources",),
        environment={"ANVIL_CONTROLLER_TOKEN": TOKEN},
        opener=_opener_for([], {}),
        received_at=NOW,
    )[0]

    assert sample.capability_status is CapabilityStatus.MISSING
    assert sample.value is None
