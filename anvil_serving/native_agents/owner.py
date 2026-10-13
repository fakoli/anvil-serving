"""Private Pi owner adapter. No arbitrary upstream URL or credentials from callers."""
import json
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler, ProxyHandler
from .auth import protected_read
from .types import AgentsError

class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise AgentsError(502, "owner_redirect")

def canonical_origin(value):
    url = urlsplit(value)
    if (url.scheme != "http" or url.hostname != "127.0.0.1" or not url.port
        or not 1 <= url.port <= 65535 or url.path not in ("", "/") or url.query or url.fragment
        or url.username is not None or url.password is not None or url.netloc != f"127.0.0.1:{url.port}"):
        raise ValueError("owner must be a fixed canonical loopback origin")
    return f"http://127.0.0.1:{url.port}"

class PiOwner:
    def __init__(self, workspace):
        self.url = canonical_origin(workspace.owner_url) + "/api/native-agents-owner"
        self.token = protected_read(workspace.owner_token_file,4096).decode().strip()
        if not self.token or any(ord(c) < 33 or ord(c) > 126 for c in self.token): raise ValueError("invalid owner token")
        self.opener = build_opener(ProxyHandler({}), NoRedirect())

    def request(self, action, session=None, stream=False, **values):
        data = {"action": action, **({"sessionID": session} if session else {}), **values}
        request = Request(self.url, data=json.dumps(data, separators=(",", ":")).encode(), method="POST",
                          headers={"Authorization": "Bearer " + self.token, "Content-Type": "application/json"})
        try:
            response = self.opener.open(request, timeout=60 if action == "invoke" else 10)
            expected = "text/event-stream" if stream else "application/json"
            if response.headers.get_content_type() != expected:
                response.close(); raise AgentsError(502, "owner_invalid_response")
            if stream: return response
            with response:
                raw = response.read(8388609)
                if len(raw) > 8388608: raise AgentsError(502, "owner_response_too_large")
                result = json.loads(raw)
                if not isinstance(result, dict): raise AgentsError(502, "owner_invalid_response")
                return result
        except HTTPError as e:
            e.close()
            raise AgentsError(e.code if e.code in (400,404,409,422) else 503, "owner_rejected" if e.code < 500 else "owner_unavailable") from None
        except (URLError, TimeoutError, OSError, ValueError): raise AgentsError(503, "owner_unavailable") from None

    def operation(self, session, request): return self.request("operation",session,requestID=request)
    def sessions(self): return self.request("sessions")["sessions"]
    def ensure(self, session, cwd): return self.request("ensure", session, cwd=cwd)
    def history(self, session, before=None): return self.request("history", session, before=before)
    def state(self, session): return self.request("state", session)
    def catalog(self, session): return self.request("catalog", session)
    def interactions(self, session): return self.request("interactions", session)
    def invoke(self, session, request, instance, message):
        return self.request("invoke", session, requestID=request, ownerInstance=instance, message=message)
    def invoke_command(self, session, request, instance, command_id, revision, arguments):
        return self.request("invoke_command",session,requestID=request,ownerInstance=instance,commandID=command_id,catalogRevision=revision,arguments=arguments)
    def abort(self, session, pending): return self.request("abort", session, pendingRequests=pending)
    def respond(self, session, interaction, instance, answer):
        return self.request("respond", session, interactionID=interaction, ownerInstance=instance, answer=answer)
    def events(self, session):
        with self.request("events", session, stream=True) as response:
            event_size = 0
            while True:
                line = response.readline(1048577)
                if not line: return
                event_size += len(line)
                if event_size > 1048576: raise AgentsError(502, "owner_event_too_large")
                if line in (b"\n", b"\r\n"): event_size = 0
                yield line
