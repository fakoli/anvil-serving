from anvil_serving.benchmarking.context import (
    CONTEXT_OBSERVATION_SCHEMA,
    summarize_context_degradation,
)


def observation(bucket, passed, *, latency=None, throughput=None, failure=None, telemetry=None):
    return {
        "schema": CONTEXT_OBSERVATION_SCHEMA,
        "case_id": f"case-{bucket}-{passed}",
        "case_type": "native-needle",
        "requested_tokens": bucket,
        "prompt_tokens": bucket - 10,
        "token_measurement": "usage",
        "position": 0.5,
        "target_count": 1,
        "distractor_count": 0,
        "passed": passed,
        "completed": failure is None,
        "visible_answer": "answer" if passed else "wrong",
        "latency_ms": latency,
        "throughput_tps": throughput,
        "finish_reason": "stop" if failure is None else None,
        "failure": failure,
        "engine_telemetry": telemetry,
    }


def test_curve_reports_first_drop_and_preserves_lower_evidence():
    rows = [
        observation(8192, True, latency=100, throughput=20),
        observation(8192, True, latency=110, throughput=18),
        observation(32768, True, latency=400),
        observation(32768, False, latency=450),
        observation(640000, False, failure={"code": "context_overflow"}),
    ]
    curve = summarize_context_degradation(
        rows,
        scoring={
            "baseline_bucket": 8192,
            "pass_rate_floor": 0.7,
            "max_relative_drop": 0.2,
        },
        advertised_context=650000,
    )
    assert curve["effective_context"] == 8192
    assert curve["first_material_degradation"]["requested_tokens"] == 32768
    assert curve["advertised_context"] == 650000
    assert curve["attempted_buckets"] == [8192, 32768, 640000]
    assert curve["buckets"][0]["sample_count"] == 2
    assert len(curve["buckets"][0]["samples"]) == 2
    assert curve["buckets"][2]["failures"] == {"context_overflow": 1}


def test_missing_telemetry_is_unavailable_not_inferred():
    curve = summarize_context_degradation(
        [observation(8192, True, latency=100)],
        scoring={
            "baseline_bucket": 8192,
            "pass_rate_floor": 0.7,
            "max_relative_drop": 0.2,
        },
    )
    bucket = curve["buckets"][0]
    assert bucket["latency_ms"]["available"] is True
    assert bucket["engine_telemetry"] == {"available": False, "observations": []}
    assert bucket["throughput_tps"] == {
        "available": False,
        "mean": None,
        "observations": [],
    }


def test_failed_near_limit_does_not_become_measured_context():
    curve = summarize_context_degradation(
        [
            observation(8192, True),
            observation(131072, True),
            observation(640000, False, failure={"code": "timeout"}),
        ],
        scoring={
            "baseline_bucket": 8192,
            "pass_rate_floor": 0.7,
            "max_relative_drop": 0.2,
        },
        advertised_context=650000,
    )
    assert curve["effective_context"] == 131072
    assert curve["effective_context"] != curve["advertised_context"]
