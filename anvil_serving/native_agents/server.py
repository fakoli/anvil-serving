"""Loopback native Agents HTTP service, intended behind a managed TLS edge."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import hashlib
from pathlib import Path
import re
import threading
from .core import Broker, PREFIX, MAX_BODY
from .owner import PiOwner, canonical_origin
from .store import Store
from .types import AgentsError, Directory, Workspace, ID, PRINCIPAL
from .auth import AdmissionDenied, strict_json, protected_read


def load_config(path):
    data = strict_json(protected_read(path,1048576))
    required = {"version","deployment_id","listen_port","store_file","workspaces","auth"}
    if set(data) not in (required, required | {"personal_principal"}) or data["version"] != 1:
        raise ValueError("invalid broker configuration")
    personal_principal = data.get("personal_principal")
    if "personal_principal" in data and (not isinstance(personal_principal, str) or not PRINCIPAL.fullmatch(personal_principal)):
        raise ValueError("invalid personal principal")
    if type(data["listen_port"]) is not int or not 1024 <= data["listen_port"] <= 65535: raise ValueError("invalid listen port")
    workspaces = []
    if not isinstance(data["workspaces"], list) or len(data["workspaces"]) > 256: raise ValueError("invalid workspaces")
    for w in data["workspaces"]:
        if set(w) != {"id","principal","label","directories","owner_url","owner_token_file","isolation"}: raise ValueError("invalid workspace")
        if not ID.fullmatch(w["id"]) or not PRINCIPAL.fullmatch(w["principal"]) or w["isolation"] not in ("personal","sandboxed"):
            raise ValueError("invalid workspace identity")
        if not isinstance(w["label"],str) or not 0 < len(w["label"]) <= 128 or not isinstance(w["directories"], list) or not 1 <= len(w["directories"]) <= 128: raise ValueError("invalid workspace catalog")
        directories = []
        for d in w["directories"]:
            if set(d) not in ({"id","label","path","display_path"},{"id","label","path","display_path","runtime_path"}) or not ID.fullmatch(d["id"]): raise ValueError("invalid directory")
            if any(not isinstance(d[k],str) or not 0 < len(d[k]) <= 4096 for k in ("label","path","display_path")): raise ValueError("invalid directory")
            p = Path(d["path"])
            if not p.is_absolute() or p.resolve(strict=True) != p or not p.is_dir(): raise ValueError("directory must be canonical")
            runtime = d.get("runtime_path")
            if runtime is not None and (not isinstance(runtime,str) or not runtime.startswith("/workspace/") or ".." in Path(runtime).parts or len(runtime)>4096): raise ValueError("invalid runtime directory")
            directories.append(Directory(d["id"],d["label"],p,d["display_path"],runtime))
        if len({d.id for d in directories}) != len(directories): raise ValueError("duplicate directory")
        workspaces.append(Workspace(w["id"],w["principal"],w["label"],tuple(directories),canonical_origin(w["owner_url"]),Path(w["owner_token_file"]),w["isolation"]))
    if len({w.principal for w in workspaces}) > 1 and personal_principal is None:
        raise ValueError("multiple principals require an explicit personal principal")
    if personal_principal is not None and any(w.isolation == "personal" and w.principal != personal_principal for w in workspaces):
        raise ValueError("personal workspaces are reserved for the personal principal")
    # A workspace has a dedicated owner. Reusing a shared personal endpoint would
    # collapse isolation, even if the broker's directory picker looked scoped.
    if len({w.owner_url for w in workspaces}) != len(workspaces): raise ValueError("workspace owners must be distinct")
    token_paths = [w.owner_token_file for w in workspaces]
    if any(not p.is_absolute() or p.resolve(strict=True) != p for p in token_paths) or len(set(token_paths)) != len(token_paths):
        raise ValueError("workspace owner credential paths must be distinct and canonical")
    token_digests = [hashlib.sha256(protected_read(p,4096).strip()).digest() for p in token_paths]
    if len(set(token_digests)) != len(token_digests): raise ValueError("workspace owner credentials must be distinct")
    return data, workspaces


def handler(broker):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "AnvilNativeAgents"
        def log_message(self, *args): pass  # request paths/bearers never enter access logs
        def do_GET(self): self.dispatch()
        def do_POST(self): self.dispatch()
        def do_PUT(self): self.send_json(405,{"code":"method_not_allowed"})
        def do_DELETE(self): self.send_json(405,{"code":"method_not_allowed"})
        def send_json(self,status,value):
            raw = json.dumps(value,separators=(",", ":")).encode()
            self.send_response(status); self.send_header("Content-Type","application/json"); self.send_header("Cache-Control","no-store")
            self.send_header("Content-Length",str(len(raw))); self.end_headers(); self.wfile.write(raw)
        def dispatch(self):
            stream_started = False
            try:
                if self.headers.get_all("Authorization",[]) and len(self.headers.get_all("Authorization",[])) != 1: raise AgentsError(401,"unauthorized")
                bearer = self.headers.get("Authorization","")
                if not bearer.startswith("Bearer ") or len(bearer) > 16384 or any(ord(c) < 33 or ord(c) > 126 for c in bearer[7:]): raise AgentsError(401,"unauthorized")
                try: admission = broker.admission(bearer[7:])
                except AdmissionDenied: raise AgentsError(401,"unauthorized") from None
                if self.headers.get("Transfer-Encoding") or len(self.headers.get_all("Content-Length",[])) > 1: raise AgentsError(400,"invalid_request")
                length = self.headers.get("Content-Length","0")
                if not length.isdigit() or int(length) > MAX_BODY: raise AgentsError(413,"request_too_large")
                size = int(length); body = None
                if self.command == "POST":
                    if self.headers.get_content_type() != "application/json" or size == 0: raise AgentsError(400,"invalid_request")
                    self.connection.settimeout(10)
                    try: body = strict_json(self.rfile.read(size))
                    except AdmissionDenied: raise AgentsError(400,"invalid_request") from None
                elif size: raise AgentsError(400,"invalid_request")
                match = re.fullmatch(re.escape(PREFIX)+r"/workspaces/([A-Za-z0-9_-]+)/sessions/([A-Za-z0-9_-]+)/events",self.path)
                if self.command == "GET" and match:
                    iterator = broker.events(admission,*match.groups())
                    first = next(iterator, None)  # authorize and open before 200
                    self.send_response(200); self.send_header("Content-Type","text/event-stream"); self.send_header("Cache-Control","no-store")
                    self.send_header("Connection","close"); self.end_headers(); self.close_connection = True; stream_started = True
                    try:
                        if first: self.wfile.write(first); self.wfile.flush()
                        for chunk in iterator: self.wfile.write(chunk); self.wfile.flush()
                    finally: iterator.close()
                else:
                    status, result = broker.handle(admission,self.command,self.path,body)
                    self.send_json(status,result)
            except AgentsError as error:
                if not stream_started: self.send_json(error.status,{"code":error.code})
                self.close_connection = True
            except (ValueError,TypeError,KeyError):
                if not stream_started: self.send_json(400,{"code":"invalid_request"})
                self.close_connection = True
            except (BrokenPipeError,ConnectionResetError,TimeoutError): self.close_connection = True
            except Exception:
                if not stream_started: self.send_json(503,{"code":"unavailable"})
                self.close_connection = True
    return Handler


class BoundedServer(ThreadingHTTPServer):
    daemon_threads = True
    def __init__(self,*args,maximum_connections=64,**kwargs):
        self.slots = threading.BoundedSemaphore(maximum_connections)
        super().__init__(*args,**kwargs)
    def get_request(self):
        request,address = super().get_request()
        request.settimeout(10)
        return request,address
    def process_request(self,request,client_address):
        if not self.slots.acquire(blocking=False):
            try: request.sendall(b"HTTP/1.1 503 Service Unavailable\r\nConnection: close\r\nContent-Length: 0\r\n\r\n")
            finally: self.shutdown_request(request)
            return
        try: super().process_request(request,client_address)
        except BaseException:
            self.slots.release(); raise
    def process_request_thread(self,request,client_address):
        try: super().process_request_thread(request,client_address)
        finally: self.slots.release()

def serve(config_path):
    from .auth import NativeAuthenticator
    data, workspaces = load_config(config_path)
    auth = NativeAuthenticator(data["auth"])
    broker = Broker(data["deployment_id"],workspaces,Store(Path(data["store_file"])),
                    {w.id:PiOwner(w) for w in workspaces},auth.authorize,auth.recheck)
    server = BoundedServer(("127.0.0.1",data["listen_port"]),handler(broker))
    server.daemon_threads = True
    try: server.serve_forever()
    finally: server.server_close(); broker.worker.shutdown(); broker.stop_worker.shutdown(); broker.interaction_worker.shutdown()
