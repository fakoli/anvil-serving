import json
from pathlib import Path


FIXTURE = Path(__file__).with_name("voice_latency_model_ab_matrix.json")
TIMING_KEYS = {"ttfa_ms", "turn_latency_ms", "stt_ms", "llm_ms", "tts_ms"}


def _load_matrix():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_voice_latency_matrix_records_required_artifact_fields():
    matrix = _load_matrix()
    assert matrix["schema_version"] == "anvil-serving.voice-latency-model-ab.matrix/v1"
    assert matrix["source_revision"]["git_commit"]
    assert matrix["prompt_sets"]
    assert matrix["artifacts"]

    for artifact in matrix["artifacts"]:
        assert artifact["profile"]
        assert artifact["prompt_set_id"]
        assert artifact["source_revision"]["git_commit"]
        assert artifact["candidate_identity"]["candidate_id"]
        assert artifact["route_identity"]["provider"]
        assert set(artifact["stage_timings_ms"]) == TIMING_KEYS
        assert isinstance(artifact["errors"], list)
        assert isinstance(artifact["turn_shape"], dict)


def test_voice_latency_matrix_retains_failed_candidates_and_tool_turn():
    matrix = _load_matrix()
    artifacts = matrix["artifacts"]
    failed = [a for a in artifacts if a["status"] in {"failed_unavailable", "blocked_unavailable"}]
    assert failed
    assert all(a["errors"] for a in failed)

    candidate_profiles = {
        a["profile"]
        for a in artifacts
        if a["candidate_identity"]["role"] == "candidate"
    }
    assert {
        "candidate-qwen3-32b",
        "candidate-gemma4-12b",
        "candidate-gemma4-e4b",
    } <= candidate_profiles

    assert any(
        a["turn_shape"].get("tool_relevant")
        and "weather" in a["turn_shape"].get("user_turn", "").lower()
        for a in artifacts
    )
    assert matrix["summary"]["failed_candidates_retained"] is True
    assert matrix["summary"]["tool_relevant_turn_included"] is True


def test_voice_latency_matrix_has_measured_baseline_timing():
    matrix = _load_matrix()
    baseline = next(a for a in matrix["artifacts"] if a["artifact_id"] == "baseline-mini-prior-rerun")
    assert baseline["status"] == "measured_prior"
    assert baseline["profile"] == "mini-audio"
    assert baseline["errors"] == []
    assert baseline["stage_timings_ms"]["ttfa_ms"] > 0
    assert baseline["stage_timings_ms"]["llm_ms"] > 0
