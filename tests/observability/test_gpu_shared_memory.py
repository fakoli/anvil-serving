from __future__ import annotations

from datetime import datetime, timezone

from anvil_serving.observability.probes.gpu_shared_memory import (
    collect_windows_shared_gpu_memory,
)
from anvil_serving.observability.schema import CapabilityStatus


NOW = datetime(2026, 7, 11, 20, 0, tzinfo=timezone.utc)


def test_permission_failure_is_explicit() -> None:
    def denied():
        raise PermissionError("Access is denied")

    sample = collect_windows_shared_gpu_memory(
        provider=denied, host_id="dark", collected_at=NOW
    )[0]

    assert sample.capability_status is CapabilityStatus.PERMISSION_DENIED
    assert sample.value is None
