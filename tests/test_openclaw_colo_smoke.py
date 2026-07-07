import importlib.util
import json
import types
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "examples" / "openclaw" / "colo_smoke.py"
spec = importlib.util.spec_from_file_location("colo_smoke", MODULE_PATH)
colo_smoke = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(colo_smoke)


def _args(**overrides):
    values = {
        "live": False,
        "fixture": True,
        "artifact": ".anvil/evidence/test.json",
        "pretty": False,
        "gateway_host": "fakoli-mini",
        "gateway_user": "",
        "router_base_url": "http://100.87.34.66:8000/v1",
        "config": "examples/fakoli-dark/anvil-router.live.toml",
        "expected_context_window": 131072,
        "timeout_seconds": 30,
        "run_generations": False,
        "run_interaction_benchmark": False,
        "fast_generation_max_tokens": 48,
        "heavy_generation_max_tokens": 256,
        "expect_min_tokens_per_second": 0.0,
        "repair": False,
    }
    values.update(overrides)
    return types.SimpleNamespace(**values)


def test_fixture_artifact_schema_and_story_proofs_are_complete():
    artifact = colo_smoke.assemble_artifact(_args())

    assert artifact["schema_version"] == "openclaw-colo-smoke/v1"
    for key in (
        "stories",
        "proofs",
        "environment",
        "openclaw_config",
        "plugin_runtime",
        "router_probes",
        "e2e_turns",
        "benchmarks",
        "interaction_benchmarks",
        "drift",
        "verdict",
    ):
        assert key in artifact

    assert artifact["verdict"]["status"] == "pass"
    assert colo_smoke.validate_story_proofs(artifact["stories"], artifact["proofs"]) == []


def test_story_proof_validation_fails_when_story_has_no_proof():
    artifact = colo_smoke.assemble_artifact(_args())
    proofs = [proof for proof in artifact["proofs"] if "S005" not in proof["story_ids"]]

    assert "story S005 has no proof" in colo_smoke.validate_story_proofs(artifact["stories"], proofs)


def test_redaction_removes_literal_tokens_but_keeps_metric_keys_and_env_names():
    secret = "router-secret-token-12345"
    payload = {
        "apiKey": secret,
        "authorization": "Bearer " + secret,
        "token_env": "ANVIL_ROUTER_TOKEN",
        "output_tokens_total": 42,
        "nested": [{"message": "saw " + secret}],
    }

    redacted = colo_smoke.redact(payload)
    rendered = json.dumps(redacted, sort_keys=True)

    assert secret not in rendered
    assert "ANVIL_ROUTER_TOKEN" in rendered
    assert redacted["output_tokens_total"] == 42
    assert colo_smoke.serialized_secret_findings(redacted) == []


def test_config_audit_detects_missing_preset_and_context_window_drift():
    summary = json.loads(json.dumps(colo_smoke.fixture_openclaw_config(_args())))
    summary["provider"]["models"] = [
        model for model in summary["provider"]["models"] if model["id"] != "quick-edit"
    ]
    summary["provider"]["models"][0]["contextWindow"] = 1

    issues = colo_smoke.audit_openclaw_config(
        summary,
        expected_base_url="http://100.87.34.66:8000/v1",
        expected_context_window=131072,
    )
    codes = {issue["code"] for issue in issues}

    assert "missing_presets" in codes
    assert "context_window_drift" in codes


def test_config_audit_fails_on_literal_api_key_shape():
    summary = json.loads(json.dumps(colo_smoke.fixture_openclaw_config(_args())))
    summary["provider"]["api_key_shape"] = "literal"

    issues = colo_smoke.audit_openclaw_config(
        summary,
        expected_base_url="http://100.87.34.66:8000/v1",
    )
    literal = next(issue for issue in issues if issue["code"] == "literal_api_key")

    assert literal["severity"] == "fail"


def test_repair_mode_records_preview_without_apply():
    artifact = colo_smoke.assemble_artifact(_args(repair=True))
    repair = artifact["repair"]

    assert repair["requested"] is True
    assert repair["human_gate_required"] is True
    assert repair["applied"] is False
    assert repair["preview_command"][:4] == ["anvil-serving", "harness", "sync", "openclaw"]
    assert "--gateway-host" in repair["preview_command"]
    assert "fakoli-mini" in repair["preview_command"]


def test_generation_probe_budget_gives_heavy_intents_room_for_content():
    table = colo_smoke.generation_probe_fallback_max_tokens_table(48, 256)

    assert table["review"] == 256
    assert table["long-context"] == 256
    assert table["quick-edit"] == 48
    assert table["chat-fast"] == 48


def test_generation_probe_budget_reads_tier_params_from_router_config(tmp_path):
    config = tmp_path / "router.toml"
    config.write_text(
        """
[router]
mapping_version = "test"

[[router.tiers]]
id = "fast-local"
base_url = "http://127.0.0.1:30003/v1"
model = "fast"
dialect = "openai"
context_limit = 32768
privacy = "local"
tool_support = true
auth_env = "ANVIL_FAST_LOCAL_KEY"
params = { generation_probe_max_tokens = 64 }

[[router.tiers]]
id = "heavy-local"
base_url = "http://127.0.0.1:30002/v1"
model = "heavy"
dialect = "openai"
context_limit = 131072
privacy = "local"
tool_support = true
auth_env = "ANVIL_HEAVY_LOCAL_KEY"
params = { generation_probe_max_tokens = 384 }

[router.presets]
chat-fast = ["fast-local", "heavy-local"]
review = ["heavy-local"]
long-context = ["heavy-local"]
""",
        encoding="utf-8",
    )

    budgets = colo_smoke.load_generation_probe_budgets(
        str(config),
        fast_default=48,
        heavy_default=256,
    )

    assert budgets["source"] == "router-config"
    assert budgets["by_tier"]["fast-local"] == 64
    assert budgets["by_tier"]["heavy-local"] == 384
    assert budgets["by_preset"]["chat-fast"] == 64
    assert budgets["by_preset"]["review"] == 384
    assert budgets["by_preset"]["long-context"] == 384


def test_interaction_benchmark_recipe_reads_tier_params_from_router_config(tmp_path):
    config = tmp_path / "router.toml"
    config.write_text(
        """
[router]
mapping_version = "test"

[[router.tiers]]
id = "fast-local"
base_url = "http://127.0.0.1:30003/v1"
model = "fast"
dialect = "openai"
context_limit = 32768
privacy = "local"
tool_support = true
auth_env = "ANVIL_FAST_LOCAL_KEY"

[router.tiers.params]
generation_probe_max_tokens = 64
interaction_benchmark_max_tokens = 192
interaction_benchmark_stream_max_tokens = 128

[[router.tiers]]
id = "heavy-local"
base_url = "http://127.0.0.1:30002/v1"
model = "heavy"
dialect = "openai"
context_limit = 131072
privacy = "local"
tool_support = true
auth_env = "ANVIL_HEAVY_LOCAL_KEY"

[router.tiers.params]
generation_probe_max_tokens = 384
interaction_benchmark_max_tokens = 1024
interaction_benchmark_stream_max_tokens = 512
interaction_benchmark_reasoning_effort = "low"
interaction_benchmark_max_tokens_by_intent = { planning = 2048 }
interaction_benchmark_stream_max_tokens_by_intent = { planning = 1024 }

[router.presets]
chat-fast = ["fast-local", "heavy-local"]
review = ["heavy-local"]
planning = ["heavy-local"]
""",
        encoding="utf-8",
    )

    recipes = colo_smoke.load_interaction_benchmark_recipes(str(config))

    assert recipes["source"] == "router-config"
    assert recipes["by_tier"]["fast-local"]["max_tokens"] == 192
    assert recipes["by_tier"]["heavy-local"]["max_tokens"] == 1024
    assert recipes["by_tier"]["heavy-local"]["reasoning_effort"] == "low"
    assert recipes["by_preset"]["review"]["max_tokens"] == 1024
    assert recipes["by_preset"]["planning"]["max_tokens"] == 2048
    assert recipes["by_preset"]["planning"]["stream_max_tokens"] == 1024


def test_interaction_benchmark_summary_flags_failures_and_truncation():
    summary = colo_smoke.summarize_interaction_benchmarks(
        [
            {
                "case_id": "review_ok",
                "intent": "review",
                "mode": "exact",
                "status": 200,
                "latency_ms": 1000.0,
                "finish_reason": "stop",
                "usage": {"completion_tokens": 200},
                "output_tokens": 200,
                "tokens_per_second": 200.0,
            },
            {
                "case_id": "planning_length",
                "intent": "planning",
                "mode": "exact",
                "status": 200,
                "latency_ms": 2000.0,
                "finish_reason": "length",
                "max_tokens": 1024,
                "usage": {"completion_tokens": 1024},
                "output_tokens": 1024,
                "tokens_per_second": 512.0,
            },
            {
                "case_id": "long_context_503",
                "intent": "long-context",
                "mode": "stream",
                "status": 503,
                "output_tokens": 0,
            },
        ],
        requested=True,
    )
    codes = {item["code"] for item in summary["warnings"]}

    assert summary["aggregate"]["requests"] == 3
    assert summary["aggregate"]["completed"] == 2
    assert "interaction_benchmark_truncated" in codes
    assert "interaction_benchmark_request_failed" in codes


def test_live_e2e_gate_fails_truncated_or_non_sentinel_generation():
    issues = colo_smoke.e2e_issues([
        {
            "case_id": "live_review",
            "status": 200,
            "finish_reason": "length",
            "max_tokens": 128,
            "response_preview": "anvil-col",
        }
    ], live_requested=True)
    codes = {issue["code"] for issue in issues}

    assert "generation_truncated" in codes
    assert "generation_sentinel_missing" in codes


def test_remote_collector_no_token_path_returns_three_values():
    assert "return probes, [], []" in colo_smoke.REMOTE_COLLECTOR


def test_gateway_collector_reports_malformed_json(monkeypatch):
    def fake_command_result(argv, *, timeout, stdin=None):
        assert argv[0] == "ssh"
        assert stdin == colo_smoke.REMOTE_COLLECTOR
        return {"returncode": 0, "stdout": "not-json", "duration_ms": 12}

    monkeypatch.setattr(colo_smoke, "command_result", fake_command_result)

    result = colo_smoke.run_gateway_collector(_args(live=True, fixture=False))

    assert result["ok"] is False
    assert result["error"] == "bad_json"


def test_streaming_generation_summary_warns_when_exact_tps_unavailable():
    summary = colo_smoke.summarize_benchmarks(
        [
            {
                "case_id": "live_review",
                "status": 200,
                "latency_ms": 100.0,
                "output_tokens": None,
                "tokens_per_second": None,
            }
        ],
        min_tps=1.0,
    )

    assert summary["aggregate"]["completed"] == 1
    assert summary["aggregate"]["tokens_per_second_avg"] is None
    assert summary["warnings"][0]["code"] == "throughput_unavailable"


def test_cli_fixture_writes_clean_artifact(tmp_path, capsys):
    artifact_path = tmp_path / "openclaw-colo.json"

    rc = colo_smoke.main(["--fixture", "--artifact", str(artifact_path)])
    output = capsys.readouterr().out
    data = json.loads(artifact_path.read_text(encoding="utf-8"))

    assert rc == 0
    assert "verdict: pass" in output
    assert data["verdict"]["status"] == "pass"
    assert colo_smoke.serialized_secret_findings(data) == []
