"""Tests for `anvil-serving eval` — the unified eval runner.

subprocess + HTTP are injected (`_call`/`_open` seams), so nothing is shelled and
no endpoint is contacted.
"""
import os
import types

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


# ---- tier resolution from the shipped manifest ------------------------------

def test_tiers_read_from_manifest():
    tiers = ev._tiers()
    assert {"heavy", "fast"} <= set(tiers)
    assert tiers["fast"]["model"] == "gpt-oss-20b"
    assert tiers["fast"]["base_url"] == "http://127.0.0.1:30001/v1"
    assert tiers["heavy"]["base_url"] == "http://127.0.0.1:30000/v1"


# ---- preflight/benchmark endpoint resolution --------------------------------

def test_unreachable_tier_hints_and_does_not_shell(capsys):
    a = types.SimpleNamespace(tier="fast", base_url=None, model=None, extra=[])
    calls = []
    rc = ev._run_endpoint_eval("preflight.py", a,
                               _call=lambda argv: calls.append(argv) or 0,
                               _open=_unreachable)
    assert rc == 3
    assert calls == []  # never shelled the eval
    assert "serves up fast" in capsys.readouterr().err


def test_reachable_tier_fills_base_url_and_model():
    a = types.SimpleNamespace(tier="fast", base_url=None, model=None,
                              extra=["--requests", "3"])
    captured = {}
    rc = ev._run_endpoint_eval("preflight.py", a,
                               _call=lambda argv: captured.update(argv=argv) or 0,
                               _open=_reachable)
    assert rc == 0
    argv = captured["argv"]
    assert "--base-url" in argv and "http://127.0.0.1:30001/v1" in argv
    assert "--model" in argv and "gpt-oss-20b" in argv
    assert "--requests" in argv and "3" in argv          # passthrough preserved
    assert os.path.basename(argv[0]) == os.path.basename(ev.sys.executable)
    assert argv[1].endswith("preflight.py")


def test_explicit_base_url_and_model_need_no_tier():
    a = types.SimpleNamespace(tier=None, base_url="http://x/v1", model="m", extra=[])
    captured = {}
    rc = ev._run_endpoint_eval("benchmark.py", a,
                               _call=lambda argv: captured.update(argv=argv) or 0,
                               _open=_unreachable)
    assert rc == 0
    assert "http://x/v1" in captured["argv"] and "m" in captured["argv"]


def test_unknown_tier_errors():
    a = types.SimpleNamespace(tier="nope", base_url=None, model=None, extra=[])
    assert ev._run_endpoint_eval("preflight.py", a,
                                 _call=lambda argv: 0, _open=_reachable) == 2


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
    assert ran == ["eval_gen.py"]  # did not proceed to grading


def test_bootstrap_builds_replay_command():
    a = types.SimpleNamespace(eval_data="EVDIR", out="OUT.json")
    captured = {}
    ev._run_bootstrap(a, _call=lambda argv: captured.update(argv=argv) or 0)
    argv = captured["argv"]
    assert "anvil_serving.router.profile_bootstrap" in argv
    assert "--replay" in argv and "EVDIR" in argv
    assert "--out" in argv and "OUT.json" in argv
