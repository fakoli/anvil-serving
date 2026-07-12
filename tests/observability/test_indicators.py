from __future__ import annotations

from datetime import datetime, timedelta, timezone

from anvil_serving.observability.dashboard.indicators import build_indicators
from anvil_serving.observability.retention import RetentionStore


NOW = datetime(2026, 7, 11, 20, 0, tzinfo=timezone.utc)


def _sample(metric, value, *, labels=None, status="ok", unit="bytes", stale=False):
    return {
        "host_id": "dark",
        "metric": metric,
        "value": value,
        "unit": unit,
        "capability_status": status,
        "labels": labels or {},
        "freshness": {"is_stale": stale},
    }


def test_pressure_health_and_freshness_are_explicit() -> None:
    snapshot = {
        "samples": [
            _sample("host.memory.pressure", 92, unit="percent"),
            _sample(
                "service.health",
                False,
                labels={
                    "component": "router",
                    "health": "failed",
                    "served_identity": "anvil-router",
                    "owning_host": "dark",
                },
                status="failed",
            ),
            _sample("gpu.utilization", 20, status="stale", unit="percent", stale=True),
        ]
    }

    indicators = build_indicators(snapshot)

    assert indicators["pressure"][0]["severity"] == "critical"
    assert indicators["health"][0]["component"] == "router"
    assert indicators["freshness"][0]["stale"] is True


def test_workload_ownership_never_upgrades_ambiguous_attribution() -> None:
    snapshot = {
        "samples": [
            _sample(
                "container.memory.used",
                100,
                labels={"container_name": "model-fast", "attribution": "configured"},
            ),
            _sample(
                "gpu.process.memory.used",
                50,
                labels={"process": "python", "attribution": "ambiguous"},
            ),
        ]
    }

    ownership = build_indicators(snapshot)["ownership"]

    assert ownership[0]["owner"] == "model-fast"
    assert ownership[0]["attribution"] == "reliable"
    assert ownership[1]["owner"] == "python"
    assert ownership[1]["attribution"] == "inferred"


def test_model_loading_transition_distinguishes_shared_to_vram() -> None:
    store = RetentionStore()
    before = {
        "samples": [
            _sample("host.memory.used", 1000),
            _sample("gpu.memory.shared.used", 600),
            _sample("gpu.memory.used", 100),
        ]
    }
    after = {
        "samples": [
            _sample("host.memory.used", 900),
            _sample("gpu.memory.shared.used", 200),
            _sample("gpu.memory.used", 500),
        ]
    }
    store.add(before, observed_at=NOW, probe_duration_seconds=0.1, expected_interval_seconds=2)
    store.add(
        after,
        observed_at=NOW + timedelta(seconds=2),
        probe_duration_seconds=0.1,
        expected_interval_seconds=2,
    )

    loading = build_indicators(after, retention=store)["model_loading"]

    assert loading == {
        "phase": "shared-to-vram",
        "attribution": "inferred",
        "host_memory": 900.0,
        "shared_gpu_memory": 200.0,
        "dedicated_vram": 500.0,
    }
