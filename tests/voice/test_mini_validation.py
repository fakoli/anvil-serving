from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace


def test_report_flag_uses_default_path():
    from scripts.voice import mini_validation

    args = mini_validation.build_parser().parse_args(["--report"])

    assert args.report == mini_validation.DEFAULT_REPORT_PATH
    assert Path(args.report).is_absolute()


def test_validate_one_serve_probes_readiness_after_unconfigured_bringup():
    from anvil_serving.voice.serves._common import ServeNotConfigured
    from scripts.voice import mini_validation

    class FakeServe:
        def __init__(self) -> None:
            self.wait_ready_called = False

        def bring_up(self) -> None:
            raise ServeNotConfigured("missing manifest")

        def wait_ready(self, *, timeout: float):
            self.wait_ready_called = True
            assert timeout == 1.0
            return SimpleNamespace(
                ready=True,
                docker_state="unconfigured",
                detail="healthy",
            )

    serve = FakeServe()
    result = mini_validation.validate_one_serve(
        "stt",
        SimpleNamespace(serve_name="stt"),
        serve,
        ready_timeout=1.0,
        serves_manifest_path=None,
    )

    assert serve.wait_ready_called is True
    assert result.bring_up_ok is False
    assert result.ready is True
    assert result.docker_state == "unconfigured"
    assert "missing manifest" in (result.error or "")


def test_validate_one_serve_records_nonzero_bringup_returncode():
    from scripts.voice import mini_validation

    class FakeServe:
        def bring_up(self) -> int:
            return 7

        def wait_ready(self, *, timeout: float):
            return SimpleNamespace(
                ready=True,
                docker_state="running",
                detail="healthy",
            )

    result = mini_validation.validate_one_serve(
        "tts",
        SimpleNamespace(serve_name="tts"),
        FakeServe(),
        ready_timeout=1.0,
        serves_manifest_path=None,
    )

    assert result.bring_up_ok is False
    assert result.bring_up_returncode == 7
    assert "nonzero exit code 7" in (result.error or "")


def test_build_verdict_supported_for_16gb_ready_remote_benchmark():
    from scripts.voice import mini_validation

    verdict = mini_validation.build_verdict({
        "host_memory_after_load": {"total_gb": 16.0, "available_gb": 4.0},
        "host_matches_expected_mini": True,
        "stt": {
            "ready": True,
            "bring_up_ok": True,
            "container_mem_after_benchmark": "1GiB / 16GiB",
        },
        "tts": {
            "ready": True,
            "bring_up_ok": True,
            "container_mem_after_benchmark": "512MiB / 16GiB",
        },
        "stt_local_endpoint": True,
        "tts_local_endpoint": True,
        "llm_routed_remote": True,
        "llm_endpoint_is_fakoli_dark": True,
        "llm_auth_env": "ANVIL_ROUTER_TOKEN",
        "llm_auth_env_present": True,
        "route_proof": {"ok": True},
        "benchmark": {
            "ttfa_ms": 100.0,
            "turn_latency_ms": 500.0,
            "tts_first_audio_observed": True,
            "tts_output_bytes": 3200,
            "tts_rtf": 0.2,
        },
        "benchmark_error": None,
    })

    assert verdict["status"] == "supported"
    assert verdict["failure_modes"] == []


def test_build_verdict_rejects_healthy_non_target_host():
    from scripts.voice import mini_validation

    verdict = mini_validation.build_verdict({
        "host_memory_after_load": {"total_gb": 96.0, "available_gb": 60.0},
        "host_matches_expected_mini": True,
        "stt": {"ready": True, "bring_up_ok": True, "container_mem_after_benchmark": "1GiB / 96GiB"},
        "tts": {"ready": True, "bring_up_ok": True, "container_mem_after_benchmark": "512MiB / 96GiB"},
        "stt_local_endpoint": True,
        "tts_local_endpoint": True,
        "llm_routed_remote": True,
        "llm_endpoint_is_fakoli_dark": True,
        "llm_auth_env": "ANVIL_ROUTER_TOKEN",
        "llm_auth_env_present": True,
        "route_proof": {"ok": True},
        "benchmark": {
            "ttfa_ms": 100.0,
            "turn_latency_ms": 500.0,
            "tts_first_audio_observed": True,
            "tts_output_bytes": 3200,
            "tts_rtf": 0.2,
        },
        "benchmark_error": None,
    })

    assert verdict["status"] == "unsupported"
    assert "host_not_16gb_class" in verdict["failure_modes"]


def test_build_verdict_rejects_unmeasured_host_memory():
    from scripts.voice import mini_validation

    verdict = mini_validation.build_verdict({
        "host_memory_after_load": {"total_gb": None, "available_gb": None},
        "host_matches_expected_mini": True,
        "stt": {"ready": True, "bring_up_ok": True, "container_mem_after_benchmark": "1GiB / 16GiB"},
        "tts": {"ready": True, "bring_up_ok": True, "container_mem_after_benchmark": "512MiB / 16GiB"},
        "stt_local_endpoint": True,
        "tts_local_endpoint": True,
        "llm_routed_remote": True,
        "llm_endpoint_is_fakoli_dark": True,
        "llm_auth_env": "ANVIL_ROUTER_TOKEN",
        "llm_auth_env_present": True,
        "route_proof": {"ok": True},
        "benchmark": {
            "ttfa_ms": 100.0,
            "turn_latency_ms": 500.0,
            "tts_first_audio_observed": True,
            "tts_output_bytes": 3200,
            "tts_rtf": 0.2,
        },
        "benchmark_error": None,
    })

    assert verdict["status"] == "unsupported"
    assert "host_memory_unmeasured" in verdict["failure_modes"]


def test_build_verdict_rejects_non_target_failure_as_unsupported():
    from scripts.voice import mini_validation

    verdict = mini_validation.build_verdict({
        "host_memory_after_load": {"total_gb": 96.0, "available_gb": 60.0},
        "host_matches_expected_mini": False,
        "stt": {"ready": False},
        "tts": {"ready": True},
        "stt_local_endpoint": True,
        "tts_local_endpoint": True,
        "llm_routed_remote": False,
        "llm_endpoint_is_fakoli_dark": False,
        "llm_auth_env": None,
        "llm_auth_env_present": False,
        "route_proof": {"ok": False},
        "benchmark": None,
        "benchmark_error": "connection refused",
    })

    assert verdict["status"] == "unsupported"
    assert "stt_not_ready" in verdict["failure_modes"]
    assert "llm_not_routed_to_remote_fakoli_dark" in verdict["failure_modes"]
    assert "benchmark_error" in verdict["failure_modes"]
    assert any("not a 16GB-class" in reason for reason in verdict["reasons"])


def test_build_verdict_rejects_missing_memory_audio_and_auth():
    from scripts.voice import mini_validation

    verdict = mini_validation.build_verdict({
        "host_memory_after_load": {"total_gb": 16.0, "available_gb": 4.0},
        "host_matches_expected_mini": True,
        "stt": {"ready": True, "bring_up_ok": True, "container_mem_after_benchmark": None},
        "tts": {"ready": True, "bring_up_ok": True, "container_mem_after_benchmark": None},
        "stt_local_endpoint": True,
        "tts_local_endpoint": True,
        "llm_routed_remote": True,
        "llm_endpoint_is_fakoli_dark": False,
        "llm_auth_env": "ANVIL_ROUTER_TOKEN",
        "llm_auth_env_present": False,
        "route_proof": {"ok": True},
        "benchmark": {
            "ttfa_ms": 100.0,
            "turn_latency_ms": 500.0,
            "tts_first_audio_observed": False,
            "tts_output_bytes": 0,
            "tts_rtf": None,
        },
        "benchmark_error": None,
    })

    assert verdict["status"] == "unsupported"
    assert "stt_container_memory_missing" in verdict["failure_modes"]
    assert "tts_container_memory_missing" in verdict["failure_modes"]
    assert "first_audio_missing" in verdict["failure_modes"]
    assert "llm_not_routed_to_remote_fakoli_dark" in verdict["failure_modes"]
    assert "llm_auth_token_unset" in verdict["failure_modes"]


def test_main_writes_report_and_appends_row(monkeypatch, tmp_path):
    from scripts.voice import mini_validation

    monkeypatch.setattr(
        mini_validation.voice_config,
        "load_manifest",
        lambda path: {"voice": {"name": "test"}},
    )
    result = {
        "measured_at": "2026-07-06T00:00:00Z",
        "hostname": "mini",
        "host_memory_after_load": {"total_gb": 16.0, "used_gb": 8.0, "available_gb": 8.0},
        "host_is_16gb_class": True,
        "host_matches_expected_mini": True,
        "verdict": {"status": "supported", "failure_modes": [], "reasons": ["all required Mini validation checks passed"]},
        "stt": {"ready": True, "container_mem": "1GiB / 16GiB", "error": None},
        "tts": {"ready": True, "container_mem": "512MiB / 16GiB", "error": None},
        "benchmark": {"ttfa_ms": 100.0, "turn_latency_ms": 400.0},
        "benchmark_error": None,
    }
    monkeypatch.setattr(
        mini_validation,
        "run_validation",
        lambda data, *, ready_timeout, serves_manifest_path, target_host_pattern: result,
    )
    rows: list[str] = []
    def append_row(row: str) -> bool:
        rows.append(row)
        return True

    monkeypatch.setattr(mini_validation, "append_finding_row", append_row)
    report = tmp_path / "mini-report.json"

    rc = mini_validation.main(["--config", "ignored.toml", "--report", str(report)])

    assert rc == 0
    assert report.exists()
    assert '"status": "supported"' in report.read_text(encoding="utf-8")
    assert rows and "| 2026-07-06T00:00:00Z | mini | 16.0 GB; 16gb_class=True | supported |" in rows[0]


def test_main_does_not_append_without_report(monkeypatch):
    from scripts.voice import mini_validation

    monkeypatch.setattr(
        mini_validation.voice_config,
        "load_manifest",
        lambda path: {"voice": {"name": "test"}},
    )
    monkeypatch.setattr(
        mini_validation,
        "run_validation",
        lambda data, *, ready_timeout, serves_manifest_path, target_host_pattern: {
            "measured_at": "2026-07-06T00:00:00Z",
            "hostname": "mini",
            "host_memory_after_load": {"total_gb": 16.0, "used_gb": 8.0, "available_gb": 8.0},
            "host_is_16gb_class": True,
            "verdict": {"status": "supported", "failure_modes": [], "reasons": []},
            "stt": {"ready": True, "container_mem": "1GiB / 16GiB", "error": None},
            "tts": {"ready": True, "container_mem": "512MiB / 16GiB", "error": None},
            "benchmark": {"ttfa_ms": 100.0, "turn_latency_ms": 400.0},
            "benchmark_error": None,
        },
    )

    def fail_append(row: str) -> bool:
        raise AssertionError("append should not run without --report")

    monkeypatch.setattr(mini_validation, "append_finding_row", fail_append)

    assert mini_validation.main(["--config", "ignored.toml"]) == 0


def test_main_returns_nonzero_when_supported_report_row_cannot_append(monkeypatch, tmp_path):
    from scripts.voice import mini_validation

    monkeypatch.setattr(
        mini_validation.voice_config,
        "load_manifest",
        lambda path: {"voice": {"name": "test"}},
    )
    monkeypatch.setattr(
        mini_validation,
        "run_validation",
        lambda data, *, ready_timeout, serves_manifest_path, target_host_pattern: {
            "measured_at": "2026-07-06T00:00:00Z",
            "hostname": "mini",
            "host_memory_after_load": {"total_gb": 16.0, "used_gb": 8.0, "available_gb": 8.0},
            "host_is_16gb_class": True,
            "verdict": {"status": "supported", "failure_modes": [], "reasons": []},
            "stt": {"ready": True, "container_mem": "1GiB / 16GiB", "error": None},
            "tts": {"ready": True, "container_mem": "512MiB / 16GiB", "error": None},
            "benchmark": {"ttfa_ms": 100.0, "turn_latency_ms": 400.0},
            "benchmark_error": None,
        },
    )
    monkeypatch.setattr(mini_validation, "append_finding_row", lambda row: False)

    rc = mini_validation.main(["--config", "ignored.toml", "--report", str(tmp_path / "report.json")])

    assert rc == 1


def test_main_returns_nonzero_for_unsupported_without_override(monkeypatch, tmp_path):
    from scripts.voice import mini_validation

    monkeypatch.setattr(
        mini_validation.voice_config,
        "load_manifest",
        lambda path: {"voice": {"name": "test"}},
    )
    monkeypatch.setattr(
        mini_validation,
        "run_validation",
        lambda data, *, ready_timeout, serves_manifest_path, target_host_pattern: {
            "measured_at": "2026-07-06T00:00:00Z",
            "hostname": "mini",
            "host_memory_after_load": {"total_gb": 16.0, "used_gb": 8.0, "available_gb": 8.0},
            "host_is_16gb_class": True,
            "verdict": {"status": "unsupported", "failure_modes": ["stt_not_ready"], "reasons": []},
            "stt": {"ready": False, "container_mem": None, "error": "down"},
            "tts": {"ready": True, "container_mem": "512MiB / 16GiB", "error": None},
            "benchmark": None,
            "benchmark_error": "down",
        },
    )
    monkeypatch.setattr(mini_validation, "append_finding_row", lambda row: True)

    rc = mini_validation.main(["--config", "ignored.toml", "--report", str(tmp_path / "report.json")])

    assert rc == 1


def test_main_allows_unsupported_negative_control_with_flag(monkeypatch, tmp_path):
    from scripts.voice import mini_validation

    monkeypatch.setattr(
        mini_validation.voice_config,
        "load_manifest",
        lambda path: {"voice": {"name": "test"}},
    )
    monkeypatch.setattr(
        mini_validation,
        "run_validation",
        lambda data, *, ready_timeout, serves_manifest_path, target_host_pattern: {
            "measured_at": "2026-07-06T00:00:00Z",
            "hostname": "workstation",
            "host_memory_after_load": {"total_gb": 96.0, "used_gb": 30.0, "available_gb": 66.0},
            "host_is_16gb_class": False,
            "verdict": {"status": "unsupported", "failure_modes": ["host_not_16gb_class"], "reasons": []},
            "stt": {"ready": True, "container_mem": "1GiB / 96GiB", "error": None},
            "tts": {"ready": True, "container_mem": "512MiB / 96GiB", "error": None},
            "benchmark": {"ttfa_ms": 100.0, "turn_latency_ms": 400.0},
            "benchmark_error": None,
        },
    )
    monkeypatch.setattr(mini_validation, "append_finding_row", lambda row: True)

    rc = mini_validation.main([
        "--config", "ignored.toml",
        "--report", str(tmp_path / "report.json"),
        "--allow-unsupported",
    ])

    assert rc == 0
