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


def test_exact_existing_session_cannot_be_replaced_by_fresh_process():
    old, new = check(), check("new_session")
    declaration = contract((old, new))
    fresh = pi_web_state(new, observation(new), now=NOW)
    states = session_states(declaration, (old, new), [fresh], now=NOW)
    assert states["target-1"]["new_session"]["state"] == "accepted"
    assert states["target-1"]["existing_session"]["state"] == "pending"
    substituted = pi_web_state(old, observation(new), now=NOW)
    assert substituted["pending_reason"] == "identity-mismatch"
    missing = session_states(declaration, (new,), [fresh], now=NOW)
    assert missing["target-1"]["existing_session"]["state"] == "pending"
    with pytest.raises(ValueError, match="session_inventory_mismatch"):
        session_states(declaration, (replace(new, expected_identity_digest="c" * 64),), [fresh], now=NOW)
    with pytest.raises(ValueError, match="duplicate_session_inventory"):
        session_states(declaration, (old, replace(old, kind="new_session")), [], now=NOW)


def test_every_declared_cli_web_profile_stays_in_denominator():
    item = check()
    other = _contract()["targets"][0] | {
        "target_id": "cli-2", "installation_id": "installation-2", "profile_id": "profile-2", "runtime_id": "cli-2", "checks": ["session-existing-loaded", "session-new-loaded"]}
    states = session_states(contract((item,), (other,)), (item,),
                            [pi_web_state(item, observation(item), now=NOW)], now=NOW)
    assert states["target-1"]["existing_session"]["state"] == "accepted"
    assert states["target-1"]["new_session"]["state"] == "pending"
    assert all(row["state"] == "pending" and row["pending_reason"] == "unsupported-capability" for row in states["cli-2"].values())
    for changed in (replace(item, expected_identity_digest="c" * 64),
                    replace(item, identity=replace(item.identity, profile_id="other"))):
        with pytest.raises(ValueError):
            session_states(contract((item,)), (changed,), [], now=NOW)


@pytest.mark.parametrize("changes", [
    {"loaded": False}, {"model": None}, {"catalog_digest": None}, {"model_digest": None},
    {"catalog_digest": "b" * 64}, {"model_digest": "b" * 64},
    {"model": {"provider": "anvil", "id": "llm.primary"}},
    {"model": {"provider": "anvil", "id": "llm.primary", "contextWindow": True, "maxTokens": 8192}},
    {"observed_at": "2026-09-28T00:00:00Z"}, {"observed_at": "2026-09-29T00:00:00Z"},
    {"observed_at": None}, {"v": True}, {"systemPrompt": "must-not-leak"},
])
def test_incomplete_stale_or_overbroad_owner_observation_remains_pending(changes):
    item = check()
    result = pi_web_state(item, observation(item, **changes), now=NOW)
    assert result["state"] == "pending" and result["reloads"] == 0
    assert "must-not-leak" not in json.dumps(result)


def test_complete_catalog_is_required_beside_selected_model():
    item = check()
    full = pi_web_state(item, observation(item), now=NOW)
    assert full["state"] == "accepted"
    assert session_states(contract((item,)), (item,), [full], now=NOW)["target-1"]["existing_session"]["state"] == "accepted"
    stale = NOW.replace(hour=2)
    assert session_states(contract((item,)), (item,), [full], now=stale)["target-1"]["existing_session"]["state"] == "pending"
    with pytest.raises(ValueError, match="invalid_session_observations"):
        session_states(contract((item,)), (item,), [full, full], now=NOW)


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
    thread = threading.Thread(target=server.serve_forever, daemon=True)
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


def test_packaged_native_observer_executes_read_only_and_filters_private_state():
    """Execute shipped TypeScript with a native-session double, not a reimplementation."""
    from pathlib import Path
    import subprocess
    from anvil_serving.workbench_app.pi_web import bridge_manifest
    import hashlib

    patch = Path(__file__).parents[1] / "anvil_serving/_pi_web_bridge/0.9.2-host-bridge.patch"
    raw = patch.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == bridge_manifest("0.9.2")["patch_sha256"]

    def added_file(name):
        block = raw.decode().split(f"+++ b/{name}\n", 1)[1].split("diff --git ", 1)[0]
        return "\n".join(line[1:] for line in block.splitlines() if line.startswith("+"))

    payload = {"source": added_file("lib/propagation-observer.ts"),
               "route": added_file("app/api/propagation/session-state/route.ts"),
               "auth": added_file("lib/propagation-observer-auth.ts"),
               "model": MODEL, "digest": pi_model_digest(MODEL),
               "catalog": check().catalog_digest,
               "second_model": MODEL | {"id": "llm.secondary", "maxTokens": 4096},
               "multi_catalog": pi_catalog_digest([MODEL, MODEL | {"id": "llm.secondary", "maxTokens": 4096}])}
    program = r'''
const assert = require('node:assert/strict');
const {stripTypeScriptTypes} = require('node:module');
const {createHash, timingSafeEqual} = require('node:crypto');
const input = JSON.parse(require('node:fs').readFileSync(0, 'utf8'));
let selected = {...input.model, apiKey: 'private-marker', headers: {secret: 'private-marker'}};
let catalog = [selected, {provider:'cloud', id:'private-marker'}];
let alive = true, busy = false, lookup = 0, effects = 0;
const inner = {
  sessionId:'existing-1', get model() { return selected; },
  modelRuntime:{getAvailableSnapshot() { return catalog; }},
  messages:['private-marker'], systemPrompt:'private-marker',
};
const mutation = () => { effects++; throw Error('forbidden lifecycle effect'); };
const session = {inner, isAlive:()=>alive, isRunning:()=>busy, prompt:mutation,
                 stop:mutation, destroy:mutation, reload:mutation, setModel:mutation};
const getRpcSession = id => { lookup++; return id === 'existing-1' ? session : undefined; };
const source = stripTypeScriptTypes((input.auth + '\n' + input.source).replace(/^import .*;\n/gm, '').replace(/export /g, ''));
const api = new Function('getRpcSession','createHash','timingSafeEqual', source +
  '\nreturn {observePropagationSession, propagationObserverAuthorized, validPropagationNativeId};')
  (getRpcSession,createHash,timingSafeEqual);
const token = 'observer-' + 'x'.repeat(40);
process.env.PI_WEB_PROPAGATION_OBSERVER_TOKEN=token;
process.env.PI_WEB_WORKBENCH_BRIDGE_TOKEN='bridge-'+'y'.repeat(40);
process.env.PI_WEB_PASSWORD='password-'+'z'.repeat(40);
assert.equal(api.propagationObserverAuthorized('Bearer '+token),true);
for (const invalid of [null,'Bearer wrong','Basic '+token])
  assert.equal(api.propagationObserverAuthorized(invalid),false);
for (const name of ['PI_WEB_WORKBENCH_BRIDGE_TOKEN','PI_WEB_PASSWORD']) {
  const saved=process.env[name]; process.env[name]=token;
  assert.equal(api.propagationObserverAuthorized('Bearer '+token),false);
  process.env[name]=saved;
}
process.env.PI_WEB_PROPAGATION_OBSERVER_TOKEN='short';
assert.equal(api.propagationObserverAuthorized('Bearer short'),false);
process.env.PI_WEB_PROPAGATION_OBSERVER_TOKEN=token;
let value=api.observePropagationSession('existing-1');
assert.deepEqual(Object.keys(value).sort(),['v','observed_at','native_id','loaded','busy','model','model_digest','catalog_digest'].sort());
assert.equal(value.model_digest,input.digest); assert.equal(value.catalog_digest,input.catalog);
assert.equal(value.loaded,true); assert.equal(value.busy,false);
assert.equal(JSON.stringify(value).includes('private-marker'),false);
assert.equal(JSON.stringify(value).includes('baseUrl'),false);
busy=true; value=api.observePropagationSession('existing-1');
assert.equal(value.busy,true); assert.equal(effects,0);
catalog=[input.second_model, selected];
assert.equal(api.observePropagationSession('existing-1').catalog_digest,input.multi_catalog);
catalog.reverse();
assert.equal(api.observePropagationSession('existing-1').catalog_digest,input.multi_catalog);
catalog=[selected, selected];
assert.equal(api.observePropagationSession('existing-1').catalog_digest,null);
catalog=[{...selected, api:undefined}];
assert.equal(api.observePropagationSession('existing-1').catalog_digest,null);
selected={provider:'cloud',id:'private-marker'};
assert.equal(api.observePropagationSession('existing-1').model,null);
alive=false;
assert.equal(api.observePropagationSession('existing-1').loaded,false);
assert.equal(api.observePropagationSession('unknown-1').loaded,false);
assert.equal(api.validPropagationNativeId('../private'),false);
assert.equal(effects,0);
const NextResponse={json:(body,options={})=>({body,status:options.status||200,headers:options.headers})};
const routeSource=stripTypeScriptTypes(input.route.replace(/import [\s\S]*?;\n/g,'').replace(/export /g,''));
const GET=new Function('NextResponse',...Object.keys(api),routeSource+'\nreturn GET;')(NextResponse,...Object.values(api));
const request=(query,auth)=>new Request('http://127.0.0.1/api/propagation/session-state'+query,{headers:auth?{authorization:auth}:{}});
let before=lookup;
assert.equal(GET(request('?id=existing-1')).status,404); assert.equal(lookup,before);
for (const query of ['?id=../private','?id=existing-1&id=other','?id=existing-1&extra=x']) {
  assert.equal(GET(request(query,'Bearer '+token)).status,404); assert.equal(lookup,before);
}
const response=GET(request('?id=existing-1','Bearer '+token));
assert.equal(response.status,200); assert.equal(response.headers['Cache-Control'],'no-store');
assert.equal(effects,0);
'''
    result = subprocess.run(["node", "--disable-warning=ExperimentalWarning", "-e", program],
                            input=json.dumps(payload), capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr


def test_approved_session_check_set_controls_not_required_and_missing_evidence():
    item = check("new_session")
    target = contract((item,)).value["targets"][0] | {"checks": ["session-new-loaded"]}
    declaration = parse_contract(_contract(targets=[target]))
    states = session_states(declaration, (item,), [], now=NOW)["target-1"]
    assert states["existing_session"] == {"state": "not-required", "pending_reason": None, "observed_at": None}
    assert states["new_session"]["pending_reason"] == "session-acceptance-pending"
    with pytest.raises(ValueError, match="conflicting_session_target"):
        session_states(declaration, (check(),), [], now=NOW)
    states = session_states(declaration, (), [], now=NOW)["target-1"]
    assert states["new_session"]["pending_reason"] == "unsupported-capability"


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


def test_native_same_process_requires_pinned_generation_and_rejects_untyped_fields():
    from dataclasses import replace
    from anvil_serving.propagation_sessions import NativeSessionCheck, native_session_state
    check = NativeSessionCheck('target-1', ReceiptIdentity('install-1', 'profile-1', 'runtime-1'),
        '1' * 64, '2' * 64, '3' * 64, '4' * 64, 'fixture-1', 'prior-session',
        '2026-01-01T00:00:00Z', '2026-01-01T00:01:00Z', 'existing_session',
        'native-provider', 'model-1', 8192, 512, 'process-generation')
    receipt = {'schema': 'native-session-acceptance/v1', 'check_digest': check.digest,
        'started_at': '2026-01-01T00:01:01Z', 'completed_at': '2026-01-01T00:01:02Z',
        'native_session_id': 'prior-session', 'continuity_kind': 'same_process',
        'configured_route': True, 'provider': 'native-provider', 'model': 'model-1',
        'context_tokens': None, 'max_output_tokens': None, 'turn_completed': True,
        'tool_probe_passed': True, 'history_probe_passed': True, 'fallback_used': False,
        'physical_catalog_digest': '4' * 64, 'process_generation': 'process-generation'}
    now = datetime(2026, 1, 1, 0, 1, 3, tzinfo=timezone.utc)
    assert native_session_state(check, receipt, now=now)['state'] == 'accepted'
    for field, value in (('process_generation', 'restarted-process'), ('continuity_kind', []),
                         ('turn_completed', 1), ('max_output_tokens', True),
                         ('completed_at', '2026-01-01T00:01:04Z')):
        assert native_session_state(check, {**receipt, field: value}, now=now)['state'] == 'pending'
    missing_generation = replace(check, previous_process_generation=None)
    assert native_session_state(missing_generation,
        {**receipt, 'check_digest': missing_generation.digest}, now=now)['state'] == 'pending'
    assert native_session_state(check, {**receipt, 'extra': 'untrusted'}, now=now)['state'] == 'pending'
