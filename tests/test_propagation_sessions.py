"""Required T011 owner checks: exact session binding and zero lifecycle effects."""
from dataclasses import replace
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading

import pytest

from anvil_serving.control_plane.propagation import ReceiptIdentity, parse_contract
from anvil_serving.propagation_sessions import (
    SessionCheck, _hash, observe_pi_web, pending, pi_model_digest, pi_catalog_digest,
    pi_web_state, session_states,
)
from tests.test_propagation_contracts import _contract

NOW = datetime(2026, 9, 28, 1, tzinfo=timezone.utc)
MODEL = {"provider": "anvil", "id": "llm.primary", "api": "openai-completions",
         "baseUrl": "http://127.0.0.1:8000/v1", "contextWindow": 262144, "maxTokens": 8192}


def check(kind="existing_session", **changes):
    value = SessionCheck("target-1", ReceiptIdentity("installation-1", "profile-1", "runtime-1"),
                         "a" * 64, "b" * 64,
                         "existing-1" if kind == "existing_session" else "new-1", kind,
                         "anvil", "llm.primary", pi_model_digest(MODEL),
                         pi_catalog_digest([MODEL]))
    return replace(value, **changes)


def observation(item, **changes):
    value = {"v": 1, "observed_at": NOW.strftime("%Y-%m-%dT%H:%M:%SZ"),
             "native_id": item.session_id, "loaded": True, "busy": False,
             "model": {key: MODEL[key] for key in ("provider", "id", "contextWindow", "maxTokens")},
             "model_digest": item.model_digest, "catalog_digest": item.catalog_digest}
    return value | changes


def contract(required, extra=()):
    target = _contract()["targets"][0] | {"expected_identity_digest": "a" * 64,
        "checks": ["catalog-equal", "session-existing-loaded", "session-new-loaded"]}
    return parse_contract(_contract(targets=[target, *extra]))


def test_http_read_is_authenticated_bounded_and_cannot_reload_racing_work():
    item = check()
    seen = []
    active = {"running": False, "status": 200, "body": None}

    class Owner(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_GET(self):
            seen.append((self.command, self.path, self.headers.get("Authorization")))
            # New work arrives after an idle observation. No lifecycle operation
            # exists in this adapter, so the new conversation remains running.
            active["running"] = True
            body = active["body"]
            if body is None:
                body = json.dumps(observation(item, busy=True, observed_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))).encode()
            self.send_response(active["status"])
            if active["status"] == 302:
                self.send_header("Location", "/must-not-follow")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            pytest.fail("session observation attempted a mutation")

    server = ThreadingHTTPServer(("127.0.0.1", 0), Owner)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    thread.start()
    try:
        kwargs = {"port": server.server_port, "token": "test-observer-token-" + "x" * 32}
        result = observe_pi_web(item, **kwargs)
        assert result["state"] == "accepted" and result["reloads"] == 0
        assert active["running"] and seen[-1] == ("GET", "/api/propagation/session-state?id=existing-1", "Bearer " + kwargs["token"])
        active["status"] = 302
        before = len(seen)
        assert observe_pi_web(item, **kwargs)["state"] == "pending"
        assert len(seen) == before + 1
        active.update(status=200, body=b"x" * (65536 + 1))
        assert observe_pi_web(item, **kwargs)["pending_reason"] == "invalid-observation"
        active.update(status=401, body=b"private-response-token")
        result = observe_pi_web(item, **kwargs)
        assert result["pending_reason"] == "authorization-denied"
        assert "private-response-token" not in json.dumps(result)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_unsupported_owner_does_not_gain_idle_reload_authority():
    result = pending(check(), "unsupported-capability")
    assert result["state"] == "pending" and result["reload_supported"] is False and result["reloads"] == 0


def test_session_projection_closes_raw_fields_and_retains_failure_freshness():
    item = check()
    declaration = contract((item,))
    value = pi_web_state(item, observation(item, model_digest="f" * 64), now=NOW)
    detail = session_states(declaration, (item,), [value], now=NOW)["target-1"]["existing_session"]
    assert detail == {"state": "pending", "pending_reason": "loaded-model-mismatch", "observed_at": value["observed_at"]}
    with pytest.raises(ValueError, match="unexpected_session_observation"):
        session_states(declaration, (item,), [value | {"prompt": "private-marker"}], now=NOW)
    malformed = pi_web_state(item, observation(item), now=NOW) | {"observed_at": "bad"}
    detail = session_states(declaration, (item,), [malformed], now=NOW)["target-1"]["existing_session"]
    assert detail["state"] == "pending" and detail["observed_at"] is None


def test_catalog_digest_is_complete_order_independent_and_strict():
    secondary = MODEL | {"id": "llm.secondary", "maxTokens": 4096}
    expected = _hash(["pi-session-catalog/v1", sorted([pi_model_digest(MODEL), pi_model_digest(secondary)])])
    assert pi_catalog_digest([secondary, MODEL, {"provider": "cloud"}]) == expected
    assert pi_catalog_digest([MODEL, secondary]) == expected
    assert pi_catalog_digest([MODEL]) != expected
    with pytest.raises(ValueError, match="duplicate_loaded_model"):
        pi_catalog_digest([MODEL, MODEL])
    for value in [MODEL | {"api": None}, MODEL | {"maxTokens": 16_777_217}, MODEL | {"baseUrl": "é" * 2049}]:
        with pytest.raises(ValueError):
            pi_catalog_digest([value])


def test_native_acceptance_separates_resume_from_loaded_limits():
    from anvil_serving.propagation_sessions import NativeSessionCheck, native_session_state
    check = NativeSessionCheck('target-1', ReceiptIdentity('install-1', 'profile-1', 'runtime-1'),
        '1' * 64, '2' * 64, '3' * 64, '4' * 64, 'fixture-1', 'prior-session',
        '2026-01-01T00:00:00Z', '2026-01-01T00:01:00Z', 'new_session',
        'native-provider', 'model-1', 8192, 512)
    receipt = {'schema': 'native-session-acceptance/v1', 'check_digest': check.digest,
        'started_at': '2026-01-01T00:01:01Z', 'completed_at': '2026-01-01T00:01:02Z',
        'native_session_id': 'new-session', 'continuity_kind': 'loaded_fixture',
        'configured_route': True, 'provider': 'native-provider', 'model': 'model-1',
        'context_tokens': 8192, 'max_output_tokens': 512, 'turn_completed': True, 'tool_probe_passed': True,
        'history_probe_passed': False, 'fallback_used': False,
        'physical_catalog_digest': '4' * 64, 'process_generation': None}
    now = datetime(2026, 1, 1, 0, 1, 3, tzinfo=timezone.utc)
    assert native_session_state(check, receipt, now=now)['state'] == 'accepted'
    for field, value, reason in (
        ('max_output_tokens', None, 'unsupported-capability'),
        ('context_tokens', 4096, 'loaded-model-mismatch'),
        ('native_session_id', 'prior-session', 'identity-mismatch'),
        ('configured_route', False, 'identity-mismatch'),
        ('physical_catalog_digest', '5' * 64, 'identity-mismatch'),
        ('fallback_used', None, 'unsupported-capability'),
        ('fallback_used', True, 'session-acceptance-pending'),
        ('started_at', '2026-01-01T00:00:59Z', 'stale-observation'),
        ('tool_probe_passed', False, 'session-acceptance-pending'),
        ('turn_completed', False, 'session-acceptance-pending')):
        observed = native_session_state(check, {**receipt, field: value}, now=now)
        assert observed['state'] == 'pending'
        assert observed['pending_reason'] == reason
    from dataclasses import replace
    resumed = replace(check, kind='existing_session')
    resumed_receipt = {**receipt, 'check_digest': resumed.digest,
                      'native_session_id': 'prior-session', 'continuity_kind': 'durable_resume',
                      'history_probe_passed': True, 'max_output_tokens': None, 'context_tokens': None}
    result = native_session_state(resumed, resumed_receipt, now=now)
    assert result['state'] == 'accepted'
    assert result['actual_max_output_tokens'] is None
    assert native_session_state(resumed, {**resumed_receipt, 'history_probe_passed': False},
                                now=now)['state'] == 'pending'
    assert native_session_state(check, receipt,
        now=datetime(2026, 1, 1, 0, 7, 3, tzinfo=timezone.utc))['pending_reason'] == 'stale-observation'
    # Managed Hermes caps describe this invocation, so both resumed and new
    # turns must prove the actual bound endpoint and ceiling.
    for selected, value in ((check, receipt), (resumed, resumed_receipt)):
        managed = replace(selected, managed_endpoint_digest='6'*64)
        actual = {**value, 'schema': 'native-session-acceptance/v2', 'check_digest': managed.digest,
            'max_output_tokens': 512, 'output_cap_mode': 'managed_invocation', 'endpoint_sha256': '6'*64}
        assert native_session_state(managed, actual, now=now)['state'] == 'accepted'
        for key, bad in (('max_output_tokens', None), ('max_output_tokens', True),
                         ('max_output_tokens', 513), ('endpoint_sha256', '7'*64),
                         ('output_cap_mode', 'router_inherited'), ('schema', 'native-session-acceptance/v1')):
            assert native_session_state(managed, {**actual, key:bad}, now=now)['state'] == 'pending'
