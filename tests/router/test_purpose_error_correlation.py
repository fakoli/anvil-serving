"""Real HTTP purpose failures retain their status and terminal correlation."""
from __future__ import annotations


import pytest

from anvil_serving.router_diagnostics import diagnose_record


@pytest.mark.parametrize("kind", ["embedding", "rerank"])
def test_unmeasured_purpose_latency_is_not_reported_as_measured_zero(kind):
    diagnosis = diagnose_record({"kind": kind, "latency_ms": 0})
    assert diagnosis["timing"]["latency_ms"] is None
