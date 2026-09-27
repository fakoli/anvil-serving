"""Strict, inert value contracts for approved propagation v1 work."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
import re
from typing import Any, Callable, Mapping

SCHEMA = "anvil-propagation/v1"
CAPABILITY_SCHEMA = "anvil-propagation-capabilities/v1"
MAX_CONTRACT_BYTES = 256 * 1024
MAX_RECEIPT_BYTES = 64 * 1024
MAX_TARGETS = 128
MAX_RECEIPT_AGE = timedelta(minutes=5)
MAX_DEADLINE = timedelta(days=7)
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_CONTRACT_FIELDS = frozenset(("schema", "scope", "revision", "generation", "approval_ref", "approval_digest", "activation_ref", "activation_digest", "inputs", "effect_set_digest", "targets", "execution_profile_ref", "execution_profile_digest", "session_policy", "preview_policy", "issued_at", "deadline_at"))
_INPUT_FIELDS = frozenset(("catalog_digest", "monitoring_inventory_digest", "execution_profile_digest", "artifact_digest", "installation_inventory_digest"))
_TARGET_FIELDS = frozenset(("target_id", "installation_id", "profile_id", "runtime_id", "resource_keys", "expected_identity_ref", "expected_identity_digest", "checks", "effects"))
_SESSION_FIELDS = frozenset(("preserve_active_conversations", "loaded_state_required", "idle_reload"))
_PREVIEW_FIELDS = frozenset(("all_required_targets", "web_runtime_required", "monitoring_required"))
_RECEIPT_FIELDS = frozenset(("schema", "contract_digest", "revision", "generation", "job_id", "effect_id", "target_id", "installation_id", "profile_id", "runtime_id", "before_digest", "after_digest", "applied", "verified", "observed_at", "check_set_digest", "outcome", "failure_code", "evidence_ref", "evidence_digest", "issuer"))
_OUTCOMES = frozenset(("pending", "success", "failed", "uncertain", "unsupported"))
_EFFECTS = frozenset(("catalog-apply", "monitoring-apply", "session-idle-reload"))

ADMISSION_SCOPE = "propagation:admission"
DISPATCH_SCOPE = "propagation:dispatch"
ACTIVITY_SCOPE = "propagation:activity"
STATUS_SCOPE = "propagation:status"


class PropagationContractError(ValueError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class ActiveIdentity:
    activation_ref: str
    activation_digest: str


@dataclass(frozen=True, slots=True)
class ApprovedAuthority:
    approval_ref: str
    approval_digest: str
    contract_digest: str


@dataclass(frozen=True, slots=True)
class ReceiptIdentity:
    installation_id: str
    profile_id: str
    runtime_id: str


@dataclass(frozen=True, slots=True)
class ReceiptContext:
    contract_digest: str
    revision: str
    generation: int
    job_id: str
    effect_id: str
    identity: ReceiptIdentity


@dataclass(frozen=True, slots=True)
class PropagationContract:
    canonical: bytes
    digest: str

    @property
    def value(self) -> dict[str, Any]:
        return json.loads(self.canonical.decode("ascii"))


def _refuse(code: str = "malformed_payload") -> None:
    raise PropagationContractError(code)


def _duplicate(pairs: list[tuple[object, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if type(key) is not str or key in result:
            _refuse()
        result[key] = value
    return result


def _float(_: str) -> object:
    _refuse()


def _canonical(value: Any) -> bytes:
    try:
        return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("ascii")
    except (TypeError, ValueError, UnicodeError):
        _refuse()


def _load(raw: bytes | str | Mapping[str, Any], maximum: int) -> dict[str, Any]:
    if isinstance(raw, Mapping):
        value = json.loads(_canonical(dict(raw)).decode("ascii"), object_pairs_hook=_duplicate, parse_float=_float)
    else:
        if isinstance(raw, str):
            raw = raw.encode("utf-8")
        if not isinstance(raw, bytes):
            _refuse()
        if len(raw) > maximum:
            _refuse("payload_too_large")
        try:
            value = json.loads(raw.decode("utf-8"), object_pairs_hook=_duplicate, parse_float=_float)
        except (UnicodeDecodeError, TypeError, ValueError, RecursionError, PropagationContractError):
            _refuse()
    if type(value) is not dict:
        _refuse()
    encoded = _canonical(value)
    if len(encoded) > maximum:
        _refuse("payload_too_large")
    return value


def _id(value: object) -> str:
    if type(value) is not str or _ID.fullmatch(value) is None:
        _refuse("malformed_identity")
    return value


def _digest(value: object) -> str:
    if type(value) is not str or _SHA256.fullmatch(value) is None:
        _refuse()
    return value


def _exact(value: Any, fields: frozenset[str]) -> dict[str, Any]:
    if type(value) is not dict or frozenset(value) != fields:
        _refuse()
    return value


def _declared_set(value: Any, *, allowed: frozenset[str] | None = None) -> list[str]:
    if type(value) is not list or not value:
        _refuse()
    result = [_id(item) for item in value]
    if result != sorted(set(result)) or (allowed is not None and not set(result) <= allowed):
        _refuse()
    return result


def _utc(value: Any) -> datetime:
    if type(value) is not str or not value.endswith("Z"):
        _refuse()
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        _refuse()
    if parsed.tzinfo != timezone.utc:
        _refuse()
    return parsed


def _validate_contract(value: dict[str, Any]) -> None:
    _exact(value, _CONTRACT_FIELDS)
    if value["schema"] != SCHEMA or type(value["generation"]) is not int or isinstance(value["generation"], bool) or value["generation"] <= 0:
        _refuse()
    for field in ("scope", "revision", "approval_ref", "activation_ref", "execution_profile_ref"):
        _id(value[field])
    for field in ("approval_digest", "activation_digest", "effect_set_digest", "execution_profile_digest"):
        _digest(value[field])
    inputs = _exact(value["inputs"], _INPUT_FIELDS)
    for digest in inputs.values():
        _digest(digest)
    if inputs["execution_profile_digest"] != value["execution_profile_digest"]:
        _refuse()
    targets = value["targets"]
    if type(targets) is not list or not targets or len(targets) > MAX_TARGETS:
        _refuse()
    identities: list[tuple[str, str, str]] = []
    for target in targets:
        _exact(target, _TARGET_FIELDS)
        for field in ("target_id", "installation_id", "profile_id", "runtime_id", "expected_identity_ref"):
            _id(target[field])
        _digest(target["expected_identity_digest"])
        _declared_set(target["resource_keys"])
        _declared_set(target["checks"])
        _declared_set(target["effects"], allowed=_EFFECTS)
        identities.append((target["installation_id"], target["profile_id"], target["runtime_id"]))
    if identities != sorted(identities) or len(set(identities)) != len(identities):
        _refuse("malformed_identity")
    for policy, fields in ((value["session_policy"], _SESSION_FIELDS), (value["preview_policy"], _PREVIEW_FIELDS)):
        _exact(policy, fields)
        if any(type(item) is not bool for item in policy.values()):
            _refuse()
    if not value["session_policy"]["preserve_active_conversations"] or not value["session_policy"]["loaded_state_required"] or not all(value["preview_policy"].values()):
        _refuse()
    issued, deadline = _utc(value["issued_at"]), _utc(value["deadline_at"])
    if deadline <= issued or deadline - issued > MAX_DEADLINE:
        _refuse()


def parse_contract(raw: bytes | str | Mapping[str, Any]) -> PropagationContract:
    value = _load(raw, MAX_CONTRACT_BYTES)
    _validate_contract(value)
    canonical = _canonical(value)
    return PropagationContract(canonical, hashlib.sha256(canonical).hexdigest())


def admit_contract(raw: bytes | str | Mapping[str, Any], approval_lookup: Callable[[str], ApprovedAuthority | None], active_identity: ActiveIdentity) -> PropagationContract:
    contract = parse_contract(raw)
    value = contract.value
    try:
        approved = approval_lookup(value["approval_ref"])
    except Exception:
        approved = None
    if not isinstance(approved, ApprovedAuthority):
        _refuse("approval_not_found")
    if (approved.approval_ref != value["approval_ref"] or approved.approval_digest != value["approval_digest"] or approved.contract_digest != contract.digest or active_identity.activation_ref != value["activation_ref"] or active_identity.activation_digest != value["activation_digest"]):
        _refuse("approval_mismatch")
    return contract


def parse_receipt(raw: bytes | str | Mapping[str, Any], *, context: ReceiptContext, authenticated_issuer: str, now: datetime) -> dict[str, Any]:
    value = _load(raw, MAX_RECEIPT_BYTES)
    _exact(value, _RECEIPT_FIELDS)
    if value["schema"] != SCHEMA or type(value["generation"]) is not int or isinstance(value["generation"], bool) or value["generation"] <= 0 or type(value["applied"]) is not bool or type(value["verified"]) is not bool or value["outcome"] not in _OUTCOMES:
        _refuse()
    for field in ("revision", "job_id", "effect_id", "target_id", "installation_id", "profile_id", "runtime_id", "evidence_ref", "issuer"):
        _id(value[field])
    for field in ("contract_digest", "before_digest", "after_digest", "check_set_digest", "evidence_digest"):
        _digest(value[field])
    if value["failure_code"] is not None:
        _id(value["failure_code"])
    observed = _utc(value["observed_at"])
    if now.tzinfo != timezone.utc or observed > now or now - observed > MAX_RECEIPT_AGE:
        _refuse("stale_receipt")
    identity = context.identity
    if (value["contract_digest"], value["revision"], value["generation"], value["job_id"], value["effect_id"], value["installation_id"], value["profile_id"], value["runtime_id"]) != (context.contract_digest, context.revision, context.generation, context.job_id, context.effect_id, identity.installation_id, identity.profile_id, identity.runtime_id) or value["issuer"] != _id(authenticated_issuer):
        _refuse("receipt_identity_mismatch")
    return json.loads(_canonical(value).decode("ascii"))


def authorize_receipt_lookup(identity: ReceiptIdentity, caller: ReceiptIdentity) -> None:
    if identity != caller:
        _refuse("receipt_lookup_denied")


_OPERATIONS = (
    ("propagation.accept.v1", ADMISSION_SCOPE, ("approval_ref", "request_id"), ("intent_id", "workflow_id", "contract_digest")),
    ("propagation.status.v1", STATUS_SCOPE, ("intent_id",), ("targets", "observed_at")),
    ("propagation.resume.v1", ADMISSION_SCOPE, ("intent_id", "expected_digest"), ("attempt_id", "state")),
    ("propagation.cancel.v1", ADMISSION_SCOPE, ("intent_id", "expected_digest"), ("state",)),
    ("propagation.dispatch.pending.v1", DISPATCH_SCOPE, ("cursor",), ("intents", "next_cursor")),
    ("propagation.dispatch.record.v1", DISPATCH_SCOPE, ("intent_id", "workflow_id"), ("recorded",)),
    ("fleet.propagation.preview.v1", ACTIVITY_SCOPE, ("intent_id",), ("preview_digest", "targets")),
    ("fleet.propagation.submit.v1", ACTIVITY_SCOPE, ("intent_id", "preview_digest", "operation_id"), ("job_id", "state")),
    ("fleet.propagation.status.v1", STATUS_SCOPE, ("job_id",), ("outcomes", "receipt_refs")),
    ("fleet.propagation.verify.v1", ACTIVITY_SCOPE, ("intent_id", "job_id"), ("checks", "receipts")),
    ("fleet.propagation.convergence.v1", ACTIVITY_SCOPE, ("intent_id", "verification_id"), ("changed", "reloads", "checks")),
    ("fleet.propagation.cancel.v1", ACTIVITY_SCOPE, ("job_id",), ("state",)),
)


def capability_declaration() -> dict[str, Any]:
    return {"schema": CAPABILITY_SCHEMA, "operations": [
        {"name": name, "required_scope": scope, "input_fields": fields, "result_fields": result, "max_input_bytes": MAX_RECEIPT_BYTES, "available": False}
        for name, scope, fields, result in _OPERATIONS
    ]}
