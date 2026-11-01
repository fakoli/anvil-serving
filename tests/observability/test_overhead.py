from __future__ import annotations


from anvil_serving.observability.benchmark.overhead import (
    BenchmarkOutcome,
    ResourceObservation,
    evaluate_overhead,
    publish_overhead_result,
)


MIB = 1024 * 1024


def _observations(*, cpu=0.5, rss=50 * MIB, disk=0, gpu=0):
    return [
        ResourceObservation(
            timestamp_seconds=index,
            cpu_percent=cpu,
            rss_bytes=rss,
            disk_write_bytes=disk * index,
            docker_requests=index,
            network_requests=index * 2,
            gpu_allocated_bytes=gpu,
            subprocess_cpu_percent=3 if index == 1 else 0,
            subprocess_rss_bytes=10 * MIB if index == 1 else 0,
        )
        for index in range(3)
    ]


def test_sanitized_result_is_external_published_and_indexed(tmp_path) -> None:
    repo = tmp_path / "repo"
    findings = repo / "docs" / "findings"
    findings.mkdir(parents=True)
    index = findings / "README.md"
    index.write_text(
        "# Findings\n\n| Date | File | Subject |\n|------|------|---------|\n",
        encoding="utf-8",
    )
    result = evaluate_overhead(
        "normal",
        _observations(),
        collection_off=BenchmarkOutcome(100, 1),
        collection_on=BenchmarkOutcome(100, 1),
    )
    result["note"] = "secret-value"

    manifest = publish_overhead_result(
        {"normal": result},
        evidence_root=tmp_path / "private",
        repo_root=repo,
        finding_path=findings / "2026-07-11-overhead.md",
        findings_index=index,
        secrets=("secret-value",),
    )

    assert manifest["passed"] is True
    assert manifest["sha256"] in (findings / "2026-07-11-overhead.md").read_text()
    assert (
        b"secret-value"
        not in (tmp_path / "private" / "observability-overhead.json.gz").read_bytes()
    )
    assert "](2026-07-11-overhead.md)" in index.read_text(encoding="utf-8")
