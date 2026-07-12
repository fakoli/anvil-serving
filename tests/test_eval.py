"""Tests for `anvil-serving eval` — the unified eval runner.

subprocess + HTTP are injected (`_call`/`_open` seams), so nothing is shelled and
no endpoint is contacted.
"""
import os
import types
import urllib.error

import pytest

from anvil_serving import eval as ev


class _CM:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _reachable(*a, **k):
    return _CM()


def _unreachable(*a, **k):
    raise OSError("connection refused")


def _http_error(*a, **k):
    raise urllib.error.HTTPError("http://x", 503, "loading", {}, None)


def _rec(captured):
    """A fake _call that records argv and returns 0."""
    return lambda argv, **k: captured.update(argv=argv) or 0


# ---- tier resolution from the shipped manifest ------------------------------

def test_tiers_read_from_manifest():
    tiers = ev._tiers()
    assert {"heavy", "fast"} <= set(tiers)
    assert tiers["fast"]["model"] == "qwen36-35b-a3b-nvfp4"
    assert tiers["fast"]["base_url"] == "http://127.0.0.1:30003/v1"
    assert tiers["heavy"]["base_url"] == "http://127.0.0.1:30002/v1"


def test_resolve_endpoint_target_supports_manifest_and_direct_inputs(tmp_path):
    manifest = tmp_path / "serves.toml"
    manifest.write_text(
        '[[serve]]\nname = "fast"\ncontainer = "fast"\nport = 31000\n'
        'model = "served-fast"\nengine = "vllm"\nup = "docker start fast"\n',
        encoding="utf-8",
    )
    assert ev.resolve_endpoint_target(tier="fast", manifest=str(manifest))[:2] == (
        "http://127.0.0.1:31000/v1", "served-fast"
    )
    assert ev.resolve_endpoint_target(base_url="http://127.0.0.1:8000/v1", model="m")[:2] == (
        "http://127.0.0.1:8000/v1", "m"
    )


def test_resolve_endpoint_target_rejects_incomplete_modes():
    with pytest.raises(ValueError, match="--manifest requires --tier"):
        ev.resolve_endpoint_target(manifest="serves.toml")
    with pytest.raises(ValueError, match="both --base-url and --model"):
        ev.resolve_endpoint_target(base_url="http://127.0.0.1:8000/v1")


# ---- reachability (lenient) -------------------------------------------------

def test_reachable_true_on_response():
    assert ev._reachable(1, "/health", _open=_reachable) is True


def test_reachable_true_on_http_error_means_server_is_up():
    # a serve still loading (503) is UP, not "unreachable"
    assert ev._reachable(1, "/health", _open=_http_error) is True


def test_reachable_false_only_on_connection_failure():
    assert ev._reachable(1, "/health", _open=_unreachable) is False


# ---- preflight/benchmark endpoint resolution --------------------------------

def test_unreachable_tier_hints_and_does_not_shell(capsys):
    a = types.SimpleNamespace(tier="fast", base_url=None, model=None)
    calls = []
    rc = ev._run_endpoint_eval("preflight.py", a, [],
                               _call=lambda argv: calls.append(argv) or 0,
                               _open=_unreachable)
    assert rc == 3
    assert calls == []  # never shelled the eval
    assert "serves up fast" in capsys.readouterr().err


def test_reachable_tier_fills_base_url_and_model_and_passthrough():
    a = types.SimpleNamespace(tier="fast", base_url=None, model=None)
    captured = {}
    rc = ev._run_endpoint_eval("preflight.py", a, ["--requests", "3"],
                               _call=_rec(captured), _open=_reachable)
    assert rc == 0
    argv = captured["argv"]
    assert "--base-url" in argv and "http://127.0.0.1:30003/v1" in argv
    assert "--model" in argv and "qwen36-35b-a3b-nvfp4" in argv
    assert "--requests" in argv and "3" in argv          # passthrough preserved
    assert argv[1].endswith("preflight.py")


def test_base_url_override_skips_reachability_gate():
    # explicit --base-url points elsewhere -> do NOT gate on the local tier port
    a = types.SimpleNamespace(tier="fast", base_url="http://remote:8000/v1", model=None)
    captured = {}
    rc = ev._run_endpoint_eval("benchmark.py", a, [],
                               _call=_rec(captured), _open=_unreachable)
    assert rc == 0  # not 3 — gate skipped
    assert "http://remote:8000/v1" in captured["argv"]
    assert "qwen36-35b-a3b-nvfp4" in captured["argv"]  # model still filled from the tier


def test_explicit_base_url_and_model_need_no_tier():
    a = types.SimpleNamespace(tier=None, base_url="http://x/v1", model="m")
    captured = {}
    rc = ev._run_endpoint_eval("benchmark.py", a, [], _call=_rec(captured), _open=_unreachable)
    assert rc == 0
    assert "http://x/v1" in captured["argv"] and "m" in captured["argv"]


def test_unknown_tier_errors():
    a = types.SimpleNamespace(tier="nope", base_url=None, model=None)
    assert ev._run_endpoint_eval("preflight.py", a, [], _call=lambda argv: 0, _open=_reachable) == 2


def test_manifest_error_is_surfaced_not_swallowed(monkeypatch, capsys):
    def boom():
        raise ValueError("serve entry missing name/container/port")
    monkeypatch.setattr(ev, "_tiers", boom)
    a = types.SimpleNamespace(tier="fast", base_url=None, model=None)
    rc = ev._run_endpoint_eval("preflight.py", a, [], _call=lambda argv: 0, _open=_reachable)
    assert rc == 2
    assert "cannot read serves manifest" in capsys.readouterr().err


# ---- flag passthrough via main() (no `--` separator needed) -----------------

def test_main_passthrough_without_separator(monkeypatch):
    captured = {}
    monkeypatch.setattr(ev, "_run_endpoint_eval",
                        lambda script, a, extra, **k: captured.update(script=script, extra=extra) or 0)
    rc = ev.main(["preflight", "--tier", "fast", "--requests", "3"])
    assert rc == 0
    assert captured["script"] == "preflight.py"
    assert captured["extra"] == ["--requests", "3"]


def test_main_planning_rejects_unknown_flags():
    with pytest.raises(SystemExit):
        ev.main(["planning", "--bogus"])


# ---- planning + bootstrap orchestration -------------------------------------

def test_planning_offline_skips_gen():
    a = types.SimpleNamespace(offline=True, dir="D")
    ran = []
    ev._run_planning(a, _call=lambda argv, cwd=None: ran.append(os.path.basename(argv[-1])) or 0)
    assert ran == ["grade_struct.py", "aggregate.py"]


def test_planning_live_runs_gen_first():
    a = types.SimpleNamespace(offline=False, dir="D")
    ran = []
    ev._run_planning(a, _call=lambda argv, cwd=None: ran.append(os.path.basename(argv[-1])) or 0)
    assert ran[0] == "eval_gen.py"
    assert ran[-2:] == ["grade_struct.py", "aggregate.py"]


def test_planning_aborts_if_gen_fails():
    a = types.SimpleNamespace(offline=False, dir="D")
    ran = []

    def call(argv, cwd=None):
        name = os.path.basename(argv[-1])
        ran.append(name)
        return 1 if name == "eval_gen.py" else 0

    assert ev._run_planning(a, _call=call) == 1
    assert ran == ["eval_gen.py"]


def test_planning_relative_dir_made_absolute():
    # a relative --dir must be abspath'd so cwd=d doesn't double-join the script path
    a = types.SimpleNamespace(offline=True, dir="reldir")
    paths = []
    ev._run_planning(a, _call=lambda argv, cwd=None: paths.append(argv[-1]) or 0)
    assert paths and all(os.path.isabs(p) for p in paths)


def test_bootstrap_builds_replay_command():
    a = types.SimpleNamespace(eval_data="EVDIR", out="OUT.json")
    captured = {}
    ev._run_bootstrap(a, _call=_rec(captured))
    argv = captured["argv"]
    assert "anvil_serving.router.profile_bootstrap" in argv
    assert "--replay" in argv and "EVDIR" in argv
    assert "--out" in argv and "OUT.json" in argv
