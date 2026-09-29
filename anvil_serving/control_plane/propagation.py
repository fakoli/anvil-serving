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
MAX_PAGE_ITEMS = 8
MAX_RECEIPT_AGE = timedelta(minutes=5)
MAX_DEADLINE = timedelta(days=7)
_UTC = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$")
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_CONTRACT_FIELDS = frozenset(("schema", "authority_mode", "scope", "revision", "generation", "approval_ref", "approval_digest", "activation_ref", "activation_digest", "inputs", "effect_set_digest", "targets", "execution_profile_ref", "execution_profile_digest", "session_policy", "preview_policy", "issued_at", "deadline_at"))
_INPUT_FIELDS = frozenset(("catalog_digest", "monitoring_inventory_digest", "execution_profile_digest", "artifact_digest", "installation_inventory_digest"))
_TARGET_FIELDS = frozenset(("target_id", "installation_id", "profile_id", "runtime_id", "resource_keys", "expected_identity_ref", "expected_identity_digest", "checks", "effects"))
_SESSION_FIELDS = frozenset(("preserve_active_conversations", "loaded_state_required", "idle_reload"))
_PREVIEW_FIELDS = frozenset(("all_required_targets", "web_runtime_required", "monitoring_required"))
_EFFECT_SCOPE_FIELDS = (
    "schema", "scope", "revision", "generation", "activation_ref", "activation_digest", "inputs", "targets",
    "execution_profile_ref", "execution_profile_digest", "session_policy", "preview_policy",
)
_RECEIPT_FIELDS = frozenset(("schema", "contract_digest", "revision", "generation", "job_id", "effect_id", "target_id", "installation_id", "profile_id", "runtime_id", "before_digest", "after_digest", "applied", "verified", "observed_at", "check_set_digest", "outcome", "failure_code", "evidence_ref", "evidence_digest", "issuer"))
_OUTCOMES = frozenset(("pending", "success", "failed", "uncertain", "unsupported"))
_FAILURES = frozenset(("verification-failed", "transport-unavailable", "receipt-rejected", "session-acceptance-pending", "unsupported-capability"))
_EFFECTS = frozenset(("catalog-apply", "monitoring-apply", "session-idle-reload"))

ADMISSION_SCOPE = "propagation:admission"
DISPATCH_SCOPE = "propagation:dispatch"
ACTIVITY_SCOPE = "propagation:activity"
STATUS_SCOPE = "propagation:status"
RECOVERY_SCOPE = "propagation:recovery"
PREVIEW_MODE_OPERATIONS = frozenset({
    "propagation.profile.v1",
    "propagation.preview.v1",
    "propagation.recovery.verify.v1",
})


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
    target_id: str
    check_set_digest: str
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
    except (TypeError, ValueError, UnicodeError, RecursionError):
        _refuse()


def effect_scope_digest(value: Mapping[str, Any]) -> str:
    """Digest only stable approved authority, never refreshable observations.

    The full contract digest still binds the execution window.  This projection
    deliberately omits execution-window timestamps and approval identity.  The
    approval artifact binds this digest, so including its own identity would
    create a circular dependency.  A fresh observation cannot expand approved
    resources, effects, pins, or session policy.
    """

    try:
        projection = {field: value[field] for field in _EFFECT_SCOPE_FIELDS}
    except (KeyError, TypeError):
        _refuse()
    return hashlib.sha256(_canonical(projection)).hexdigest()


def _load(raw: bytes | str | Mapping[str, Any], maximum: int) -> dict[str, Any]:
    if isinstance(raw, Mapping):
        try:
            value = json.loads(_canonical(dict(raw)).decode("ascii"), object_pairs_hook=_duplicate, parse_float=_float)
        except (TypeError, ValueError, RecursionError, PropagationContractError):
            _refuse()
    else:
        if isinstance(raw, str):
            try:
                raw = raw.encode("utf-8")
            except UnicodeError:
                _refuse()
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
    if type(value) is not str or _UTC.fullmatch(value) is None:
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
    if (value["schema"] != SCHEMA or type(value["authority_mode"]) is not str
            or value["authority_mode"] not in {"preview", "effects"}
            or type(value["generation"]) is not int or isinstance(value["generation"], bool)
            or value["generation"] <= 0):
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
    target_ids = [target["target_id"] for target in targets]
    if (identities != sorted(identities) or len(set(identities)) != len(identities)
            or len(set(target_ids)) != len(target_ids)):
        _refuse("malformed_identity")
    for policy, fields in ((value["session_policy"], _SESSION_FIELDS), (value["preview_policy"], _PREVIEW_FIELDS)):
        _exact(policy, fields)
        if any(type(item) is not bool for item in policy.values()):
            _refuse()
    if not value["session_policy"]["preserve_active_conversations"] or not value["session_policy"]["loaded_state_required"] or not all(value["preview_policy"].values()):
        _refuse()
    if value["effect_set_digest"] != effect_scope_digest(value):
        _refuse("effect_scope_mismatch")
    issued, deadline = _utc(value["issued_at"]), _utc(value["deadline_at"])
    if deadline <= issued or deadline - issued > MAX_DEADLINE:
        _refuse()


def parse_contract(raw: bytes | str | Mapping[str, Any]) -> PropagationContract:
    value = _load(raw, MAX_CONTRACT_BYTES)
    _validate_contract(value)
    canonical = _canonical(value)
    return PropagationContract(canonical, hashlib.sha256(canonical).hexdigest())


def admit_contract(raw: bytes | str | Mapping[str, Any], approval_lookup: Callable[[str], ApprovedAuthority | None], active_identity: ActiveIdentity, now: datetime) -> PropagationContract:
    contract = parse_contract(raw)
    value = contract.value
    if value["authority_mode"] != "effects":
        _refuse("execution_not_authorized")
    issued, deadline = _utc(value["issued_at"]), _utc(value["deadline_at"])
    if not isinstance(now, datetime) or now.tzinfo != timezone.utc or now.utcoffset() != timedelta(0):
        _refuse("malformed_payload")
    if issued > now or now >= deadline:
        _refuse("approval_expired")
    if not value["session_policy"]["idle_reload"] and any("session-idle-reload" in target["effects"] for target in value["targets"]):
        _refuse("policy_mismatch")
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
    if value["schema"] != SCHEMA or type(value["generation"]) is not int or isinstance(value["generation"], bool) or value["generation"] <= 0 or type(value["applied"]) is not bool or type(value["verified"]) is not bool or type(value["outcome"]) is not str or value["outcome"] not in _OUTCOMES:
        _refuse()
    for field in ("revision", "job_id", "effect_id", "target_id", "installation_id", "profile_id", "runtime_id", "evidence_ref", "issuer"):
        _id(value[field])
    for field in ("contract_digest", "before_digest", "after_digest", "check_set_digest", "evidence_digest"):
        _digest(value[field])
    if value["failure_code"] is not None and (type(value["failure_code"]) is not str or value["failure_code"] not in _FAILURES):
        _refuse()
    if (value["outcome"] == "success") != (value["failure_code"] is None) or (value["verified"] and value["outcome"] != "success"):
        _refuse("receipt_outcome_mismatch")
    observed = _utc(value["observed_at"])
    if not isinstance(now, datetime) or now.tzinfo != timezone.utc or now.utcoffset() != timedelta(0):
        _refuse("malformed_payload")
    if observed > now or now - observed > MAX_RECEIPT_AGE:
        _refuse("stale_receipt")
    identity = context.identity
    if (value["contract_digest"], value["revision"], value["generation"], value["job_id"], value["effect_id"], value["target_id"], value["check_set_digest"], value["installation_id"], value["profile_id"], value["runtime_id"]) != (context.contract_digest, context.revision, context.generation, context.job_id, context.effect_id, context.target_id, context.check_set_digest, identity.installation_id, identity.profile_id, identity.runtime_id) or value["issuer"] != _id(authenticated_issuer):
        _refuse("receipt_identity_mismatch")
    return json.loads(_canonical(value).decode("ascii"))


def authorize_receipt_lookup(identity: ReceiptIdentity, caller: ReceiptIdentity) -> None:
    if identity != caller:
        _refuse("receipt_lookup_denied")


_OPERATIONS = (
    ("propagation.accept.v1", ADMISSION_SCOPE, ("approval_ref", "request_id"), ("intent_id", "workflow_id", "contract_digest")),
    ("propagation.profile.v1", STATUS_SCOPE, (), ("profile_id", "profile_digest", "owner_profile_digest", "installed")),
    ("propagation.preview.v1", STATUS_SCOPE, ("cursor",), ("preview_digest", "targets")),
    ("propagation.status.v1", STATUS_SCOPE, ("intent_id", "cursor"), ("targets", "observed_at")),
    ("propagation.resume.v1", ADMISSION_SCOPE, ("intent_id", "expected_digest"), ("attempt_id", "state")),
    ("propagation.cancel.v1", ADMISSION_SCOPE, ("intent_id", "expected_digest"), ("state",)),
    ("propagation.recovery.verify.v1", RECOVERY_SCOPE, ("profile_id",), ("state", "evidence_digest", "verified_at")),
    ("propagation.dispatch.pending.v1", DISPATCH_SCOPE, ("cursor",), ("intents", "next_cursor")),
    ("propagation.dispatch.record.v1", DISPATCH_SCOPE, ("intent_id", "workflow_id", "contract_digest"), ("recorded",)),
    ("fleet.propagation.preview.v1", ACTIVITY_SCOPE, ("intent_id", "cursor"), ("preview_digest", "targets")),
    ("fleet.propagation.submit.v1", ACTIVITY_SCOPE, ("intent_id", "preview_digest", "operation_id"), ("contract_digest", "operation_id", "job_id", "state")),
    ("fleet.propagation.current.v1", ACTIVITY_SCOPE, ("intent_id", "job_id", "contract_digest", "generation", "target_id", "resource_id"), ("current", "observed_at")),
    ("fleet.propagation.status.v1", STATUS_SCOPE, ("job_id", "cursor"), ("outcomes", "receipt_refs")),
    ("fleet.propagation.verify.v1", ACTIVITY_SCOPE, ("intent_id", "job_id", "cursor"), ("checks", "receipts")),
    ("fleet.propagation.convergence.v1", ACTIVITY_SCOPE, ("intent_id", "verification_id", "cursor"), ("changed", "reloads", "checks")),
    ("fleet.propagation.cancel.v1", ACTIVITY_SCOPE, ("job_id",), ("state",)),
)


def _object(properties: dict[str, Any], required: tuple[str, ...] | list[str] | None = None) -> dict[str, Any]:
    """Produce one closed, bounded JSON-schema object declaration."""

    return {
        "type": "object",
        "additionalProperties": False,
        "maxProperties": len(properties),
        "properties": properties,
        "required": list(properties) if required is None else list(required),
    }


def _identifier() -> dict[str, Any]:
    return {"type": "string", "minLength": 1, "maxLength": 128, "pattern": _ID.pattern}


def _digest_schema() -> dict[str, Any]:
    return {"type": "string", "minLength": 64, "maxLength": 64, "pattern": _SHA256.pattern}


def _nullable(schema: dict[str, Any]) -> dict[str, Any]:
    result = {**schema, "type": [schema["type"], "null"]}
    if "enum" in schema:
        result["enum"] = [*schema["enum"], None]
    return result


def _timestamp() -> dict[str, Any]:
    return {"type": "string", "minLength": 20, "maxLength": 20, "pattern": _UTC.pattern}


def _pending_reason() -> dict[str, Any]:
    return _nullable({"type": "string", "enum": sorted(_FAILURES | {
        "not-started", "offline", "job-running", "preview-pending",
        "cancellation-pending", "recovery-required", "deadline-expired",
        "stale-revision", "identity-mismatch", "drift-detected",
    })})


def _session_state() -> dict[str, Any]:
    return _object({name: {"type": "string", "enum": [
        "pending", "accepted", "unsupported", "not-required",
    ]} for name in ("files", "new_session", "existing_session")})


def _cursor() -> dict[str, Any]:
    return {**_nullable(_identifier()), "description": (
        "Null starts a read or marks the final response page. Non-null cursors "
        "are owner-issued and bind the operation, caller scope and immutable "
        "snapshot. Continuation only reads that snapshot; it never starts "
        "another verification or convergence pass. Expired snapshots refuse."
    )}


def _array(item: dict[str, Any], maximum: int) -> dict[str, Any]:
    return {"type": "array", "maxItems": maximum, "items": item}


def _target_row() -> dict[str, Any]:
    return _object({
        "target_id": _identifier(),
        "installation_id": _identifier(),
        "profile_id": _identifier(),
        "runtime_id": _identifier(),
    })


def _receipt_ref() -> dict[str, Any]:
    return _object({
        "receipt_id": _identifier(),
        "target_id": _identifier(),
        "effect_id": _identifier(),
        "issuer": _identifier(),
        "receipt_digest": _digest_schema(),
    })


def _outcome_row() -> dict[str, Any]:
    return _object({
        **_target_row()["properties"],
        "check_set_digest": _digest_schema(),
        "outcome": {"type": "string", "enum": sorted(_OUTCOMES)},
        "applied": {"type": "boolean"},
        "verified": {"type": "boolean"},
        "desired_revision": _identifier(),
        "applied_revision": _nullable(_identifier()),
        "verified_revision": _nullable(_identifier()),
        "last_contact_at": _nullable(_timestamp()),
        "observed_at": _nullable(_timestamp()),
        "age_seconds": {"type": ["integer", "null"], "minimum": 0},
        "freshness": {"type": "string", "enum": ["fresh", "stale", "unknown"]},
        "pending_reason": _pending_reason(),
        "session_state": _session_state(),
        "metric_coverage": {"type": "string", "enum": ["pending", "supported", "partial", "unsupported"]},
        "receipt_ref": _nullable(_receipt_ref()),
    })


def _check_row() -> dict[str, Any]:
    return _object({
        "target_id": _identifier(),
        "check_id": _identifier(),
        "outcome": {"type": "string", "enum": sorted(_OUTCOMES)},
        "pending_reason": _pending_reason(),
        "observed_at": _nullable(_timestamp()),
        "evidence_ref": _nullable(_identifier()),
        "evidence_digest": _nullable(_digest_schema()),
    })


def _preview_row() -> dict[str, Any]:
    return _object({
        **_target_row()["properties"],
        "ready": {"type": "boolean"},
        "pending_reason": _pending_reason(),
        "observed_at": _nullable(_timestamp()),
        "observed_digest": _nullable(_digest_schema()),
        "permitted_effects": _array({"type": "string", "enum": sorted(_EFFECTS)}, len(_EFFECTS)),
    })


def _page(properties: dict[str, Any]) -> dict[str, Any]:
    result = _object({
        "contract_digest": _digest_schema(),
        "target_set_digest": _digest_schema(),
        "target_count": {"type": "integer", "minimum": 1, "maximum": MAX_TARGETS},
        "snapshot_id": _identifier(),
        "observed_at": _timestamp(),
        "total_items": {"type": "integer", "minimum": 0},
        "next_cursor": _cursor(),
        **properties,
    })
    result["description"] = (
        "All pages retain the same contract, complete target denominator, "
        "snapshot, observation time and total item count. Rows are ordered by "
        "target and then check/effect identity; total_items counts all row "
        "collections together. The owner bounds serialized bytes as well as "
        "row counts. Read through next_cursor=null and reject mixed, missing "
        "or duplicate rows before evaluating whole-fleet completion. A partial "
        "page or an empty receipt collection never removes a required target."
    )
    return result


def _intent_row() -> dict[str, Any]:
    return _object({
        "intent_id": _identifier(),
        "workflow_id": _identifier(),
        "contract_digest": _digest_schema(),
        "target_set_digest": _digest_schema(),
        "effect_set_digest": _digest_schema(),
        "issued_at": _timestamp(),
        "deadline_at": _timestamp(),
        "targets": _array(_object({
            "target_id": _identifier(),
            "check_set_digest": _digest_schema(),
        }), MAX_TARGETS),
    })


def _input_schema(name: str, fields: tuple[str, ...]) -> dict[str, Any]:
    properties: dict[str, Any] = {
        field: _cursor() if field == "cursor" else _digest_schema() if field.endswith("digest") else {"type": "integer", "minimum": 1} if field == "generation" else _identifier()
        for field in fields
    }
    return _object(properties)


def _result_schema(name: str) -> dict[str, Any]:
    if name == "propagation.accept.v1":
        return _object({"intent_id": _identifier(), "workflow_id": _identifier(), "contract_digest": _digest_schema()})
    if name == "propagation.profile.v1":
        return _object({"profile_id": _identifier(), "profile_digest": _digest_schema(),
                        "owner_profile_digest": _digest_schema(),
                        "mode": {"type": "string", "enum": ["effects", "preview"]},
                        "installed": {"type": "boolean"}, "observed_at": _timestamp()})
    if name == "propagation.status.v1":
        return _page({"intent_id": _identifier(), "workflow_id": _identifier(),
                      "contract_digest": _digest_schema(), "target_set_digest": _digest_schema(),
                      "job_id": _nullable(_identifier()),
                      "workflow_progress": {"type": "string", "enum": [
                          "unavailable", "pending", "running", "completed", "failed",
                          "cancelled", "recovery_required",
                      ]},
                      "workflow_observed_at": _nullable(_timestamp()),
                      "workflow_age_seconds": {"type": ["integer", "null"], "minimum": 0},
                      "targets": _array(_outcome_row(), MAX_PAGE_ITEMS),
                      "state": {"type": "string", "enum": ["pending", "running", "completed", "failed", "cancelled", "recovery_required"]},
                      "all_targets_verified": {"type": "boolean"}})
    if name == "propagation.resume.v1":
        return _object({"attempt_id": _identifier(), "state": {"type": "string", "enum": ["accepted", "refused", "reconciling"]}})
    if name == "propagation.cancel.v1":
        return _object({"state": {"type": "string", "enum": ["requested", "confirmed", "uncertain"]}})
    if name == "propagation.recovery.verify.v1":
        return _object({"profile_id": _identifier(),
                        "state": {"type": "string", "enum": ["passed", "failed", "recovery_required"]},
                        "evidence_digest": _nullable(_digest_schema()),
                        "verified_at": _nullable(_timestamp())})
    if name == "propagation.dispatch.pending.v1":
        return _object({"intents": _array(_intent_row(), 100), "next_cursor": _cursor()})
    if name == "propagation.dispatch.record.v1":
        return _object({"recorded": {"type": "boolean"}})
    if name in {"propagation.preview.v1", "fleet.propagation.preview.v1"}:
        return _page({"preview_digest": _nullable(_digest_schema()),
                      "effect_set_digest": _digest_schema(), "expires_at": _timestamp(),
                      "all_required_ready": {"type": "boolean"},
                      "targets": _array(_preview_row(), MAX_PAGE_ITEMS)})
    if name == "fleet.propagation.submit.v1":
        return _object({"contract_digest": _digest_schema(), "operation_id": _identifier(), "job_id": _identifier(), "state": {"type": "string", "enum": ["created", "existing", "conflict", "refused"]}})
    if name == "fleet.propagation.current.v1":
        return _object({"current": {"type": "boolean"}, "observed_at": _timestamp()})
    if name == "fleet.propagation.status.v1":
        return _page({"job_id": _identifier(),
                      "state": {"type": "string", "enum": ["running", "applied", "failed", "cancelled", "recovery_required"]},
                      "heartbeat_at": _timestamp(), "completed_at": _nullable(_timestamp()),
                      "outcomes": _array(_outcome_row(), MAX_PAGE_ITEMS),
                      "receipt_refs": _array(_receipt_ref(), MAX_PAGE_ITEMS)})
    if name == "fleet.propagation.verify.v1":
        return _page({"job_id": _identifier(), "verification_id": _identifier(),
                      "kind": {"type": "string", "enum": ["verify"]},
                      "changed": {"type": "integer", "minimum": 0},
                      "reloads": {"type": "integer", "minimum": 0},
                      "outcomes": _array(_outcome_row(), MAX_PAGE_ITEMS),
                      "checks": _array(_check_row(), MAX_PAGE_ITEMS),
                      "receipts": _array(_receipt_ref(), MAX_PAGE_ITEMS),
                      "all_targets_verified": {"type": "boolean"}})
    if name == "fleet.propagation.convergence.v1":
        return _page({"job_id": _identifier(), "verification_id": _identifier(),
                      "kind": {"type": "string", "enum": ["convergence"]},
                      "outcomes": _array(_outcome_row(), MAX_PAGE_ITEMS),
                      "receipts": _array(_receipt_ref(), MAX_PAGE_ITEMS),
                      "changed": {"type": "integer", "minimum": 0},
                      "reloads": {"type": "integer", "minimum": 0},
                      "all_targets_verified": {"type": "boolean"},
                      "checks": _array(_check_row(), MAX_PAGE_ITEMS)})
    if name == "fleet.propagation.cancel.v1":
        return _object({"job_id": _identifier(), "state": {"type": "string", "enum": ["requested", "confirmed", "uncertain"]}})
    raise AssertionError("unknown propagation operation")


def capability_declaration() -> dict[str, Any]:
    return {"schema": CAPABILITY_SCHEMA, "operations": [
        {
            "name": name,
            "required_scope": scope,
            "input_schema": _input_schema(name, fields),
            "result_schema": _result_schema(name),
            "max_input_bytes": MAX_RECEIPT_BYTES,
            "max_result_bytes": MAX_RECEIPT_BYTES,
            "available": False,
        }
        for name, scope, fields, _result in _OPERATIONS
    ]}
