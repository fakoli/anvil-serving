"""Serving consumes a bounded Anvil CLI contract, never a second vendor API."""

import io
import json
import math
import os
from pathlib import Path
import subprocess

import pytest

from anvil_serving import cli, jev
from anvil_serving import jev_cli


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


def test_disabled_and_policy_denials_never_inspect_input_or_environment():
    class Hostile(dict):
        def __iter__(self):
            pytest.fail("disabled input or environment inspected")
        def __contains__(self, key):
            pytest.fail("disabled credential lookup")
    for settings, consent, disabled in [(jev.validate_policy({}), True, False), (policy() | {"allow_api": False}, True, False),
                                        (policy(), False, False), (policy(), True, True)]:
        result = jev.advise("voice_intent", Hostile(), allow_export=consent, disabled=disabled, environment=Hostile(),
                            policy_reader=lambda: settings, bridge=lambda *_: pytest.fail("disabled subprocess"))
        assert not result["request_started"] and not result["used"]


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


@pytest.mark.parametrize("mutate", [
    lambda data: data.update(model="latest"),
    lambda data: data.update(used=False),
    lambda data: data.update(usage={"input_tokens": True, "output_tokens": 1}),
    lambda data: data.update(private_error="sentinel"),
    lambda data: data.update(input_digest="a" * 64),
    lambda data: data["answers"]["intent"].update(choice="restart"),
    lambda data: data["answers"]["intent"].update(confidence=math.nan),
    lambda data: data["answers"]["intent"].update(probabilities={"forged": 1}),
    lambda data: data["answers"]["intent"].update(command="restart"),
])
def test_invalid_report_never_reaches_a_consumer(mutate):
    response = completed("voice_intent", {"intent": choice("conversation", ["conversation", "read_only_information", "operational_change_request", "unclear", "unsupported"])}, {"text": "hello"})
    mutate(response["data"])
    result = jev.advise("voice_intent", {"text": "hello"}, allow_export=True, policy_reader=policy, bridge=lambda *_: response)
    assert result["status"] == "invalid_response" and result["answers"] == {} and not result["used"]
    assert "sentinel" not in json.dumps(result)


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


def test_policy_rechecked_at_last_dispatch_boundary():
    readings = iter([policy(), jev.validate_policy({})])
    result = jev.advise("voice_intent", {"text": "hello"}, allow_export=True,
        policy_reader=lambda: next(readings), bridge=lambda *_: pytest.fail("revoked call dispatched"))
    assert result["reason"] == "policy_changed" and not result["request_started"]


def test_subprocess_uses_closed_argv_bounded_output_and_minimal_environment(monkeypatch):
    seen = []
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


@pytest.mark.parametrize("value", [{"enabled": 1}, {"model": "jev-latest"}, {"capabilities": ["routes"]}, {"timeout_seconds": float("inf")}, {"anvil_binary": "relative"}])
def test_policy_rejects_unapproved_extensions(value):
    with pytest.raises(ValueError):
        jev.validate_policy(value)


@pytest.mark.parametrize("value", [
    {}, {"text": "x" * 4097}, {"text": "hello", "path": "/private"},
    {"text": "-----BEGIN PRIVATE KEY-----"}, {"text": True},
])
def test_invalid_selected_text_does_not_start_bridge(value):
    result = jev.advise("voice_intent", value, allow_export=True, policy_reader=policy,
                        bridge=lambda *_: pytest.fail("invalid input dispatched"))
    assert result["status"] == "blocked" and not result["request_started"]


def test_duplicate_json_and_invalid_context_candidates_are_rejected():
    with pytest.raises(ValueError):
        jev.decode_json('{"enabled":false,"enabled":true}')
    for rows in ([{"id": "none", "text": "one"}], [{"id": "NONE", "text": "one"}],
                 [{"id": "a" * 49, "text": "one"}], [{"id": "a", "text": "one"}] * 2,
                 [{"id": f"candidate{number}", "text": "one"} for number in range(25)]):
        result = jev.advise("context_ranking", {"intent": "read", "candidates": rows},
            allow_export=True, policy_reader=policy, bridge=lambda *_: pytest.fail("invalid candidates dispatched"))
        assert not result["used"] and not result["request_started"]


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


@pytest.mark.parametrize("failure,started", [(FileNotFoundError("private path"), False),
                                            (subprocess.TimeoutExpired("private argv", 2), True)])
def test_optional_binary_failures_are_sanitized_and_nonfatal(failure, started):
    def fail(*args):
        raise failure
    result = jev.advise("voice_intent", {"text": "hello"}, allow_export=True, policy_reader=policy, bridge=fail)
    assert result["status"] == "unavailable" and result["request_started"] is started
    assert "private" not in json.dumps(result)


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


def test_optional_files_refuse_links_and_nonregular_sources(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.write_text("{}")
    link = tmp_path / "jev.json"
    link.symlink_to(source)
    monkeypatch.setenv("ANVIL_SERVING_HOME", str(tmp_path))
    assert jev.status()["reason"] == "invalid_configuration"
    with pytest.raises(ValueError):
        jev.read_regular(link, 32)
    if hasattr(os, "mkfifo"):
        fifo = tmp_path / "fifo"
        os.mkfifo(fifo)
        with pytest.raises(ValueError):
            jev.read_regular(fifo, 32)


def test_config_writer_refuses_concurrent_disable(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("ANVIL_SERVING_HOME", str(tmp_path))
    path = jev.policy_path()
    original_write = jev_cli.atomic_write_json
    initial = policy()
    original_write(path, initial)
    def concurrent(temporary, value):
        original_write(path, initial | {"enabled": False, "allow_api": False, "allow_export": False})
        original_write(temporary, value)
    monkeypatch.setattr(jev_cli, "atomic_write_json", concurrent)
    assert cli.main(["workbench", "jev", "setup", "--anvil-binary", str(Path(jev.__file__).absolute()), "--confirm", "--json"]) != 0
    assert not jev.load_policy()["enabled"]
    assert jev.load_policy()["anvil_binary"] == initial["anvil_binary"]
    assert "concurrently" in capsys.readouterr().out


def test_config_writers_use_existing_nonblocking_owner_lock(tmp_path, monkeypatch, capsys):
    from anvil_serving.service_runtime.operations import _lock
    monkeypatch.setenv("ANVIL_SERVING_HOME", str(tmp_path))
    jev_cli.atomic_write_json(jev.policy_path(), policy() | {"enabled": False})
    with _lock(jev.policy_path()):
        assert cli.main(["workbench", "jev", "enable", "voice_intent", "--allow-api", "--allow-export", "--confirm", "--json"]) != 0
    assert not jev.load_policy()["enabled"]
    capsys.readouterr()


def test_disable_then_identical_reenable_invalidates_inflight_advice(tmp_path, monkeypatch):
    monkeypatch.setenv("ANVIL_SERVING_HOME", str(tmp_path))
    path = jev.policy_path()
    initial = policy()
    jev_cli.atomic_write_json(path, initial)
    value = {"text": "hello"}
    def bridge(*args):
        jev_cli.atomic_write_json(path, initial | {"enabled": False})
        jev_cli.atomic_write_json(path, initial)
        return completed("voice_intent", {"intent": choice("conversation", ["conversation", "read_only_information", "operational_change_request", "unclear", "unsupported"])}, value)
    result = jev.advise("voice_intent", value, allow_export=True, bridge=bridge)
    assert result["reason"] == "policy_changed" and not result["used"] and result["request_started"]
    assert dict(jev.load_policy()) == initial
