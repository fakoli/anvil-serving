"""Serving consumes a bounded Anvil CLI contract, never a second vendor API."""

import io
import json
from pathlib import Path
import subprocess

import pytest

from anvil_serving import cli, jev


def policy():
    return jev.validate_policy({"enabled": True, "capabilities": list(jev.CAPABILITIES), "allow_api": True,
                                "allow_export": True, "anvil_binary": str(Path(__file__).absolute())})


def choice(selected, choices):
    return {"type": "choice", "choice": selected, "confidence": 0.8,
            "probabilities": {key: int(key == selected) for key in choices}}


def completed(capability, answers, value):
    return {"ok": True, "command": "jev bridge", "data": {key: value for key, value in
        (jev.report(capability, "completed", "completed", started=True) | {"used": True,
         "answers": answers, "input_digest": jev.digest(jev.selected_input(value)), "rubric_digest": "b" * 64,
         "usage": {"input_tokens": 10, "output_tokens": 2}}).items() if key != "advisory"}}


def test_exact_bridge_payload_redacts_known_secret_and_retains_attribution():
    calls = []
    value = {"observation": "401 password=sentinel-secret"}
    answer = completed("incident_triage", {"category": choice("authentication", jev.INCIDENT_CHECKS)}, value)
    result = jev.advise("incident_triage", value, allow_export=True,
        policy_reader=policy, environment={}, bridge=lambda *args: calls.append(args) or answer)
    assert result["used"] and result["advisory"] and result["model"] == jev.MODEL
    assert "sentinel-secret" not in json.dumps(calls)
    assert set(calls[0][1]) == {"jev", "allow_api", "allow_export", "capability", "input"}
    assert jev.advice_view(result, {})["next_check"] == jev.INCIDENT_CHECKS["authentication"]


def test_sort_is_stable_baseline_preserved_and_no_calls_after_policy_change():
    value = {"intent": "read", "candidates": [{"id": key, "text": key} for key in ("first", "second", "third")]}
    scores = {key: {"type": "score", "score": score, "confidence": .5, "probabilities": {"0": 0, "1": 0, "2": 1}} for key, score in (("first", 1), ("second", 2), ("third", 2))}
    response = completed("context_ranking", scores, value)
    result = jev.advise("context_ranking", value, allow_export=True, policy_reader=policy, bridge=lambda *_: response)
    view = jev.advice_view(result, value)
    assert view["baseline"] == ["first", "second", "third"]
    assert view["order"] == ["second", "third", "first"]
    readings = iter([policy(), policy(), jev.validate_policy({})])
    late = jev.advise("context_ranking", value, allow_export=True, policy_reader=lambda: next(readings), bridge=lambda *_: response)
    assert late["reason"] == "policy_changed" and late["request_started"] and late["answers"] == {}
    assert jev.advice_view(late, value)["order"] == view["baseline"]


def test_subprocess_uses_closed_argv_bounded_output_and_minimal_environment(monkeypatch):
    seen = []
    monkeypatch.setattr("anvil_serving.control_plane.mcp.runtime._terminate_process_tree", lambda process: None)
    class Process:
        stdout = io.BytesIO(b'{"ok":true}')
        returncode = 0
        def wait(self, **kwargs): return 0
        def poll(self): return 0
    def popen(argv, **kwargs):
        seen.append((argv, kwargs, kwargs["stdin"].read()))
        return Process()
    monkeypatch.setattr(jev.subprocess, "Popen", popen)
    assert jev._bridge(policy(), {"selected": "text"}, {"TYPESAFE_API_KEY": "private", "OTHER_KEY": "excluded", "PATH": "/bin"}) == {"ok": True}
    assert seen[0][0] == [policy()["anvil_binary"], "jev", "bridge", "--json"]
    assert seen[0][1]["stderr"] == subprocess.DEVNULL
    assert seen[0][1]["env"] == {"TYPESAFE_API_KEY": "private", "PATH": "/bin", "NO_COLOR": "1", "TERM": "dumb"}
    assert json.loads(seen[0][2]) == {"selected": "text"}
    Process.stdout = io.BytesIO(b"x" * (jev.MAX_OUTPUT + 1))
    with pytest.raises(ValueError):
        jev._bridge(policy(), {}, {})


def test_cli_short_workflow_is_default_off_repeatable_and_disabled_input_unread(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("ANVIL_SERVING_HOME", str(tmp_path))
    assert cli.main(["workbench", "jev", "setup", "--anvil-binary", str(Path(__file__).absolute()), "--confirm", "--json"]) == 0
    assert not json.loads(capsys.readouterr().out)["data"]["enabled"]
    assert cli.main(["workbench", "jev", "setup", "--anvil-binary", str(Path(__file__).absolute()), "--confirm", "--json"]) == 0
    assert not json.loads(capsys.readouterr().out)["data"]["changed"]
    assert cli.main(["workbench", "jev", "advise", "voice_intent", "--input", str(tmp_path / "missing"), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["data"]["status"] == "disabled"
    assert cli.main(["workbench", "jev", "enable", "voice_intent", "--allow-api", "--allow-export", "--confirm", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["data"]["capabilities"] == ["voice_intent"]
    assert cli.main(["workbench", "jev", "disable", "--confirm", "--json"]) == 0
    assert not json.loads(capsys.readouterr().out)["data"]["enabled"]


@pytest.mark.parametrize("capability,detail", [
    ("context_ranking", "text"), ("skill_suggestion", "description"),
])
def test_secret_shaped_candidate_ids_are_rejected_without_export_or_identity_loss(capability, detail):
    identifiers = ["AKIA" + "A" * 16, "sk-" + "a" * 16]
    value = {"intent": "read", "candidates": [
        {"id": key, detail: "selected public summary"} for key in identifiers
    ]}
    result = jev.advise(capability, value, allow_export=True, policy_reader=policy, environment={},
        bridge=lambda *_: pytest.fail("secret-shaped candidate IDs dispatched"))
    assert result["status"] == "blocked" and not result["request_started"]
    assert not result["used"] and result["answers"] == {}
    assert [row["id"] for row in value["candidates"]] == identifiers
    view = jev.advice_view(result, value)
    if capability == "context_ranking":
        assert view["order"] == view["baseline"] == identifiers


def test_cli_rejects_secret_file_without_opening_it(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(jev, "load_policy", policy)
    secret = tmp_path / ".env.private"
    secret.write_text("sentinel-secret")
    original = Path.open
    def guarded(path, *args, **kwargs):
        if path == secret:
            pytest.fail("secret input was opened")
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, "open", guarded)
    assert cli.main(["workbench", "jev", "advise", "voice_intent", "--input", str(secret), "--allow-export", "--json"]) != 0
    assert "sentinel-secret" not in capsys.readouterr().out


def test_invalid_typed_answer_retains_independently_validated_usage():
    value = {'observation': 'Unknown failure'}
    response = completed('incident_triage', {'category': choice('authentication', jev.INCIDENT_CHECKS)}, value)
    response['data']['answers']['category']['choice'] = 'unoffered'
    response['data']['usage_receipt'] = {'state': 'validated', 'input_tokens': 10, 'output_tokens': 2}
    result = jev.advise('incident_triage', value, allow_export=True, policy_reader=policy, bridge=lambda *_: response)
    assert not result['used'] and result['answers'] == {}
    assert result['usage_receipt'] == response['data']['usage_receipt']
    response['data']['usage_receipt']['input_tokens'] = True
    result = jev.advise('incident_triage', value, allow_export=True, policy_reader=policy, bridge=lambda *_: response)
    assert 'usage_receipt' not in result
