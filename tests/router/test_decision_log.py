"""Metadata-only audit records for the direct gateway."""
from __future__ import annotations

import dataclasses
import json
import http.client
from pathlib import Path

import pytest

from anvil_serving.router.decision_log import (
    AttemptRecord, DecisionLog, DecisionLogWriter,
    DecisionRecord, summarize_decisions,
)
from anvil_serving.router.decision_log import decision_line
from anvil_serving.router.replica_scheduler import (
    PressureFreshness,
    ReplicaDecision,
    ReplicaDecisionReason,
    ReplicaScore,
)


def _record(*, reason="served", served=True):
    return DecisionRecord(
        kind="chat",
        requested_tier="primary-local",
        attempts=(AttemptRecord("primary-local", served, reason, 3, 2 if served else 0, "served" if served else "skipped"),),
        served_tier="primary-local" if served else None,
        total_prompt_tokens=3,
        total_completion_tokens=2 if served else 0,
        route="llm.primary",
    )


def test_summary_keeps_only_metadata_and_redacts_secret_shaped_values():
    log = DecisionLog()
    log.record(_record(reason="Bearer this-is-a-token"))

    summary = summarize_decisions(log.records)

    attempt = summary["records"][0]["attempts"][0]
    assert attempt["reason"] == "<redacted>"
    assert "this-is-a-token" not in repr(summary)
    assert summary["totals"]["served_tiers"] == {"primary-local": 1}


class _UnsafeValue:
    def __str__(self):
        raise AssertionError("optional metadata must not be stringified")


class _StringSubclass(str):
    pass


def test_history_ignores_malformed_and_oversized_lines_without_leaking_them(tmp_path):
    path = tmp_path / "decisions.jsonl"
    writer = DecisionLogWriter(str(path))
    log = DecisionLog(sink=writer)
    log.record(dataclasses.replace(_record(), session_id="safe-session"))
    with path.open("ab") as handle:
        handle.write(b'{"session_id":"safe-session","prompt":"PRIVATE-PROMPT"}\n')
        handle.write(b"{" + b"x" * (64 * 1024 + 1) + b"}\n")
        handle.write(b"not-json\n")
    history = log.lookup_history(session_id="safe-session")
    encoded = repr(history)
    assert len(history["records"]) == 2
    assert "PRIVATE-PROMPT" not in encoded
    assert "PRIVATE-PROMPT" not in encoded


def test_real_http_session_history_is_accepted_by_diagnose_session(tmp_path):
    from anvil_serving import router_diagnostics
    from anvil_serving.router.config import load
    from anvil_serving.router.serve import RoutingBackend
    from tests.router.helpers import http_get, server_context

    class Backend:
        def generate(self, request):
            yield "ok"

    config = load(Path(__file__).resolve().parents[2] / "configs" / "example.toml")
    session_id = "pi-8f6b2a7c-77e5-4d83-a1c4-b4afed0ab123"
    log = DecisionLog(sink=DecisionLogWriter(str(tmp_path / "decisions.jsonl")))
    routing = RoutingBackend(config, {"primary-local": Backend()}, decision_log=log)
    with server_context(routing, token="test-credential") as (host, port):
        for _ in range(2):
            connection = http.client.HTTPConnection(host, port, timeout=5)
            try:
                connection.request("POST", "/v1/chat/completions", json.dumps({
                    "model": "llm.primary", "messages": [{"role": "user", "content": "private prompt"}],
                }), {
                    "Authorization": "Bearer test-credential", "Content-Type": "application/json",
                    "X-Anvil-Session-Id": session_id,
                })
                response = connection.getresponse()
                assert response.status == 200
                response.read()
            finally:
                connection.close()
        status, _, body = http_get(
            host, port, f"/v1/requests?session_id={session_id}&history=1",
            token="test-credential",
        )
        history = json.loads(body)
        assert status == 200
        assert history["object"] == "router_request_history"
        assert history["scope"] == "decision_log_jsonl"
        assert history["session_id"] == session_id
        assert len(history["records"]) == 2
        assert history["truncated"] is False
        assert history["available"] is True
        result = router_diagnostics.diagnose_session(
            session_id, router_url=f"http://{host}:{port}", token="test-credential",
        )
    assert result["scope"] == "decision_log_jsonl"
    assert result["session_id"] == session_id
    assert len(result["requests"]) == 2


def _scheduler_decision():
    score = ReplicaScore(
        "member-a", 0, 2, False, 100, 0, PressureFreshness.FRESH,
    )
    return ReplicaDecision(
        "member-a", ("member-a",), (score,), ReplicaDecisionReason.SELECTED,
    )


@pytest.mark.parametrize("mutate", [
    lambda payload: payload.update(extra="private-token"),
    lambda payload: payload.update(selected_member_id="member-b"),
    lambda payload: payload.update(eligible_member_ids=["member-a"] * 17),
    lambda payload: payload["scores"][0].update(local_numerator=True),
    lambda payload: payload["scores"][0].update(freshness="future-private"),
    lambda payload: payload["scores"][0].update(local_numerator=-1),
], ids=["extra", "mismatch", "oversize", "numeric-bool", "freshness", "numeric-range"])
def test_captured_scheduler_json_requires_the_closed_canonical_shape(mutate):
    payload = _scheduler_decision().to_dict()
    mutate(payload)
    summary = summarize_decisions([{"replica_scheduler": payload}])
    assert "replica_scheduler" not in summary["records"][0]


def test_invalid_scheduler_never_leaks_private_nested_values_to_any_audit_surface(tmp_path):
    decision = _scheduler_decision()
    object.__setattr__(decision, "selected_member_id", "private-token")
    record = dataclasses.replace(
        _record(), replica_member_id="member-a", replica_selection="identity_passed",
        replica_scheduler=decision,
    )
    path = tmp_path / "private-scheduler.jsonl"
    log = DecisionLog(sink=DecisionLogWriter(str(path)))
    log.record(record)
    combined = repr(log.last) + repr(log.summary()) + decision_line(log.last) + path.read_text()
    assert "private-token" not in combined
    assert log.last.replica_member_id == "member-a"
