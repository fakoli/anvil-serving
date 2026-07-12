from __future__ import annotations

import math

import pytest

from anvil_serving.observability.benchmark.overhead import (
    BenchmarkOutcome,
    ResourceObservation,
    evaluate_overhead,
    measure_callable,
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


def test_normal_and_benchmark_profiles_enforce_fixed_limits() -> None:
    normal = evaluate_overhead(
        "normal",
        _observations(cpu=1, rss=100 * MIB),
        collection_off=BenchmarkOutcome(100, 1),
        collection_on=BenchmarkOutcome(100.5, 1.005),
    )
    benchmark = evaluate_overhead(
        "benchmark",
        _observations(cpu=2, rss=150 * MIB),
        collection_off=BenchmarkOutcome(100, 1),
        collection_on=BenchmarkOutcome(99.5, 1.005),
    )

    assert normal["passed"] is True
    assert benchmark["passed"] is True
    assert normal["limits"]["max_rss_bytes"] == 100 * MIB
    assert benchmark["limits"]["max_average_cpu_percent"] == 2
    assert benchmark["metrics"]["subprocess_spikes"] == {
        "cpu_percent": 3,
        "rss_bytes": 10 * MIB,
    }


def test_every_limit_breach_fails_without_relaxation() -> None:
    result = evaluate_overhead(
        "benchmark",
        _observations(cpu=2.01, rss=150 * MIB + 1, disk=1, gpu=1),
        collection_off=BenchmarkOutcome(100, 1),
        collection_on=BenchmarkOutcome(98.9, 1.011),
    )

    assert result["passed"] is False
    assert len(result["failures"]) == 6
    assert result["limits"]["max_average_cpu_percent"] == 2
    assert result["limits"]["max_benchmark_effect_percent"] == 1


def test_sustained_averages_peaks_and_api_costs_are_reported() -> None:
    samples = _observations()
    samples[1] = ResourceObservation(1, 1, 75 * MIB, 0, 7, 11, 0, 5, 12 * MIB)
    samples[2] = ResourceObservation(2, 0.5, 50 * MIB, 0, 9, 12, 0)
    result = evaluate_overhead(
        "normal",
        samples,
        collection_off=BenchmarkOutcome(100, 1),
        collection_on=BenchmarkOutcome(100, 1),
    )

    assert result["metrics"]["cpu_percent"] == {"average": 2 / 3, "peak": 1}
    assert result["metrics"]["rss_bytes"]["peak"] == 75 * MIB
    assert result["metrics"]["docker_requests"]["peak"] == 7
    assert result["metrics"]["network_requests"]["peak"] == 11


def test_repeatable_measurement_uses_bounded_duration_and_provider() -> None:
    calls = {"workload": 0, "provider": 0}

    def workload():
        calls["workload"] += 1

    def provider():
        calls["provider"] += 1
        return {"cpu_percent": 0, "rss_bytes": 1}

    observations = measure_callable(workload, provider, duration_seconds=0.1, interval_seconds=0.02)

    assert len(observations) >= 2
    assert calls["workload"] == calls["provider"] == len(observations)


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf, True])
def test_malformed_or_non_finite_measurements_fail_closed(value) -> None:
    with pytest.raises(ValueError):
        ResourceObservation(value, 0, 0, 0, 0, 0, 0)
    with pytest.raises(ValueError):
        BenchmarkOutcome(value, 1)


def test_counter_resets_and_timestamp_reordering_fail_closed() -> None:
    observations = _observations()
    observations[1] = ResourceObservation(0, 0, 0, 0, 0, 0, 0)
    with pytest.raises(ValueError, match="timestamps"):
        evaluate_overhead(
            "normal",
            observations,
            collection_off=BenchmarkOutcome(1, 1),
            collection_on=BenchmarkOutcome(1, 1),
        )


def test_fractional_byte_counters_fail_closed() -> None:
    with pytest.raises(ValueError, match="integer"):
        ResourceObservation(0, 0, 0.5, 0, 0, 0, 0)


def test_publication_rejects_empty_results_without_orphaning_raw_evidence(tmp_path) -> None:
    with pytest.raises(ValueError, match="at least one"):
        publish_overhead_result(
            {},
            evidence_root=tmp_path / "private",
            repo_root=tmp_path / "repo",
            finding_path=tmp_path / "repo/docs/findings/result.md",
            findings_index=tmp_path / "repo/docs/findings/README.md",
        )

    assert not (tmp_path / "private/observability-overhead.json.gz").exists()


def test_publication_rejects_malformed_pass_state_before_writing(tmp_path) -> None:
    findings = tmp_path / "repo/docs/findings"
    findings.mkdir(parents=True)
    (findings / "README.md").write_text(
        "| Date | File | Subject |\n|------|------|---------|\n", encoding="utf-8"
    )
    with pytest.raises(ValueError, match="pass state"):
        publish_overhead_result(
            {"normal": {"profile": "normal", "passed": "false"}},
            evidence_root=tmp_path / "private",
            repo_root=tmp_path / "repo",
            finding_path=findings / "result.md",
            findings_index=findings / "README.md",
        )

    assert not (tmp_path / "private/observability-overhead.json.gz").exists()

    observations = _observations()
    observations[2] = ResourceObservation(2, 0, 0, 0, 0, 0, 0)
    with pytest.raises(ValueError, match="docker_requests"):
        evaluate_overhead(
            "normal",
            observations,
            collection_off=BenchmarkOutcome(1, 1),
            collection_on=BenchmarkOutcome(1, 1),
        )


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
