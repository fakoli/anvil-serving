from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import http.client
import json
from pathlib import Path
import shutil
import socket
import stat
import subprocess
import threading
import time
from types import SimpleNamespace

import pytest

from anvil_serving.workbench_app.pi_egress import PiEgressError, targets


@pytest.mark.parametrize("address", ["127.0.0.1", "169.254.169.254", "169.254.170.2", "::1", "fe80::1", "::ffff:169.254.169.254", "100.100.100.200", "168.63.129.16", "0.0.0.0"])
def test_provider_resolution_rejects_metadata_and_local_addresses(address):
    with pytest.raises(PiEgressError):
        targets(["https://provider.example"], resolve=lambda *_a, **_k: [(None, None, None, None, (address, 443))])


def test_provider_origins_are_exact_and_addresses_are_pinned():
    result = targets(["https://provider.example:8443"], resolve=lambda *_a, **_k: [(None, None, None, None, ("192.0.2.10", 8443))])
    assert result == [{"scheme": "https", "hostname": "provider.example", "port": 8443, "addresses": ["192.0.2.10"]}]
    for origin in ["https://*.example", "https://user:pass@provider.example", "https://provider.example/path", "file:///etc/passwd"]:
        with pytest.raises(PiEgressError):
            targets([origin])


def test_real_node_gateway_restricts_requests_and_injects_only_server_credentials(tmp_path):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node runtime unavailable")
    hits = []
    class Fixture(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            hits.append((self.path, dict(self.headers), body))
            self.send_response(200); self.end_headers(); self.wfile.write(b"fixture-ok provider-private-secret")
    fixture = ThreadingHTTPServer(("127.0.0.1", 0), Fixture)
    threading.Thread(target=fixture.serve_forever, daemon=True).start()
    with socket.socket() as allocator:
        allocator.bind(("127.0.0.1", 0)); port = allocator.getsockname()[1]
    # Test-only direct policy uses loopback HTTP. Operator configuration requires HTTPS.
    policy = tmp_path / "allowlist.json"
    policy.write_text(json.dumps({"api":"openai-completions", "base_path":"/v1", "models":["allowed-model"], "max_tokens":100, "targets": [{"scheme": "http", "hostname": "allowed.invalid", "port": fixture.server_port, "addresses": ["127.0.0.1"]}]}))
    secret = tmp_path / "credential"; secret.write_text("provider-private-secret")
    source = Path(__file__).resolve().parents[2] / "pi_runner" / "proxy.cjs"
    process = subprocess.Popen([node, str(source), str(policy), str(port), str(secret)], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    try:
        for _ in range(100):
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=.1): break
            except OSError: time.sleep(.01)
        allowed = {"model":"allowed-model", "messages":[], "stream":False, "max_tokens":40}
        cases = [("POST", "/v1/chat/completions", allowed, 200),
                 ("POST", "/v1/chat/completions", allowed | {"model":"other"}, 403),
                 ("POST", "/v1/chat/completions", allowed | {"max_tokens":101}, 403),
                 ("POST", "/v1/chat/completions", allowed | {"messages":[{"image_url":"http://169.254.169.254"}]}, 403),
                 ("GET", "/v1/models", {}, 403),
                 ("POST", "http://allowed.invalid/v1/chat/completions", allowed, 403),
                 ("CONNECT", "allowed.invalid:443", {}, 403),
                 ("CONNECT", "same-cdn-peer.invalid:443", {}, 403)]
        for method, url, body, expected in cases:
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
            conn.request(method, url, json.dumps(body), {"Content-Type":"application/json", "Authorization":"Bearer attacker", "X-Api-Key":"attacker"})
            response = conn.getresponse()
            assert response.status == expected
            data = response.read()
            assert b"provider-private-secret" not in data
            if expected == 200:
                assert data == b"fixture-ok [redacted]"
            conn.close()
        assert len(hits) == 1
        assert hits[0][1]["authorization"] == "Bearer provider-private-secret"
        assert "x-api-key" not in hits[0][1]
        assert hits[0][2]["max_tokens"] == 40
    finally:
        process.terminate(); process.wait(timeout=5)
        fixture.shutdown(); fixture.server_close()


def test_network_identity_is_session_unique_and_extra_peer_is_rejected(tmp_path):
    import hashlib
    from anvil_serving.workbench_app.pi_egress import PiEgress, _encoded
    config = {'state_root':str(tmp_path), 'engine_binary':'docker', 'image':'sha256:'+'a'*64, 'uid':1000, 'gid':1000,
              'provider_egress':{'provider':['https://provider.example']},
              'provider_endpoints':{'provider':{'credential_env':'KEY'}},
              'provider_secret_refs':{'provider':{'KEY':'file:/private/credential'}}}
    egress = PiEgress(config)
    assert egress.record_path('provider', 'a'*32) != egress.record_path('provider', 'b'*32)
    egress.root.mkdir()
    key = 'a'*32
    policy = egress.root / ('provider--'+key+'-allowlist.json'); policy.write_bytes(_encoded({'models':['model']}))
    digest = hashlib.sha256(policy.read_bytes()).hexdigest()
    record = {'provider':'provider', 'origins':config['provider_egress']['provider'], 'network':'isolated-pi-session', 'network_id':'network-id', 'proxy':'anvil-proxy', 'proxy_id':'proxy-id', 'image':config['image'], 'session_id':key, 'runner_name':'anvil-pi-owned', 'secret_path':'/private/credential', 'policy_path':str(policy), 'digest':digest, 'suffix':'suffix', 'health':'health'}
    egress.record_path('provider', key).write_bytes(_encoded(record))
    egress.record_path('provider').write_bytes(_encoded({'policy':{'models':['model']},'image':config['image']}))
    network = {'Id':'network-id', 'Internal':True, 'Labels':{'anvil.pi.egress':'suffix'}, 'Options':{'com.docker.network.bridge.gateway_mode_'+family:'isolated' for family in ('ipv4','ipv6')}, 'Containers':{'proxy-id':{'Name':'anvil-proxy'}, 'runner-id':{'Name':'anvil-pi-owned'}}}
    proxy = {'Id':'proxy-id','Image':'image-id', 'Config':{'Image':config['image'], 'Entrypoint':['node'], 'Cmd':['/opt/pi-runner/proxy.cjs','/policy/allowlist.json'], 'User':'1000:1000', 'Healthcheck':{'Test':['CMD-SHELL','health']}},
             'Mounts':[{'Type':'bind','Source':source,'Destination':dest,'RW':False} for source,dest in ((str(policy),'/policy/allowlist.json'),('/private/credential','/policy/credential'))],
             'HostConfig':{'LogConfig':{'Type':'none'},'ReadonlyRootfs':True,'Privileged':False,'CapDrop':['ALL'],'SecurityOpt':['no-new-privileges'],'Memory':96*1024**2,'NanoCpus':250000000,'PidsLimit':32},
             'NetworkSettings':{'Networks':{'isolated-pi-session':{'NetworkID':'network-id'},'bridge':{}}}, 'State':{'Running':True,'Health':{'Status':'healthy'}}}
    def inspect(kind, identity):
        if kind == 'image':return {'Id':'image-id'}
        if kind == 'network':return network
        if identity == 'runner-id':return {'Config':{'Labels':{'anvil.pi.session':key}}}
        return proxy
    egress._inspect = inspect
    assert egress.verify('provider',session_id=key)['network_id'] == 'network-id'
    network['Containers']['peer-id']={'Name':'anvil-pi-other'}
    with pytest.raises(PiEgressError, match='unrelated peer'):
        egress.verify('provider',session_id=key)


def test_gateway_creation_intent_precedes_engine_mutations(tmp_path, monkeypatch):
    from subprocess import CompletedProcess
    from anvil_serving.workbench_app import pi_egress
    secret=tmp_path/'credential'; secret.write_text('fixture-secret'); secret.chmod(0o600)
    original_stat = pi_egress.Path.stat

    def protected_stat(path, *args, **kwargs):
        value = original_stat(path, *args, **kwargs)
        if path == secret:
            return SimpleNamespace(st_mode=stat.S_IFREG | 0o600)
        return value

    monkeypatch.setattr(pi_egress.Path, "stat", protected_stat)
    config={'state_root':str(tmp_path),'engine_binary':'docker','image':'sha256:'+'a'*64,'uid':1000,'gid':1000,
            'models':{'provider':['model']},'provider_egress':{'provider':['https://provider.example']},
            'provider_endpoints':{'provider':{'base_url':'https://provider.example/v1','api':'openai-completions','credential_env':'KEY'}},
            'provider_secret_refs':{'provider':{'KEY':'file:'+str(secret)}}}
    monkeypatch.setattr(pi_egress,'targets',lambda _origins:[{'scheme':'https','hostname':'provider.example','port':443,'addresses':['192.0.2.10']}])
    egress=pi_egress.PiEgress(config,run=lambda args,**_k:CompletedProcess(args,1,'',f'Error response from daemon: network {args[-1]} not found'))
    egress.setup('provider',confirm=True)
    def fail(*_args):
        intent=json.loads(egress.record_path('provider','a'*32).read_text())
        assert intent['phase']=='creating' and intent['network_id'] is None and intent['proxy_id'] is None
        raise PiEgressError('interrupted creation')
    egress._command=fail
    with pytest.raises(PiEgressError,match='interrupted creation'):
        egress.setup('provider',confirm=True,session_id='a'*32,runner_name='anvil-pi-'+'b'*20)
    assert egress.record_path('provider','a'*32).is_file()
