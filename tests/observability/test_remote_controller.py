from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from anvil_serving.observability.probes.remote_controller import (
    collect_remote_telemetry,
)
from anvil_serving.observability.schema import CapabilityStatus, TelemetrySample


TOKEN = "controller-secret-token"
NOW = datetime(2026, 7, 11, 20, 0, tzinfo=timezone.utc)


class Response:
    def __init__(self, payload: bytes):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, size=-1):
        return self.payload if size < 0 else self.payload[:size]


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


def test_receipt_time_reclassifies_expired_remote_sample_as_stale() -> None:
    samples = collect_remote_telemetry(
        endpoint="http://127.0.0.1:8765",
        auth_env="ANVIL_CONTROLLER_TOKEN",
        expected_host_id="generic-macos-host",
        capabilities=("host-resources",),
        environment={"ANVIL_CONTROLLER_TOKEN": TOKEN},
        opener=_opener_for([_remote_sample(age_seconds=6)], {}),
        received_at=NOW,
    )

    assert samples[0].capability_status is CapabilityStatus.STALE
    assert samples[0].value == 1024


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


def test_remote_host_identity_mismatch_fails_closed() -> None:
    sample = collect_remote_telemetry(
        endpoint="http://127.0.0.1:8765",
        auth_env="ANVIL_CONTROLLER_TOKEN",
        expected_host_id="different-host",
        capabilities=("host-resources",),
        environment={"ANVIL_CONTROLLER_TOKEN": TOKEN},
        opener=_opener_for([_remote_sample()], {}),
        received_at=NOW,
    )[0]

    assert sample.capability_status is CapabilityStatus.FAILED
    assert sample.value is None


def test_capability_wiring_is_generic_and_rejects_duplicates() -> None:
    try:
        collect_remote_telemetry(
            endpoint="http://127.0.0.1:8765",
            auth_env="ANVIL_CONTROLLER_TOKEN",
            expected_host_id="generic-host",
            capabilities=("host-resources", "host-resources"),
            environment={"ANVIL_CONTROLLER_TOKEN": TOKEN},
            opener=_opener_for([], {}),
            received_at=NOW,
        )
    except ValueError as exc:
        assert "duplicates" in str(exc)
    else:
        raise AssertionError("duplicate capability must be rejected")


def test_module_has_no_raw_ssh_transport_path() -> None:
    import inspect
    import anvil_serving.observability.probes.remote_controller as module

    source = inspect.getsource(module).lower()
    assert "ssh" not in source
    assert "controllertransport" in source


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


def test_remote_sample_requires_freshness_window() -> None:
    sample = _remote_sample()
    object.__setattr__(sample, "stale_after_seconds", None)

    result = collect_remote_telemetry(
        endpoint="http://127.0.0.1:8765",
        auth_env="ANVIL_CONTROLLER_TOKEN",
        expected_host_id="generic-macos-host",
        capabilities=("host-resources",),
        environment={"ANVIL_CONTROLLER_TOKEN": TOKEN},
        opener=_opener_for([sample], {}),
        received_at=NOW,
    )[0]

    assert result.capability_status is CapabilityStatus.FAILED


def test_materially_future_remote_timestamp_fails_closed() -> None:
    sample = _remote_sample()
    future = NOW + timedelta(minutes=1)
    object.__setattr__(sample, "source_timestamp", future)
    object.__setattr__(sample, "collection_timestamp", future)

    result = collect_remote_telemetry(
        endpoint="http://127.0.0.1:8765",
        auth_env="ANVIL_CONTROLLER_TOKEN",
        expected_host_id="generic-macos-host",
        capabilities=("host-resources",),
        environment={"ANVIL_CONTROLLER_TOKEN": TOKEN},
        opener=_opener_for([sample], {}),
        received_at=NOW,
    )[0]

    assert result.capability_status is CapabilityStatus.FAILED
