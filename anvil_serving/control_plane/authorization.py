"""Fail-closed scoped authorization for new operator-only surfaces.

This module intentionally does not participate in legacy controller or router
authentication.  Callers must opt into this policy for a newly introduced
surface and must supply the required scope explicitly.
"""

from __future__ import annotations

from dataclasses import dataclass
import hmac
import json
import os
from pathlib import Path
import re
from typing import Callable, Mapping
import unicodedata
import weakref

# Reuse the repository's hardened bounded-reader and Windows handle-identity
# precedent rather than implementing a second platform-specific file reader.
from ..operator_config import _read_bounded, _windows_file_identity


POLICY_SCHEMA_VERSION = 1
MAX_POLICY_BYTES = 64 * 1024
MAX_CLIENTS = 32
MIN_CREDENTIAL_BYTES = 16
MAX_CREDENTIAL_BYTES = 4096
MAX_CREDENTIAL_SOURCE_BYTES = 64 * 1024

WORKLOADS_READ = "workloads:read"
NODE_ADMIN_BOOTSTRAP = "node-admin:bootstrap"
ALLOWED_SCOPES = frozenset((WORKLOADS_READ, NODE_ADMIN_BOOTSTRAP))

_CLIENT_ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
_ENV_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,255}$")
_POLICY_KEYS = frozenset(("schema_version", "clients"))
_CLIENT_KEYS = frozenset(("id", "scopes", "credential_env", "credential_file"))

_MESSAGES = {
    "authorization_policy_missing": "scoped authorization policy is unavailable",
    "authorization_policy_malformed": "scoped authorization policy is malformed",
    "authorization_credential_malformed": "scoped authorization credential is malformed",
    "authorization_scope_unknown": "requested authorization scope is unknown",
    "authorization_scope_denied": "scoped authorization is denied",
}

CredentialReader = Callable[[str, str], str | bytes | None]


class AuthorizationError(ValueError):
    """A bounded failure suitable for a fixed deny-new-surfaces response."""

    def __init__(self, code: str) -> None:
        if code not in _MESSAGES:
            code = "authorization_policy_malformed"
        self.code = code
        super().__init__(_MESSAGES[code])

    def __repr__(self) -> str:
        return f"AuthorizationError(code={self.code!r})"


class _Credential:
    """Secret holder deliberately outside public dataclass projections."""

    __slots__ = ("__material",)

    def __init__(self, material: bytes) -> None:
        self.__material = material

    def matches(self, candidate: bytes) -> bool:
        return hmac.compare_digest(self.__material, candidate)

    def __repr__(self) -> str:
        return "_Credential(<redacted>)"


@dataclass(frozen=True, slots=True)
class AuthorizationPrincipal:
    """A safe principal projection containing no credential material."""

    client_id: str
    scopes: frozenset[str]


@dataclass(frozen=True, eq=False, slots=True, weakref_slot=True)
class AuthorizationPolicy:
    """Immutable, local-only scoped authorization policy.

    Credential matchers remain process-local in a private weak mapping. A
    copied, reconstructed, or deserialized policy deliberately loses authority
    and ``check_scope`` fails it closed; this core provides no hot reload or
    cross-process policy transfer mechanism.
    """

    principals: tuple[AuthorizationPrincipal, ...]


@dataclass(frozen=True, slots=True)
class AuthorizationDecision:
    """Allowlisted decision data safe for endpoint or CLI adapters."""

    allowed: bool
    code: str
    client_id: str | None = None
    scopes: frozenset[str] = frozenset()


_POLICY_CREDENTIALS: weakref.WeakKeyDictionary[AuthorizationPolicy, tuple[_Credential, ...]] = (
    weakref.WeakKeyDictionary()
)


def _reject_duplicate_object(pairs: list[tuple[object, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if not isinstance(key, str) or key in result:
            raise AuthorizationError("authorization_policy_malformed")
        result[key] = value
    return result


def _validate_local_path(path: Path, error_code: str) -> Path:
    try:
        rendered = str(path)
        if not rendered or not path.is_absolute() or any(
            unicodedata.category(character).startswith("C") for character in rendered
        ):
            raise AuthorizationError(error_code)
        if rendered.startswith(("//", "\\\\", "\\\\?\\", "\\\\.\\", "\\??\\")):
            raise AuthorizationError(error_code)
        return path
    except AuthorizationError:
        raise
    except Exception:
        raise AuthorizationError(error_code) from None


def _read_bounded_regular_file(path: Path, maximum: int, error_code: str) -> bytes:
    """Read one validated local regular file via the operator-config guard."""

    safe_path = _validate_local_path(path, error_code)
    try:
        expected_windows_identity = _windows_file_identity(safe_path) if os.name == "nt" else None
        return _read_bounded(
            safe_path,
            max_bytes=maximum,
            expected_windows_identity=expected_windows_identity,
        )
    except Exception:
        raise AuthorizationError(error_code) from None


def _normalize_credential(value: str | bytes | None, *, scoped: bool = True) -> bytes:
    if value is None:
        raise AuthorizationError("authorization_credential_malformed")
    if isinstance(value, bytes):
        if len(value) > MAX_CREDENTIAL_SOURCE_BYTES:
            raise AuthorizationError("authorization_credential_malformed")
        try:
            text = value.decode("utf-8")
        except UnicodeDecodeError:
            raise AuthorizationError("authorization_credential_malformed") from None
    elif isinstance(value, str):
        if len(value) > MAX_CREDENTIAL_SOURCE_BYTES:
            raise AuthorizationError("authorization_credential_malformed")
        try:
            source_bytes = value.encode("utf-8")
        except UnicodeEncodeError:
            raise AuthorizationError("authorization_credential_malformed") from None
        if len(source_bytes) > MAX_CREDENTIAL_SOURCE_BYTES:
            raise AuthorizationError("authorization_credential_malformed")
        text = value
    else:
        raise AuthorizationError("authorization_credential_malformed")
    normalized = text.strip()
    if not normalized:
        raise AuthorizationError("authorization_credential_malformed")
    if any(unicodedata.category(character).startswith("C") for character in normalized):
        raise AuthorizationError("authorization_credential_malformed")
    try:
        material = normalized.encode("utf-8")
    except UnicodeEncodeError:
        raise AuthorizationError("authorization_credential_malformed") from None
    minimum = MIN_CREDENTIAL_BYTES if scoped else 1
    maximum = MAX_CREDENTIAL_BYTES if scoped else MAX_CREDENTIAL_SOURCE_BYTES
    if not minimum <= len(material) <= maximum:
        raise AuthorizationError("authorization_credential_malformed")
    return material


def _validate_file_reference(reference: object) -> str:
    if not isinstance(reference, str) or not reference or len(reference) > 512:
        raise AuthorizationError("authorization_policy_malformed")
    if (
        reference != reference.strip()
        or "://" in reference
        or any(unicodedata.category(character).startswith("C") for character in reference)
    ):
        raise AuthorizationError("authorization_policy_malformed")
    try:
        _validate_local_path(Path(reference), "authorization_policy_malformed")
    except Exception:
        raise AuthorizationError("authorization_policy_malformed") from None
    return reference


def _resolve_credential(
    kind: str,
    reference: str,
    *,
    env: Mapping[str, str] | None,
    secret_reader: CredentialReader | None,
) -> bytes:
    if secret_reader is not None:
        try:
            value = secret_reader(kind, reference)
        except Exception:
            raise AuthorizationError("authorization_credential_malformed") from None
        return _normalize_credential(value)
    if kind == "credential_env":
        try:
            value = None if env is None else env.get(reference)
        except Exception:
            raise AuthorizationError("authorization_credential_malformed") from None
        return _normalize_credential(value)
    return _normalize_credential(
        _read_bounded_regular_file(
            Path(reference), MAX_CREDENTIAL_SOURCE_BYTES, "authorization_credential_malformed"
        )
    )


def _parse_policy(raw: bytes) -> dict[str, object]:
    if len(raw) > MAX_POLICY_BYTES:
        raise AuthorizationError("authorization_policy_malformed")
    try:
        text = raw.decode("utf-8")
        parsed = json.loads(text, object_pairs_hook=_reject_duplicate_object)
    except (UnicodeDecodeError, ValueError, RecursionError, TypeError, AuthorizationError):
        raise AuthorizationError("authorization_policy_malformed") from None
    if not isinstance(parsed, dict) or set(parsed) != _POLICY_KEYS:
        raise AuthorizationError("authorization_policy_malformed")
    if type(parsed["schema_version"]) is not int or parsed["schema_version"] != POLICY_SCHEMA_VERSION:
        raise AuthorizationError("authorization_policy_malformed")
    if not isinstance(parsed["clients"], list) or len(parsed["clients"]) > MAX_CLIENTS:
        raise AuthorizationError("authorization_policy_malformed")
    return parsed


def load_authorization_policy(
    path: str | Path | None,
    *,
    env: Mapping[str, str] | None = None,
    secret_reader: CredentialReader | None = None,
    legacy_token: str | bytes | None = None,
) -> AuthorizationPolicy:
    """Load a closed scoped policy, resolving only its explicit references.

    ``env`` is deliberately not defaulted to process state.  A caller that
    wants an environment-backed policy must inject the intended mapping.
    """

    if path is None:
        raise AuthorizationError("authorization_policy_missing")
    try:
        policy_path = Path(path)
    except Exception:
        raise AuthorizationError("authorization_policy_malformed") from None
    raw = _read_bounded_regular_file(policy_path, MAX_POLICY_BYTES, "authorization_policy_malformed")
    parsed = _parse_policy(raw)
    try:
        normalized_legacy = None if legacy_token is None else _normalize_credential(legacy_token, scoped=False)
    except AuthorizationError:
        raise AuthorizationError("authorization_policy_malformed") from None

    principals: list[AuthorizationPrincipal] = []
    credentials: list[_Credential] = []
    client_ids: set[str] = set()
    references: set[str] = set()
    duplicate_material = False
    legacy_match = False
    for item in parsed["clients"]:
        if not isinstance(item, dict) or set(item) - _CLIENT_KEYS:
            raise AuthorizationError("authorization_policy_malformed")
        required = {"id", "scopes"}
        credential_keys = set(item) & {"credential_env", "credential_file"}
        if set(item) != required | credential_keys or len(credential_keys) != 1:
            raise AuthorizationError("authorization_policy_malformed")
        client_id = item["id"]
        scopes = item["scopes"]
        if not isinstance(client_id, str) or not _CLIENT_ID_RE.fullmatch(client_id):
            raise AuthorizationError("authorization_policy_malformed")
        if client_id in client_ids:
            raise AuthorizationError("authorization_policy_malformed")
        if not isinstance(scopes, list) or not scopes:
            raise AuthorizationError("authorization_policy_malformed")
        if any(not isinstance(scope, str) or scope not in ALLOWED_SCOPES for scope in scopes):
            raise AuthorizationError("authorization_policy_malformed")
        scope_set = frozenset(scopes)
        if len(scope_set) != len(scopes):
            raise AuthorizationError("authorization_policy_malformed")
        kind = next(iter(credential_keys))
        reference = item[kind]
        if kind == "credential_env":
            if not isinstance(reference, str) or not _ENV_NAME_RE.fullmatch(reference):
                raise AuthorizationError("authorization_policy_malformed")
        else:
            reference = _validate_file_reference(reference)
        if reference in references:
            raise AuthorizationError("authorization_policy_malformed")
        credential = _resolve_credential(kind, reference, env=env, secret_reader=secret_reader)
        for stored_credential in credentials:
            duplicate_material = stored_credential.matches(credential) or duplicate_material
        if normalized_legacy is not None:
            legacy_match = hmac.compare_digest(credential, normalized_legacy) or legacy_match
        principals.append(AuthorizationPrincipal(client_id, scope_set))
        credentials.append(_Credential(credential))
        client_ids.add(client_id)
        references.add(reference)
    if duplicate_material or legacy_match:
        raise AuthorizationError("authorization_policy_malformed")
    policy = AuthorizationPolicy(tuple(principals))
    _POLICY_CREDENTIALS[policy] = tuple(credentials)
    return policy


def check_scope(
    policy: AuthorizationPolicy | None,
    presented_token: str | bytes | None,
    required_scope: str,
) -> AuthorizationDecision:
    """Check exactly one scope without granting any legacy operation access."""

    if not isinstance(required_scope, str) or required_scope not in ALLOWED_SCOPES:
        return AuthorizationDecision(False, "authorization_scope_unknown")
    if policy is None:
        return AuthorizationDecision(False, "authorization_policy_missing")
    if not isinstance(policy, AuthorizationPolicy):
        return AuthorizationDecision(False, "authorization_policy_malformed")
    try:
        credential = _normalize_credential(presented_token)
    except AuthorizationError:
        return AuthorizationDecision(False, "authorization_credential_malformed")

    credentials = _POLICY_CREDENTIALS.get(policy)
    if credentials is None or len(credentials) != len(policy.principals):
        return AuthorizationDecision(False, "authorization_policy_malformed")
    matched: AuthorizationPrincipal | None = None
    for principal, stored_credential in zip(policy.principals, credentials):
        is_match = stored_credential.matches(credential)
        if is_match:
            matched = principal
    if matched is None or required_scope not in matched.scopes:
        return AuthorizationDecision(False, "authorization_scope_denied")
    return AuthorizationDecision(True, "authorized", matched.client_id, matched.scopes)
