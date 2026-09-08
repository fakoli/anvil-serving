"""Independent application sessions and explicit resource/action grants."""

from __future__ import annotations

import base64
import hmac
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict, deque
from dataclasses import dataclass
from http.cookies import SimpleCookie

from .contracts import ObservatoryError, identifier, strict_json


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class GrafanaLogin:
    """Reuse the configured Grafana identity provider; never forward its credential.

    This is a login-only bounded call to a server-configured URL. It does not
    retain the user's password or return Grafana tokens to the browser.
    """

    def __init__(self, url: str):
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("invalid authentication origin")
        if parsed.scheme == "http" and parsed.hostname != "127.0.0.1":
            raise ValueError("authentication requires HTTPS or loopback")
        self.url = url.rstrip("/") + "/api/user"
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())

    def __call__(self, username: str, password: str) -> bool:
        authorization = base64.b64encode(f"{username}:{password}".encode()).decode()
        request = urllib.request.Request(self.url, headers={"Authorization": f"Basic {authorization}", "Accept": "application/json"})
        try:
            with self.opener.open(request, timeout=5) as response:
                raw = response.read(16385)
                if len(raw) > 16384 or response.status != 200:
                    return False
                payload = strict_json(raw)
                return type(payload) is dict and payload.get("login") == username and payload.get("isDisabled") is not True
        except Exception:
            return False


@dataclass(frozen=True)
class Principal:
    identity: str
    username: str
    role: str
    resources: frozenset[str]
    actions: frozenset[str]

    def can_read(self, resource: str) -> bool:
        return resource in self.resources or "*" in self.resources

    def can_operate(self, resource: str, action: str) -> bool:
        return self.can_read(resource) and action in self.actions


@dataclass(frozen=True)
class Session:
    key: str
    csrf: str
    principal: Principal
    expires_at: float


class Access:
    COOKIE = "anvil_observatory_session"

    def __init__(self, users: list[dict], *, authenticate, origin: str, base_path="/", operate=False, lifetime=3600, clock=time.time):
        parsed = urllib.parse.urlsplit(origin)
        if parsed.scheme not in {"https", "http"} or parsed.path or parsed.query or parsed.fragment or parsed.username or parsed.password or not parsed.netloc:
            raise ValueError("provide one exact application origin")
        if parsed.scheme == "http" and parsed.hostname != "127.0.0.1":
            raise ValueError("application requires HTTPS or loopback test origin")
        if type(lifetime) is not int or not 60 <= lifetime <= 28800:
            raise ValueError("invalid session lifetime")
        if type(base_path) is not str or not base_path.startswith("/") or not base_path.endswith("/") or any(s in base_path for s in ("..", "?", "#", ";", "\\")):
            raise ValueError("invalid cookie path")
        self.origin, self.base_path = origin, base_path
        self.operate, self.lifetime, self.clock = operate, lifetime, clock
        self.authenticate = authenticate
        self.users = {}
        for user in users:
            name = identifier(user["username"])
            principal = Principal(identifier(user["id"]), name, user.get("role", "viewer"),
                                  frozenset(user.get("resources", [])), frozenset(user.get("actions", [])))
            if name in self.users or principal.role not in {"viewer", "operator", "administrator"}:
                raise ValueError("invalid principal")
            self.users[name] = principal
        self._sessions: dict[str, Session] = {}
        self._attempts: dict[str, deque] = defaultdict(deque)
        self._lock = threading.RLock()

    def require_origin(self, headers) -> None:
        values = headers.get_all("Origin") or []
        if values != [self.origin]:
            raise ObservatoryError("origin_denied", "Open this application at its configured private address.", 403)

    def check_host(self, headers) -> None:
        if headers.get_all("Host") != [urllib.parse.urlsplit(self.origin).netloc]:
            raise ObservatoryError("host_denied", "Use the configured application address.", 403)

    def login(self, username: object, password: object, *, client: str, previous=None) -> Session:
        if type(username) is not str or type(password) is not str or not 1 <= len(username) <= 192 or not 1 <= len(password) <= 1024 or ":" in username:
            raise ObservatoryError("unauthenticated", "Sign-in was not accepted.", 401)
        now = self.clock()
        with self._lock:
            # One bounded bucket per connection source avoids attacker-supplied
            # usernames creating an unbounded map. Successful auth clears it.
            if client not in self._attempts and len(self._attempts) >= 1024:
                self._attempts.pop(next(iter(self._attempts)))
            attempts = self._attempts[client]
            while attempts and attempts[0] < now - 60:
                attempts.popleft()
            if len(attempts) >= 10:
                raise ObservatoryError("login_limited", "Please wait a minute before trying to sign in again.", 429)
            attempts.append(now)
        if username not in self.users or not self.authenticate(username, password):
            raise ObservatoryError("unauthenticated", "Sign-in was not accepted.", 401)
        session = Session(secrets.token_urlsafe(32), secrets.token_urlsafe(32), self.users[username], now + self.lifetime)
        with self._lock:
            self._sessions = {key: value for key, value in self._sessions.items() if value.expires_at > now}
            if len(self._sessions) >= 256:
                raise ObservatoryError("sessions_full", "Too many active sessions; try later.", 429)
            if previous:
                self._sessions.pop(previous.key, None)
            self._sessions[session.key] = session
            self._attempts.pop(client, None)
        return session

    def session(self, headers, *, required=True) -> Session | None:
        values = headers.get_all("Cookie") or []
        key = None
        if len(values) == 1 and len(values[0]) <= 8192:
            # Reject duplicated security-critical cookie names.
            if sum(part.strip().startswith(self.COOKIE + "=") for part in values[0].split(";")) == 1:
                cookies = SimpleCookie()
                try:
                    cookies.load(values[0])
                    key = cookies[self.COOKIE].value if self.COOKIE in cookies else None
                except Exception:
                    pass
        with self._lock:
            session = self._sessions.get(key)
            if session is not None and session.expires_at <= self.clock():
                self._sessions.pop(key, None)
                session = None
        if session is None and required:
            raise ObservatoryError("unauthenticated", "Sign in to continue.", 401)
        return session

    def mutation(self, headers) -> Session:
        self.require_origin(headers)
        session = self.session(headers)
        values = headers.get_all("X-CSRF-Token") or []
        if len(values) != 1 or not hmac.compare_digest(values[0], session.csrf):
            raise ObservatoryError("csrf_denied", "Refresh your session before applying this change.", 403)
        return session

    def permit(self, session: Session, resource: str, action: str | None = None) -> None:
        if not session.principal.can_read(resource) or (action and (not self.operate or not session.principal.can_operate(resource, action))):
            raise ObservatoryError("permission_denied", "Your session does not grant this resource action.", 403)

    def logout(self, session: Session) -> None:
        with self._lock:
            self._sessions.pop(session.key, None)

    def cookie(self, session: Session | None) -> str:
        # Path-scoped cookie intentionally has no __Host- prefix. Secure remains
        # mandatory even when an isolated unit test uses loopback HTTP.
        return (f"{self.COOKIE}={session.key if session else ''}; Path={self.base_path}; "
                f"Max-Age={self.lifetime if session else 0}; Secure; HttpOnly; SameSite=Strict")
