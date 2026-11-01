"""Tests for `anvil-serving benchmark` — the request-replay throughput probe.

Covers the two dogfooding bugs:
  BUG 1: small-context serves 400 because sampled/rendered ctx ignores max_model_len.
  BUG 2: thinking-by-default models report a FALSE 0 tok/s with no chat_template_kwargs.

Pure / no network: we exercise the clamp + body-builder seams directly, and inject a
fake urlopen for the /v1/models probe.
"""
import hashlib
import io
import json
import urllib.request
from pathlib import Path

import pytest

from anvil_serving import benchmark as bm
from anvil_serving.benchmarking import artifacts as benchmark_artifacts
from anvil_serving.benchmarking import requests as benchmark_requests
from anvil_serving.benchmarking import runner as benchmark_runner
from anvil_serving.benchmarking import specs as benchmark_specs


# ---- BUG 1: context clamp keeps prompts under a small serve's max_model_len -------


def test_control_evidence_retains_portable_relative_reference(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    evidence_path = Path("docs/control-proof.json")
    evidence_path.parent.mkdir()
    raw = json.dumps({
        "schema": "anvil-serving.control-evidence/v1",
        "status": "verified",
        "control_mechanism": "reasoning_effort",
        "source": "preflight",
        "observed_at": "2026-08-01T00:00:00Z",
    }).encode("utf-8")
    evidence_path.write_bytes(raw)

    reference, digest = benchmark_specs.load_control_evidence(
        str(evidence_path),
        status="verified",
        mechanism="reasoning_effort",
    )

    assert reference == "docs/control-proof.json"
    assert digest == hashlib.sha256(raw).hexdigest()


def test_output_contract_requires_canary_at_byte_zero_and_rejects_foreign_marker():
    expected = "ANVIL_REQ_0_00001"
    good = benchmark_requests.output_contract_observation(
        expected + " code code", expected_marker=expected, response_words=2
    )
    assert good["request_canary"]["passed"] is True
    assert good["controlled_output"]["exact_adherence"] is True

    late = benchmark_requests.output_contract_observation(
        "prefix " + expected, expected_marker=expected
    )
    assert late["request_canary"]["marker_at_start"] is False
    assert late["request_canary"]["passed"] is False

    foreign = benchmark_requests.output_contract_observation(
        expected + " ANVIL_REQ_9_00002", expected_marker=expected
    )
    assert foreign["request_canary"]["foreign_marker_count"] == 1
    assert foreign["request_canary"]["passed"] is False


# ---- BUG 2: --no-thinking injects enable_thinking:false into the request body ------


def test_post_chat_sampler_controls_reach_openai_wire_body(monkeypatch):
    seen = {}

    class _Response:
        headers = {}

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return b'{"choices": []}'

    def fake_urlopen(request, timeout):
        seen["body"] = json.loads(request.data)
        seen["timeout"] = timeout
        return _Response()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    benchmark_requests.post_chat(
        "http://127.0.0.1:30002/v1", "model", None,
        [{"role": "user", "content": "hi"}],
        temperature=1.0, top_p=0.95,
    )
    assert seen == {
        "body": {
            "model": "model",
            "messages": [{"role": "user", "content": "hi"}],
            "max_tokens": 128,
            "temperature": 1.0,
            "top_p": 0.95,
            "stream": False,
        },
        "timeout": 120,
    }


def test_final_answer_regex_rejects_conflicting_later_marker():
    check = {"name": "answer", "matches_regex": r"\bFINAL\s*=\s*B\b[*]*\s*$"}
    assert benchmark_specs.evaluate_text_checks("FINAL=B is not correct; FINAL=C", [check]) == [
        {"name": "answer", "passed": False}
    ]


def test_timeout_triage_accepts_retained_answer_and_requires_latency_fix():
    prompt = next(
        item for item in benchmark_runner.INTELLIGENCE_PROMPTS
        if item["id"] == "parallel_timeout_triage"
    )
    retained_answer = benchmark_specs.evaluate_text_checks(
        "The turn times out because total latency is ~3000 ms (>2500 ms), mainly "
        "from the 1800 ms LLM; fix it by reducing LLM latency with a faster/cached "
        "model or by streaming LLM output into TTS to overlap delays.",
        prompt["checks"],
    )
    streaming_answer = benchmark_specs.evaluate_text_checks(
        "The 3000 ms total exceeds the timeout; stream TTS while the LLM finishes.",
        prompt["checks"],
    )
    irrelevant_latency_answer = benchmark_specs.evaluate_text_checks(
        "The turn times out because total latency is ~3000 ms.",
        prompt["checks"],
    )

    assert all(item["passed"] for item in retained_answer)
    assert all(item["passed"] for item in streaming_answer)
    assert not all(item["passed"] for item in irrelevant_latency_answer)


# ---- bonus: /v1/models auto-detect of max_model_len --------------------------------

def _fake_urlopen(payload):
    class _CM:
        def __enter__(self):
            return io.BytesIO(json.dumps(payload).encode())

        def __exit__(self, *a):
            return False

    return lambda req, timeout=15: _CM()


def test_detect_max_model_len_reads_model_card(monkeypatch):
    payload = {"object": "list", "data": [{"id": "coder", "max_model_len": 16384}]}
    monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen(payload))
    assert bm.detect_max_model_len("http://x/v1", "coder") == 16384


def test_atomic_json_rejects_non_finite_numbers_without_replacing_target(tmp_path):
    target = tmp_path / "artifact.json"
    target.write_text('{"previous": true}\n', encoding="utf-8")
    with pytest.raises(ValueError):
        benchmark_artifacts.atomic_write_json(target, {"metric": float("nan")})
    assert target.read_text(encoding="utf-8") == '{"previous": true}\n'


def test_benchmark_artifact_json_out_writes_summary_and_metrics(monkeypatch, tmp_path):
    monkeypatch.setattr(bm, "stream_chat",
                        lambda *a, **k: dict(
                            ttft=0.1,
                            e2e=0.2,
                            out_toks=8,
                            finish_reasons=["stop"],
                            stream_terminal_observed=True,
                            visible_content_captured_chars=11,
                            visible_content_capture_limit=8192,
                            usage={"prompt_tokens": 100, "prompt_tokens_details": {"cached_tokens": 40}},
                        ))

    out = tmp_path / "benchmark.json"
    rc = bm.main([
        "--base-url", "http://127.0.0.1:30002/v1",
        "--model", "local-heavy",
        "--engine", "vllm",
        "--gpu", "dark-heavy",
        "--requests", "2",
        "--concurrency", "2",
        "--max-model-len", "131072",
        "--json-out", str(out),
    ])

    assert rc in (0, None)
    summary = json.loads(out.read_text(encoding="utf-8"))
    assert summary["schema"] == "anvil-serving.benchmark/v1"
    assert summary["measurement_protocol"] == "capacity-v3"
    assert summary["model"] == "local-heavy"
    assert summary["engine"] == "vllm"
    assert summary["gpu"] == "dark-heavy"
    assert summary["requests"] == 2
    assert summary["completed"] == 2
    assert summary["context_seed"] == 0
    assert summary["shared_prefix_tokens"] == 8000
    assert sum(summary["context_distribution"].values()) == 2
    assert summary["metrics"]["ttft_p50_ms"] == 100.0
    assert summary["metrics"]["generation_p50_ms"] == 100.0
    assert summary["metrics"]["e2e_p50_ms"] == 200.0
    assert summary["metrics"]["effective_prefill_tok_s_p50"] == 1000.0
    assert summary["metrics"]["decode_tok_s_p50"] == pytest.approx(70.0)
    assert summary["metrics"]["mean_inter_token_latency_ms_p50"] == pytest.approx(
        100.0 / 7
    )
    assert summary["metrics"]["prompt_tokens"] == 200
    assert summary["metrics"]["throughput_tok_s"] > 0
    assert summary["metrics"]["throughput_performance_eligible"] is True
    assert summary["metrics"]["output_tokens"] == 16
    assert summary["metrics"]["output_token_sources"] == {"unknown": 2}
    assert summary["metrics"]["prefix_cache_hit_avg"] == 0.4
    assert [row["request_index"] for row in summary["request_timings"]] == [0, 1]
    assert summary["timing_methodology"]["clock"] == "client time.perf_counter"
    assert "not raw per-token ITL" in summary["timing_methodology"]["mean_inter_token_latency"]
    assert summary["metric_population"]["p99_interpretation"] == "descriptive-nearest-rank-only"
    assert summary["timestamp_precision"].startswith("nanosecond-unix-fields")
    assert isinstance(summary["started_at_unix_ns"], int)
    assert summary["wall_clock_ms"] > 0
    assert summary["workload_identity"] == {
        "prompt_contract_revision": "capacity-prompt-v1",
        "workload_kind": "synthetic-summary",
    }
    assert summary["request_timings"][0]["stream_terminal_observed"] is True
    assert summary["request_timings"][0]["finish_reasons"] == ["stop"]
    assert summary["request_timings"][0]["visible_content_captured_chars"] == 11
    assert summary["request_timings"][0]["visible_content_capture_limit"] == 8192


# ---- GENERATE: benchmarking a serve ALSO records its recipe (--recipe-out) ---------
# Hermetic: capture_from_container / capture_hardware are injected as fakes, so no
# real docker / GPU / network is touched. The emitted block is proven parseable.
import tomllib        # noqa: E402


_STUB_SUMMARY = {
    "run_id": "benchmark-20260703T000000Z",
    "max_context_tokens": 131072,
    "context_tokens": None,
    "metrics": {"throughput_tok_s": 183.24, "ttft_p50_ms": 412.7},
}

_FAKE_CAP = {
    "serve": {
        "engine": "vllm",
        "image": "vllm/vllm-openai:nightly",
        "port": 30002,
        "env": ["FLASHINFER_CUDA_ARCH_LIST=12.0f"],
        "flags": ["--kv-cache-dtype fp8"],
    },
    "hardware": {"gpu_uuid": "GPU-11111111-1111-1111-1111-111111111111"},
}


def _fake_capture(name):
    assert name == "heavy-serve"
    return _FAKE_CAP


def _fake_hardware(gpu_uuid=None):
    assert gpu_uuid == "GPU-11111111-1111-1111-1111-111111111111"
    return {"gpu": "NVIDIA RTX PRO 6000 Blackwell Max-Q", "vram_total_gb": 96}


def test_main_recipe_out_end_to_end_is_hermetic(monkeypatch, capsys):
    """`benchmark ... --recipe-out -` through main(): the benchmark itself is stubbed
    (fake stream_chat, no /v1/models probe) and capture is faked -> ZERO network."""
    from anvil_serving import serve_recipes as sr

    monkeypatch.setattr(bm, "stream_chat",
                        lambda *a, **k: dict(ttft=0.1, e2e=0.2, out_toks=64, usage=None))
    monkeypatch.setattr(sr, "capture_from_container", _fake_capture)
    monkeypatch.setattr(sr, "capture_hardware", _fake_hardware)

    rc = bm.main([
        "--base-url", "http://127.0.0.1:30002/v1", "--model", "local-heavy",
        "--requests", "2", "--concurrency", "2", "--max-model-len", "131072",
        "--recipe-out", "-", "--recipe-from-container", "heavy-serve",
        "--recipe-fit", "benchmark-quality",
    ])
    assert rc in (0, None)
    out = capsys.readouterr().out
    assert "[[recipe]]" in out
    block = out[out.index("[[recipe]]"):]
    parsed = tomllib.loads("schema='x'\n" + block)["recipe"][0]
    assert parsed["model"] == "local-heavy"
    assert parsed["fit"]["suited"] == ["benchmark-quality"]


# ---- Fast-tier bakeoff evidence mode -------------------------------------------


def test_bakeoff_context_probe_includes_reasoning_headroom(monkeypatch, tmp_path):
    completion_caps = []

    def fake_stream_chat(_base, _model, _prompt, _key, max_tokens, **_kwargs):
        completion_caps.append(max_tokens)
        return dict(
            time_to_first_output=0.01,
            ttft=0.02,
            e2e=0.03,
            out_toks=3,
            content_chunks=1,
            reasoning_chunks=1,
            output_token_source="usage",
            usage={"prompt_tokens": 100, "completion_tokens": 3},
        )

    monkeypatch.setattr(bm, "stream_chat", fake_stream_chat)
    out = tmp_path / "reasoning-bakeoff.json"

    bm.main([
        "--bakeoff",
        "--base-url", "http://127.0.0.1:39010/v1",
        "--model", "reasoner",
        "--candidate-id", "reasoner",
        "--config-id", "reasoning-low",
        "--context-targets", "1024",
        "--suite", "chat,context",
        "--max-model-len", "4096",
        "--visible-answer-tokens", "128",
        "--reasoning-headroom-tokens", "512",
        "--evidence-out", str(out),
    ])

    assert completion_caps == [640]


def test_bakeoff_session_intelligence_and_thinking_evidence(monkeypatch, tmp_path):
    responses = iter([
        "RIVER-918",
        "--- a/app.py\n+++ b/app.py\n@@\n-timeout = 30\n+timeout = 45\n retries = 2\n",
        "The three stages exceed the timeout budget, so use a faster LLM or reduce output.",
    ])

    def fake_post_chat(*args, **kwargs):
        assert kwargs["chat_template_kwargs"] == {"enable_thinking": False}
        return {
            "latency_s": 0.10,
            "response": {
                "choices": [{
                    "finish_reason": "stop",
                    "message": {"content": next(responses)}
                }]
            },
        }

    monkeypatch.setattr(bm, "post_chat", fake_post_chat)
    out = tmp_path / "quality.json"
    rc = bm.main([
        "--bakeoff",
        "--base-url", "http://127.0.0.1:39013/v1",
        "--model", "glm-4.7-flash",
        "--candidate-id", "glm47-flash",
        "--config-id", "llamacpp-q6-32k",
        "--suite", "session,intelligence",
        "--eval-repetitions", "1",
        "--thinking-mode", "disabled",
        "--evidence-out", str(out),
    ])

    assert rc == 0
    evidence = json.loads(out.read_text(encoding="utf-8"))
    assert evidence["session"]["status"] == "passed"
    assert evidence["intelligence"]["status"] == "passed"
    assert evidence["score_inputs"]["session_recall_passed"] is True
    assert evidence["score_inputs"]["intelligence_pass_rate"] == 1.0
    assert evidence["thinking"]["mode"] == "disabled"
    assert evidence["thinking"]["chat_template_kwargs"] == {"enable_thinking": False}
    assert evidence["thinking"]["control_mechanism"] == "chat_template_kwargs"
    assert evidence["thinking"]["control_status"] == "requested_unverified"
    assert evidence["thinking"]["unsupported"] is False
    assert evidence["failures"] == []


# ---- --suite-file: externally-authored eval specs through the check engine -------

_SUITE_SPEC = {
    "suite": "planning-regression",
    "date": "2026-07-11",
    "work_class": "planning",
    "evals": [
        {
            "id": "diff_edit",
            "prompt": "Return a unified diff changing timeout to 45.",
            "max_tokens": 64,
            "checks": [
                {"name": "diff_shape", "contains_all": ["---", "+++"]},
                {"name": "new_value", "contains": "+timeout = 45"},
            ],
        },
        {
            "id": "weather_tool",
            "messages": [{"role": "user", "content": "Record zip 98101."}],
            "tools": [{"type": "function", "function": {"name": "record_weather_zip"}}],
            "expect_tool": {"name": "record_weather_zip", "required_args": {"zip": "98101"}},
            "checks": [],
        },
    ],
}


def _write_spec(tmp_path, spec):
    path = tmp_path / "suite.json"
    path.write_text(json.dumps(spec), encoding="utf-8")
    return str(path)


def _suite_main_args(tmp_path, spec_path, out_name="suite-evidence.json"):
    out = tmp_path / out_name
    return out, [
        "--bakeoff",
        "--base-url", "http://127.0.0.1:39015/v1",
        "--model", "glm-4.7-flash",
        "--candidate-id", "glm47-flash",
        "--config-id", "sglang-32k",
        "--suite", "tool",  # built-in suites still run alongside the external spec
        "--eval-repetitions", "1",
        "--suite-file", spec_path,
        "--evidence-out", str(out),
    ]


def test_suite_file_runs_external_evals_into_evidence(monkeypatch, tmp_path):
    seen = []

    def fake_post_chat(base, model, key, messages, max_tokens=128, timeout=120,
                       tools=None, chat_template_kwargs=None):
        seen.append({"messages": messages, "max_tokens": max_tokens, "tools": tools})
        if tools and tools[0]["function"]["name"] == "record_weather_zip":
            message = {"tool_calls": [{
                "id": "call_1", "type": "function",
                "function": {"name": "record_weather_zip", "arguments": '{"zip": "98101"}'},
            }]}
        else:
            message = {"content": "--- a/app.py\n+++ b/app.py\n@@\n-timeout = 30\n+timeout = 45\n"}
        finish = "tool_calls" if tools else "stop"
        return {"latency_s": 0.1, "response": {"choices": [{
            "finish_reason": finish, "message": message,
        }]}}

    monkeypatch.setattr(bm, "post_chat", fake_post_chat)
    spec_path = _write_spec(tmp_path, _SUITE_SPEC)
    out, argv = _suite_main_args(tmp_path, spec_path)
    rc = bm.main(argv)

    assert rc == 0
    evidence = json.loads(out.read_text(encoding="utf-8"))
    section = evidence["suites"]["planning-regression"]
    assert section["status"] == "passed"
    assert section["work_class"] == "planning"
    assert section["date"] == "2026-07-11"
    assert section["source_sha256"] == hashlib.sha256(
        (tmp_path / "suite.json").read_bytes()
    ).hexdigest()
    assert [c["status"] for c in section["checks"]] == ["passed", "passed"]
    assert section["checks"][0]["text_checks"] == [
        {"name": "diff_shape", "passed": True},
        {"name": "new_value", "passed": True},
    ]
    assert section["checks"][1]["tool_call"]["valid"] is True
    assert section["checks"][1]["tool_call"]["arguments"] == {"zip": "98101"}
    assert evidence["failures"] == []
    # per-eval validator labels reflect what actually graded the eval
    assert section["checks"][0]["validator"] == "deterministic_text_checks"
    assert section["checks"][1]["validator"] == "tool_call"
    # built-in tool suite ran too (--suite tool) and the external evals forwarded
    # their own max_tokens / tools / messages verbatim (external evals run LAST,
    # after the selected built-in suites).
    assert evidence["tool"]["status"] == "passed"
    external = seen[-2:]
    assert external[0]["max_tokens"] == 64
    assert external[1]["max_tokens"] == 256  # spec default
    assert external[1]["messages"] == [{"role": "user", "content": "Record zip 98101."}]
    assert external[1]["tools"][0]["function"]["name"] == "record_weather_zip"


def test_suite_file_failed_checks_land_in_failures(monkeypatch, tmp_path):
    def fake_post_chat(*args, **kwargs):
        # plain text: fails the diff checks on eval 1 AND the expect_tool on eval 2
        return {"latency_s": 0.1,
                "response": {"choices": [{
                    "finish_reason": "stop", "message": {"content": "no diff here"},
                }]}}

    monkeypatch.setattr(bm, "post_chat", fake_post_chat)
    out, argv = _suite_main_args(tmp_path, _write_spec(tmp_path, _SUITE_SPEC))
    rc = bm.main(argv)

    assert rc == 1  # artifact is retained, but the quality gate failed
    evidence = json.loads(out.read_text(encoding="utf-8"))
    section = evidence["suites"]["planning-regression"]
    assert section["status"] == "failed"
    assert [c["status"] for c in section["checks"]] == ["failed", "failed"]
    assert "diff_shape" in section["checks"][0]["error"]
    assert section["checks"][1]["tool_call"]["valid"] is False
    suite_failures = [f for f in evidence["failures"] if f["suite"] == "planning-regression"]
    assert [f["eval_id"] for f in suite_failures] == ["diff_edit", "weather_tool"]


_OK_CHECKS = [{"name": "c", "contains": "x"}]  # valid filler so each case isolates ONE defect


def _one_eval(**over):
    base = {"id": "x", "prompt": "p", "checks": _OK_CHECKS}
    base.update(over)
    return {"suite": "s", "evals": [base]}


@pytest.mark.parametrize("spec", [
    dict(
        _one_eval(checks=[{
            "name": "answer",
            "matches_regex": r"^\s*B\s*$",
        }]),
        evidence_use="ranking",
        validator_strength="exact_choice",
    ),
    {
        "suite": "s",
        "evidence_use": "ranking",
        "validator_strength": "typed_structure",
        "evals": [{
            "id": "tool",
            "prompt": "Call choose with choice B.",
            "tools": [{
                "type": "function",
                "function": {
                    "name": "choose",
                    "parameters": {
                        "type": "object",
                        "properties": {"choice": {"type": "string"}},
                        "required": ["choice"],
                    },
                },
            }],
            "expect_tool": {
                "name": "choose",
                "required_args": {"choice": "B"},
            },
        }],
    },
])
def test_ranking_suite_accepts_executable_strong_validators(tmp_path, spec):
    loaded = benchmark_specs.load_suite_spec(_write_spec(tmp_path, spec))

    assert loaded["evidence_use"] == "ranking"


def test_canonical_dry_runs_do_not_probe_or_write(monkeypatch, tmp_path, capsys):
    def boom(*args, **kwargs):
        raise AssertionError("dry-run must not access the endpoint")

    monkeypatch.setattr(bm, "detect_max_model_len", boom)
    monkeypatch.setattr(bm, "post_chat", boom)
    monkeypatch.setattr(bm, "stream_chat", boom)

    quality_out = tmp_path / "quality.json"
    assert bm.main([
        "quality",
        "--base-url", "http://127.0.0.1:39015/v1",
        "--model", "candidate",
        "--candidate-id", "candidate",
        "--config-id", "config",
        "--suite", "tool",
        "--output", str(quality_out),
        "--dry-run",
    ]) == 0
    assert json.loads(capsys.readouterr().out)["workload"] == "quality"
    assert not quality_out.exists()

    capacity_out = tmp_path / "capacity.json"
    assert bm.main([
        "capacity",
        "--base-url", "http://127.0.0.1:39015/v1",
        "--model", "candidate",
        "--prompt-cache-mode", "unique",
        "--shared-prefix-tokens", "999",
        "--output", str(capacity_out),
        "--dry-run",
    ]) == 0
    capacity_plan = json.loads(capsys.readouterr().out)
    assert capacity_plan["workload"] == "capacity"
    assert capacity_plan["capacity"]["shared_prefix_tokens"] == 0
    assert not capacity_out.exists()


def test_notebook_metadata_is_validated_before_live_work(monkeypatch, tmp_path):
    def boom(*args, **kwargs):
        raise AssertionError("notebook validation must precede endpoint access")

    monkeypatch.setattr(bm, "post_chat", boom)
    suite = tmp_path / "ranking.json"
    suite.write_text(json.dumps({
        "suite": "ranking",
        "evidence_use": "ranking",
        "validator_strength": "exact_choice",
        "evals": [{
            "id": "choice",
            "prompt": "Reply only A.",
            "checks": [{"name": "exact", "matches_regex": "^A$"}],
        }],
    }), encoding="utf-8")
    with pytest.raises(SystemExit) as exc:
        bm.main([
            "quality",
            "--base-url", "http://127.0.0.1:39015/v1",
            "--model", "candidate",
            "--candidate-id", "candidate",
            "--config-id", "config",
            "--suite-file", str(suite),
            "--notebook", str(tmp_path / "notebook.json"),
        ])
    assert exc.value.code == 2


def test_quality_dry_run_reports_exact_reasoning_control(capsys):
    assert bm.main([
        "quality",
        "--base-url", "http://127.0.0.1:39015/v1",
        "--model", "deepseek-v4-flash-0731",
        "--candidate-id", "deepseek-v4-flash-0731",
        "--config-id", "tp2-w4a16",
        "--suite", "intelligence",
        "--reasoning-effort", "max",
        "--dry-run",
    ]) == 0
    plan = json.loads(capsys.readouterr().out)
    assert plan["workload"] == "quality"
    assert plan["thinking"]["reasoning_effort"] == "max"
    assert plan["thinking"]["control_mechanism"] == "reasoning_effort"
    assert plan["thinking"]["control_requested"] == "max"
