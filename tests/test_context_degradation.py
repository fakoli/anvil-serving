from anvil_serving.benchmarking.context import (
    CONTEXT_OBSERVATION_SCHEMA,
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
