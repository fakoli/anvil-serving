"""Bounded fixed-destination relays for a whole-owner network sandbox.

The inference gateway accepts a systemd-created UNIX listener (fd3), verifies
SO_PEERCRED, and injects one model/path-scoped router key outside the owner. The
separate ingress relay binds loopback and forwards only to that owner's socket.
Neither mode accepts a destination or credential from a request.
"""
from dataclasses import dataclass
import http.client
from http.server import BaseHTTPRequestHandler
import os
from pathlib import Path
import select
import socket
import socketserver
import struct
import stat
import threading
import time
from .auth import strict_json, protected_read
from .owner import canonical_origin
from urllib.parse import urlsplit

MAX_REQUEST = 8 * 1024 * 1024
MAX_RESPONSE = 32 * 1024 * 1024

@dataclass(frozen=True)
class ModelGateway:
    owner_uid: int
    router_origin: str
    credential_file: Path
    models: tuple[str,...]

    @classmethod
    def parse(cls, value):
        if not isinstance(value,dict) or set(value)!={"owner_uid","router_origin","credential_file","models"}: raise ValueError("invalid model gateway")
        if type(value["owner_uid"]) is not int or not 60000<=value["owner_uid"]<=64999: raise ValueError("invalid owner uid")
        models=value["models"]
        if not isinstance(models,list) or not 1<=len(models)<=128 or len(set(models))!=len(models) or any(not isinstance(m,str) or not 1<=len(m)<=160 or any(ord(c)<33 or ord(c)>126 for c in m) for m in models): raise ValueError("explicit models required")
        return cls(value["owner_uid"],canonical_origin(value["router_origin"]),Path(value["credential_file"]),tuple(models))

    def request(self, method, path, body):
        if method!="POST" or path!="/v1/chat/completions": raise ValueError("inference path denied")
        value=strict_json(body)
        allowed={"model","messages","tools","tool_choice","parallel_tool_calls","temperature","top_p","frequency_penalty","presence_penalty","max_tokens","max_completion_tokens","stream","stream_options","stop","reasoning_effort","seed"}
        if not isinstance(value,dict) or set(value)-allowed or value.get("model") not in self.models: raise ValueError("model/request denied")
        messages=value.get("messages")
        if not isinstance(messages,list) or not 1<=len(messages)<=4096:raise ValueError("messages required")
        for message in messages:
            if not isinstance(message,dict) or set(message)-{"role","content","name","tool_calls","tool_call_id","reasoning_content"} or message.get("role") not in ("system","developer","user","assistant","tool"):raise ValueError("invalid message")
            content=message.get("content")
            if content is not None and not isinstance(content,(str,list)):raise ValueError("invalid content")
            if isinstance(content,list):
                for item in content:
                    if not isinstance(item,dict):raise ValueError("invalid content")
                    if item.get("type")=="text" and set(item)=={"type","text"} and isinstance(item["text"],str):continue
                    if item.get("type")=="image_url" and set(item)=={"type","image_url"}:
                        image=item["image_url"]
                        if isinstance(image,dict) and not set(image)-{"url","detail"} and isinstance(image.get("url"),str) and image["url"].startswith(("data:image/png;base64,","data:image/jpeg;base64,","data:image/webp;base64,")):continue
                    raise ValueError("remote/multimodal content denied")
        tools=value.get("tools",[])
        if not isinstance(tools,list) or len(tools)>128:raise ValueError("invalid tools")
        for tool in tools:
            if not isinstance(tool,dict) or set(tool)!={"type","function"} or tool["type"]!="function":raise ValueError("hosted tools denied")
            function=tool["function"]
            if not isinstance(function,dict) or set(function)-{"name","description","parameters","strict"} or not isinstance(function.get("name"),str):raise ValueError("invalid function")
            def schema(item):
                if isinstance(item,dict):
                    if "$ref" in item and (not isinstance(item["$ref"],str) or not item["$ref"].startswith("#")):raise ValueError("remote schema denied")
                    for child in item.values():schema(child)
                elif isinstance(item,list):
                    for child in item:schema(child)
            schema(function.get("parameters",{}))
        choice=value.get("tool_choice","auto")
        if isinstance(choice,dict):
            if set(choice)!={"type","function"} or choice["type"]!="function" or not isinstance(choice["function"],dict) or set(choice["function"])!={"name"} or not isinstance(choice["function"]["name"],str):raise ValueError("invalid tool choice")
        elif choice not in ("auto","none","required"):raise ValueError("invalid tool choice")


def peer_uid(connection):
    if not hasattr(socket,"SO_PEERCRED"): raise ValueError("Linux peer credentials required")
    return struct.unpack("3i",connection.getsockopt(socket.SOL_SOCKET,socket.SO_PEERCRED,struct.calcsize("3i")))[1]


def model_handler(policy, credential):
    if not credential or any(ord(c)<33 or ord(c)>126 for c in credential): raise ValueError("invalid scoped router credential")
    origin=urlsplit(policy.router_origin)
    class Handler(BaseHTTPRequestHandler):
        protocol_version="HTTP/1.1"
        server_version="AnvilModelGateway"
        sys_version=""
        def log_message(self,*args): pass
        def setup(self): self.request.settimeout(15); super().setup()
        def do_GET(self): self.route()
        def do_POST(self): self.route()
        def do_CONNECT(self): self.deny(405)
        def do_PUT(self): self.deny(405)
        def do_DELETE(self): self.deny(405)
        def deny(self,status):
            self.send_response(status); self.send_header("Content-Length","0"); self.send_header("Connection","close"); self.end_headers(); self.close_connection=True
        def route(self):
            upstream=None; started=False
            try:
                if peer_uid(self.connection)!=policy.owner_uid: self.deny(403); return
                lengths=self.headers.get_all("Content-Length",[])
                if self.headers.get_all("Transfer-Encoding") or len(lengths)>1: self.deny(400); return
                if self.command=="POST" and len(lengths)!=1: self.deny(411); return
                size=int(lengths[0]) if lengths else 0
                if not 0<=size<=MAX_REQUEST: self.deny(413); return
                body=self.rfile.read(size)
                if len(body)!=size: self.deny(400); return
                policy.request(self.command,self.path,body)
                upstream=http.client.HTTPConnection(origin.hostname,origin.port,timeout=120)
                upstream.request(self.command,self.path,body=body or None,headers={"Authorization":"Bearer "+credential,"Content-Type":"application/json","Accept":"application/json, text/event-stream","Connection":"close"})
                response=upstream.getresponse()
                if 300<=response.status<400: self.deny(502); return
                content=response.headers.get_content_type()
                if content not in ("application/json","text/event-stream"): self.deny(502); return
                self.send_response(response.status); self.send_header("Content-Type",content); self.send_header("Cache-Control","no-store"); self.send_header("Transfer-Encoding","chunked"); self.send_header("Connection","close"); self.end_headers(); started=True
                total=0; deadline=time.monotonic()+900
                while time.monotonic()<deadline:
                    chunk=response.read1(65536)
                    if not chunk:
                        self.wfile.write(b"0\r\n\r\n");self.wfile.flush();break
                    total+=len(chunk)
                    if total>MAX_RESPONSE: break
                    self.wfile.write(f"{len(chunk):x}\r\n".encode()+chunk+b"\r\n"); self.wfile.flush()
            except (ValueError,TypeError,RecursionError):
                if not started: self.deny(400)
            except (OSError,http.client.HTTPException):
                if not started:
                    try:self.deny(502)
                    except OSError:pass
            finally:
                self.close_connection=True
                if upstream:upstream.close()
    return Handler


class BoundedMixIn(socketserver.ThreadingMixIn):
    daemon_threads=True
    def process_request(self,request,address):
        if not self.slots.acquire(blocking=False): self.shutdown_request(request); return
        try:super().process_request(request,address)
        except BaseException:self.slots.release();raise
    def process_request_thread(self,request,address):
        try:super().process_request_thread(request,address)
        finally:self.slots.release()


def activated_listener(connection):
    return (connection.family==socket.AF_UNIX and connection.getsockopt(socket.SOL_SOCKET,socket.SO_TYPE)==socket.SOCK_STREAM
            and bool(connection.getsockopt(socket.SOL_SOCKET,socket.SO_ACCEPTCONN)))


def verify_activated_listener(listener, expected, uid):
    from .sandbox import _root_custody
    expected=Path(expected)
    if not activated_listener(listener) or listener.getsockname()!=str(expected):raise ValueError("gateway socket binding mismatch")
    _root_custody(expected.parent)
    info=expected.lstat()
    if not stat.S_ISSOCK(info.st_mode) or info.st_uid!=uid or stat.S_IMODE(info.st_mode)!=0o600:raise ValueError("gateway socket owner mismatch")


class BoundedUnixServer(BoundedMixIn,socketserver.UnixStreamServer):
    def __init__(self, fd, handler, maximum_connections=8):
        self.slots=threading.BoundedSemaphore(maximum_connections)
        super().__init__("unused",handler,bind_and_activate=False)
        self.socket.close(); self.socket=socket.socket(fileno=fd)
        if not activated_listener(self.socket):raise ValueError("activated UNIX listener required")
        self.server_address=self.socket.getsockname()


def connect_owned_socket(target, uid):
    """Pin the socket inode with O_PATH and connect through its procfs magic link.

    Every path component is opened relative to its pinned parent with NOFOLLOW.
    A caller cannot replace a checked leaf with a symlink between stat/connect.
    Linux is mandatory; there is deliberately no pathname fallback.
    """
    if not hasattr(os,"O_PATH"):raise ValueError("Linux O_PATH required")
    p=Path(target)
    if not p.is_absolute() or str(p)!=target or target.startswith("//") or ".." in p.parts:raise ValueError("canonical socket required")
    directory=os.open("/",os.O_PATH|os.O_DIRECTORY)
    leaf=None
    try:
        for part in p.parts[1:-1]:
            child=os.open(part,os.O_PATH|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=directory)
            os.close(directory);directory=child
        leaf=os.open(p.name,os.O_PATH|os.O_NOFOLLOW,dir_fd=directory)
        info=os.fstat(leaf)
        if not stat.S_ISSOCK(info.st_mode) or info.st_uid!=uid or stat.S_IMODE(info.st_mode)!=0o600:raise ValueError("invalid owner socket")
        connection=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM);connection.settimeout(10)
        try:connection.connect("/proc/self/fd/"+str(leaf))
        except BaseException:connection.close();raise
        return connection
    finally:
        if leaf is not None:os.close(leaf)
        os.close(directory)


def ingress_handler(target, uid):
    class Handler(socketserver.BaseRequestHandler):
        def handle(self):
            upstream=None
            try:
                upstream=connect_owned_socket(target,uid); peers=(self.request,upstream); last=time.monotonic(); total=0
                for peer in peers:peer.setblocking(False)
                # Data has fixed-size buffers; no queue can grow with a slow reader.
                while time.monotonic()-last<130:
                    readable,_,_=select.select(peers,[],[],1)
                    for source in readable:
                        data=source.recv(65536)
                        if not data:return
                        total+=len(data)
                        if total>64*1024*1024:return
                        destination=upstream if source is self.request else self.request
                        view=memoryview(data); deadline=time.monotonic()+15
                        while view:
                            _,writable,_=select.select([], [destination], [],max(0,deadline-time.monotonic()))
                            if not writable:return
                            sent=destination.send(view)
                            if sent<=0:return
                            view=view[sent:]
                        last=time.monotonic()
            except (OSError,ValueError):pass
            finally:
                if upstream:upstream.close()
    return Handler


class BoundedTCPServer(BoundedMixIn,socketserver.TCPServer):
    allow_reuse_address=True
    daemon_threads=True
    def __init__(self,port,target,uid,maximum_connections=32):
        if type(port) is not int or not 1024<=port<=65535:raise ValueError("invalid loopback port")
        self.slots=threading.BoundedSemaphore(maximum_connections)
        super().__init__(("127.0.0.1",port),ingress_handler(target,uid))


def main():
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument("--config",required=True);args=parser.parse_args()
    config=strict_json(protected_read(args.config,65536))
    if not isinstance(config,dict):raise ValueError("invalid relay configuration")
    if set(config)=={"mode","spec"} and config["mode"]=="owner":
        from .sandbox import launch
        raise SystemExit(launch(config["spec"]))
    if set(config)=={"mode","policy","id","deployment_id"} and config["mode"]=="model":
        if os.environ.get("LISTEN_FDS")!="1" or os.environ.get("LISTEN_PID")!=str(os.getpid()):raise ValueError("one systemd socket required")
        from .sandbox import _ID
        if any(not isinstance(config[k],str) or not _ID.fullmatch(config[k]) for k in ("id","deployment_id")):raise ValueError("invalid gateway identity")
        policy=ModelGateway.parse(config["policy"])
        expected=Path("/run/anvil-native-agents")/config["deployment_id"]/config["id"]/"model.sock"
        listener=socket.socket(fileno=os.dup(3))
        try:
            verify_activated_listener(listener,expected,policy.owner_uid)
        finally:listener.close()
        # No unrelated descriptors or host sockets reach request handling.
        import resource
        os.closerange(4,resource.getrlimit(resource.RLIMIT_NOFILE)[0])
        credential=protected_read(policy.credential_file,4096).decode().strip()
        server=BoundedUnixServer(3,model_handler(policy,credential))
    elif set(config)=={"mode","port","id","deployment_id","uid"} and config["mode"]=="ingress":
        from .sandbox import _ID
        if any(not isinstance(config[k],str) or not _ID.fullmatch(config[k]) for k in ("id","deployment_id")) or type(config["uid"]) is not int or not 60000<=config["uid"]<=64999:raise ValueError("invalid owner relay")
        target=Path("/var/lib/anvil-native-agents")/config["deployment_id"]/"owners"/config["id"]/"channels/ingress/owner.sock"
        server=BoundedTCPServer(config["port"],str(target),config["uid"])
    else:raise ValueError("invalid relay mode")
    try:server.serve_forever()
    finally:server.server_close()

if __name__=="__main__":main()
