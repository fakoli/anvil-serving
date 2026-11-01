from __future__ import annotations


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


def _benchmark_sample_ok() -> dict:
    return {
        "source": "tts_endpoint",
        "text": "testing the local voice proof",
        "audio_bytes": 16000,
        "sample_rate": 16000,
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
        "llm_endpoint_is_primary_node": True,
        "llm_auth_env": "ANVIL_ROUTER_TOKEN",
        "llm_auth_env_present": True,
        "benchmark_sample": _benchmark_sample_ok(),
        "benchmark_sample_error": None,
        "benchmark": _benchmark_ok(),
        "benchmark_error": None,
    }


def test_build_verdict_rejects_missing_memory_audio_and_auth():
    from scripts.voice import mini_validation

    result = _supported_result()
    result.update({
        "stt": {"ready": True, "bring_up_ok": True, "container_mem_after_benchmark": None},
        "tts": {"ready": True, "bring_up_ok": True, "container_mem_after_benchmark": None},
        "llm_endpoint_is_primary_node": False,
        "llm_auth_env_present": False,
        "benchmark_sample": None,
        "benchmark_sample_error": "no audio",
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
    assert "llm_not_routed_to_remote_primary_node" in verdict["failure_modes"]
    assert "llm_auth_token_unset" in verdict["failure_modes"]
    assert "benchmark_sample_error" in verdict["failure_modes"]
    assert "benchmark_sample_missing" in verdict["failure_modes"]
