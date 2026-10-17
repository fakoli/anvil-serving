"""Keep the published C4 KV accounting bound to its retained observations."""

import hashlib
import json
import math
from pathlib import Path


def test_fp4_kv_accounting_matches_sources():
    findings = Path(__file__).resolve().parents[1] / "docs" / "findings"
    bundle = findings / "2026-10-17-glm53-nvidia-fp4-promotion-evidence"
    accounting = bundle / "kv-accounting.json"
    data = json.loads(accounting.read_text())
    summary = json.loads((bundle / "summary.json").read_text())
    assert summary["kv_accounting"]["sha256"] == hashlib.sha256(accounting.read_bytes()).hexdigest()
    pool = data["pool"]
    config = bundle / pool["configured_byte_source"]["path"]
    assert hashlib.sha256(config.read_bytes()).hexdigest() == pool["configured_byte_source"]["sha256"]
    assert f"--kv-cache-memory-bytes {pool['bytes_per_gpu']}" in json.loads(config.read_text())["stdout"]
    assert pool["status"] == "configured"
    assert pool["total_bytes"] == pool["gpu_count"] * pool["bytes_per_gpu"]
    assert pool["total_gib"] == pool["total_bytes"] / 2**30
    assert len(data["rows"]) == 5
    for row in data["rows"]:
        assert row["requested_client_concurrency"] == 4
        assert row["occupancy_status"] == "measured" and row["gib_status"] == "estimated"
        fraction = row["sampled_peak_block_occupancy_fraction"]
        assert 0 <= fraction <= 1
        assert math.isclose(row["occupied_gib_estimate"], pool["total_gib"] * fraction)
        assert math.isclose(row["unused_pool_gib_estimate"], pool["total_gib"] * (1 - fraction))
        source = row["source"]
        if "path" in source:
            path = bundle / source["path"]
            assert hashlib.sha256(path.read_bytes()).hexdigest() == source["sha256"]
            native = json.loads(path.read_text())
            assert native["engine_sampling"]["kv_usage_fraction_max"] == fraction
            assert native["engine_sampling"]["active_max"] == row["sampled_engine_active_max"]
        else:
            assert any(item["role"] == source["private_source_role"] and item["sha256"] == source["sha256"]
                       for item in summary["source_summaries"])
