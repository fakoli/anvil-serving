"""Critical metadata-only router telemetry contracts."""

import pytest

from anvil_serving.router.decision_log import AttemptRecord, DecisionRecord
from anvil_serving.router.router_telemetry import render_capacity_prometheus, render_prometheus


def _record(*, route="llm.primary", latency_ms=0):
    return DecisionRecord(
        kind="chat", requested_tier="primary-local",
        attempts=(AttemptRecord("primary-local", True, "served", 3, 2, "served"),),
        served_tier="primary-local", total_prompt_tokens=3, total_completion_tokens=2,
        route=route, request_bytes=10, response_bytes=20, latency_ms=latency_ms,
    )


def test_prometheus_is_gauge_only_and_escapes_safe_model_labels():
    payload = render_prometheus((_record(route='model"\\name', latency_ms=15),))
    assert "# TYPE anvil_router_decision_buffer_requests gauge" in payload
    assert "anvil_router_decision_buffer_requests{model=\"\",outcome=\"succeeded\"} 1" in payload
    assert 'model="model\\"\\\\name"' in payload
    assert "# TYPE anvil_router_decision_buffer_latency_ms_p95 gauge" in payload
    assert "anvil_router_decision_buffer_latency_ms_p95{model=\"\"} 15" in payload
    assert "unsafe\\nroute" not in payload


def _replica_capacity_snapshot():
    return {"data": [{
        "id": "primary-local", "aliases": ["llm.primary"], "loaded": True,
        "capacity": {"context_limit_tokens": 262_144}, "multimodal": {}, "live": {},
        "admission": {
            "status": "available", "state": "admitting", "active_requests": 1,
            "draining": False, "member_active_requests": {"member-a": 1, "member-b": 0},
            "max_concurrency": 7,
            "members": [
                {"id": "member-a", "state": "admitting", "active_requests": 1, "max_concurrency": 4, "draining": False},
                {"id": "member-b", "state": "admitting", "active_requests": 0, "max_concurrency": 3, "draining": False},
            ],
        },
        "members": [
            {"id": "member-a", "freshness": "fresh", "pressure_ppm": 625_000, "requests_state": "valid", "kv_state": "missing"},
            {"id": "member-b", "freshness": "stale", "pressure_ppm": None, "requests_state": "valid", "kv_state": "valid"},
        ],
    }]}


@pytest.mark.parametrize("mutate", [
    lambda row: row["admission"].update(active_requests=True),
    lambda row: row["admission"]["members"][0].update(max_concurrency=100_001),
    lambda row: row["members"][0].update(pressure_ppm=2_000_000_000_000_001),
    lambda row: row["members"][0].update(freshness="stale"),
    lambda row: row["members"][1].update(id="private/value"),
])
def test_capacity_prometheus_rejects_malformed_replica_groups(mutate):
    snapshot = _replica_capacity_snapshot()
    mutate(snapshot["data"][0])
    payload = render_capacity_prometheus(snapshot)
    assert "anvil_router_replica_" not in payload
    assert "private/value" not in payload


def test_capacity_prometheus_enforces_per_tier_and_total_member_bounds():
    rows = []
    for tier_index in range(17):
        tier_id = f"tier-{tier_index}"
        member_ids = [f"member-{index}" for index in range(16)]
        rows.append({
            "id": tier_id, "aliases": [],
            "admission": {
                "status": "available", "state": "admitting", "active_requests": 0, "draining": False,
                "member_active_requests": {member_id: 0 for member_id in member_ids}, "max_concurrency": 16,
                "members": [{"id": member_id, "state": "admitting", "active_requests": 0, "max_concurrency": 1, "draining": False} for member_id in member_ids],
            },
            "members": [{"id": member_id, "freshness": "unknown", "pressure_ppm": None, "requests_state": "missing", "kv_state": "missing"} for member_id in member_ids],
        })
    payload = render_capacity_prometheus({"data": rows})
    assert payload.count("anvil_router_replica_member_active_requests{tier=") == 256
    assert 'tier="tier-15"' in payload
    assert 'tier="tier-16"' not in payload
    oversized = rows[0].copy()
    oversized["admission"] = rows[0]["admission"].copy()
    oversized["admission"]["members"] = rows[0]["admission"]["members"] + [{"id": "member-extra", "state": "admitting", "active_requests": 0, "max_concurrency": 1, "draining": False}]
    oversized["admission"]["member_active_requests"] = {**rows[0]["admission"]["member_active_requests"], "member-extra": 0}
    oversized["members"] = rows[0]["members"] + [{"id": "member-extra", "freshness": "unknown", "pressure_ppm": None, "requests_state": "missing", "kv_state": "missing"}]
    assert "anvil_router_replica_" not in render_capacity_prometheus({"data": [oversized]})
