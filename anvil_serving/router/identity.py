"""Closed immutable caller metadata; existing credentials retain all authority."""
from __future__ import annotations

from dataclasses import asdict, dataclass, fields, replace
from datetime import datetime, timezone
import hashlib
import hmac
import json
import math
import os
import re
import unicodedata

from ..control_plane.authorization import ALLOWED_SCOPES, AuthorizationDecision
from ..observability.dashboard.access import _raw_base64url
from ..observability.dashboard.contracts import ObservatoryError, strict_json
from ..control_plane.mcp.auth_file import AuthFileError, read_private_auth_file
from .keys import KeyStoreError

_OPAQUE = re.compile(r"[A-Za-z0-9_:.\-]{1,128}\Z")
_CREDENTIAL = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,63}\Z")
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
SCHEMA = "router-usage/v1"


class IdentityError(KeyStoreError):
    def __init__(self):
        super().__init__("invalid caller policy metadata")


class WebUIIdentityError(IdentityError):
    """Fixed public failure for required native signed identity."""

    code = "identity_invalid"
    status = 401

    def __init__(self):
        KeyStoreError.__init__(self, "invalid forwarded identity")


def _subject(value):
    try:
        _require(type(value) is str and 1 <= len(value.encode("utf-8")) <= 128
                 and not any(unicodedata.category(c).startswith("C") for c in value))
    except UnicodeError:
        raise IdentityError() from None
    return value


@dataclass(frozen=True, slots=True)
class EndUser:
    """Verified attribution, never an authorization grant or profile claims."""

    instance: str
    issuer: str
    subject: str
    state: str = "verified_unmapped"
    owner: str | None = None
    generation: str | None = None
    epoch: str | None = None
    approval_revision: int | None = None

    def __post_init__(self):
        opaque_id(self.instance)
        _require(self.issuer == "open-webui")
        _subject(self.subject)
        _require(type(self.state) is str and self.state in {"verified", "verified_unmapped"})
        if self.state == "verified_unmapped":
            _require(all(v is None for v in (self.owner, self.generation, self.epoch, self.approval_revision)))
        else:
            from .connect_keys import identity, Denied
            try:
                identity(self.owner, self.generation, self.epoch)
                _revision(self.approval_revision)
            except Denied:
                raise IdentityError() from None

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, value):
        return cls(**_closed(cls, value))


@dataclass(frozen=True, slots=True)
class WebUIProfile:
    """Secret-free operator references, bound to an authenticated service ID."""

    credential_id: str
    credential_kind: str
    instance: str
    signer_env: str | None = None
    signer_file: str | None = None
    issuer: str = "open-webui"
    require_user: bool = True
    clock_skew_seconds: int = 30

    def __post_init__(self):
        _require(type(self.credential_id) is str and _CREDENTIAL.fullmatch(self.credential_id) is not None)
        _require(type(self.credential_kind) is str and self.credential_kind in {"device_key", "configured_scope"})
        opaque_id(self.instance)
        _require(self.issuer == "open-webui" and self.require_user is True)
        _require(type(self.clock_skew_seconds) is int and 0 <= self.clock_skew_seconds <= 30)
        _require((self.signer_env is None) != (self.signer_file is None))
        if self.signer_env is not None:
            from .config import ConfigError, _validate_auth_env
            try:
                _require(type(self.signer_env) is str and len(self.signer_env) <= 128)
                _validate_auth_env(self.signer_env, "WebUI signer")
            except (ConfigError, TypeError):
                raise IdentityError() from None
        else:
            _require(type(self.signer_file) is str and 1 <= len(self.signer_file) <= 4096
                     and os.path.isabs(self.signer_file)
                     and not any(unicodedata.category(c).startswith("C") for c in self.signer_file))


def validate_webui_profiles(profiles):
    _require(type(profiles) is tuple and len(profiles) <= 32)
    credentials, instances, references = set(), set(), set()
    for profile in profiles:
        _require(type(profile) is WebUIProfile)
        reference = (profile.signer_env, profile.signer_file)
        _require(profile.credential_id not in credentials and profile.instance not in instances
                 and reference not in references)
        credentials.add(profile.credential_id)
        instances.add(profile.instance)
        references.add(reference)
    return profiles


class WebUIBinding:
    """Immutable process-local signer holder; deliberately has no serializer."""

    __slots__ = ("profile", "_signer")

    def __init__(self, profile, signer):
        _require(type(profile) is WebUIProfile and type(signer) is bytes and 32 <= len(signer) <= 4096)
        object.__setattr__(self, "profile", profile)
        object.__setattr__(self, "_signer", signer)

    def __setattr__(self, name, value):
        raise AttributeError("immutable WebUI binding")

    def __repr__(self):
        return "WebUIBinding(<protected>)"


def load_webui_bindings(profiles, *, env=None):
    """Resolve configured references at startup, never from request input."""
    try:
        validate_webui_profiles(profiles)
        environment = os.environ if env is None else env
        bindings = []
        for profile in profiles:
            raw = (environment.get(profile.signer_env) if profile.signer_env is not None else
                   read_private_auth_file(profile.signer_file, max_bytes=4096).decode("utf-8"))
            _require(type(raw) is str)
            signer = raw.strip().encode("utf-8")  # Native WebUI trims its env reference.
            binding = WebUIBinding(profile, signer)
            # Native iss is global: one actual signer cannot separate instances.
            _require(not any(hmac.compare_digest(signer, b._signer) for b in bindings))
            bindings.append(binding)
        return tuple(bindings)
    except (IdentityError, AuthFileError, OSError, ValueError, TypeError, UnicodeError):
        raise WebUIIdentityError() from None


def select_webui_binding(bindings, caller):
    """Select using the already authenticated CallerSnapshot, never wire IDs."""
    try:
        _require(type(bindings) is tuple and all(type(b) is WebUIBinding for b in bindings))
        validate_webui_profiles(tuple(b.profile for b in bindings))
        for index, binding in enumerate(bindings):
            _require(not any(hmac.compare_digest(binding._signer, other._signer)
                             for other in bindings[:index]))
        _require(type(caller) is CallerSnapshot)
        matches = [b for b in bindings if b.profile.credential_id == caller.credential_id]
        if not matches:
            return None
        binding = matches[0]
        kind = {"key_policy": "device_key", "configured_scope": "configured_scope"}.get(caller.grant.kind)
        _require(caller.actor.kind == "service" and kind == binding.profile.credential_kind)
        return binding
    except IdentityError:
        raise WebUIIdentityError() from None


def verify_webui(headers, binding, now_utc) -> EndUser:
    """Verify native HS256 assertions after service credential authentication.

    The injected clock is an aware UTC datetime or finite epoch seconds.
    Native repeated assertions remain valid; profile claims grant no authority.
    """
    try:
        _require(type(binding) is WebUIBinding)
        values = headers.get_all("X-OpenWebUI-User-Jwt", [])
        _require(type(values) is list and len(values) == 1)
        token = values[0]
        _require(type(token) is str and len(token) <= 8192 and token.isascii())
        parts = token.split(".")
        _require(len(parts) == 3 and all(parts))
        header_raw, payload_raw = _raw_base64url(parts[0]), _raw_base64url(parts[1])
        signature = _raw_base64url(parts[2], size=32)
        _require(len(header_raw) <= 512 and len(payload_raw) <= 4096)
        header = strict_json(header_raw.decode("utf-8"))
        claims = strict_json(payload_raw.decode("utf-8"))
        _require(type(header) is dict and set(header) in ({"alg"}, {"alg", "typ"})
                 and header["alg"] == "HS256" and ("typ" not in header or header["typ"] == "JWT"))
        _require(type(claims) is dict)
        expected = hmac.digest(binding._signer, (parts[0] + "." + parts[1]).encode("ascii"), "sha256")
        _require(hmac.compare_digest(signature, expected))
        _require(claims.get("iss") == binding.profile.issuer)
        subject = _subject(claims.get("sub"))
        iat, exp = claims.get("iat"), claims.get("exp")
        _require(type(iat) is int and type(exp) is int and 0 < exp - iat <= 300)
        if type(now_utc) is datetime:
            _require(now_utc.tzinfo is not None and now_utc.utcoffset() == timezone.utc.utcoffset(now_utc))
            now_utc = now_utc.timestamp()
        _require(type(now_utc) in (int, float) and math.isfinite(now_utc))
        skew = binding.profile.clock_skew_seconds
        _require(iat <= now_utc + skew and exp > now_utc - skew)
        return EndUser(binding.profile.instance, binding.profile.issuer, subject)
    except (IdentityError, ObservatoryError, ValueError, TypeError, UnicodeError, RecursionError,
            AttributeError, OverflowError):
        raise WebUIIdentityError() from None


def forwarded_caller(caller, end_user):
    """Attach verified attribution while preserving every authenticated grant fact."""
    _require(type(caller) is CallerSnapshot and type(end_user) is EndUser)
    return replace(caller, end_user=end_user, attribution_state="verified_forwarded")


def _require(condition):
    if not condition:
        raise IdentityError()


def opaque_id(value):
    _require(type(value) is str and _OPAQUE.fullmatch(value) is not None)
    return value


def _revision(value):
    _require(type(value) is int and 1 <= value < 2**53)


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _digest(value):
    return hashlib.sha256(_canonical(value)).hexdigest()


def _closed(cls, value):
    _require(type(value) is dict and set(value) == {f.name for f in fields(cls)})
    return dict(value)


def _grants(models, paths):
    from .keys import KeyStore, KeyStoreError
    _require(type(models) is tuple and type(paths) is tuple)
    try:
        _require(KeyStore._grants(list(models), list(paths)) == (models, paths))
    except KeyStoreError:
        raise IdentityError() from None


@dataclass(frozen=True, slots=True)
class Actor:
    kind: str
    id: str | None = None
    binding_revision: int | None = None
    generation: str | None = None
    epoch: str | None = None

    def __post_init__(self):
        _require(type(self.kind) is str and self.kind in {"human", "service", "unattributed"})
        if self.kind == "unattributed":
            _require(all(v is None for v in (self.id, self.binding_revision, self.generation, self.epoch)))
        else:
            opaque_id(self.id)
            if self.binding_revision is not None:
                _revision(self.binding_revision)
            if self.generation is not None or self.epoch is not None:
                from .connect_keys import identity, Denied
                try:
                    _require(self.kind == "human")
                    identity(self.id, self.generation, self.epoch)
                    _revision(self.binding_revision)
                except Denied:
                    raise IdentityError() from None

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, value):
        return cls(**_closed(cls, value))


@dataclass(frozen=True, slots=True)
class EffectiveGrant:
    kind: str
    reference: str | None = None
    revision: int | None = None
    policy_digest: str | None = None
    models: tuple[str, ...] = ()
    paths: tuple[str, ...] = ()
    scopes: tuple[str, ...] = ()
    rpm: int | None = None
    created_at: int | None = None
    expires_at: int | None = None
    owner: str | None = None
    generation: str | None = None
    epoch: str | None = None
    approval_revision: int | None = None
    client_id: str | None = None
    account_models: tuple[str, ...] = ()
    account_paths: tuple[str, ...] = ()
    account_rpm: int | None = None
    account_expires_days: int | None = None

    def __post_init__(self):
        _require(type(self.kind) is str and self.kind in {"connect", "key_policy", "configured_scope", "legacy"})
        for seq in (self.models, self.paths, self.scopes, self.account_models, self.account_paths):
            _require(type(seq) is tuple)
        if self.reference is not None:
            opaque_id(self.reference)
        if self.policy_digest is not None:
            _require(type(self.policy_digest) is str and _DIGEST.fullmatch(self.policy_digest) is not None)
        connect_fields = (self.owner, self.generation, self.epoch, self.approval_revision,
                          self.account_rpm, self.account_expires_days)
        if self.kind in {"key_policy", "connect"}:
            _grants(self.models, self.paths)
            _require(type(self.rpm) is int and 1 <= self.rpm <= 100000)
            _require(type(self.created_at) is int and self.created_at >= 0)
            _require(self.expires_at is None or type(self.expires_at) is int and self.expires_at > self.created_at)
            _require(self.reference is not None and not self.scopes and self.client_id is None)
        else:
            _require(not self.models and not self.paths and self.rpm is None and self.created_at is None and self.expires_at is None)
        if self.kind == "connect":
            from .connect_keys import identity, Denied
            try:
                identity(self.owner, self.generation, self.epoch)
            except Denied:
                raise IdentityError() from None
            _revision(self.approval_revision)
            _revision(self.revision)
            _require(self.revision == self.approval_revision and self.policy_digest is None)
            _grants(self.account_models, self.account_paths)
            _require(type(self.account_rpm) is int and self.rpm <= self.account_rpm <= 100000)
            _require(type(self.account_expires_days) is int and 1 <= self.account_expires_days <= 90)
            _require(self.expires_at is not None and self.expires_at - self.created_at <= self.account_expires_days * 86400)
            _require(set(self.models) <= set(self.account_models) and set(self.paths) <= set(self.account_paths))
            _require(self.reference == connect_reference(self.owner, self.generation, self.epoch, self.approval_revision))
        else:
            _require(all(v is None for v in connect_fields) and not self.account_models and not self.account_paths and self.revision is None)
        if self.kind == "configured_scope":
            _require(type(self.client_id) is str and _CREDENTIAL.fullmatch(self.client_id) is not None)
            _require(self.reference == self.client_id and self.scopes and all(type(s) is str and s in ALLOWED_SCOPES for s in self.scopes))
            _require(self.scopes == tuple(sorted(set(self.scopes))))
            _require(self.policy_digest == scope_digest(self.client_id, self.scopes))
        else:
            _require(not self.scopes and self.client_id is None)
        if self.kind == "key_policy":
            _require(_CREDENTIAL.fullmatch(self.reference) is not None)
            _require(self.policy_digest == _digest({"models": self.models, "paths": self.paths, "rpm": self.rpm,
                     "created_at": self.created_at, "expires_at": self.expires_at}))
        if self.kind == "legacy":
            _require(self.reference is None and self.policy_digest is None)

    def to_dict(self):
        value = asdict(self)
        for name in ("models", "paths", "scopes", "account_models", "account_paths"):
            value[name] = list(value[name])
        return value

    @classmethod
    def from_dict(cls, value):
        value = _closed(cls, value)
        for name in ("models", "paths", "scopes", "account_models", "account_paths"):
            _require(type(value[name]) is list)
            value[name] = tuple(value[name])
        return cls(**value)


def connect_reference(owner, generation, epoch, revision):
    # The bounded reference hashes public policy facts, never credential material.
    return "connect:" + _digest([owner, generation, epoch, revision])


def scope_digest(client_id, scopes):
    return _digest({"schema_version": 1, "client_id": client_id, "scopes": sorted(scopes)})


@dataclass(frozen=True, slots=True)
class CallerSnapshot:
    credential_id: str
    actor: Actor
    grant: EffectiveGrant
    attribution_state: str
    schema: str = SCHEMA
    end_user: EndUser | None = None

    def __post_init__(self):
        _require(self.schema == SCHEMA)
        _require(self.end_user is None or type(self.end_user) is EndUser)
        _require(type(self.actor) is Actor and type(self.grant) is EffectiveGrant)
        _require(type(self.credential_id) is str and (self.credential_id == "_legacy" or _CREDENTIAL.fullmatch(self.credential_id) is not None))
        state = "legacy" if self.grant.kind == "legacy" else (
            "unbound" if self.actor.kind == "unattributed" else "owned_" + self.actor.kind)
        if self.end_user is not None:
            _require(self.actor.kind == "service" and self.grant.kind in {"key_policy", "configured_scope"})
            state = "verified_forwarded"
        _require(self.attribution_state == state)
        if self.grant.kind == "legacy":
            _require(self.credential_id == "_legacy" and self.actor.kind == "unattributed")
        else:
            _require(self.credential_id != "_legacy")
        if self.grant.kind == "key_policy":
            _require(self.credential_id == self.grant.reference and self.actor.generation is None)
            _require(self.actor.kind == "unattributed" or self.actor.binding_revision is not None)
        if self.grant.kind == "connect":
            _require(self.actor == Actor("human", self.grant.owner, self.grant.approval_revision, self.grant.generation, self.grant.epoch))
        if self.grant.kind == "configured_scope":
            _require(self.credential_id == self.grant.client_id and self.actor == Actor("service", self.grant.client_id))
        _require(len(_canonical(self.to_dict())) <= 16384)

    def to_dict(self):
        return {"schema": self.schema, "credential_id": self.credential_id, "actor": self.actor.to_dict(),
                "end_user": self.end_user.to_dict() if self.end_user is not None else None,
                "grant": self.grant.to_dict(), "attribution_state": self.attribution_state}

    def to_json(self):
        return _canonical(self.to_dict()).decode("utf-8")

    @classmethod
    def from_dict(cls, value):
        value = _closed(cls, value)
        value["actor"] = Actor.from_dict(value["actor"])
        value["grant"] = EffectiveGrant.from_dict(value["grant"])
        if value["end_user"] is not None:
            value["end_user"] = EndUser.from_dict(value["end_user"])
        return cls(**value)

    @classmethod
    def from_json(cls, raw):
        _require(type(raw) is str)
        try:
            data = raw.encode("utf-8")
            _require(len(data) <= 16384)
            return cls.from_dict(strict_json(data))
        except (ValueError, TypeError, UnicodeError, RecursionError):
            raise IdentityError() from None


@dataclass(frozen=True, slots=True)
class AdmissionDecision:
    retry_after: int
    caller_snapshot: CallerSnapshot | None

    def __post_init__(self):
        _require(type(self.retry_after) is int and self.retry_after >= 0)
        _require(self.caller_snapshot is None if self.retry_after else type(self.caller_snapshot) is CallerSnapshot)


def configured_scope_caller(decision: AuthorizationDecision) -> CallerSnapshot:
    _require(type(decision) is AuthorizationDecision and decision.allowed is True and decision.code == "authorized")
    _require(type(decision.scopes) is frozenset and all(type(s) is str and s in ALLOWED_SCOPES for s in decision.scopes))
    scopes = tuple(sorted(decision.scopes))
    grant = EffectiveGrant("configured_scope", reference=decision.client_id, client_id=decision.client_id,
                           scopes=scopes, policy_digest=scope_digest(decision.client_id, scopes))
    return CallerSnapshot(decision.client_id, Actor("service", decision.client_id), grant, "owned_service")


def legacy_caller() -> CallerSnapshot:
    return CallerSnapshot("_legacy", Actor("unattributed"), EffectiveGrant("legacy"), "legacy")
