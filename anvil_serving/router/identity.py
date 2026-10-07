"""Closed immutable caller metadata; existing credentials retain all authority."""
from __future__ import annotations

from dataclasses import asdict, dataclass, fields
import hashlib
import json
import re

from ..control_plane.authorization import ALLOWED_SCOPES, AuthorizationDecision
from ..observability.dashboard.contracts import strict_json
from .keys import KeyStoreError

_OPAQUE = re.compile(r"[A-Za-z0-9_:.\-]{1,128}\Z")
_CREDENTIAL = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,63}\Z")
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
SCHEMA = "router-usage/v1"


class IdentityError(KeyStoreError):
    def __init__(self):
        super().__init__("invalid caller policy metadata")


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
    end_user: None = None  # T004 adds the verified native EndUser projection.

    def __post_init__(self):
        _require(self.schema == SCHEMA and self.end_user is None)
        _require(type(self.actor) is Actor and type(self.grant) is EffectiveGrant)
        _require(type(self.credential_id) is str and (self.credential_id == "_legacy" or _CREDENTIAL.fullmatch(self.credential_id) is not None))
        state = "legacy" if self.grant.kind == "legacy" else (
            "unbound" if self.actor.kind == "unattributed" else "owned_" + self.actor.kind)
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
                "end_user": None, "grant": self.grant.to_dict(), "attribution_state": self.attribution_state}

    def to_json(self):
        return _canonical(self.to_dict()).decode("utf-8")

    @classmethod
    def from_dict(cls, value):
        value = _closed(cls, value)
        value["actor"] = Actor.from_dict(value["actor"])
        value["grant"] = EffectiveGrant.from_dict(value["grant"])
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
