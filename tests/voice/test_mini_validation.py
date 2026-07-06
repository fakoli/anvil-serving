from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace


def _docker_proof(mem: str) -> dict:
    return {
        "source": "docker_stats",
        "container_mem": mem,
        "observed_after_benchmark": True,
        "model_probe": _model_probe(),
    }


def _model_probe(model: str = "model") -> dict:
    return {
        "ok": True,
        "expected_model": model,
        "model_ids": [model],
        "model_present": True,
    }


def _native_proof(*, pid: int, rss_mb: float, port: int) -> dict:
    return {
        "source": "macos_process_rss",
        "pid": pid,
        "rss_mb": rss_mb,
        "endpoint_host": "127.0.0.1",
        "port": port,
        "observed_after_benchmark": True,
        "model_probe": _model_probe(),
    }


def _benchmark_ok() -> dict:
    return {
        "ttfa_ms": 100.0,
        "turn_latency_ms": 500.0,
        "tts_first_audio_observed": True,
        "tts_output_bytes": 16000,
        "tts_audio_seconds": 0.5,
        "tts_source_sample_rate": 16000,
        "tts_rtf": 0.2,
        "stt_hypothesis": "hello world",
        "llm_reply": "reply",
    }


def _supported_result() -> dict:
    return {
        "platform": "darwin",
        "host_memory_after_load": {"total_gb": 16.0, "used_gb": 12.0, "available_gb": 4.0},
        "host_matches_expected_mini": True,
        "host_hw_model_matches_expected": True,
        "stt": {
            "ready": True,
            "bring_up_ok": True,
            "container_mem_after_benchmark": "1GiB / 16GiB",
            "memory_proof_after_benchmark": _docker_proof("1GiB / 16GiB"),
        },
        "tts": {
            "ready": True,
            "bring_up_ok": True,
            "container_mem_after_benchmark": "512MiB / 16GiB",
            "memory_proof_after_benchmark": _docker_proof("512MiB / 16GiB"),
        },
        "stt_local_endpoint": True,
        "tts_local_endpoint": True,
        "llm_routed_remote": True,
        "llm_endpoint_is_fakoli_dark": True,
        "llm_auth_env": "ANVIL_ROUTER_TOKEN",
        "llm_auth_env_present": True,
        "route_proof": {"ok": True},
        "route_auth_negative": {"auth_enforced": True},
        "benchmark": _benchmark_ok(),
        "benchmark_error": None,
    }


def test_report_flag_uses_default_path():
    from scripts.voice import mini_validation

    args = mini_validation.build_parser().parse_args(["--report"])

    assert args.report == mini_validation.DEFAULT_REPORT_PATH
    assert Path(args.report).is_absolute()


def test_default_config_prefers_mini_manifest():
    from scripts.voice import mini_validation

    assert Path(mini_validation.DEFAULT_CONFIG).name == "fakoli-mini.toml"


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


def test_validate_one_serve_external_lifecycle_skips_managed_bringup():
    from scripts.voice import mini_validation

    class FakeServe:
        def __init__(self) -> None:
            self.bring_up_called = False

        def bring_up(self) -> None:
            self.bring_up_called = True
            raise AssertionError("external lifecycle should not call bring_up")

        def wait_ready(self, *, timeout: float):
            assert timeout == 1.0
            return SimpleNamespace(
                ready=True,
                docker_state="unconfigured",
                detail="healthy external endpoint",
            )

    serve = FakeServe()
    result = mini_validation.validate_one_serve(
        "stt",
        SimpleNamespace(serve_name="stt", base_url="http://127.0.0.1:30010/v1"),
        serve,
        ready_timeout=1.0,
        serves_manifest_path=None,
        lifecycle="external",
    )

    assert serve.bring_up_called is False
    assert result.bring_up_ok is True
    assert result.lifecycle == "external"
    assert result.ready is True
    assert result.error is None


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

    verdict = mini_validation.build_verdict(_supported_result())

    assert verdict["status"] == "supported"
    assert verdict["failure_modes"] == []


def test_build_verdict_accepts_endpoint_attributed_native_memory():
    from scripts.voice import mini_validation

    result = _supported_result()
    result["stt"]["memory_proof_after_benchmark"] = _native_proof(pid=111, rss_mb=512.0, port=30010)
    result["tts"]["memory_proof_after_benchmark"] = _native_proof(pid=222, rss_mb=256.0, port=30011)

    verdict = mini_validation.build_verdict(result)

    assert verdict["status"] == "supported"
    assert verdict["failure_modes"] == []


def test_build_verdict_rejects_healthy_non_target_host():
    from scripts.voice import mini_validation

    result = _supported_result()
    result["host_memory_after_load"] = {"total_gb": 96.0, "used_gb": 36.0, "available_gb": 60.0}
    verdict = mini_validation.build_verdict(result)

    assert verdict["status"] == "unsupported"
    assert "host_not_16gb_class" in verdict["failure_modes"]


def test_build_verdict_rejects_unmeasured_host_memory():
    from scripts.voice import mini_validation

    result = _supported_result()
    result["host_memory_after_load"] = {"total_gb": None, "available_gb": None, "used_gb": None}
    verdict = mini_validation.build_verdict(result)

    assert verdict["status"] == "unsupported"
    assert "host_memory_unmeasured" in verdict["failure_modes"]
    assert "host_available_memory_unmeasured" in verdict["failure_modes"]


def test_build_verdict_rejects_non_target_failure_as_unsupported():
    from scripts.voice import mini_validation

    result = _supported_result()
    result.update({
        "host_memory_after_load": {"total_gb": 96.0, "used_gb": 36.0, "available_gb": 60.0},
        "host_matches_expected_mini": False,
        "stt": {"ready": False},
        "tts": {"ready": True},
        "llm_routed_remote": False,
        "llm_endpoint_is_fakoli_dark": False,
        "llm_auth_env": None,
        "llm_auth_env_present": False,
        "route_proof": {"ok": False},
        "route_auth_negative": {"auth_enforced": True},
        "benchmark": None,
        "benchmark_error": "connection refused",
    })
    verdict = mini_validation.build_verdict(result)

    assert verdict["status"] == "unsupported"
    assert "stt_not_ready" in verdict["failure_modes"]
    assert "llm_not_routed_to_remote_fakoli_dark" in verdict["failure_modes"]
    assert "benchmark_error" in verdict["failure_modes"]
    assert any("not a 16GB-class" in reason for reason in verdict["reasons"])


def test_build_verdict_rejects_missing_memory_audio_and_auth():
    from scripts.voice import mini_validation

    result = _supported_result()
    result.update({
        "stt": {"ready": True, "bring_up_ok": True, "container_mem_after_benchmark": None},
        "tts": {"ready": True, "bring_up_ok": True, "container_mem_after_benchmark": None},
        "llm_endpoint_is_fakoli_dark": False,
        "llm_auth_env_present": False,
        "route_auth_negative": {"auth_enforced": False},
        "benchmark": {
            "ttfa_ms": 100.0,
            "turn_latency_ms": 500.0,
            "tts_first_audio_observed": False,
            "tts_output_bytes": 0,
            "tts_audio_seconds": 0.0,
            "tts_rtf": None,
            "stt_hypothesis": "",
            "llm_reply": "",
        },
    })
    verdict = mini_validation.build_verdict(result)

    assert verdict["status"] == "unsupported"
    assert "stt_memory_missing" in verdict["failure_modes"]
    assert "tts_memory_missing" in verdict["failure_modes"]
    assert "first_audio_missing" in verdict["failure_modes"]
    assert "stt_hypothesis_missing" in verdict["failure_modes"]
    assert "llm_reply_missing" in verdict["failure_modes"]
    assert "tts_audio_too_short" in verdict["failure_modes"]
    assert "llm_not_routed_to_remote_fakoli_dark" in verdict["failure_modes"]
    assert "llm_auth_token_unset" in verdict["failure_modes"]
    assert "llm_auth_not_enforced" in verdict["failure_modes"]


def test_build_verdict_rejects_wrong_mini_host_even_when_otherwise_perfect():
    from scripts.voice import mini_validation

    result = _supported_result()
    result["host_matches_expected_mini"] = False
    verdict = mini_validation.build_verdict(result)

    assert verdict["status"] == "unsupported"
    assert "host_not_expected_mini" in verdict["failure_modes"]


def test_build_verdict_rejects_wrong_mini_hardware_model():
    from scripts.voice import mini_validation

    result = _supported_result()
    result["host_hw_model_matches_expected"] = False
    verdict = mini_validation.build_verdict(result)

    assert verdict["status"] == "unsupported"
    assert "host_hw_model_unmatched" in verdict["failure_modes"]


def test_build_verdict_rejects_non_macos_platform():
    from scripts.voice import mini_validation

    result = _supported_result()
    result["platform"] = "linux"
    verdict = mini_validation.build_verdict(result)

    assert verdict["status"] == "unsupported"
    assert "host_not_macos_mini" in verdict["failure_modes"]


def test_build_verdict_rejects_non_loopback_stt_or_tts_endpoint():
    from scripts.voice import mini_validation

    base = _supported_result()

    stt_bad = dict(base, stt_local_endpoint=False, tts_local_endpoint=True)
    tts_bad = dict(base, stt_local_endpoint=True, tts_local_endpoint=False)

    assert "stt_not_local_loopback" in mini_validation.build_verdict(stt_bad)["failure_modes"]
    assert "tts_not_local_loopback" in mini_validation.build_verdict(tts_bad)["failure_modes"]


def test_build_verdict_rejects_failed_route_proof_even_with_remote_url():
    from scripts.voice import mini_validation

    result = _supported_result()
    result["route_proof"] = {"ok": False, "validation_errors": ["wrong tier"]}
    verdict = mini_validation.build_verdict(result)

    assert verdict["status"] == "unsupported"
    assert "llm_not_routed_to_remote_fakoli_dark" in verdict["failure_modes"]


def test_build_verdict_rejects_missing_endpoint_model_proof():
    from scripts.voice import mini_validation

    result = _supported_result()
    result["stt"]["memory_proof_after_benchmark"]["model_probe"] = {
        "ok": True,
        "model_present": False,
        "model_ids": ["other"],
    }
    result["tts"]["memory_proof_after_benchmark"]["model_probe"] = {
        "ok": False,
        "model_present": False,
        "model_ids": [],
    }

    verdict = mini_validation.build_verdict(result)

    assert verdict["status"] == "unsupported"
    assert "stt_model_not_advertised" in verdict["failure_modes"]
    assert "tts_model_not_advertised" in verdict["failure_modes"]
    assert "stt_memory_missing" in verdict["failure_modes"]
    assert "tts_memory_missing" in verdict["failure_modes"]


def test_process_mem_for_endpoint_requires_exact_loopback_listener(monkeypatch):
    from scripts.voice import mini_validation

    monkeypatch.setattr(mini_validation.sys, "platform", "darwin")

    def fake_run(argv, **kwargs):
        if argv[0] == "lsof":
            return SimpleNamespace(
                returncode=0,
                stdout="p123\ncPython\nnTCP 127.0.0.1:30010 (LISTEN)\n",
                stderr="",
            )
        if argv[0] == "ps":
            return SimpleNamespace(
                returncode=0,
                stdout="204800 /usr/bin/python3 -m mlx_audio.server --port 30010\n",
                stderr="",
            )
        raise AssertionError(argv)

    proof = mini_validation.process_mem_for_endpoint("http://127.0.0.1:30010/v1", _run=fake_run)

    assert proof == {
        "source": "macos_process_rss",
        "pid": 123,
        "rss_mb": 200.0,
        "endpoint_host": "127.0.0.1",
        "port": 30010,
        "listener": "TCP 127.0.0.1:30010 (LISTEN)",
        "command": "/usr/bin/python3 -m mlx_audio.server --port 30010",
    }


def test_process_mem_for_endpoint_accepts_lsof_field_output_without_listen_suffix(monkeypatch):
    from scripts.voice import mini_validation

    monkeypatch.setattr(mini_validation.sys, "platform", "darwin")

    def fake_run(argv, **kwargs):
        if argv[0] == "lsof":
            return SimpleNamespace(
                returncode=0,
                stdout="p123\ncPython\nnTCP 127.0.0.1:30010\n",
                stderr="",
            )
        if argv[0] == "ps":
            return SimpleNamespace(
                returncode=0,
                stdout="204800 /usr/bin/python3 -m mlx_audio.server --port 30010\n",
                stderr="",
            )
        raise AssertionError(argv)

    proof = mini_validation.process_mem_for_endpoint("http://127.0.0.1:30010/v1", _run=fake_run)

    assert proof and proof["pid"] == 123
    assert proof["rss_mb"] == 200.0


def test_external_memory_proof_ignores_stale_docker_stats(monkeypatch):
    from scripts.voice import mini_validation

    monkeypatch.setattr(mini_validation, "container_mem_for_serve", lambda name, manifest: "9GiB / 16GiB")
    monkeypatch.setattr(
        mini_validation,
        "process_mem_for_endpoint",
        lambda base_url, *, _run: {
            "source": "macos_process_rss",
            "pid": 123,
            "rss_mb": 200.0,
            "endpoint_host": "127.0.0.1",
            "port": 30010,
        },
    )

    proof = mini_validation.memory_proof_for_serve(
        "stt",
        "http://127.0.0.1:30010/v1",
        "serves.toml",
        observed_after_benchmark=True,
        lifecycle="external",
        model_probe=_model_probe(),
    )

    assert proof and proof["source"] == "macos_process_rss"
    assert "container_mem" not in proof


def test_valid_memory_proof_rejects_unobserved_or_zero_native_rss():
    from scripts.voice import mini_validation

    assert not mini_validation.valid_memory_proof({
        "source": "macos_process_rss",
        "pid": 123,
        "rss_mb": 0,
        "endpoint_host": "127.0.0.1",
        "port": 30010,
        "observed_after_benchmark": True,
        "model_probe": _model_probe(),
    })
    assert not mini_validation.valid_memory_proof({
        "source": "macos_process_rss",
        "pid": 123,
        "rss_mb": 1.0,
        "endpoint_host": "127.0.0.1",
        "port": 30010,
        "observed_after_benchmark": False,
        "model_probe": _model_probe(),
    })
    assert not mini_validation.valid_memory_proof({
        "source": "macos_process_rss",
        "pid": 123,
        "rss_mb": 1.0,
        "endpoint_host": "127.0.0.1",
        "port": 30010,
        "observed_after_benchmark": True,
    })


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
        lambda data, *, ready_timeout, serves_manifest_path, target_host_pattern, target_hw_model_pattern: result,
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
        lambda data, *, ready_timeout, serves_manifest_path, target_host_pattern, target_hw_model_pattern: {
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
        lambda data, *, ready_timeout, serves_manifest_path, target_host_pattern, target_hw_model_pattern: {
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
        lambda data, *, ready_timeout, serves_manifest_path, target_host_pattern, target_hw_model_pattern: {
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


def test_main_returns_nonzero_for_experimental_without_override(monkeypatch, tmp_path):
    from scripts.voice import mini_validation

    monkeypatch.setattr(
        mini_validation.voice_config,
        "load_manifest",
        lambda path: {"voice": {"name": "test"}},
    )
    monkeypatch.setattr(
        mini_validation,
        "run_validation",
        lambda data, *, ready_timeout, serves_manifest_path, target_host_pattern, target_hw_model_pattern: {
            "measured_at": "2026-07-06T00:00:00Z",
            "hostname": "mini",
            "host_memory_after_load": {"total_gb": 16.0, "used_gb": 15.0, "available_gb": 1.0},
            "host_is_16gb_class": True,
            "verdict": {"status": "experimental", "failure_modes": [], "reasons": ["low headroom"]},
            "stt": {"ready": True, "container_mem": "1GiB / 16GiB", "error": None},
            "tts": {"ready": True, "container_mem": "512MiB / 16GiB", "error": None},
            "benchmark": {"ttfa_ms": 100.0, "turn_latency_ms": 400.0},
            "benchmark_error": None,
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
        lambda data, *, ready_timeout, serves_manifest_path, target_host_pattern, target_hw_model_pattern: {
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
