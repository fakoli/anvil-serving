"""Independent application sessions and explicit resource/action grants."""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import re
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


_CONNECT_HEADER = "X-Anvil-Connect-Identity"
_CONNECT_B64 = re.compile(r"[A-Za-z0-9_-]+\Z")
_CONNECT_ASSERTION = re.compile(r"acai1\.([A-Za-z0-9_-]{1,4096})\.([A-Za-z0-9_-]{43})\Z")
_CONNECT_HEX32 = re.compile(r"[a-f0-9]{32}\Z")
_CONNECT_EPOCH = re.compile(r"[a-f0-9]{64}\Z")
_CONNECT_ID = re.compile(r"[a-z][a-z0-9-]{0,62}\Z")
_ENV_NAME = re.compile(r"[A-Z][A-Z0-9_]{0,127}\Z")


def _connect_denied() -> None:
    raise ObservatoryError("connect_assertion_denied", "Anvil Connect identity verification failed.", 401)


def _raw_base64url(value: object, *, size: int | None = None) -> bytes:
    if type(value) is not str or not _CONNECT_B64.fullmatch(value) or "=" in value:
        raise ValueError("invalid raw base64url")
    try:
        decoded = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    except Exception as exc:
        raise ValueError("invalid raw base64url") from exc
    if base64.urlsafe_b64encode(decoded).decode().rstrip("=") != value or (size is not None and len(decoded) != size):
        raise ValueError("noncanonical raw base64url")
    return decoded


def _opaque_connect_principal(value: object) -> bool:
    if type(value) is not str or "\x00" in value or "\r" in value or "\n" in value or "\t" in value:
        return False
    try:
        return 1 <= len(value.encode("utf-8")) <= 192
    except UnicodeEncodeError:
        return False


@dataclass(frozen=True)
class ConnectBinding:
    subject: str
    sid: str
    session_generation: int
    policy_generation: int
    epoch: str

    def key(self) -> tuple[str, str, int, int, str]:
        return (self.subject, self.sid, self.session_generation, self.policy_generation, self.epoch)


@dataclass(frozen=True)
class ConnectAssertion:
    binding: ConnectBinding
    session_exp: int


class ConnectVerifier:
    """Verify compact, manager-injected Anvil Connect identity assertions."""

    def __init__(self, config: object, *, origin: str, environment, clock=time.time):
        if type(config) is not dict or set(config) != {"resource", "keys", "principals"}:
            raise ValueError("invalid Connect authentication configuration")
        if type(config["resource"]) is not str or not _CONNECT_ID.fullmatch(config["resource"]):
            raise ValueError("invalid Connect resource")
        self.resource = config["resource"]
        parsed = urllib.parse.urlsplit(origin)
        self.host = parsed.netloc
        self.clock = clock
        if type(config["keys"]) is not list or not 1 <= len(config["keys"]) <= 2:
            raise ValueError("Connect authentication requires one or two keys")
        self.keys = {}
        for item in config["keys"]:
            if type(item) is not dict or set(item) != {"id", "secret_env"}:
                raise ValueError("invalid Connect signing key")
            key_id = item["id"]
            name = item["secret_env"]
            if type(key_id) is not str or not _CONNECT_ID.fullmatch(key_id) or key_id in self.keys or type(name) is not str or not _ENV_NAME.fullmatch(name):
                raise ValueError("invalid Connect signing key")
            try:
                secret = _raw_base64url(environment.get(name), size=32)
            except ValueError as exc:
                raise ValueError("Connect signing key is unavailable") from exc
            self.keys[key_id] = secret
        if type(config["principals"]) is not dict or len(config["principals"]) > 256:
            raise ValueError("invalid Connect principal mapping")
        self.principals = dict(config["principals"])
        self._replays: dict[str, int] = {}
        self._lock = threading.Lock()

    def verify(self, headers, *, method: str, target: str, consume_replay=False) -> ConnectAssertion:
        values = headers.get_all(_CONNECT_HEADER) or []
        if len(values) != 1 or type(values[0]) is not str or len(values[0]) > 8192:
            _connect_denied()
        match = _CONNECT_ASSERTION.fullmatch(values[0])
        if match is None or not target.startswith("/") or len(target) > 8192:
            _connect_denied()
        encoded, encoded_mac = match.groups()
        try:
            payload_raw = _raw_base64url(encoded)
            mac = _raw_base64url(encoded_mac, size=32)
            payload = strict_json(payload_raw)
            if type(payload) is not dict or set(payload) != {
                "v", "iss", "kid", "sub", "sid", "sg", "pg", "epoch", "resource", "host",
                "method", "target_sha256", "iat", "exp", "session_exp", "jti",
            }:
                raise ValueError("invalid assertion schema")
            kid = identifier(payload["kid"])
            secret = self.keys[kid]
            expected = hmac.new(secret, ("acai1." + encoded).encode("ascii"), hashlib.sha256).digest()
            if not hmac.compare_digest(mac, expected):
                raise ValueError("invalid assertion MAC")
            if (type(payload["v"]) is not int or payload["v"] != 1 or payload["iss"] != "anvil-connect"
                    or not _opaque_connect_principal(payload["sub"])
                    or type(payload["sid"]) is not str or not _CONNECT_HEX32.fullmatch(payload["sid"])
                    or type(payload["epoch"]) is not str or not _CONNECT_EPOCH.fullmatch(payload["epoch"])
                    or any(type(payload[name]) is not int or payload[name] <= 0 or payload[name] > 2**64 - 1 for name in ("sg", "pg"))
                    or any(type(payload[name]) is not int or isinstance(payload[name], bool) or payload[name] < 0 or payload[name] > 2**63 - 1 for name in ("iat", "exp", "session_exp"))
                    or payload["resource"] != self.resource or payload["host"] != self.host or payload["method"] != method
                    or type(payload["target_sha256"]) is not str or not re.fullmatch(r"[a-f0-9]{64}", payload["target_sha256"])
                    or type(payload["jti"]) is not str or not _CONNECT_HEX32.fullmatch(payload["jti"])):
                raise ValueError("invalid assertion fields")
            target_hash = hashlib.sha256(target.encode("ascii")).hexdigest()
            if not hmac.compare_digest(payload["target_sha256"], target_hash):
                raise ValueError("wrong target")
            now = int(self.clock())
            if (payload["exp"] < payload["iat"] or payload["exp"] > payload["iat"] + 30
                    or payload["exp"] > payload["session_exp"] or payload["session_exp"] < now - 5
                    or payload["iat"] > now + 5 or payload["exp"] < now - 5):
                raise ValueError("expired assertion")
        except (KeyError, TypeError, UnicodeEncodeError, ValueError, ObservatoryError):
            _connect_denied()
        assertion = ConnectAssertion(
            ConnectBinding(payload["sub"], payload["sid"], payload["sg"], payload["pg"], payload["epoch"]),
            payload["session_exp"],
        )
        if consume_replay:
            with self._lock:
                self._replays = {key: expiry for key, expiry in self._replays.items() if expiry >= now - 5}
                if payload["jti"] in self._replays:
                    _connect_denied()
                if len(self._replays) >= 4096:
                    raise ObservatoryError("connect_replay_full", "Anvil Connect identity verification is temporarily unavailable.", 503)
                self._replays[payload["jti"]] = payload["exp"]
        return assertion


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
    connect_binding: ConnectBinding | None = None
    profile_id: str | None = None


class Access:
    COOKIE = "anvil_observatory_session"

    def __init__(self, users: list[dict], *, authenticate, origin: str, base_path="/", operate=False,
                 lifetime=3600, clock=time.time, connect=None, environment=None, profile_store=None):
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
        self.users_by_id = {}
        for user in users:
            name = identifier(user["username"])
            principal = Principal(identifier(user["id"]), name, user.get("role", "viewer"),
                                  frozenset(user.get("resources", [])), frozenset(user.get("actions", [])))
            if name in self.users or principal.role not in {"viewer", "operator", "administrator"}:
                raise ValueError("invalid principal")
            self.users[name] = principal
            if principal.identity in self.users_by_id:
                raise ValueError("duplicate principal identity")
            self.users_by_id[principal.identity] = principal
        self._sessions: dict[str, Session] = {}
        self._connect_sessions: dict[tuple[str, str, int, int, str], str] = {}
        self._attempts: dict[str, deque] = defaultdict(deque)
        self._lock = threading.RLock()
        self.connect = None
        self._connect_principals = {}
        self.profile_store = profile_store
        if connect is not None:
            if profile_store is None:
                raise ValueError("Connect authentication requires a profile store")
            self.connect = ConnectVerifier(connect, origin=origin, environment=os.environ if environment is None else environment, clock=clock)
            for subject, native_id in self.connect.principals.items():
                if not _opaque_connect_principal(subject) or type(native_id) is not str or native_id not in self.users_by_id:
                    raise ValueError("invalid Connect principal mapping")
                self._connect_principals[subject] = self.users_by_id[native_id]

    def require_origin(self, headers) -> None:
        values = headers.get_all("Origin") or []
        if values != [self.origin]:
            raise ObservatoryError("origin_denied", "Open this application at its configured private address.", 403)

    def check_host(self, headers) -> None:
        if headers.get_all("Host") != [urllib.parse.urlsplit(self.origin).netloc]:
            raise ObservatoryError("host_denied", "Use the configured application address.", 403)

    def login(self, username: object, password: object, *, client: str, previous=None) -> Session:
        if self.connect is not None:
            raise ObservatoryError("connect_login_disabled", "Sign in through Anvil Connect.", 405)
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
                self._remove_session_locked(key)
                session = None
        if session is None and required:
            raise ObservatoryError("unauthenticated", "Sign in to continue.", 401)
        return session

    def connect_session(self, headers, *, method: str, target: str, bootstrap=False, consume_replay=False) -> tuple[Session, bool]:
        """Require one fresh assertion and a local session bound to it.

        Only the session read is allowed to create a native session. All other
        facade reads and writes need both the current signed assertion and the
        existing cookie bound to the exact Connect session generations.
        """
        if self.connect is None:
            raise RuntimeError("Connect authentication is not configured")
        assertion = self.connect.verify(headers, method=method, target=target, consume_replay=consume_replay)
        now = self.clock()
        cookie_session = self.session(headers, required=False)
        if cookie_session and cookie_session.connect_binding == assertion.binding:
            return cookie_session, False
        if not bootstrap:
            _connect_denied()
        if method != "GET":
            _connect_denied()
        binding_key = assertion.binding.key()
        with self._lock:
            self._prune_sessions_locked(now)
            # A Connect cookie that no longer matches the freshly verified
            # browser session is unusable. Drop this presented stale binding
            # before bootstrap so repeated logout/login cycles cannot consume
            # the native session cap. A legacy cookie remains untouched.
            if cookie_session is not None and cookie_session.connect_binding is not None:
                self._remove_session_locked(cookie_session.key)
            existing_key = self._connect_sessions.get(binding_key)
            existing = self._sessions.get(existing_key) if existing_key else None
            if existing is not None:
                return existing, True
            mapped = self._connect_principals.get(assertion.binding.subject)
            profile_id = None
            if mapped is None:
                profile = self.profile_store.connect_profile(assertion.binding.subject)
                profile_id = profile["id"]
                mapped = Principal(profile_id, profile_id, "viewer", frozenset(), frozenset())
            if len(self._sessions) >= 256:
                raise ObservatoryError("sessions_full", "Too many active sessions; try later.", 429)
            expires_at = min(now + self.lifetime, float(assertion.session_exp))
            if expires_at <= now:
                _connect_denied()
            session = Session(secrets.token_urlsafe(32), secrets.token_urlsafe(32), mapped, expires_at,
                              connect_binding=assertion.binding, profile_id=profile_id)
            self._sessions[session.key] = session
            self._connect_sessions[binding_key] = session.key
            return session, True

    def _prune_sessions_locked(self, now: float) -> None:
        for key, value in list(self._sessions.items()):
            if value.expires_at <= now:
                self._remove_session_locked(key)

    def _remove_session_locked(self, key: str | None) -> None:
        if key is None:
            return
        value = self._sessions.pop(key, None)
        if value is not None and value.connect_binding is not None:
            self._connect_sessions.pop(value.connect_binding.key(), None)

    def mutation(self, headers, *, session=None) -> Session:
        self.require_origin(headers)
        session = session or self.session(headers)
        values = headers.get_all("X-CSRF-Token") or []
        if len(values) != 1 or not hmac.compare_digest(values[0], session.csrf):
            raise ObservatoryError("csrf_denied", "Refresh your session before applying this change.", 403)
        return session

    def permit(self, session: Session, resource: str, action: str | None = None) -> None:
        if not session.principal.can_read(resource) or (action and (not self.operate or not session.principal.can_operate(resource, action))):
            raise ObservatoryError("permission_denied", "Your session does not grant this resource action.", 403)

    def logout(self, session: Session) -> None:
        with self._lock:
            self._remove_session_locked(session.key)

    def cookie(self, session: Session | None) -> str:
        # Path-scoped cookie intentionally has no __Host- prefix. Secure remains
        # mandatory even when an isolated unit test uses loopback HTTP.
        seconds = (self.lifetime if session and session.connect_binding is None
                   else max(0, int(session.expires_at - self.clock())) if session else 0)
        return (f"{self.COOKIE}={session.key if session else ''}; Path={self.base_path}; "
                f"Max-Age={seconds}; Secure; HttpOnly; SameSite=Strict")
