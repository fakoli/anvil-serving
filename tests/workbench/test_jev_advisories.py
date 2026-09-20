from dataclasses import replace
import http.client
import json
from pathlib import Path
import shutil
import subprocess

import pytest

from anvil_serving import jev
from tests.observability.test_observatory_fallback import fallback_site as fallback_site
from tests.workbench.test_service import site as site  # real authenticated HTTP fixture


def settings():
    return jev.validate_policy({"enabled": True, "capabilities": list(jev.CAPABILITIES), "allow_api": True,
                                "allow_export": True, "anvil_binary": "/usr/bin/anvil"})


def body(resource="serve-a"):
    return {"resource_id": resource, "generation": "request-a", "allow_export": True,
            "input": {"observation": "Selected synthetic 401 observation"}}


@pytest.fixture
def fallback_advice(fallback_site):
    console, call, state, _ = fallback_site
    # Narrow the real fallback login's grant so this exercises resource ACLs,
    # not only the wildcard owner used by the underlying fallback fixture.
    owner = replace(console.fallback_access.users["operator"], resources=frozenset({"serve-a"}))
    console.fallback_access.users["operator"] = owner
    console.fallback_access.users_by_id[owner.identity] = owner
    status, result, cookie = call("POST", "session", body={"username": "operator", "password": "accepted"})
    assert status == 200
    assert not console.access._sessions and console.fallback_access._sessions
    def advise(value=None, *, extra=None):
        headers = {"Host": "backup.example.test", "Origin": "https://backup.example.test",
                   "Cookie": cookie.split(";", 1)[0], "X-CSRF-Token": result["data"]["csrf_token"],
                   "Content-Type": "application/json", **(extra or {})}
        connection = http.client.HTTPConnection("127.0.0.1", state["port"], timeout=5)
        try:
            connection.request("POST", "/workbench/api/workbench/v1/advisories/incident_triage",
                               body=json.dumps(body() if value is None else value), headers=headers)
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()
    return console, advise


def test_fallback_advice_rechecks_its_own_session_and_resource_grants(fallback_advice, monkeypatch):
    console, advise = fallback_advice
    monkeypatch.setattr(jev, "load_policy", settings)
    calls = []
    def offline(*args, **kwargs):
        assert kwargs["policy_reader"]() == settings()
        calls.append(args)
        return jev.report("incident_triage", "unavailable", "offline")
    monkeypatch.setattr(jev, "advise", offline)
    status, result = advise()
    assert status == 200 and result["data"]["annotation"]["advisory"]
    assert advise(extra={"X-CSRF-Token": "bad"})[0] == 403
    assert advise(body("other"))[0] == 403
    assert advise(extra={"Origin": "https://console.example.test"})[0] == 403
    assert len(calls) == 1 and not console.access._sessions


def test_disabled_fallback_advice_does_not_read_selected_content(fallback_advice, monkeypatch):
    _, advise = fallback_advice
    monkeypatch.setattr(jev, "load_policy", lambda: jev.validate_policy({}))
    monkeypatch.setattr(jev, "advise", lambda *_args, **_kwargs: pytest.fail("disabled advice dispatched"))
    status, result = advise(body() | {"input": "not inspected"})
    assert status == 200 and result["data"]["annotation"]["status"] == "disabled"


@pytest.mark.parametrize("phase", ["before_dispatch", "after_advice"])
def test_revoked_fallback_session_is_rejected_at_each_recheck(fallback_advice, monkeypatch, phase):
    console, advise = fallback_advice
    monkeypatch.setattr(jev, "load_policy", settings)
    reached = []
    def revoke():
        reached.append(phase)
        with console.fallback_access._lock:
            console.fallback_access._sessions.clear()
    if phase == "before_dispatch":
        original = jev.selected_input
        def prepare(value):
            revoke()
            return original(value)
        monkeypatch.setattr(jev, "selected_input", prepare)
        monkeypatch.setattr(jev.subprocess, "Popen", lambda *_args, **_kwargs: pytest.fail("revoked fallback session dispatched"))
    else:
        def offline(*_args, **_kwargs):
            revoke()
            return jev.report("incident_triage", "unavailable", "offline", started=True)
        monkeypatch.setattr(jev, "advise", offline)
    assert advise()[0] == 401
    assert reached == [phase]


def test_attributed_advice_uses_same_auth_csrf_and_no_resource_actions(site, monkeypatch):
    console, call, _ = site
    monkeypatch.setattr(jev, "load_policy", settings)
    calls = []
    monkeypatch.setattr(jev, "advise", lambda *args, **kwargs: calls.append(args) or jev.report("incident_triage", "unavailable", "offline"))
    status, result = call("POST", "advisories/incident_triage", body())
    assert status == 200 and result["data"]["annotation"]["advisory"]
    assert result["data"]["generation"] == "request-a" and len(calls) == 1
    assert call("POST", "advisories/incident_triage", body("other"))[0] == 403
    assert call("POST", "advisories/incident_triage", body(), extra={"X-CSRF-Token": "bad"})[0] == 403
    assert len(calls) == 1
    assert "Selected synthetic" not in json.dumps(result)


def test_disabled_does_not_inspect_selected_content(site, monkeypatch):
    _, call, _ = site
    monkeypatch.setattr(jev, "load_policy", lambda: jev.validate_policy({}))
    monkeypatch.setattr(jev, "advise", lambda *_args, **_kwargs: pytest.fail("disabled advisor called"))
    invalid = body() | {"input": "must not be read"}
    status, result = call("POST", "advisories/incident_triage", invalid)
    assert status == 200 and result["data"]["annotation"]["status"] == "disabled"


def test_revoked_session_cannot_receive_a_late_advice(site, monkeypatch):
    console, call, _ = site
    monkeypatch.setattr(jev, "load_policy", settings)
    def revoke(*_args, **_kwargs):
        with console.access._lock:
            console.access._sessions.clear()
        return jev.report("incident_triage", "unavailable", "offline", started=True)
    monkeypatch.setattr(jev, "advise", revoke)
    assert call("POST", "advisories/incident_triage", body())[0] == 401


def test_revoked_session_during_input_preparation_cannot_dispatch(site, monkeypatch):
    console, call, _ = site
    monkeypatch.setattr(jev, "load_policy", settings)
    original = jev.selected_input
    def revoke(value):
        with console.access._lock:
            console.access._sessions.clear()
        return original(value)
    monkeypatch.setattr(jev, "selected_input", revoke)
    monkeypatch.setattr(jev.subprocess, "Popen", lambda *_args, **_kwargs: pytest.fail("revoked session dispatched"))
    assert call("POST", "advisories/incident_triage", body())[0] == 401


def test_ui_requires_explicit_export_and_discards_stale_ordering(tmp_path):
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is required for the frontend behavior gate")
    source = (Path(__file__).parents[2] / "anvil_serving/observability/dashboard/static/views/jev.js").read_text()
    (tmp_path / "jev.mjs").write_text(source.replace("./common.js", "./common.mjs").replace("./api.js", "./api.mjs"))
    (tmp_path / "common.mjs").write_text('''
export function el(tag, props={}, ...children) {
  return {tag, children, value:'', checked:false, ...props,
    append(...items){this.children.push(...items)},
    replaceChildren(...items){this.children=items}};
}
export const button=(text, click)=>el('button',{text,click});
export const field=(text,control)=>el('label',{text},control);
export const select=(choices,value,onChange)=>el('select',{choices,value,onChange});
export const notice=text=>el('notice',{text}), words=text=>text;
export const jsonDetails=value=>el('details',{value});
''')
    (tmp_path / "api.mjs").write_text('''
export const calls=[];
export const workbenchRequest=(path, options)=>new Promise(resolve=>calls.push({path,options,resolve}));
''')
    script = '''
import assert from 'node:assert/strict';
import {webcrypto} from 'node:crypto';
import {jevAssistanceView} from './jev.mjs';
import {calls} from './api.mjs';
if (!globalThis.crypto) Object.defineProperty(globalThis,'crypto',{value:webcrypto});
const catalog={jev:{enabled:true,allow_api:true,allow_export:true,capabilities:['context_ranking']},advisory_resources:[{id:'allowed',label:'Allowed'}]};
const root=jevAssistanceView({signal:new AbortController().signal},catalog);
const all=(node)=>[node,...(node.children||[]).flatMap(all)];
const label=text=>all(root).find(node=>node.tag==='label'&&node.text===text).children[0];
const button=text=>all(root).find(node=>node.tag==='button'&&node.text===text);
const consent=label('Permit TypeSafe/Jev to process only this selected text');
const run=button('Request Jev advice');
assert.equal(calls.length,0);
assert.equal(consent.checked,false);
await run.click();
assert.equal(calls.length,0);
const capability=label('Assistance'); capability.value='context_ranking'; capability.onChange();
label('Intent or selected safe observation').value='Read selected context';
for (const [index,row] of all(root).filter(node=>node.tag==='fieldset').entries()) {
  row.children.find(node=>node.text==='Name').children[0].value=`Name ${index+1}`;
  row.children.find(node=>node.text==='Safe description or selected snippet').children[0].value=`Snippet ${index+1}`;
}
consent.checked=true;
const first=run.click();
assert.equal(calls.length,1);
assert.equal(calls[0].options.body.allow_export,true);
assert.equal(calls[0].options.body.resource_id,'allowed');
button('Restore original order').click();
const answer=index=>({generation:calls[index].options.body.generation,order:['candidate2','candidate1'],annotation:{used:true,model:'jev-1.13.0',status:'completed',request_started:true}});
calls[0].resolve(answer(0)); await first;
const order=()=>all(root).find(node=>node.tag==='ol').children.map(node=>node.children[0].text);
assert.deepEqual(order(),['Name 1','Name 2']);
assert.equal(all(root).some(node=>String(node.text).includes('Ranked with Jev')),false);
const second=run.click(); calls[1].resolve(answer(1)); await second;
assert.deepEqual(order(),['Name 2','Name 1']);
assert.equal(all(root).some(node=>String(node.text).includes('Ranked with Jev')),true);
button('Restore original order').click();
assert.deepEqual(order(),['Name 1','Name 2']);
button('Disable for this selection').click();
assert.equal(consent.checked,false);
assert.deepEqual(order(),[]);
const off=jevAssistanceView({signal:new AbortController().signal},{...catalog,jev:{enabled:false}});
assert.equal(all(off).find(node=>node.text==='Request Jev advice').disabled,true);
assert.equal(calls.length,2);
'''
    result = subprocess.run([node, "--input-type=module", "--eval", script], cwd=tmp_path,
        stdin=subprocess.DEVNULL, text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr
