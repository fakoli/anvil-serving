import json
from pathlib import Path
import re
from types import SimpleNamespace

from anvil_serving import routed_eval
from anvil_serving.routed_eval import run_routed_eval


ROUTER_SHA = "a" * 64


class _Response:
    def __init__(self, payload):
        self._body = json.dumps(payload).encode("utf-8")
        self.headers = {"Content-Length": str(len(self._body))}

    def read(self, size=-1):
        return self._body if size < 0 else self._body[:size]

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class _CatalogOpener:
    def __init__(self):
        self._payloads = [
            {
                "object": "router_status",
                "package_version": "0.34.3",
                "config_sha256": ROUTER_SHA,
                "model_aliases": ["llm.primary", "llm.secondary"],
            },
            {"object": "list", "data": [
                {
                    "id": "primary", "aliases": ["llm.primary"],
                    "context_limit_tokens": 1_048_576,
                    "modalities": ["text"], "thinking": {"supported": True},
                    "compat": {}, "limits": {"max_output_tokens": 8192},
                },
                {
                    "id": "secondary", "aliases": ["llm.secondary"],
                    "context_limit_tokens": 262_144,
                    "modalities": ["text", "image"],
                    "thinking": {"supported": True},
                    "compat": {}, "limits": {"max_output_tokens": 8192},
                },
            ]},
        ]

    def open(self, _request, timeout):
        assert timeout == 30
        return _Response(self._payloads.pop(0))


def _opener_with_context(context_limit=262_144, discovery_context=None):
    discovery_context = context_limit if discovery_context is None else discovery_context

    def opener(request, timeout):
        assert timeout == 30
        if request.full_url.endswith("/router/status"):
            return _Response({
                "package_version": "0.34.3",
                "config_sha256": ROUTER_SHA,
                "model_aliases": ["llm.primary", "llm.secondary"],
            })
        if request.full_url.endswith("/models"):
            return _Response({"object": "list", "data": [
                {"id": "llm.primary", "context_window": 1_048_576},
                {"id": "llm.secondary", "context_window": discovery_context},
            ]})
        if "/models/fingerprints?" in request.full_url:
            return _Response({"data": [{
                "id": "secondary-local",
                "aliases": ["llm.secondary"],
                "fingerprint": {"config_fingerprint": "q4-mtp3"},
                "served_identity": {"observed": "compat-qwen38"},
                "readiness": {"state": "ready", "reason": "identity_passed"},
            }]})
        if "/models/capacity?" in request.full_url:
            return _Response({"data": [{
                "id": "secondary-local",
                "aliases": ["llm.secondary"],
                "loaded": True,
                "engine": {"name": "llama.cpp", "quantization": "Q4_0"},
                "capacity": {
                    "context_limit_tokens": context_limit,
                    "configured_max_concurrency": 1,
                },
            }]})
        raise AssertionError(request.full_url)

    return opener


def _passing_syncer(**kwargs):
    assert kwargs["confirm"] is True
    assert kwargs["dry_run"] is False
    assert kwargs["restart_openclaw_on_change"] is True
    assert kwargs["environ"]["ANVIL_ROUTER_TOKEN"] == "secret-not-retained"
    assert kwargs["refresh_openclaw_service"]() == 0
    return {
        "config_sha256": ROUTER_SHA,
        "package_version": "0.34.3",
        "models": [
            {"id": "llm.primary", "context_window": 1_048_576, "max_output_tokens": 8192},
            {"id": "llm.secondary", "context_window": 262_144, "max_output_tokens": 8192},
        ],
        "changed": ["openclaw", "pi_models", "pi_settings"],
        "backup_created": True,
        "openclaw_restarted": True,
        "dry_run": False,
    }


def _marker(argv):
    prompt = argv[argv.index("--message") + 1] if "--message" in argv else argv[argv.index("-z") + 1]
    match = re.search(r'file at (".*?")\.', prompt)
    assert match is not None
    marker = Path(json.loads(match.group(1))).read_text(encoding="utf-8")
    assert marker not in prompt
    return marker


def _passing_runner(argv, _timeout):
    marker = _marker(argv)
    if argv[0] == "openclaw":
        return SimpleNamespace(returncode=0, stderr="", stdout=json.dumps({
            "status": "ok",
            "result": {
                "payloads": [{"text": marker}],
                "meta": {
                    "agentMeta": {
                        "provider": "anvil", "model": "llm.secondary",
                        "contextTokens": 262_144,
                    },
                    "executionTrace": {
                        "winnerProvider": "anvil", "winnerModel": "llm.secondary",
                        "fallbackUsed": False,
                    },
                    "stopReason": "stop",
                },
            },
        }))
    usage_path = Path(argv[argv.index("--usage-file") + 1])
    usage_path.write_text(json.dumps({
        "provider": "custom", "model": "llm.secondary", "api_calls": 1,
        "completed": True, "failed": False, "input_tokens": 100,
        "output_tokens": 10, "reasoning_tokens": 0,
    }), encoding="utf-8")
    return SimpleNamespace(returncode=0, stderr="", stdout=marker + "\n")


def _arguments(tmp_path, **overrides):
    values = {
        "base_url": "http://127.0.0.1:8000/v1",
        "alias": "llm.secondary",
        "api_key_env": "ANVIL_ROUTER_TOKEN",
        "expected_served_model": "compat-qwen38",
        "expected_config_fingerprint": "q4-mtp3",
        "expected_router_config_sha256": ROUTER_SHA,
        "min_context_tokens": 250_000,
        "clients": "openclaw,hermes",
        "openclaw_provider": "anvil",
        "hermes_provider": "anvil",
        "hermes_expected_provider": "custom",
        "timeout_seconds": 30,
        "output": str(tmp_path / "routed.json"),
        "run_id": "q4-mtp3-live",
        "environment": {"ANVIL_ROUTER_TOKEN": "secret-not-retained"},
        "opener": _opener_with_context(),
        "runner": _passing_runner,
        "syncer": _passing_syncer,
        "restart_openclaw": lambda: 0,
        "refresh_openclaw_service": lambda: 0,
    }
    values.update(overrides)
    return values


def test_routed_eval_rejects_hermes_silent_fallback_even_when_marker_matches(tmp_path):
    def fallback_runner(argv, timeout):
        completed = _passing_runner(argv, timeout)
        if argv[0] == "hermes":
            usage_path = Path(argv[argv.index("--usage-file") + 1])
            usage = json.loads(usage_path.read_text(encoding="utf-8"))
            usage.update({"provider": "openai-codex", "model": "gpt-5.6-sol"})
            usage_path.write_text(json.dumps(usage), encoding="utf-8")
        return completed

    artifact = run_routed_eval(**_arguments(tmp_path, runner=fallback_runner))

    hermes = next(result for result in artifact["results"] if result["client"] == "hermes")
    assert artifact["passed"] is False
    assert hermes["checks"]["marker_exact"] is True
    assert hermes["checks"]["provider_exact"] is False
    assert hermes["checks"]["model_exact"] is False


def test_routed_eval_persists_bounded_permission_failure(tmp_path):
    private_path = str(tmp_path / "private-install" / "openclaw")

    def denied_runner(_argv, _timeout):
        raise PermissionError(private_path)

    artifact = run_routed_eval(**_arguments(
        tmp_path, clients="openclaw", runner=denied_runner,
    ))

    assert artifact["passed"] is False
    assert artifact["results"][0]["failure"] == "client executable could not be launched"
    persisted = (tmp_path / "routed.json").read_text(encoding="utf-8")
    assert private_path not in persisted
    assert json.loads(persisted)["passed"] is False


def test_resolve_client_executable_skips_non_executable_fallback(monkeypatch, tmp_path):
    candidate = tmp_path / ".local" / "bin" / "openclaw"
    candidate.parent.mkdir(parents=True)
    candidate.write_text("not executable", encoding="utf-8")

    monkeypatch.setattr(routed_eval.shutil, "which", lambda _name: None)
    monkeypatch.setattr(routed_eval.Path, "home", classmethod(lambda _cls: tmp_path))
    monkeypatch.setattr(routed_eval.os, "access", lambda _path, _mode: False)

    assert routed_eval._resolve_client_executable("openclaw") == "openclaw"


def test_routed_eval_dry_run_has_no_network_process_or_file_side_effect(tmp_path):
    def blocked(*_args, **_kwargs):
        raise AssertionError("dry-run must not perform I/O")

    artifact = run_routed_eval(**_arguments(
        tmp_path, dry_run=True, environment={}, opener=blocked, runner=blocked,
    ))

    assert artifact["dry_run"] is True
    assert artifact["passed"] is None
    assert artifact["credentials"]["available"] is False
    assert not (tmp_path / "routed.json").exists()
