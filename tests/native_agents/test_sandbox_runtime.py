import http.client
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import json
import os
import socket
import threading
import tempfile
from pathlib import Path
import pytest
from anvil_serving.native_agents.sandbox_runtime import ModelGateway,model_handler,BoundedUnixServer


def policy(): return ModelGateway(61001,"http://127.0.0.1:9999",Path("/protected/key"),("allowed",))

@pytest.mark.parametrize("method,path,body",[("CONNECT","127.0.0.1:80",b""),("POST","/admin",b'{}'),("POST","/v1/chat/completions?url=http://evil",b'{"model":"allowed"}'),("POST","http://evil/v1/responses",b'{"model":"allowed"}'),("POST","/v1/responses",b'{"model":"other"}'),("POST","/v1/responses",b'{"model":"allowed","background":true}'),("POST","/v1/responses",b'{"model":"other","model":"allowed"}')])
def test_model_gateway_denies_escapes_before_upstream(method,path,body):
    with pytest.raises(ValueError):policy().request(method,path,body)


def test_model_gateway_allows_fixed_inference_only():
    policy().request("POST","/v1/chat/completions",b'{"model":"allowed","messages":[{"role":"user","content":"test"}],"tools":[{"type":"function","function":{"name":"read","parameters":{"type":"object","properties":{"path":{"type":"string"}}}}}]}')


class UnixHTTPConnection(http.client.HTTPConnection):
    def __init__(self,path):super().__init__("localhost",timeout=3);self.path=path
    def connect(self):self.sock=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM);self.sock.settimeout(3);self.sock.connect(self.path)


def test_gateway_peer_identity_scoped_key_header_filter_stream_and_redirect(tmp_path,monkeypatch):
    seen=[];redirect=[False]
    class Router(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_POST(self):
            seen.append(dict(self.headers));self.rfile.read(int(self.headers["Content-Length"]))
            self.send_response(302 if redirect[0] else 200);self.send_header("Content-Type","text/event-stream");self.end_headers();self.wfile.write(b'data: {"text":"test"}\n\ndata: [DONE]\n\n')
    router=ThreadingHTTPServer(("127.0.0.1",0),Router);threading.Thread(target=router.serve_forever,daemon=True).start()
    sock=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM);temporary=tempfile.TemporaryDirectory(prefix="ang-",dir=os.environ.get("ANVIL_SANDBOX_TEST_TMP","/tmp"));target=temporary.name+"/model.sock";sock.bind(target);sock.listen()
    current=[61001];monkeypatch.setattr("anvil_serving.native_agents.sandbox_runtime.peer_uid",lambda _:current[0])
    p=ModelGateway(61001,f"http://127.0.0.1:{router.server_port}",Path("/protected/key"),("allowed",))
    if not hasattr(os,"O_PATH"):
        # Darwin lacks SO_ACCEPTCONN on AF_UNIX. This test exercises forwarding;
        # activated-listener custody and descriptor pinning require Linux.
        monkeypatch.setattr("anvil_serving.native_agents.sandbox_runtime.activated_listener",lambda s:s.family==socket.AF_UNIX)
    gateway=BoundedUnixServer(os.dup(sock.fileno()),model_handler(p,"scoped-fixture-key"));threading.Thread(target=gateway.serve_forever,daemon=True).start()
    try:
        def post():
            conn=UnixHTTPConnection(target);conn.request("POST","/v1/chat/completions",body=b'{"model":"allowed","messages":[{"role":"user","content":"test"}]}',headers={"Authorization":"Bearer caller","X-Principal":"spoofed"});response=conn.getresponse();result=(response.status,response.read());conn.close();return result
        assert post()==(200,b'data: {"text":"test"}\n\ndata: [DONE]\n\n')
        assert seen[0]["Authorization"]=="Bearer scoped-fixture-key" and "X-Principal" not in seen[0]
        current[0]=61002
        # Wrong peers are rejected before the body is read. Sending only the
        # headers proves that boundary without racing a body write against close.
        rejected=UnixHTTPConnection(target)
        try:
            rejected.putrequest("POST","/v1/chat/completions")
            rejected.putheader("Content-Length","64")
            rejected.endheaders()
            response=rejected.getresponse()
            assert response.status==403
            response.read()
        finally:rejected.close()
        assert len(seen)==1
        current[0]=61001;redirect[0]=True;assert post()[0]==502;assert len(seen)==2
        redirect[0]=False;monkeypatch.setattr("anvil_serving.native_agents.sandbox_runtime.MAX_RESPONSE",4)
        with pytest.raises(http.client.IncompleteRead):post()
    finally:gateway.shutdown();gateway.server_close();sock.close();router.shutdown();router.server_close();temporary.cleanup()


@pytest.mark.parametrize("change",[
    {"tools":[{"type":"mcp","server_url":"http://127.0.0.1/admin"}]},
    {"tools":[{"type":"web_search"}]},
    {"store":True},
    {"background":True},
    {"messages":[{"role":"user","content":[{"type":"image_url","image_url":{"url":"http://169.254.169.254/"}}]}]},
    {"tools":[{"type":"function","function":{"name":"read","parameters":{"$ref":"http://localhost/private"}}}]}
])
def test_hosted_tools_remote_fetch_and_provider_controls_denied(change):
    value={"model":"allowed","messages":[{"role":"user","content":"test"}],**change}
    with pytest.raises(ValueError):policy().request("POST","/v1/chat/completions",json.dumps(value).encode())


@pytest.mark.skipif(not hasattr(os,"O_PATH"),reason="Linux descriptor pinning is mandatory")
def test_ingress_rejects_symlink_and_pins_socket_across_leaf_swap(monkeypatch):
    from anvil_serving.native_agents.sandbox_runtime import connect_owned_socket
    with tempfile.TemporaryDirectory(prefix="ang-",dir=os.environ.get("ANVIL_SANDBOX_TEST_TMP","/tmp")) as directory:
        root=Path(directory);good=root/"owner.sock";evil=root/"other.sock"
        first=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM);first.bind(str(good));good.chmod(0o600);first.listen()
        other=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM);other.bind(str(evil));evil.chmod(0o600);other.listen();other.settimeout(.1)
        link=root/"link.sock";link.symlink_to(evil)
        try:
            with pytest.raises(ValueError):connect_owned_socket(str(link),os.getuid())
            original=os.fstat;changed=[False]
            def swap(fd):
                info=original(fd)
                if not changed[0]:
                    changed[0]=True;good.rename(root/"original.sock");good.symlink_to(evil)
                return info
            monkeypatch.setattr(os,"fstat",swap)
            connection=connect_owned_socket(str(good),os.getuid())
            accepted,_=first.accept();connection.sendall(b"safe");assert accepted.recv(4)==b"safe"
            with pytest.raises(TimeoutError):other.accept()
            accepted.close();connection.close()
        finally:first.close();other.close()


@pytest.mark.skipif(not hasattr(os,'O_PATH'),reason='Linux descriptor pinning is mandatory')
@pytest.mark.parametrize('kind',['mode700','mode400','wrong-uid','parent-symlink','grandparent-symlink'])
def test_ingress_socket_custody_failures(kind):
    from anvil_serving.native_agents.sandbox_runtime import connect_owned_socket
    with tempfile.TemporaryDirectory(prefix='ang-',dir=os.environ.get('ANVIL_SANDBOX_TEST_TMP','/tmp')) as directory:
        root=Path(directory);parent=root/'a'/'b';parent.mkdir(parents=True);target=parent/'owner.sock'
        server=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM);server.bind(str(target));server.listen();target.chmod(0o600)
        uid=os.getuid()
        try:
            if kind=='mode700':target.chmod(0o700)
            elif kind=='mode400':target.chmod(0o400)
            elif kind=='wrong-uid':uid+=1
            elif kind=='parent-symlink':parent.rename(parent.with_name('actual'));parent.symlink_to(parent.with_name('actual'))
            else:(root/'a').rename(root/'actual');(root/'a').symlink_to(root/'actual')
            with pytest.raises((ValueError,OSError)):connect_owned_socket(str(target),uid)
        finally:server.close()


@pytest.mark.parametrize('kind',['valid','wrong-path','wrong-uid','mode700','mode400','not-listening','wrong-type','symlink'])
def test_activated_socket_binding_and_custody(monkeypatch,kind):
    from anvil_serving.native_agents.sandbox_runtime import verify_activated_listener
    with tempfile.TemporaryDirectory(prefix='ang-',dir=os.environ.get('ANVIL_SANDBOX_TEST_TMP','/tmp')) as directory:
        target=Path(directory)/'model.sock'
        server=socket.socket(socket.AF_UNIX,socket.SOCK_DGRAM if kind=='wrong-type' else socket.SOCK_STREAM);server.bind(str(target));target.chmod(0o600)
        if kind not in ('not-listening','wrong-type'):server.listen()
        monkeypatch.setattr('anvil_serving.native_agents.sandbox._root_custody',lambda _:None)
        if not hasattr(os,'O_PATH'):
            monkeypatch.setattr('anvil_serving.native_agents.sandbox_runtime.activated_listener',lambda _:kind not in ('not-listening','wrong-type'))
        expected=target;uid=os.getuid()
        if kind=='wrong-path':expected=target.with_name('other.sock')
        if kind=='wrong-uid':uid+=1
        if kind=='mode700':target.chmod(0o700)
        if kind=='mode400':target.chmod(0o400)
        if kind=='symlink':target.rename(target.with_name('actual.sock'));target.symlink_to(target.with_name('actual.sock'))
        try:
            if kind=='valid':verify_activated_listener(server,expected,uid)
            else:
                with pytest.raises((ValueError,OSError)):verify_activated_listener(server,expected,uid)
        finally:server.close()
