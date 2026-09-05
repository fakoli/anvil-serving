"""Pure value and archive contracts for managed fleet bootstrap.

This module deliberately performs no extraction, installation, transport,
topology mutation, operation-id generation, or lifecycle writes.  Later
bootstrap tasks must consume these validated values and recheck filesystem
boundaries immediately before mutation.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import stat
import struct
import unicodedata
import uuid
import zipfile
import zlib
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from enum import Enum
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .targets import ExecutionPlan


MANIFEST_SCHEMA = "anvil-serving.fleet-bootstrap-manifest/v1"
RECEIPT_SCHEMA = "anvil-serving.fleet-bootstrap-receipt/v1"
PLAN_SCHEMA = "anvil-serving.fleet-bootstrap-plan/v1"
CONTROLLER_OPERATION_CATALOG_SCHEMA = (
    "anvil-serving.controller-operation-catalog/v1"
)
RECEIVER_FRAME_SCHEMA = "anvil-serving.fleet-bootstrap-receiver-frame/v1"
MAX_BUNDLE_BYTES = 16 * 1024 * 1024
MAX_MANIFEST_BYTES = 16 * 1024
MAX_SHIM_BYTES = 256 * 1024
MAX_WHEEL_EXPANDED_BYTES = 16 * 1024 * 1024
MAX_WHEEL_ENTRIES = 4096
MAX_ARCHIVE_NAME_BYTES = 1024
MAX_ARCHIVE_COMPONENT_BYTES = 255
MAX_RECEIPT_BYTES = 16 * 1024
MAX_RECEIVER_METADATA_BYTES = 4096
OUTER_BUNDLE_NAMES = ("manifest.json", "runtime.whl", "bootstrap_shim.py")

_DOS_EPOCH = (1980, 1, 1, 0, 0, 0)
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
_NODE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
_VERSION_RE = re.compile(r"^[0-9]{1,9}\.[0-9]{1,9}\.[0-9]{1,9}(?:(?:a|b|rc)[0-9]{1,9})?$")
_PROTOCOL_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
_TIMESTAMP_RE = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{6}Z$"
)
_UUID4_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)
_WINDOWS_DEVICES = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{number}" for number in range(1, 10)}
    | {f"LPT{number}" for number in range(1, 10)}
    | {f"COM{number}" for number in ("¹", "²", "³")}
    | {f"LPT{number}" for number in ("¹", "²", "³")}
)
_WINDOWS_FORBIDDEN_CHARACTERS = frozenset('<>:"|?*')
_FILE_ATTRIBUTE_REPARSE_POINT = 0x400
_MAX_JSON_DEPTH = 16
_MAX_JSON_NODES = 128
_EOCD_SIGNATURE = b"PK\x05\x06"
_CENTRAL_SIGNATURE = b"PK\x01\x02"
_EOCD_BYTES = 22
_CENTRAL_BYTES = 46
_MANIFEST_FIELDS = frozenset(
    {
        "schema",
        "package_version",
        "source_commit",
        "runtime_sha256",
        "shim_sha256",
        "expected_node",
        "platform",
        "install_adapter",
        "supervisor_adapter",
        "install_root_class",
        "controller_protocol_min",
        "controller_protocol_max",
    }
)
_RECEIPT_FIELDS = frozenset(
    {
        "schema",
        "operation_id",
        "host",
        "topology_sha256",
        "plan_sha256",
        "manifest_sha256",
        "bundle_sha256",
        "platform",
        "install_adapter",
        "supervisor_adapter",
        "phase",
        "outcome",
        "created_at",
        "updated_at",
        "acceptance",
        "rollback",
        "error_code",
        "trigger_error_code",
    }
)
_RECEIVER_IDENTITY_FIELDS = frozenset({"schema", "operation", "expected_node"})
_RECEIVER_OPERATION_FIELDS = frozenset(
    {
        "schema",
        "operation",
        "expected_node",
        "operation_id",
        "plan_sha256",
        "target_config_sha256",
    }
)
_RECEIVER_STAGE_FIELDS = frozenset(
    {*_RECEIVER_OPERATION_FIELDS, "bundle_sha256", "bundle_length"}
)


class BootstrapContractError(ValueError):
    """A fixed, input-safe bootstrap contract refusal."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)


def _validate_exact_fields(
    raw: Any, fields: frozenset[str], message: str
) -> dict[str, Any]:
    if type(raw) is not dict or len(raw) != len(fields):
        raise _refuse(BootstrapErrorCode.INVALID_CONTRACT, message)
    keys = tuple(raw)
    if any(type(key) is not str for key in keys) or frozenset(keys) != fields:
        raise _refuse(BootstrapErrorCode.INVALID_CONTRACT, message)
    return raw


class BootstrapPlatform(str, Enum):
    WINDOWS = "windows"
    LINUX = "linux"


class InstallAdapter(str, Enum):
    PYTHON_WHEEL_VENV = "python-wheel-venv"


class SupervisorAdapter(str, Enum):
    WINDOWS_SCHEDULED_TASK = "windows-scheduled-task"
    LINUX_SYSTEMD_USER = "linux-systemd-user"


class InstallRootClass(str, Enum):
    USER = "user"


class ReceiverOperation(str, Enum):
    IDENTITY = "identity"
    STAGE = "stage"
    ACTIVATE = "activate"
    STATUS = "status"
    ROLLBACK = "rollback"


class BootstrapPhase(str, Enum):
    PLANNED = "planned"
    STAGED = "staged"
    VERIFIED = "verified"
    INSTALLED = "installed"
    ACTIVATED = "activated"
    RESTARTED = "restarted"
    ACCEPTED = "accepted"
    ROLLBACK_STARTED = "rollback-started"
    ROLLED_BACK = "rolled-back"
    MANUAL_RECOVERY = "manual-recovery"
    REFUSED = "refused"
    CLEANUP_FAILED = "cleanup-failed"


class BootstrapOutcome(str, Enum):
    PENDING = "pending"
    SUCCESS = "success"
    ERROR = "error"


class AcceptanceStatus(str, Enum):
    NOT_CHECKED = "not-checked"
    ACCEPTED = "accepted"
    REJECTED = "rejected"


class RollbackStatus(str, Enum):
    NOT_REQUIRED = "not-required"
    PENDING = "pending"
    VERIFIED = "verified"
    FAILED = "failed"
    UNAVAILABLE = "unavailable"


class BootstrapErrorCode(str, Enum):
    INVALID_CONTRACT = "invalid-contract"
    UNSUPPORTED_PLATFORM = "unsupported-platform"
    UNSAFE_PATH = "unsafe-path"
    INVALID_BUNDLE = "invalid-bundle"
    DIGEST_MISMATCH = "digest-mismatch"
    TOPOLOGY_DRIFT = "topology-drift"
    AUTHORIZATION_DENIED = "authorization-denied"
    PRECONDITION_FAILED = "precondition-failed"
    TRANSPORT_UNAVAILABLE = "transport-unavailable"
    RECEIVER_MISMATCH = "receiver-mismatch"
    INSTALL_FAILED = "install-failed"
    ACTIVATION_FAILED = "activation-failed"
    RESTART_FAILED = "restart-failed"
    ACCEPTANCE_FAILED = "acceptance-failed"
    ROLLBACK_FAILED = "rollback-failed"
    CLEANUP_FAILED = "cleanup-failed"
    TIMEOUT = "timeout"
    INTERNAL_ERROR = "internal-error"


def _refuse(code: BootstrapErrorCode, message: str) -> BootstrapContractError:
    return BootstrapContractError(code.value, message)


def _enum(enum_type: type[Enum], value: Any, label: str) -> Any:
    if type(value) is not str:
        raise _refuse(BootstrapErrorCode.INVALID_CONTRACT, f"{label} is invalid")
    try:
        return enum_type(value)
    except ValueError:
        raise _refuse(BootstrapErrorCode.INVALID_CONTRACT, f"{label} is invalid") from None


def _required_text(value: Any, pattern: re.Pattern[str], label: str) -> str:
    if type(value) is not str or pattern.fullmatch(value) is None:
        raise _refuse(BootstrapErrorCode.INVALID_CONTRACT, f"{label} is invalid")
    return value


def _optional_text(value: Any, pattern: re.Pattern[str], label: str) -> str | None:
    if value is None:
        return None
    return _required_text(value, pattern, label)


def _protocol(value: Any, label: str) -> str:
    text = _required_text(value, _PROTOCOL_RE, label)
    try:
        parsed = date.fromisoformat(text)
    except ValueError:
        raise _refuse(BootstrapErrorCode.INVALID_CONTRACT, f"{label} is invalid") from None
    if parsed.isoformat() != text:
        raise _refuse(BootstrapErrorCode.INVALID_CONTRACT, f"{label} is invalid")
    return text


def _timestamp(value: Any, label: str) -> datetime:
    text = _required_text(value, _TIMESTAMP_RE, label)
    try:
        parsed = datetime.strptime(text, "%Y-%m-%dT%H:%M:%S.%fZ").replace(
            tzinfo=timezone.utc
        )
    except ValueError:
        raise _refuse(BootstrapErrorCode.INVALID_CONTRACT, f"{label} is invalid") from None
    return parsed


def _operation_id(value: Any) -> str | None:
    if value is None:
        return None
    text = _required_text(value, _UUID4_RE, "operation_id")
    try:
        parsed = uuid.UUID(text)
    except ValueError:
        raise _refuse(BootstrapErrorCode.INVALID_CONTRACT, "operation_id is invalid") from None
    if parsed.version != 4 or str(parsed) != text:
        raise _refuse(BootstrapErrorCode.INVALID_CONTRACT, "operation_id is invalid")
    return text


def _object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if type(key) is not str or key in result:
            raise _refuse(BootstrapErrorCode.INVALID_CONTRACT, "JSON object is invalid")
        result[key] = value
    return result


def _nonfinite(_: str) -> None:
    raise _refuse(BootstrapErrorCode.INVALID_CONTRACT, "JSON number is invalid")


def _json_value(value: Any, *, depth: int = 0, count: list[int] | None = None) -> None:
    if count is None:
        count = [0]
    count[0] += 1
    if depth > _MAX_JSON_DEPTH or count[0] > _MAX_JSON_NODES:
        raise _refuse(BootstrapErrorCode.INVALID_CONTRACT, "JSON value is too complex")
    if value is None or type(value) in {bool, int, str}:
        return
    if type(value) is float:
        if value != value or value in {float("inf"), float("-inf")}:
            raise _refuse(BootstrapErrorCode.INVALID_CONTRACT, "JSON number is invalid")
        return
    if type(value) is list:
        if len(value) > _MAX_JSON_NODES:
            raise _refuse(BootstrapErrorCode.INVALID_CONTRACT, "JSON value is too complex")
        for item in value:
            _json_value(item, depth=depth + 1, count=count)
        return
    if type(value) is dict:
        if len(value) > _MAX_JSON_NODES:
            raise _refuse(BootstrapErrorCode.INVALID_CONTRACT, "JSON value is too complex")
        for key, item in value.items():
            if type(key) is not str:
                raise _refuse(BootstrapErrorCode.INVALID_CONTRACT, "JSON object is invalid")
            _json_value(item, depth=depth + 1, count=count)
        return
    raise _refuse(BootstrapErrorCode.INVALID_CONTRACT, "JSON value has an invalid type")


def canonical_json_bytes(value: Any) -> bytes:
    """Return the one canonical JSON byte representation accepted by bootstrap."""
    _json_value(value)
    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError):
        raise _refuse(BootstrapErrorCode.INVALID_CONTRACT, "JSON value is invalid") from None


def controller_operation_catalog_sha256(operations: tuple[str, ...]) -> str:
    """Return the identity of one validated per-node controller allowlist."""
    if (
        type(operations) is not tuple
        or not 1 <= len(operations) <= 256
        or any(type(operation) is not str for operation in operations)
        or any(_NODE_RE.fullmatch(operation) is None for operation in operations)
        or len(set(operations)) != len(operations)
        or "controller-bootstrap" not in operations
    ):
        raise _refuse(
            BootstrapErrorCode.INVALID_CONTRACT,
            "controller operation catalog is invalid",
        )
    envelope = {
        "schema": CONTROLLER_OPERATION_CATALOG_SCHEMA,
        "operations": sorted(operations),
    }
    try:
        encoded = json.dumps(
            envelope,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError):
        raise _refuse(
            BootstrapErrorCode.INVALID_CONTRACT,
            "controller operation catalog is invalid",
        ) from None
    return hashlib.sha256(encoded).hexdigest()


def _decode_json(raw: bytes, *, maximum: int) -> Any:
    if type(raw) is not bytes or not raw or len(raw) > maximum:
        raise _refuse(BootstrapErrorCode.INVALID_CONTRACT, "JSON bytes are invalid")
    try:
        text = raw.decode("utf-8")
        value = json.loads(
            text,
            object_pairs_hook=_object_pairs,
            parse_constant=_nonfinite,
        )
    except BootstrapContractError:
        raise
    except (UnicodeError, json.JSONDecodeError, RecursionError, ValueError):
        raise _refuse(BootstrapErrorCode.INVALID_CONTRACT, "JSON bytes are invalid") from None
    _json_value(value)
    return value


@dataclass(frozen=True)
class BootstrapReceiverFrame:
    operation: ReceiverOperation
    expected_node: str
    operation_id: str | None = None
    plan_sha256: str | None = None
    target_config_sha256: str | None = None
    bundle_sha256: str | None = None
    bundle_length: int | None = None
    schema: str = field(default=RECEIVER_FRAME_SCHEMA, init=False)

    def __post_init__(self) -> None:
        if type(self.operation) is not ReceiverOperation:
            raise _refuse(BootstrapErrorCode.INVALID_CONTRACT, "receiver operation is invalid")
        _required_text(self.expected_node, _NODE_RE, "expected_node")
        operational = self.operation is not ReceiverOperation.IDENTITY
        if operational:
            if self.operation_id is None:
                raise _refuse(BootstrapErrorCode.INVALID_CONTRACT, "receiver frame is invalid")
            _operation_id(self.operation_id)
            _required_text(self.plan_sha256, _SHA256_RE, "plan_sha256")
            _required_text(
                self.target_config_sha256,
                _SHA256_RE,
                "target_config_sha256",
            )
        elif any(
            value is not None
            for value in (
                self.operation_id,
                self.plan_sha256,
                self.target_config_sha256,
                self.bundle_sha256,
                self.bundle_length,
            )
        ):
            raise _refuse(BootstrapErrorCode.INVALID_CONTRACT, "receiver frame is invalid")
        if self.operation is ReceiverOperation.STAGE:
            _required_text(self.bundle_sha256, _SHA256_RE, "bundle_sha256")
            if (
                type(self.bundle_length) is not int
                or not 1 <= self.bundle_length <= MAX_BUNDLE_BYTES
            ):
                raise _refuse(
                    BootstrapErrorCode.INVALID_CONTRACT,
                    "receiver bundle length is invalid",
                )
        elif self.bundle_sha256 is not None or self.bundle_length is not None:
            raise _refuse(BootstrapErrorCode.INVALID_CONTRACT, "receiver frame is invalid")

    def to_dict(self) -> dict[str, Any]:
        value: dict[str, Any] = {
            "schema": self.schema,
            "operation": self.operation.value,
            "expected_node": self.expected_node,
        }
        if self.operation is not ReceiverOperation.IDENTITY:
            value.update(
                {
                    "operation_id": self.operation_id,
                    "plan_sha256": self.plan_sha256,
                    "target_config_sha256": self.target_config_sha256,
                }
            )
        if self.operation is ReceiverOperation.STAGE:
            value.update(
                {
                    "bundle_sha256": self.bundle_sha256,
                    "bundle_length": self.bundle_length,
                }
            )
        return value

    @classmethod
    def from_dict(cls, raw: Any) -> BootstrapReceiverFrame:
        if type(raw) is not dict:
            raise _refuse(BootstrapErrorCode.INVALID_CONTRACT, "receiver frame is invalid")
        keys = tuple(raw)
        if any(type(key) is not str for key in keys):
            raise _refuse(BootstrapErrorCode.INVALID_CONTRACT, "receiver frame is invalid")
        schema = raw.get("schema")
        operation_value = raw.get("operation")
        if type(schema) is not str or schema != RECEIVER_FRAME_SCHEMA:
            raise _refuse(BootstrapErrorCode.INVALID_CONTRACT, "receiver frame schema is invalid")
        operation = _enum(ReceiverOperation, operation_value, "receiver operation")
        fields = (
            _RECEIVER_IDENTITY_FIELDS
            if operation is ReceiverOperation.IDENTITY
            else _RECEIVER_STAGE_FIELDS
            if operation is ReceiverOperation.STAGE
            else _RECEIVER_OPERATION_FIELDS
        )
        raw = _validate_exact_fields(raw, fields, "receiver frame fields are invalid")
        return cls(
            operation=operation,
            expected_node=raw["expected_node"],
            operation_id=raw.get("operation_id"),
            plan_sha256=raw.get("plan_sha256"),
            target_config_sha256=raw.get("target_config_sha256"),
            bundle_sha256=raw.get("bundle_sha256"),
            bundle_length=raw.get("bundle_length"),
        )


def _receiver_payload(frame: BootstrapReceiverFrame, payload: bytes) -> None:
    if frame.operation is ReceiverOperation.STAGE:
        if len(payload) != frame.bundle_length:
            raise _refuse(
                BootstrapErrorCode.INVALID_CONTRACT,
                "receiver payload length is invalid",
            )
        if hashlib.sha256(payload).hexdigest() != frame.bundle_sha256:
            raise _refuse(
                BootstrapErrorCode.DIGEST_MISMATCH,
                "receiver payload digest does not match",
            )
    elif payload:
        raise _refuse(BootstrapErrorCode.INVALID_CONTRACT, "receiver trailing bytes are invalid")


def encode_receiver_frame(
    frame: BootstrapReceiverFrame, payload: bytes = b""
) -> bytes:
    """Encode one exact canonical receiver request without performing I/O."""
    if type(frame) is not BootstrapReceiverFrame or type(payload) is not bytes:
        raise _refuse(BootstrapErrorCode.INVALID_CONTRACT, "receiver frame is invalid")
    _receiver_payload(frame, payload)
    metadata = canonical_json_bytes(frame.to_dict())
    if not 1 <= len(metadata) <= MAX_RECEIVER_METADATA_BYTES:
        raise _refuse(
            BootstrapErrorCode.INVALID_CONTRACT,
            "receiver metadata length is invalid",
        )
    return struct.pack(">I", len(metadata)) + metadata + payload


def decode_receiver_frame(raw: bytes) -> tuple[BootstrapReceiverFrame, bytes]:
    """Decode one complete bounded receiver request without performing I/O."""
    maximum = 4 + MAX_RECEIVER_METADATA_BYTES + MAX_BUNDLE_BYTES
    if type(raw) is not bytes or len(raw) < 5 or len(raw) > maximum:
        raise _refuse(BootstrapErrorCode.INVALID_CONTRACT, "receiver frame bytes are invalid")
    metadata_length = struct.unpack(">I", raw[:4])[0]
    if not 1 <= metadata_length <= MAX_RECEIVER_METADATA_BYTES:
        raise _refuse(
            BootstrapErrorCode.INVALID_CONTRACT,
            "receiver metadata length is invalid",
        )
    metadata_end = 4 + metadata_length
    if metadata_end > len(raw):
        raise _refuse(BootstrapErrorCode.INVALID_CONTRACT, "receiver metadata is truncated")
    metadata = raw[4:metadata_end]
    value = _decode_json(metadata, maximum=MAX_RECEIVER_METADATA_BYTES)
    frame = BootstrapReceiverFrame.from_dict(value)
    if canonical_json_bytes(frame.to_dict()) != metadata:
        raise _refuse(BootstrapErrorCode.INVALID_CONTRACT, "receiver JSON is not canonical")
    payload = raw[metadata_end:]
    _receiver_payload(frame, payload)
    return frame, payload


@dataclass(frozen=True)
class BootstrapManifest:
    package_version: str
    source_commit: str
    runtime_sha256: str
    shim_sha256: str
    expected_node: str
    platform: BootstrapPlatform
    install_adapter: InstallAdapter
    supervisor_adapter: SupervisorAdapter
    install_root_class: InstallRootClass
    controller_protocol_min: str
    controller_protocol_max: str
    schema: str = field(default=MANIFEST_SCHEMA, init=False)

    def __post_init__(self) -> None:
        _required_text(self.package_version, _VERSION_RE, "package_version")
        _required_text(self.source_commit, _COMMIT_RE, "source_commit")
        _required_text(self.runtime_sha256, _SHA256_RE, "runtime_sha256")
        _required_text(self.shim_sha256, _SHA256_RE, "shim_sha256")
        _required_text(self.expected_node, _NODE_RE, "expected_node")
        if type(self.platform) is not BootstrapPlatform:
            raise _refuse(BootstrapErrorCode.INVALID_CONTRACT, "platform is invalid")
        if type(self.install_adapter) is not InstallAdapter:
            raise _refuse(BootstrapErrorCode.INVALID_CONTRACT, "install_adapter is invalid")
        if type(self.supervisor_adapter) is not SupervisorAdapter:
            raise _refuse(BootstrapErrorCode.INVALID_CONTRACT, "supervisor_adapter is invalid")
        if type(self.install_root_class) is not InstallRootClass:
            raise _refuse(BootstrapErrorCode.INVALID_CONTRACT, "install_root_class is invalid")
        _validate_platform_pair(self.platform, self.install_adapter, self.supervisor_adapter)
        minimum = _protocol(self.controller_protocol_min, "controller_protocol_min")
        maximum = _protocol(self.controller_protocol_max, "controller_protocol_max")
        if minimum > maximum:
            raise _refuse(BootstrapErrorCode.INVALID_CONTRACT, "controller protocol range is invalid")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "package_version": self.package_version,
            "source_commit": self.source_commit,
            "runtime_sha256": self.runtime_sha256,
            "shim_sha256": self.shim_sha256,
            "expected_node": self.expected_node,
            "platform": self.platform.value,
            "install_adapter": self.install_adapter.value,
            "supervisor_adapter": self.supervisor_adapter.value,
            "install_root_class": self.install_root_class.value,
            "controller_protocol_min": self.controller_protocol_min,
            "controller_protocol_max": self.controller_protocol_max,
        }

    def to_json_bytes(self) -> bytes:
        return canonical_json_bytes(self.to_dict())

    @classmethod
    def from_dict(cls, raw: Any) -> BootstrapManifest:
        raw = _validate_exact_fields(raw, _MANIFEST_FIELDS, "manifest fields are invalid")
        schema = raw["schema"]
        if type(schema) is not str or schema != MANIFEST_SCHEMA:
            raise _refuse(BootstrapErrorCode.INVALID_CONTRACT, "manifest schema is invalid")
        return cls(
            package_version=raw["package_version"],
            source_commit=raw["source_commit"],
            runtime_sha256=raw["runtime_sha256"],
            shim_sha256=raw["shim_sha256"],
            expected_node=raw["expected_node"],
            platform=_enum(BootstrapPlatform, raw["platform"], "platform"),
            install_adapter=_enum(InstallAdapter, raw["install_adapter"], "install_adapter"),
            supervisor_adapter=_enum(
                SupervisorAdapter, raw["supervisor_adapter"], "supervisor_adapter"
            ),
            install_root_class=_enum(
                InstallRootClass, raw["install_root_class"], "install_root_class"
            ),
            controller_protocol_min=raw["controller_protocol_min"],
            controller_protocol_max=raw["controller_protocol_max"],
        )

    @classmethod
    def from_json_bytes(cls, raw: bytes) -> BootstrapManifest:
        value = _decode_json(raw, maximum=MAX_MANIFEST_BYTES)
        manifest = cls.from_dict(value)
        if manifest.to_json_bytes() != raw:
            raise _refuse(BootstrapErrorCode.INVALID_CONTRACT, "manifest JSON is not canonical")
        return manifest

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.to_json_bytes()).hexdigest()


@dataclass(frozen=True, init=False, repr=False)
class BootstrapPlan:
    """Immutable private bootstrap identity with a bounded public projection."""

    host: str
    execution_runtime: str
    topology_sha256: str
    manifest_sha256: str
    expected_node: str
    platform: BootstrapPlatform
    staging_root: str
    install_root: str
    python_executable: str
    receiver_path: str
    receiver_sha256: str
    install_adapter: InstallAdapter
    supervisor_adapter: SupervisorAdapter
    install_root_class: InstallRootClass
    supervisor_id: str
    bootstrap_enabled: bool
    bootstrap_authorized: bool
    expected_protocol_version: str
    expected_catalog_sha256: str
    _plan_sha256: str
    schema: str = field(default=PLAN_SCHEMA, init=False)

    def __init__(self, execution: ExecutionPlan, manifest: BootstrapManifest) -> None:
        values = _bootstrap_plan_values(execution, manifest)
        for name, value in values.items():
            object.__setattr__(self, name, value)

    @property
    def plan_sha256(self) -> str:
        return self._plan_sha256

    def to_dict(self) -> dict[str, Any]:
        """Return the exact public, path-free plan projection."""
        return {
            "schema": self.schema,
            "host": self.host,
            "topology_sha256": self.topology_sha256,
            "plan_sha256": self.plan_sha256,
            "manifest_sha256": self.manifest_sha256,
            "expected_node": self.expected_node,
            "platform": self.platform.value,
            "install_adapter": self.install_adapter.value,
            "supervisor_adapter": self.supervisor_adapter.value,
            "expected_protocol_version": self.expected_protocol_version,
            "expected_catalog_sha256": self.expected_catalog_sha256,
        }

    def __repr__(self) -> str:
        return (
            "BootstrapPlan("
            f"host={self.host!r}, topology_sha256={self.topology_sha256!r}, "
            f"plan_sha256={self.plan_sha256!r}, manifest_sha256={self.manifest_sha256!r})"
        )


def build_bootstrap_plan(
    execution: ExecutionPlan, manifest: BootstrapManifest
) -> BootstrapPlan:
    """Build one validated plan solely from resolved execution and artifact identity."""
    return BootstrapPlan(execution, manifest)


def _bootstrap_plan_values(
    execution: ExecutionPlan, manifest: BootstrapManifest
) -> dict[str, Any]:
    # Imported lazily because topology owns HostBootstrap but imports this module's
    # enums.  The plan boundary must not create a topology/bootstrap import cycle.
    from .targets import CommandSpec, ExecutionPlan
    from .topology import (
        _SUPERVISOR_ID_RE,
        Host,
        HostBootstrap,
        Runtime,
        _bootstrap_paths_overlap,
        _valid_bootstrap_path,
    )

    if type(execution) is not ExecutionPlan or type(manifest) is not BootstrapManifest:
        raise _refuse(BootstrapErrorCode.INVALID_CONTRACT, "bootstrap plan inputs are invalid")
    command = execution.command
    host = execution.execution_host
    runtime = execution.execution_runtime
    declared = execution.host_bootstrap
    if (
        type(command) is not CommandSpec
        or command.execution_policy != "host-bootstrap"
        or command.name != "controller-bootstrap"
        or type(host) is not Host
        or type(runtime) is not Runtime
        or type(declared) is not HostBootstrap
    ):
        raise _refuse(BootstrapErrorCode.PRECONDITION_FAILED, "bootstrap plan is inconsistent")
    if type(declared.enabled) is not bool or type(declared.bootstrap_authorized) is not bool:
        raise _refuse(BootstrapErrorCode.INVALID_CONTRACT, "bootstrap policy is invalid")
    _required_text(host.id, _NODE_RE, "host")
    _required_text(runtime.id, _NODE_RE, "execution_runtime")
    _required_text(runtime.host, _NODE_RE, "execution host")
    if type(runtime.role) is not str:
        raise _refuse(BootstrapErrorCode.INVALID_CONTRACT, "execution runtime is invalid")
    _required_text(declared.execution_runtime, _NODE_RE, "execution_runtime")
    _required_text(declared.receiver_sha256, _SHA256_RE, "receiver_sha256")
    _required_text(declared.supervisor_id, _SUPERVISOR_ID_RE, "supervisor_id")
    _required_text(execution.topology_snapshot, _SHA256_RE, "topology_sha256")
    if type(host.os) is not str or host.os not in {"windows", "linux"}:
        raise _refuse(BootstrapErrorCode.UNSUPPORTED_PLATFORM, "bootstrap platform is unsupported")
    if any(
        type(path) is not str or not _valid_bootstrap_path(path, host.os)
        for path in (
            declared.staging_root,
            declared.install_root,
            declared.python_executable,
            declared.receiver_path,
        )
    ):
        raise _refuse(BootstrapErrorCode.INVALID_CONTRACT, "bootstrap path is invalid")
    if _bootstrap_paths_overlap(declared.staging_root, declared.install_root, host.os):
        raise _refuse(BootstrapErrorCode.INVALID_CONTRACT, "bootstrap roots are invalid")
    if (
        type(declared.install_adapter) is not InstallAdapter
        or type(declared.supervisor_adapter) is not SupervisorAdapter
    ):
        raise _refuse(BootstrapErrorCode.INVALID_CONTRACT, "bootstrap adapter is invalid")
    if not declared.enabled or not declared.bootstrap_authorized:
        raise _refuse(BootstrapErrorCode.AUTHORIZATION_DENIED, "bootstrap is not authorized")
    if (
        type(host.bootstrap) is not HostBootstrap
        or host.bootstrap != declared
        or runtime.id != declared.execution_runtime
        or runtime.host != host.id
        or runtime.role != "native"
        or type(execution.selected_target) is not str
        or execution.selected_target != f"host:{host.id}"
        or type(execution.transport_expected_node) is not str
        or execution.transport_expected_node != host.id
        or type(execution.transport) is not str
        or execution.transport != "controller"
        or type(execution.transport_id) is not str
        or not execution.transport_id
        or type(execution.transport_endpoint) is not str
        or not execution.transport_endpoint
        or type(execution.transport_auth_env) is not str
        or not execution.transport_auth_env
    ):
        raise _refuse(BootstrapErrorCode.PRECONDITION_FAILED, "bootstrap plan is inconsistent")
    command_host = execution.command_host
    command_runtime = execution.command_runtime
    if (
        type(command_host) is not Host
        or type(command_runtime) is not Runtime
        or type(command_host.id) is not str
        or type(command_runtime.id) is not str
        or type(command_runtime.host) is not str
        or command_runtime.host != command_host.id
    ):
        raise _refuse(BootstrapErrorCode.PRECONDITION_FAILED, "bootstrap plan is inconsistent")
    if any(
        value is not None
        for value in (
            execution.resource_host,
            execution.resource_runtime,
            execution.resource,
            execution.resource_endpoint,
            execution.gpu_role,
            execution.capacity,
        )
    ):
        raise _refuse(BootstrapErrorCode.PRECONDITION_FAILED, "bootstrap plan is inconsistent")
    platform = BootstrapPlatform(host.os)
    try:
        _validate_platform_pair(platform, declared.install_adapter, declared.supervisor_adapter)
    except BootstrapContractError:
        raise _refuse(
            BootstrapErrorCode.UNSUPPORTED_PLATFORM,
            "bootstrap platform adapter pairing is unsupported",
        ) from None
    if (
        manifest.expected_node != host.id
        or manifest.platform is not platform
        or manifest.install_adapter is not declared.install_adapter
        or manifest.supervisor_adapter is not declared.supervisor_adapter
        or manifest.install_root_class is not InstallRootClass.USER
    ):
        raise _refuse(BootstrapErrorCode.PRECONDITION_FAILED, "bootstrap manifest is inconsistent")
    from .control_plane.mcp.protocol import PROTOCOL_VERSION

    try:
        protocol = _protocol(PROTOCOL_VERSION, "expected_protocol_version")
    except BootstrapContractError:
        raise _refuse(BootstrapErrorCode.PRECONDITION_FAILED, "bootstrap protocol is inconsistent") from None
    if not (
        manifest.controller_protocol_min <= protocol <= manifest.controller_protocol_max
        and protocol == manifest.controller_protocol_max
    ):
        raise _refuse(BootstrapErrorCode.PRECONDITION_FAILED, "bootstrap protocol is inconsistent")
    catalog_sha256 = controller_operation_catalog_sha256(
        execution.transport_allowed_operations
    )
    values = {
        "schema": PLAN_SCHEMA,
        "host": host.id,
        "execution_runtime": runtime.id,
        "topology_sha256": execution.topology_snapshot,
        "manifest_sha256": manifest.sha256,
        "expected_node": execution.transport_expected_node,
        "platform": platform,
        "staging_root": declared.staging_root,
        "install_root": declared.install_root,
        "python_executable": declared.python_executable,
        "receiver_path": declared.receiver_path,
        "receiver_sha256": declared.receiver_sha256,
        "install_adapter": declared.install_adapter,
        "supervisor_adapter": declared.supervisor_adapter,
        "install_root_class": manifest.install_root_class,
        "supervisor_id": declared.supervisor_id,
        "bootstrap_enabled": declared.enabled,
        "bootstrap_authorized": declared.bootstrap_authorized,
        "expected_protocol_version": protocol,
        "expected_catalog_sha256": catalog_sha256,
    }
    identity = dict(values)
    identity["platform"] = platform.value
    identity["install_adapter"] = declared.install_adapter.value
    identity["supervisor_adapter"] = declared.supervisor_adapter.value
    identity["install_root_class"] = manifest.install_root_class.value
    values["_plan_sha256"] = hashlib.sha256(canonical_json_bytes(identity)).hexdigest()
    return values


def _validate_platform_pair(
    platform: BootstrapPlatform,
    install_adapter: InstallAdapter,
    supervisor_adapter: SupervisorAdapter,
) -> None:
    if install_adapter is not InstallAdapter.PYTHON_WHEEL_VENV:
        raise _refuse(BootstrapErrorCode.INVALID_CONTRACT, "install adapter is invalid")
    expected = {
        BootstrapPlatform.WINDOWS: SupervisorAdapter.WINDOWS_SCHEDULED_TASK,
        BootstrapPlatform.LINUX: SupervisorAdapter.LINUX_SYSTEMD_USER,
    }[platform]
    if supervisor_adapter is not expected:
        raise _refuse(BootstrapErrorCode.INVALID_CONTRACT, "platform adapter pairing is invalid")


@dataclass(frozen=True)
class BootstrapReceipt:
    operation_id: str | None
    host: str | None
    topology_sha256: str | None
    plan_sha256: str | None
    manifest_sha256: str | None
    bundle_sha256: str | None
    platform: BootstrapPlatform | None
    install_adapter: InstallAdapter | None
    supervisor_adapter: SupervisorAdapter | None
    phase: BootstrapPhase
    outcome: BootstrapOutcome
    created_at: str
    updated_at: str
    acceptance: AcceptanceStatus
    rollback: RollbackStatus
    error_code: BootstrapErrorCode | None
    trigger_error_code: BootstrapErrorCode | None = None
    schema: str = field(default=RECEIPT_SCHEMA, init=False)

    def __post_init__(self) -> None:
        _operation_id(self.operation_id)
        _optional_text(self.host, _NODE_RE, "host")
        for label in (
            "topology_sha256",
            "plan_sha256",
            "manifest_sha256",
            "bundle_sha256",
        ):
            _optional_text(getattr(self, label), _SHA256_RE, label)
        for value, expected, label in (
            (self.platform, BootstrapPlatform, "platform"),
            (self.install_adapter, InstallAdapter, "install_adapter"),
            (self.supervisor_adapter, SupervisorAdapter, "supervisor_adapter"),
        ):
            if value is not None and type(value) is not expected:
                raise _refuse(BootstrapErrorCode.INVALID_CONTRACT, f"{label} is invalid")
        for value, expected, label in (
            (self.phase, BootstrapPhase, "phase"),
            (self.outcome, BootstrapOutcome, "outcome"),
            (self.acceptance, AcceptanceStatus, "acceptance"),
            (self.rollback, RollbackStatus, "rollback"),
        ):
            if type(value) is not expected:
                raise _refuse(BootstrapErrorCode.INVALID_CONTRACT, f"{label} is invalid")
        if self.error_code is not None and type(self.error_code) is not BootstrapErrorCode:
            raise _refuse(BootstrapErrorCode.INVALID_CONTRACT, "error_code is invalid")
        if (
            self.trigger_error_code is not None
            and type(self.trigger_error_code) is not BootstrapErrorCode
        ):
            raise _refuse(BootstrapErrorCode.INVALID_CONTRACT, "trigger_error_code is invalid")
        adapters = (self.platform, self.install_adapter, self.supervisor_adapter)
        if any(item is None for item in adapters) and any(item is not None for item in adapters):
            raise _refuse(BootstrapErrorCode.INVALID_CONTRACT, "adapter identity is incomplete")
        if all(item is not None for item in adapters):
            _validate_platform_pair(self.platform, self.install_adapter, self.supervisor_adapter)  # type: ignore[arg-type]
        created = _timestamp(self.created_at, "created_at")
        updated = _timestamp(self.updated_at, "updated_at")
        if created > updated:
            raise _refuse(BootstrapErrorCode.INVALID_CONTRACT, "receipt timestamps are invalid")
        self._validate_consistency()

    def _has_all_identity(self) -> bool:
        return all(
            value is not None
            for value in (
                self.host,
                self.topology_sha256,
                self.plan_sha256,
                self.manifest_sha256,
                self.bundle_sha256,
                self.platform,
                self.install_adapter,
                self.supervisor_adapter,
            )
        )

    def _validate_consistency(self) -> None:
        phase = self.phase
        ordinary_pending = {
            BootstrapPhase.STAGED,
            BootstrapPhase.VERIFIED,
            BootstrapPhase.INSTALLED,
            BootstrapPhase.ACTIVATED,
            BootstrapPhase.RESTARTED,
        }
        if phase is not BootstrapPhase.CLEANUP_FAILED and self.trigger_error_code is not None:
            raise _refuse(BootstrapErrorCode.INVALID_CONTRACT, "receipt trigger is invalid")
        if (
            phase is not BootstrapPhase.CLEANUP_FAILED
            and self.error_code is BootstrapErrorCode.CLEANUP_FAILED
        ):
            raise _refuse(BootstrapErrorCode.INVALID_CONTRACT, "cleanup failure phase is invalid")
        if phase is BootstrapPhase.PLANNED:
            valid = (
                self.operation_id is None
                and self._has_all_identity()
                and self.outcome is BootstrapOutcome.SUCCESS
                and self.acceptance is AcceptanceStatus.NOT_CHECKED
                and self.rollback is RollbackStatus.NOT_REQUIRED
                and self.error_code is None
            )
        elif phase in ordinary_pending:
            valid = (
                self.operation_id is not None
                and self._has_all_identity()
                and self.outcome is BootstrapOutcome.PENDING
                and self.acceptance is AcceptanceStatus.NOT_CHECKED
                and self.rollback is RollbackStatus.NOT_REQUIRED
                and self.error_code is None
            )
        elif phase is BootstrapPhase.ACCEPTED:
            valid = (
                self.operation_id is not None
                and self._has_all_identity()
                and self.outcome is BootstrapOutcome.SUCCESS
                and self.acceptance is AcceptanceStatus.ACCEPTED
                and self.rollback is RollbackStatus.NOT_REQUIRED
                and self.error_code is None
            )
        elif phase is BootstrapPhase.ROLLBACK_STARTED:
            valid = (
                self.operation_id is not None
                and self._has_all_identity()
                and self.outcome is BootstrapOutcome.PENDING
                and self.acceptance in {AcceptanceStatus.NOT_CHECKED, AcceptanceStatus.REJECTED}
                and self.rollback is RollbackStatus.PENDING
                and self.error_code is not None
            )
        elif phase is BootstrapPhase.ROLLED_BACK:
            valid = (
                self.operation_id is not None
                and self._has_all_identity()
                and self.outcome is BootstrapOutcome.ERROR
                and self.acceptance in {AcceptanceStatus.NOT_CHECKED, AcceptanceStatus.REJECTED}
                and self.rollback is RollbackStatus.VERIFIED
                and self.error_code is not None
            )
        elif phase is BootstrapPhase.MANUAL_RECOVERY:
            valid = (
                self.operation_id is not None
                and self._has_all_identity()
                and self.outcome is BootstrapOutcome.ERROR
                and self.acceptance in {AcceptanceStatus.NOT_CHECKED, AcceptanceStatus.REJECTED}
                and self.rollback in {RollbackStatus.FAILED, RollbackStatus.UNAVAILABLE}
                and self.error_code is not None
            )
        elif phase is BootstrapPhase.REFUSED:
            valid = (
                self.outcome is BootstrapOutcome.ERROR
                and self.acceptance is AcceptanceStatus.NOT_CHECKED
                and self.rollback is RollbackStatus.NOT_REQUIRED
                and self.error_code is not None
            )
        else:
            valid = self._cleanup_consistent()
        if not valid:
            raise _refuse(BootstrapErrorCode.INVALID_CONTRACT, "receipt state is inconsistent")

    def _cleanup_consistent(self) -> bool:
        if (
            self.operation_id is None
            or not self._has_all_identity()
            or self.outcome is not BootstrapOutcome.ERROR
            or self.error_code is not BootstrapErrorCode.CLEANUP_FAILED
            or self.rollback is RollbackStatus.PENDING
        ):
            return False
        if (
            self.acceptance is AcceptanceStatus.ACCEPTED
            and self.rollback is RollbackStatus.NOT_REQUIRED
        ):
            return self.trigger_error_code is None
        return (
            self.acceptance in {AcceptanceStatus.NOT_CHECKED, AcceptanceStatus.REJECTED}
            and self.rollback
            in {
                RollbackStatus.NOT_REQUIRED,
                RollbackStatus.VERIFIED,
                RollbackStatus.FAILED,
                RollbackStatus.UNAVAILABLE,
            }
            and self.trigger_error_code is not None
            and self.trigger_error_code is not BootstrapErrorCode.CLEANUP_FAILED
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "operation_id": self.operation_id,
            "host": self.host,
            "topology_sha256": self.topology_sha256,
            "plan_sha256": self.plan_sha256,
            "manifest_sha256": self.manifest_sha256,
            "bundle_sha256": self.bundle_sha256,
            "platform": self.platform.value if self.platform is not None else None,
            "install_adapter": (
                self.install_adapter.value if self.install_adapter is not None else None
            ),
            "supervisor_adapter": (
                self.supervisor_adapter.value if self.supervisor_adapter is not None else None
            ),
            "phase": self.phase.value,
            "outcome": self.outcome.value,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "acceptance": self.acceptance.value,
            "rollback": self.rollback.value,
            "error_code": self.error_code.value if self.error_code is not None else None,
            "trigger_error_code": (
                self.trigger_error_code.value if self.trigger_error_code is not None else None
            ),
        }

    def to_json_bytes(self) -> bytes:
        return canonical_json_bytes(self.to_dict())

    @classmethod
    def from_dict(cls, raw: Any) -> BootstrapReceipt:
        raw = _validate_exact_fields(raw, _RECEIPT_FIELDS, "receipt fields are invalid")
        schema = raw["schema"]
        if type(schema) is not str or schema != RECEIPT_SCHEMA:
            raise _refuse(BootstrapErrorCode.INVALID_CONTRACT, "receipt schema is invalid")
        return cls(
            operation_id=raw["operation_id"],
            host=raw["host"],
            topology_sha256=raw["topology_sha256"],
            plan_sha256=raw["plan_sha256"],
            manifest_sha256=raw["manifest_sha256"],
            bundle_sha256=raw["bundle_sha256"],
            platform=(
                None
                if raw["platform"] is None
                else _enum(BootstrapPlatform, raw["platform"], "platform")
            ),
            install_adapter=(
                None
                if raw["install_adapter"] is None
                else _enum(InstallAdapter, raw["install_adapter"], "install_adapter")
            ),
            supervisor_adapter=(
                None
                if raw["supervisor_adapter"] is None
                else _enum(SupervisorAdapter, raw["supervisor_adapter"], "supervisor_adapter")
            ),
            phase=_enum(BootstrapPhase, raw["phase"], "phase"),
            outcome=_enum(BootstrapOutcome, raw["outcome"], "outcome"),
            created_at=raw["created_at"],
            updated_at=raw["updated_at"],
            acceptance=_enum(AcceptanceStatus, raw["acceptance"], "acceptance"),
            rollback=_enum(RollbackStatus, raw["rollback"], "rollback"),
            error_code=(
                None
                if raw["error_code"] is None
                else _enum(BootstrapErrorCode, raw["error_code"], "error_code")
            ),
            trigger_error_code=(
                None
                if raw["trigger_error_code"] is None
                else _enum(
                    BootstrapErrorCode,
                    raw["trigger_error_code"],
                    "trigger_error_code",
                )
            ),
        )

    @classmethod
    def from_json_bytes(cls, raw: bytes) -> BootstrapReceipt:
        value = _decode_json(raw, maximum=MAX_RECEIPT_BYTES)
        receipt = cls.from_dict(value)
        if receipt.to_json_bytes() != raw:
            raise _refuse(BootstrapErrorCode.INVALID_CONTRACT, "receipt JSON is not canonical")
        return receipt


@dataclass(frozen=True)
class ValidatedBootstrapBundle:
    manifest: BootstrapManifest
    manifest_sha256: str
    bundle_sha256: str
    runtime_bytes: bytes = field(repr=False)
    shim_bytes: bytes = field(repr=False)


def _zip_info(name: str) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name, date_time=_DOS_EPOCH)
    info.compress_type = zipfile.ZIP_STORED
    info.create_system = 3
    info.external_attr = (stat.S_IFREG | 0o600) << 16
    info.extra = b""
    info.comment = b""
    return info


def build_bundle(manifest: BootstrapManifest, runtime_bytes: bytes, shim_bytes: bytes) -> bytes:
    """Build the canonical deterministic outer ZIP after validating all bytes."""
    if type(manifest) is not BootstrapManifest:
        raise _refuse(BootstrapErrorCode.INVALID_CONTRACT, "manifest is invalid")
    if type(runtime_bytes) is not bytes or type(shim_bytes) is not bytes:
        raise _refuse(BootstrapErrorCode.INVALID_BUNDLE, "bundle payload type is invalid")
    if not shim_bytes or len(shim_bytes) > MAX_SHIM_BYTES:
        raise _refuse(BootstrapErrorCode.INVALID_BUNDLE, "bootstrap shim exceeds policy")
    _validate_wheel(runtime_bytes)
    if hashlib.sha256(runtime_bytes).hexdigest() != manifest.runtime_sha256:
        raise _refuse(BootstrapErrorCode.DIGEST_MISMATCH, "runtime digest does not match manifest")
    if hashlib.sha256(shim_bytes).hexdigest() != manifest.shim_sha256:
        raise _refuse(BootstrapErrorCode.DIGEST_MISMATCH, "shim digest does not match manifest")
    manifest_bytes = manifest.to_json_bytes()
    if len(manifest_bytes) > MAX_MANIFEST_BYTES:
        raise _refuse(BootstrapErrorCode.INVALID_BUNDLE, "manifest exceeds policy")
    if len(manifest_bytes) + len(runtime_bytes) + len(shim_bytes) > MAX_BUNDLE_BYTES:
        raise _refuse(BootstrapErrorCode.INVALID_BUNDLE, "expanded bundle exceeds policy")
    output = io.BytesIO()
    try:
        with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_STORED) as archive:
            archive.comment = b""
            archive.writestr(_zip_info("manifest.json"), manifest_bytes)
            archive.writestr(_zip_info("runtime.whl"), runtime_bytes)
            archive.writestr(_zip_info("bootstrap_shim.py"), shim_bytes)
    except (OSError, ValueError, zipfile.BadZipFile):
        raise _refuse(BootstrapErrorCode.INVALID_BUNDLE, "bundle could not be built") from None
    raw = output.getvalue()
    if len(raw) > MAX_BUNDLE_BYTES:
        raise _refuse(BootstrapErrorCode.INVALID_BUNDLE, "bundle exceeds policy")
    return raw


def _member_mode(info: zipfile.ZipInfo) -> int:
    return (info.external_attr >> 16) & 0xFFFF


def _regular_member(info: zipfile.ZipInfo, *, exact_mode: bool) -> bool:
    if info.is_dir() or info.filename.endswith("/"):
        return False
    if info.external_attr & _FILE_ATTRIBUTE_REPARSE_POINT:
        return False
    mode = _member_mode(info)
    kind = stat.S_IFMT(mode)
    if stat.S_ISLNK(mode) or (kind and not stat.S_ISREG(mode)):
        return False
    if exact_mode:
        return info.create_system == 3 and mode == (stat.S_IFREG | 0o600)
    return True


def _read_zip_member(archive: zipfile.ZipFile, info: zipfile.ZipInfo, limit: int) -> bytes:
    try:
        with archive.open(info, "r") as stream:
            value = stream.read(limit + 1)
            trailing = stream.read(1)
    except (OSError, EOFError, RuntimeError, NotImplementedError, zipfile.BadZipFile, zlib.error):
        raise _refuse(BootstrapErrorCode.INVALID_BUNDLE, "archive payload is invalid") from None
    if len(value) != info.file_size or len(value) > limit or trailing:
        raise _refuse(BootstrapErrorCode.INVALID_BUNDLE, "archive size is invalid")
    return value


def _preflight_zip_directory(raw: bytes, *, maximum_entries: int) -> int:
    """Count a non-ZIP64 central directory before zipfile allocates its entries."""
    start = max(0, len(raw) - (_EOCD_BYTES + 65535))
    eocd = raw.rfind(_EOCD_SIGNATURE, start)
    if eocd < 0 or eocd + _EOCD_BYTES > len(raw):
        raise _refuse(BootstrapErrorCode.INVALID_BUNDLE, "archive directory is invalid")
    try:
        (
            disk,
            directory_disk,
            entries_on_disk,
            declared_entries,
            directory_bytes,
            directory_offset,
            comment_bytes,
        ) = struct.unpack_from("<4H2LH", raw, eocd + 4)
    except struct.error:
        raise _refuse(BootstrapErrorCode.INVALID_BUNDLE, "archive directory is invalid") from None
    if (
        disk != 0
        or directory_disk != 0
        or entries_on_disk != declared_entries
        or declared_entries == 0xFFFF
        or directory_bytes == 0xFFFFFFFF
        or directory_offset == 0xFFFFFFFF
        or eocd + _EOCD_BYTES + comment_bytes != len(raw)
        or directory_offset + directory_bytes != eocd
        or declared_entries > maximum_entries
    ):
        raise _refuse(BootstrapErrorCode.INVALID_BUNDLE, "archive directory is invalid")
    cursor = directory_offset
    count = 0
    while cursor < eocd:
        if cursor + _CENTRAL_BYTES > eocd or raw[cursor : cursor + 4] != _CENTRAL_SIGNATURE:
            raise _refuse(BootstrapErrorCode.INVALID_BUNDLE, "archive directory is invalid")
        try:
            name_bytes, extra_bytes, member_comment_bytes = struct.unpack_from(
                "<3H", raw, cursor + 28
            )
        except struct.error:
            raise _refuse(BootstrapErrorCode.INVALID_BUNDLE, "archive directory is invalid") from None
        cursor += _CENTRAL_BYTES + name_bytes + extra_bytes + member_comment_bytes
        count += 1
        if cursor > eocd or count > maximum_entries:
            raise _refuse(BootstrapErrorCode.INVALID_BUNDLE, "archive directory is invalid")
    if cursor != eocd or count != declared_entries:
        raise _refuse(BootstrapErrorCode.INVALID_BUNDLE, "archive directory is invalid")
    return count


def validate_archive_path(name: str) -> PurePosixPath:
    """Validate one archive member against POSIX and Windows hazards."""
    if type(name) is not str or not name:
        raise _refuse(BootstrapErrorCode.UNSAFE_PATH, "archive path is unsafe")
    try:
        encoded = name.encode("utf-8")
    except UnicodeError:
        raise _refuse(BootstrapErrorCode.UNSAFE_PATH, "archive path is unsafe") from None
    if len(encoded) > MAX_ARCHIVE_NAME_BYTES or unicodedata.normalize("NFC", name) != name:
        raise _refuse(BootstrapErrorCode.UNSAFE_PATH, "archive path is unsafe")
    if "\\" in name or any(character in _WINDOWS_FORBIDDEN_CHARACTERS for character in name):
        raise _refuse(BootstrapErrorCode.UNSAFE_PATH, "archive path is unsafe")
    if any(unicodedata.category(character) in {"Cc", "Cs"} for character in name):
        raise _refuse(BootstrapErrorCode.UNSAFE_PATH, "archive path is unsafe")
    posix = PurePosixPath(name)
    windows = PureWindowsPath(name)
    parts = name.split("/")
    if (
        posix.is_absolute()
        or windows.is_absolute()
        or bool(windows.drive)
        or bool(windows.root)
        or any(part in {"", ".", ".."} for part in parts)
    ):
        raise _refuse(BootstrapErrorCode.UNSAFE_PATH, "archive path is unsafe")
    for part in parts:
        if len(part.encode("utf-8")) > MAX_ARCHIVE_COMPONENT_BYTES:
            raise _refuse(BootstrapErrorCode.UNSAFE_PATH, "archive path is unsafe")
        if part.endswith((".", " ")):
            raise _refuse(BootstrapErrorCode.UNSAFE_PATH, "archive path is unsafe")
        # Windows also recognizes reserved device stems with spaces before an
        # extension (for example NUL .txt); lexical suffix checks miss these.
        device = part.split(".", 1)[0].rstrip(" ").upper()
        if device in _WINDOWS_DEVICES:
            raise _refuse(BootstrapErrorCode.UNSAFE_PATH, "archive path is unsafe")
    return posix


def _collision_key(name: str) -> str:
    return "/".join(part.casefold() for part in validate_archive_path(name).parts)


def _archive_member_name(info: zipfile.ZipInfo) -> str:
    name = info.filename
    original = getattr(info, "orig_filename", None)
    if type(name) is not str or type(original) is not str or original != name:
        raise _refuse(BootstrapErrorCode.INVALID_BUNDLE, "archive member name is invalid")
    return name


def _validate_wheel(raw: bytes) -> None:
    if type(raw) is not bytes or not raw or len(raw) > MAX_BUNDLE_BYTES:
        raise _refuse(BootstrapErrorCode.INVALID_BUNDLE, "wheel bytes are invalid")
    _preflight_zip_directory(raw, maximum_entries=MAX_WHEEL_ENTRIES)
    try:
        archive = zipfile.ZipFile(io.BytesIO(raw), "r")
    except (OSError, ValueError, zipfile.BadZipFile):
        raise _refuse(BootstrapErrorCode.INVALID_BUNDLE, "wheel archive is invalid") from None
    with archive:
        try:
            infos = archive.infolist()
        except (OSError, ValueError, zipfile.BadZipFile):
            raise _refuse(BootstrapErrorCode.INVALID_BUNDLE, "wheel archive is invalid") from None
        if not infos or len(infos) > MAX_WHEEL_ENTRIES:
            raise _refuse(BootstrapErrorCode.INVALID_BUNDLE, "wheel entry count is invalid")
        total = 0
        collisions: set[str] = set()
        required_directories: set[str] = set()
        for info in infos:
            name = _archive_member_name(info)
            if info.flag_bits & 0x1 or info.compress_type not in {
                zipfile.ZIP_STORED,
                zipfile.ZIP_DEFLATED,
            }:
                raise _refuse(BootstrapErrorCode.INVALID_BUNDLE, "wheel member metadata is invalid")
            if not _regular_member(info, exact_mode=False):
                raise _refuse(BootstrapErrorCode.INVALID_BUNDLE, "wheel member type is invalid")
            key = _collision_key(name)
            parts = key.split("/")
            parents = {"/".join(parts[:index]) for index in range(1, len(parts))}
            if key in collisions or key in required_directories or collisions.intersection(parents):
                raise _refuse(BootstrapErrorCode.INVALID_BUNDLE, "wheel paths collide")
            collisions.add(key)
            required_directories.update(parents)
            if info.file_size < 0 or info.compress_size < 0:
                raise _refuse(BootstrapErrorCode.INVALID_BUNDLE, "wheel size is invalid")
            total += info.file_size
            if total > MAX_WHEEL_EXPANDED_BYTES:
                raise _refuse(BootstrapErrorCode.INVALID_BUNDLE, "wheel expansion exceeds policy")
        for info in infos:
            _read_zip_member(archive, info, info.file_size)


def validate_bundle(raw: bytes) -> ValidatedBootstrapBundle:
    """Validate canonical outer bytes, nested wheel safety, hashes, and CRCs."""
    if type(raw) is not bytes or not raw or len(raw) > MAX_BUNDLE_BYTES:
        raise _refuse(BootstrapErrorCode.INVALID_BUNDLE, "bundle bytes are invalid")
    if _preflight_zip_directory(raw, maximum_entries=len(OUTER_BUNDLE_NAMES)) != len(
        OUTER_BUNDLE_NAMES
    ):
        raise _refuse(BootstrapErrorCode.INVALID_BUNDLE, "bundle entry count is invalid")
    try:
        archive = zipfile.ZipFile(io.BytesIO(raw), "r")
    except (OSError, ValueError, zipfile.BadZipFile):
        raise _refuse(BootstrapErrorCode.INVALID_BUNDLE, "bundle archive is invalid") from None
    with archive:
        try:
            infos = archive.infolist()
        except (OSError, ValueError, zipfile.BadZipFile):
            raise _refuse(BootstrapErrorCode.INVALID_BUNDLE, "bundle archive is invalid") from None
        names = tuple(_archive_member_name(info) for info in infos)
        if names != OUTER_BUNDLE_NAMES:
            raise _refuse(BootstrapErrorCode.INVALID_BUNDLE, "bundle entries are invalid")
        if archive.comment:
            raise _refuse(BootstrapErrorCode.INVALID_BUNDLE, "bundle metadata is invalid")
        expanded = 0
        compressed = 0
        for info in infos:
            if (
                info.flag_bits & 0x1
                or info.compress_type != zipfile.ZIP_STORED
                or info.date_time != _DOS_EPOCH
                or info.extra
                or info.comment
                or not _regular_member(info, exact_mode=True)
                or info.file_size < 0
                or info.compress_size < 0
            ):
                raise _refuse(BootstrapErrorCode.INVALID_BUNDLE, "bundle metadata is invalid")
            expanded += info.file_size
            compressed += info.compress_size
        if expanded > MAX_BUNDLE_BYTES or compressed > MAX_BUNDLE_BYTES:
            raise _refuse(BootstrapErrorCode.INVALID_BUNDLE, "bundle size exceeds policy")
        manifest_bytes = _read_zip_member(archive, infos[0], MAX_MANIFEST_BYTES)
        runtime_bytes = _read_zip_member(archive, infos[1], MAX_BUNDLE_BYTES)
        shim_bytes = _read_zip_member(archive, infos[2], MAX_SHIM_BYTES)
    try:
        manifest = BootstrapManifest.from_json_bytes(manifest_bytes)
    except BootstrapContractError:
        raise _refuse(BootstrapErrorCode.INVALID_BUNDLE, "bundle manifest is invalid") from None
    if hashlib.sha256(runtime_bytes).hexdigest() != manifest.runtime_sha256:
        raise _refuse(BootstrapErrorCode.DIGEST_MISMATCH, "runtime digest does not match manifest")
    if hashlib.sha256(shim_bytes).hexdigest() != manifest.shim_sha256:
        raise _refuse(BootstrapErrorCode.DIGEST_MISMATCH, "shim digest does not match manifest")
    _validate_wheel(runtime_bytes)
    canonical = build_bundle(manifest, runtime_bytes, shim_bytes)
    if canonical != raw:
        raise _refuse(BootstrapErrorCode.INVALID_BUNDLE, "bundle bytes are not canonical")
    return ValidatedBootstrapBundle(
        manifest=manifest,
        manifest_sha256=manifest.sha256,
        bundle_sha256=hashlib.sha256(raw).hexdigest(),
        runtime_bytes=runtime_bytes,
        shim_bytes=shim_bytes,
    )


def _link_like(path: Path) -> bool:
    try:
        details = path.lstat()
    except OSError:
        raise _refuse(BootstrapErrorCode.UNSAFE_PATH, "filesystem boundary is unavailable") from None
    if path.is_symlink():
        return True
    if int(getattr(details, "st_file_attributes", 0)) & _FILE_ATTRIBUTE_REPARSE_POINT:
        return True
    is_junction = getattr(path, "is_junction", None)
    return bool(is_junction and is_junction())


def preflight_contained_path(root: str | os.PathLike[str], relative: str) -> Path:
    """Read-only containment preflight; mutation callers must recheck at use time."""
    if type(root) is not str and not isinstance(root, Path):
        raise _refuse(BootstrapErrorCode.UNSAFE_PATH, "filesystem root is invalid")
    root_path = Path(root)
    if not root_path.is_absolute() or root_path.anchor == str(root_path):
        raise _refuse(BootstrapErrorCode.UNSAFE_PATH, "filesystem root is invalid")
    parts = validate_archive_path(relative).parts
    try:
        if not root_path.exists() or not root_path.is_dir():
            raise _refuse(BootstrapErrorCode.UNSAFE_PATH, "filesystem root is unsafe")
        current = Path(root_path.anchor)
        for part in root_path.parts[1:]:
            current = current / part
            if current.exists() and _link_like(current):
                raise _refuse(BootstrapErrorCode.UNSAFE_PATH, "filesystem root is unsafe")
        root_resolved = root_path.resolve(strict=True)
        current = root_path
        for index, part in enumerate(parts):
            current = current / part
            if current.exists() or current.is_symlink():
                if _link_like(current):
                    raise _refuse(BootstrapErrorCode.UNSAFE_PATH, "filesystem path is unsafe")
                if index < len(parts) - 1 and not current.is_dir():
                    raise _refuse(BootstrapErrorCode.UNSAFE_PATH, "filesystem path is unsafe")
        candidate = (root_path / Path(*parts)).resolve(strict=False)
        candidate.relative_to(root_resolved)
    except BootstrapContractError:
        raise
    except (OSError, RuntimeError, ValueError):
        raise _refuse(BootstrapErrorCode.UNSAFE_PATH, "filesystem path is unsafe") from None
    return candidate


__all__ = [
    "AcceptanceStatus",
    "BootstrapContractError",
    "BootstrapErrorCode",
    "BootstrapManifest",
    "BootstrapOutcome",
    "BootstrapPhase",
    "BootstrapPlatform",
    "BootstrapReceipt",
    "BootstrapReceiverFrame",
    "InstallAdapter",
    "InstallRootClass",
    "MANIFEST_SCHEMA",
    "MAX_ARCHIVE_COMPONENT_BYTES",
    "MAX_ARCHIVE_NAME_BYTES",
    "MAX_BUNDLE_BYTES",
    "MAX_MANIFEST_BYTES",
    "MAX_RECEIPT_BYTES",
    "MAX_RECEIVER_METADATA_BYTES",
    "MAX_SHIM_BYTES",
    "MAX_WHEEL_ENTRIES",
    "MAX_WHEEL_EXPANDED_BYTES",
    "OUTER_BUNDLE_NAMES",
    "RECEIPT_SCHEMA",
    "RECEIVER_FRAME_SCHEMA",
    "ReceiverOperation",
    "RollbackStatus",
    "SupervisorAdapter",
    "ValidatedBootstrapBundle",
    "build_bundle",
    "canonical_json_bytes",
    "decode_receiver_frame",
    "encode_receiver_frame",
    "preflight_contained_path",
    "validate_archive_path",
    "validate_bundle",
]
