"""Keep public voice headlines bound to the retained September 8 evidence."""
import hashlib
import json
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
FINDING = ROOT / "docs/findings/2026-09-08-m4-max-voice-refresh.md"
BUNDLE = FINDING.with_name(FINDING.stem + "-evidence")


def read(name):
    return json.loads((BUNDLE / name).read_text())


def test_artifact_manifest_binds_all_retained_files():
    manifest = read("artifact-manifest.json")
    assert len(manifest["artifact_roles"]) == 10
    for role in manifest["artifact_roles"]:
        assert role["status"] in {"retained", "missing", "not-applicable"}
        for item in role["files"]:
            path = (BUNDLE / item["path"]).resolve()
            assert path.is_relative_to(BUNDLE)
            raw = path.read_bytes()
            assert len(raw) == item["bytes"]
            assert hashlib.sha256(raw).hexdigest() == item["sha256"]


def test_spoken_headlines_match_native_attempts():
    summary = read("sanitized-quality-and-capacity.json")
    for name, label in [
        ("baseline-spoken-quality.json", "baseline_qwen3_4b"),
        ("candidate-spoken-quality.json", "qwen35_9b"),
        ("qwen36-spoken-quality.json", "qwen36_35b_a3b"),
    ]:
        checks = read("native/" + name)["suites"]["spoken-assistant-bounded-v1"]["checks"]
        attempts = [attempt for check in checks for attempt in check["attempts"]]
        passed = sum(attempt["status"] == "passed" for attempt in attempts)
        count = f"{passed}/{len(attempts)}"
        assert summary["spoken_assistant"][label]["strict"] == count
        assert count in FINDING.read_text()


def test_failed_capacity_is_never_eligible():
    native = read("native/candidate-capacity.json")
    derivative = read("sanitized-quality-and-capacity.json")["capacity"]
    assert native["completed"] == 0
    assert native["failed"] == len(native["failures"]) == 10
    assert native["performance_eligible"] is False
    assert derivative["performance_headline_eligible"] is False
    assert all(row["request_canary"]["passed"] for row in native["failures"])
    assert read("native/qwen36-preflight.json")["passed"] is False


def test_deployed_session_headline_and_limits_match_native_receipt():
    session = read("native/updated-live-final.session.json")
    completed = [response for response in session["responses"] if response["status"] == "completed"]
    assert len(completed) == 1
    summary = read("sanitized-voice-summary.json")["deployed_tts_realtime"]
    for key in ("ttfa_ms", "latency_ms"):
        assert summary["follow_up"][key] == completed[0][key]
        assert f"{completed[0][key]:.2f}" in FINDING.read_text()
    receipt = read("native/final-deployment-receipt.json")
    for key in ("model_changes", "rollback_execution_tested", "reboot_tested",
                "physical_microphone_or_openclaw_client_tested"):
        assert receipt[key] is False
    assert read("summary.json")["publication"]["current_live_state_verified"] is False


def test_publication_is_reachable_and_short_copy_is_bounded():
    for name in ["docs/findings/README.md", "docs/benchmarks/runs.md",
                 "docs/benchmarks/models/voice-llm-mlx.md", "docs/benchmarks/hardware/apple-m4-max.md"]:
        assert FINDING.name in (ROOT / name).read_text()
    text = (BUNDLE / "publication-summary.md").read_text()
    post = re.search(r"\*\*X / short post[^\n]+?\*\*\s*(.*?)\n\n", text, re.S).group(1)
    assert len(" ".join(post.split())) <= 280
