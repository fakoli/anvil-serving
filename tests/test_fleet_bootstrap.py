from __future__ import annotations

import hashlib
import io
import json
import stat
import struct
import warnings
import zipfile
from dataclasses import replace

import pytest

from anvil_serving import fleet_bootstrap as bootstrap
from anvil_serving.targets import CommandSpec, resolve_execution_plan
from anvil_serving.topology import SCHEMA_VERSION, parse_topology


SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_D = "d" * 64
COMMIT = "c" * 40
OPERATION_ID = "12345678-1234-4234-9234-123456789abc"
CREATED = "2026-09-05T12:00:00.000001Z"
UPDATED = "2026-09-05T12:00:01.000002Z"


def wheel_bytes(
    entries: tuple[tuple[str, bytes], ...] = (("anvil_serving/__init__.py", b""),),
    *,
    compression: int = zipfile.ZIP_DEFLATED,
) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=compression) as archive:
        for name, payload in entries:
            info = zipfile.ZipInfo(name)
            info.compress_type = compression
            info.create_system = 3
            info.external_attr = (stat.S_IFREG | 0o644) << 16
            archive.writestr(info, payload)
    return output.getvalue()


def manifest(runtime: bytes | None = None, shim: bytes = b"print('fixed')\n") -> bootstrap.BootstrapManifest:
    runtime = wheel_bytes() if runtime is None else runtime
    return bootstrap.BootstrapManifest(
        package_version="1.2.3rc4",
        source_commit=COMMIT,
        runtime_sha256=hashlib.sha256(runtime).hexdigest(),
        shim_sha256=hashlib.sha256(shim).hexdigest(),
        expected_node="Node_1",
        platform=bootstrap.BootstrapPlatform.LINUX,
        install_adapter=bootstrap.InstallAdapter.PYTHON_WHEEL_VENV,
        supervisor_adapter=bootstrap.SupervisorAdapter.LINUX_SYSTEMD_USER,
        install_root_class=bootstrap.InstallRootClass.USER,
        controller_protocol_min="2025-11-25",
        controller_protocol_max="2026-07-28",
    )


def receipt(
    phase: bootstrap.BootstrapPhase,
    *,
    outcome: bootstrap.BootstrapOutcome,
    acceptance: bootstrap.AcceptanceStatus,
    rollback: bootstrap.RollbackStatus,
    error: bootstrap.BootstrapErrorCode | None = None,
    trigger: bootstrap.BootstrapErrorCode | None = None,
    operation_id: str | None = OPERATION_ID,
) -> bootstrap.BootstrapReceipt:
    return bootstrap.BootstrapReceipt(
        operation_id=operation_id,
        host="Node_1",
        topology_sha256=SHA_A,
        plan_sha256=SHA_B,
        manifest_sha256=SHA_A,
        bundle_sha256=SHA_B,
        platform=bootstrap.BootstrapPlatform.LINUX,
        install_adapter=bootstrap.InstallAdapter.PYTHON_WHEEL_VENV,
        supervisor_adapter=bootstrap.SupervisorAdapter.LINUX_SYSTEMD_USER,
        phase=phase,
        outcome=outcome,
        created_at=CREATED,
        updated_at=UPDATED,
        acceptance=acceptance,
        rollback=rollback,
        error_code=error,
        trigger_error_code=trigger,
    )


def bootstrap_execution(
    *,
    platform: str = "linux",
    operations: tuple[str, ...] = ("z-operation", "controller-bootstrap"),
    bootstrap_changes: dict | None = None,
):
    windows = platform == "windows"
    declared = {
        "enabled": True,
        "bootstrap_authorized": True,
        "execution_runtime": "target-native",
        "staging_root": "C:\\private-stage" if windows else "/var/tmp/private-stage",
        "install_root": "C:\\private-install" if windows else "/opt/private-install",
        "python_executable": "C:\\private-python\\python.exe" if windows else "/usr/bin/private-python",
        "receiver_path": "C:\\private-receiver\\bootstrap.py" if windows else "/opt/private-receiver/bootstrap.py",
        "receiver_sha256": SHA_A,
        "install_adapter": "python-wheel-venv",
        "supervisor_adapter": "windows-scheduled-task" if windows else "linux-systemd-user",
        "supervisor_id": "anvil-controller",
    }
    declared.update(bootstrap_changes or {})
    topology = parse_topology(
        {
            "schema_version": SCHEMA_VERSION,
            "id": "synthetic-bootstrap-plan",
            "command_host": "host:operator",
            "command_runtime": "runtime:operator-native",
            "capacity_policies": [],
            "hosts": [
                {"id": "operator", "roles": ["operator"], "address": "127.0.0.1"},
                {
                    "id": "Node_1",
                    "roles": ["controller"],
                    "address": "100.64.0.10",
                    "os": platform,
                    "bootstrap": declared,
                },
            ],
            "runtimes": [
                {"id": "operator-native", "host": "operator", "role": "native"},
                {"id": "target-native", "host": "Node_1", "role": "native"},
            ],
            "gpu_roles": [],
            "resources": [],
            "transports": [
                {
                    "id": "bootstrap-controller",
                    "kind": "controller",
                    "host": "Node_1",
                    "runtime": "target-native",
                    "endpoint": "http://100.64.0.10:8766",
                    "auth_env": "SYNTHETIC_BOOTSTRAP_TOKEN",
                    "allowed_operations": list(operations),
                    "expected_node": "Node_1",
                }
            ],
        }
    )
    command = CommandSpec(
        name="controller-bootstrap",
        resource_role=None,
        supported_transports=("controller", "ssh"),
        execution_runtime_roles=("native",),
        mutation_class="write",
        recovery_capable=True,
        gpu_role_required=False,
        execution_host_os=("windows", "linux"),
        execution_policy="host-bootstrap",
    )
    return resolve_execution_plan(topology, command, target="host:Node_1")


def plan_manifest(*, platform: str = "linux") -> bootstrap.BootstrapManifest:
    value = manifest()
    return replace(
        value,
        platform=bootstrap.BootstrapPlatform(platform),
        supervisor_adapter=(
            bootstrap.SupervisorAdapter.WINDOWS_SCHEDULED_TASK
            if platform == "windows"
            else bootstrap.SupervisorAdapter.LINUX_SYSTEMD_USER
        ),
    )


def target_config(
    *, platform: str = "linux", changes: dict | None = None
) -> bootstrap.BootstrapTargetConfig:
    plan = bootstrap.build_bootstrap_plan(
        bootstrap_execution(platform=platform), plan_manifest(platform=platform)
    )
    values = {
        "expected_node": plan.expected_node,
        "platform": plan.platform,
        "staging_root": plan.staging_root,
        "install_root": plan.install_root,
        "python_executable": plan.python_executable,
        "receiver_path": plan.receiver_path,
        "receiver_sha256": plan.receiver_sha256,
        "install_adapter": plan.install_adapter,
        "supervisor_adapter": plan.supervisor_adapter,
        "install_root_class": plan.install_root_class,
        "supervisor_id": plan.supervisor_id,
        "bootstrap_enabled": plan.bootstrap_enabled,
        "bootstrap_authorized": plan.bootstrap_authorized,
    }
    values.update(changes or {})
    return bootstrap.BootstrapTargetConfig(**values)


def rewrite_bundle(
    raw: bytes,
    *,
    order: tuple[str, ...] = bootstrap.OUTER_BUNDLE_NAMES,
    mutate_info=None,
    mutate_payload=None,
    archive_comment: bytes = b"",
) -> bytes:
    with zipfile.ZipFile(io.BytesIO(raw), "r") as source:
        payloads = {info.filename: source.read(info) for info in source.infolist()}
    output = io.BytesIO()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_STORED) as target:
            target.comment = archive_comment
            for name in order:
                info = zipfile.ZipInfo(name, (1980, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_STORED
                info.create_system = 3
                info.external_attr = (stat.S_IFREG | 0o600) << 16
                if mutate_info is not None:
                    mutate_info(name, info)
                payload = payloads.get(name, b"x")
                if mutate_payload is not None:
                    payload = mutate_payload(name, payload)
                target.writestr(info, payload)
    return output.getvalue()


def mark_first_entry_encrypted(raw: bytes) -> bytes:
    changed = bytearray(raw)
    local = changed.find(b"PK\x03\x04")
    central = changed.find(b"PK\x01\x02")
    assert local >= 0 and central >= 0
    struct.pack_into("<H", changed, local + 6, struct.unpack_from("<H", changed, local + 6)[0] | 1)
    struct.pack_into(
        "<H", changed, central + 8, struct.unpack_from("<H", changed, central + 8)[0] | 1
    )
    return bytes(changed)


def corrupt_first_compressed_byte(raw: bytes) -> bytes:
    changed = bytearray(raw)
    local = changed.find(b"PK\x03\x04")
    assert local >= 0
    name_bytes, extra_bytes = struct.unpack_from("<2H", changed, local + 26)
    payload = local + 30 + name_bytes + extra_bytes
    assert payload < len(changed)
    changed[payload] = 7
    return bytes(changed)


def receiver_frame(
    operation: bootstrap.ReceiverOperation,
    payload: bytes = b"fixed-stage-payload",
) -> bootstrap.BootstrapReceiverFrame:
    values = {
        "operation": operation,
        "expected_node": "Node_1",
    }
    if operation is not bootstrap.ReceiverOperation.IDENTITY:
        values.update(
            operation_id=OPERATION_ID,
            plan_sha256=SHA_B,
            target_config_sha256=SHA_D,
        )
    if operation is bootstrap.ReceiverOperation.STAGE:
        values.update(
            bundle_sha256=hashlib.sha256(payload).hexdigest(),
            bundle_length=len(payload),
        )
    if operation is bootstrap.ReceiverOperation.ROLLBACK:
        values["trigger_error_code"] = bootstrap.BootstrapErrorCode.ACCEPTANCE_FAILED
    return bootstrap.BootstrapReceiverFrame(**values)


def receiver_identity_result(
    *,
    outcome: bootstrap.BootstrapOutcome = bootstrap.BootstrapOutcome.SUCCESS,
    receiver_sha256: str | None = SHA_A,
    target_config_sha256: str | None = SHA_D,
    receiver_permission: bootstrap.BootstrapPermissionVerdict = (
        bootstrap.BootstrapPermissionVerdict.OWNER_READONLY
    ),
    target_config_permission: bootstrap.BootstrapPermissionVerdict = (
        bootstrap.BootstrapPermissionVerdict.OWNER_WRITABLE
    ),
    error_code: bootstrap.BootstrapErrorCode | None = None,
) -> bootstrap.BootstrapReceiverIdentityResult:
    return bootstrap.BootstrapReceiverIdentityResult(
        expected_node="Node_1",
        outcome=outcome,
        receiver_sha256=receiver_sha256,
        target_config_sha256=target_config_sha256,
        receiver_permission=receiver_permission,
        target_config_permission=target_config_permission,
        error_code=error_code,
    )


def receiver_operation_result(
    operation: bootstrap.ReceiverOperation = bootstrap.ReceiverOperation.STAGE,
    phase: bootstrap.BootstrapPhase = bootstrap.BootstrapPhase.STAGED,
    *,
    bound: bool = True,
    outcome: bootstrap.BootstrapOutcome = bootstrap.BootstrapOutcome.PENDING,
    error_code: bootstrap.BootstrapErrorCode | None = None,
    trigger_error_code: bootstrap.BootstrapErrorCode | None = None,
) -> bootstrap.BootstrapReceiverOperationResult:
    return bootstrap.BootstrapReceiverOperationResult(
        operation=operation,
        expected_node="Node_1",
        operation_id=OPERATION_ID,
        plan_sha256=SHA_B,
        target_config_sha256=SHA_D,
        bound=bound,
        bundle_sha256=SHA_A if bound else None,
        bundle_length=4096 if bound else None,
        manifest_sha256=SHA_D if bound else None,
        phase=phase,
        outcome=outcome,
        error_code=error_code,
        trigger_error_code=trigger_error_code,
    )


def receiver_result_state(phase: bootstrap.BootstrapPhase) -> dict:
    if phase in {
        bootstrap.BootstrapPhase.STAGED,
        bootstrap.BootstrapPhase.VERIFIED,
        bootstrap.BootstrapPhase.INSTALLED,
        bootstrap.BootstrapPhase.ACTIVATED,
        bootstrap.BootstrapPhase.RESTARTED,
    }:
        return {
            "outcome": bootstrap.BootstrapOutcome.PENDING,
            "error_code": None,
            "trigger_error_code": None,
        }
    if phase is bootstrap.BootstrapPhase.ROLLBACK_STARTED:
        return {
            "outcome": bootstrap.BootstrapOutcome.PENDING,
            "error_code": bootstrap.BootstrapErrorCode.ACCEPTANCE_FAILED,
            "trigger_error_code": None,
        }
    if phase in {
        bootstrap.BootstrapPhase.ROLLED_BACK,
        bootstrap.BootstrapPhase.MANUAL_RECOVERY,
    }:
        return {
            "outcome": bootstrap.BootstrapOutcome.ERROR,
            "error_code": bootstrap.BootstrapErrorCode.ACCEPTANCE_FAILED,
            "trigger_error_code": None,
        }
    if phase is bootstrap.BootstrapPhase.CLEANUP_FAILED:
        return {
            "outcome": bootstrap.BootstrapOutcome.ERROR,
            "error_code": bootstrap.BootstrapErrorCode.CLEANUP_FAILED,
            "trigger_error_code": bootstrap.BootstrapErrorCode.ACCEPTANCE_FAILED,
        }
    return {
        "outcome": bootstrap.BootstrapOutcome.PENDING,
        "error_code": None,
        "trigger_error_code": None,
    }


RECEIVER_RESULT_PHASES = {
    bootstrap.ReceiverOperation.STAGE: (
        bootstrap.BootstrapPhase.STAGED,
        bootstrap.BootstrapPhase.VERIFIED,
        bootstrap.BootstrapPhase.INSTALLED,
        bootstrap.BootstrapPhase.ACTIVATED,
        bootstrap.BootstrapPhase.RESTARTED,
        bootstrap.BootstrapPhase.ROLLBACK_STARTED,
        bootstrap.BootstrapPhase.ROLLED_BACK,
        bootstrap.BootstrapPhase.MANUAL_RECOVERY,
        bootstrap.BootstrapPhase.CLEANUP_FAILED,
    ),
    bootstrap.ReceiverOperation.ACTIVATE: (
        bootstrap.BootstrapPhase.INSTALLED,
        bootstrap.BootstrapPhase.ACTIVATED,
        bootstrap.BootstrapPhase.RESTARTED,
        bootstrap.BootstrapPhase.ROLLBACK_STARTED,
        bootstrap.BootstrapPhase.ROLLED_BACK,
        bootstrap.BootstrapPhase.MANUAL_RECOVERY,
        bootstrap.BootstrapPhase.CLEANUP_FAILED,
    ),
    bootstrap.ReceiverOperation.STATUS: (
        bootstrap.BootstrapPhase.STAGED,
        bootstrap.BootstrapPhase.VERIFIED,
        bootstrap.BootstrapPhase.INSTALLED,
        bootstrap.BootstrapPhase.ACTIVATED,
        bootstrap.BootstrapPhase.RESTARTED,
        bootstrap.BootstrapPhase.ROLLBACK_STARTED,
        bootstrap.BootstrapPhase.ROLLED_BACK,
        bootstrap.BootstrapPhase.MANUAL_RECOVERY,
        bootstrap.BootstrapPhase.CLEANUP_FAILED,
    ),
    bootstrap.ReceiverOperation.ROLLBACK: (
        bootstrap.BootstrapPhase.ROLLBACK_STARTED,
        bootstrap.BootstrapPhase.ROLLED_BACK,
        bootstrap.BootstrapPhase.MANUAL_RECOVERY,
        bootstrap.BootstrapPhase.CLEANUP_FAILED,
    ),
}


def test_receiver_rollback_has_literal_trigger_wire_bytes():
    metadata = (
        b'{"expected_node":"Node_1","operation":"rollback","operation_id":"12345678-1234-4234-9234-123456789abc",'
        b'"plan_sha256":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",'
        b'"schema":"anvil-serving.fleet-bootstrap-receiver-frame/v1",'
        b'"target_config_sha256":"dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd",'
        b'"trigger_error_code":"acceptance-failed"}'
    )
    expected = len(metadata).to_bytes(4, "big") + metadata
    frame = receiver_frame(bootstrap.ReceiverOperation.ROLLBACK, b"")
    assert bootstrap.encode_receiver_frame(frame) == expected
    assert bootstrap.decode_receiver_frame(expected) == (frame, b"")


def test_receiver_rollback_roundtrips_original_error():
    frame = replace(
        receiver_frame(bootstrap.ReceiverOperation.ROLLBACK, b""),
        trigger_error_code=bootstrap.BootstrapErrorCode.TIMEOUT,
    )
    decoded, payload = bootstrap.decode_receiver_frame(bootstrap.encode_receiver_frame(frame))
    assert decoded == frame
    assert payload == b""
    assert decoded.to_dict()["trigger_error_code"] == "timeout"


@pytest.mark.parametrize(
    "trigger",
    (
        None,
        bootstrap.BootstrapErrorCode.CLEANUP_FAILED,
        bootstrap.BootstrapOutcome.ERROR,
        "acceptance-failed",
        True,
        object(),
    ),
)
def test_receiver_rollback_constructor_rejects_missing_cleanup_and_wrong_trigger_types(
    trigger,
):
    values = receiver_frame(bootstrap.ReceiverOperation.ACTIVATE).to_dict()
    values["operation"] = bootstrap.ReceiverOperation.ROLLBACK
    values.pop("schema")
    values["trigger_error_code"] = trigger
    with pytest.raises(bootstrap.BootstrapContractError) as caught:
        bootstrap.BootstrapReceiverFrame(**values)
    assert caught.value.code == "invalid-contract"


@pytest.mark.parametrize(
    ("omit", "wire_trigger"),
    (
        (True, None),
        (False, None),
        (False, "cleanup-failed"),
        (False, "unknown"),
        (False, bootstrap.BootstrapErrorCode.ACCEPTANCE_FAILED),
    ),
)
def test_receiver_rollback_wire_rejects_missing_null_cleanup_and_unknown_trigger(
    omit, wire_trigger,
):
    raw = receiver_frame(bootstrap.ReceiverOperation.ROLLBACK, b"").to_dict()
    if omit:
        raw.pop("trigger_error_code")
    else:
        raw["trigger_error_code"] = wire_trigger
    with pytest.raises(bootstrap.BootstrapContractError) as caught:
        bootstrap.BootstrapReceiverFrame.from_dict(raw)
    assert caught.value.code == "invalid-contract"


@pytest.mark.parametrize(
    "operation",
    (
        bootstrap.ReceiverOperation.IDENTITY,
        bootstrap.ReceiverOperation.STAGE,
        bootstrap.ReceiverOperation.ACTIVATE,
        bootstrap.ReceiverOperation.STATUS,
    ),
)
def test_receiver_trigger_field_is_forbidden_for_every_nonrollback_operation(operation):
    payload = b"fixed-stage-payload" if operation is bootstrap.ReceiverOperation.STAGE else b""
    raw = receiver_frame(operation, payload).to_dict()
    raw["trigger_error_code"] = "acceptance-failed"
    with pytest.raises(bootstrap.BootstrapContractError) as caught:
        bootstrap.BootstrapReceiverFrame.from_dict(raw)
    assert caught.value.code == "invalid-contract"


def test_receiver_payload_requires_exact_length_digest_and_eof():
    payload = b"fixed-stage-payload"
    stage = receiver_frame(bootstrap.ReceiverOperation.STAGE, payload)
    metadata = bootstrap.canonical_json_bytes(stage.to_dict())
    prefix = len(metadata).to_bytes(4, "big") + metadata
    for bad in (prefix + payload[:-1], prefix + payload + b"x"):
        with pytest.raises(bootstrap.BootstrapContractError) as caught:
            bootstrap.decode_receiver_frame(bad)
        assert caught.value.code == "invalid-contract"

    wrong = replace(stage, bundle_sha256=SHA_A)
    with pytest.raises(bootstrap.BootstrapContractError) as caught:
        bootstrap.encode_receiver_frame(wrong, payload)
    assert caught.value.code == "digest-mismatch"
    wrong_metadata = bootstrap.canonical_json_bytes(wrong.to_dict())
    with pytest.raises(bootstrap.BootstrapContractError) as caught:
        bootstrap.decode_receiver_frame(
            len(wrong_metadata).to_bytes(4, "big") + wrong_metadata + payload
        )
    assert caught.value.code == "digest-mismatch"

    identity = receiver_frame(bootstrap.ReceiverOperation.IDENTITY, b"")
    with pytest.raises(bootstrap.BootstrapContractError) as caught:
        bootstrap.decode_receiver_frame(bootstrap.encode_receiver_frame(identity) + b"x")
    assert caught.value.code == "invalid-contract"


# The map above covers membership; exercise each state-validation branch below.


@pytest.mark.parametrize(
    "target_permission",
    (
        bootstrap.BootstrapPermissionVerdict.OWNER_READONLY,
        bootstrap.BootstrapPermissionVerdict.OWNER_WRITABLE,
    ),
)
def test_receiver_identity_success_accepts_both_trusted_config_permissions(
    target_permission,
):
    value = receiver_identity_result(target_config_permission=target_permission)
    assert bootstrap.decode_receiver_result(
        bootstrap.encode_receiver_result(value)
    ) == value


@pytest.mark.parametrize(
    "changes",
    (
        {"outcome": bootstrap.BootstrapOutcome.ERROR},
        {"error_code": None},
        {"error_code": bootstrap.BootstrapErrorCode.CLEANUP_FAILED},
        {"trigger_error_code": bootstrap.BootstrapErrorCode.TIMEOUT},
    ),
)
def test_receiver_rollback_started_requires_pending_original_noncleanup_error(changes):
    value = receiver_operation_result(
        bootstrap.ReceiverOperation.ROLLBACK,
        bootstrap.BootstrapPhase.ROLLBACK_STARTED,
        **receiver_result_state(bootstrap.BootstrapPhase.ROLLBACK_STARTED),
    )
    with pytest.raises(bootstrap.BootstrapContractError):
        replace(value, **changes)


@pytest.mark.parametrize(
    "changes",
    (
        {"outcome": bootstrap.BootstrapOutcome.PENDING},
        {"error_code": None},
        {"error_code": bootstrap.BootstrapErrorCode.CLEANUP_FAILED},
        {"trigger_error_code": bootstrap.BootstrapErrorCode.TIMEOUT},
    ),
)
def test_receiver_completed_rollback_requires_error_original_and_no_trigger(changes):
    value = receiver_operation_result(
        bootstrap.ReceiverOperation.ROLLBACK,
        bootstrap.BootstrapPhase.ROLLED_BACK,
        **receiver_result_state(bootstrap.BootstrapPhase.ROLLED_BACK),
    )
    with pytest.raises(bootstrap.BootstrapContractError):
        replace(value, **changes)


def test_receiver_result_direct_and_wire_types_are_exact():
    value = receiver_operation_result()
    for change in (
        {"operation": "stage"},
        {"phase": "staged"},
        {"outcome": "pending"},
        {"bound": 1},
        {"bundle_length": True},
        {"error_code": "timeout"},
    ):
        with pytest.raises(bootstrap.BootstrapContractError):
            replace(value, **change)
    with pytest.raises(bootstrap.BootstrapContractError):
        bootstrap.decode_receiver_result(bytearray(bootstrap.encode_receiver_result(value)))
    with pytest.raises(bootstrap.BootstrapContractError):
        bootstrap.encode_receiver_result(value.to_dict())

    raw = value.to_dict()
    raw["bundle_length"] = True
    metadata = bootstrap.canonical_json_bytes(raw)
    with pytest.raises(bootstrap.BootstrapContractError):
        bootstrap.decode_receiver_result(len(metadata).to_bytes(4, "big") + metadata)


@pytest.mark.parametrize(
    "phase",
    (
        bootstrap.BootstrapPhase.ROLLBACK_STARTED,
        bootstrap.BootstrapPhase.ROLLED_BACK,
        bootstrap.BootstrapPhase.MANUAL_RECOVERY,
        bootstrap.BootstrapPhase.CLEANUP_FAILED,
    ),
)
def test_receiver_result_matcher_binds_rollback_original_trigger(phase):
    frame = receiver_frame(bootstrap.ReceiverOperation.ROLLBACK, b"")
    result = receiver_operation_result(
        bootstrap.ReceiverOperation.ROLLBACK,
        phase,
        **receiver_result_state(phase),
    )
    assert bootstrap.match_receiver_result(frame, result) is result
    changed = replace(
        frame,
        trigger_error_code=bootstrap.BootstrapErrorCode.TIMEOUT,
    )
    with pytest.raises(bootstrap.BootstrapContractError) as caught:
        bootstrap.match_receiver_result(changed, result)
    assert caught.value.code == "receiver-mismatch"


def test_manifest_canonical_roundtrip_and_digest_domains():
    runtime = wheel_bytes()
    shim = b"fixed shim\n"
    value = manifest(runtime, shim)
    canonical = value.to_json_bytes()

    assert bootstrap.BootstrapManifest.from_json_bytes(canonical) == value
    assert canonical == bootstrap.canonical_json_bytes(value.to_dict())
    assert value.sha256 == hashlib.sha256(canonical).hexdigest()
    assert b"manifest_sha256" not in canonical
    assert b"bundle_sha256" not in canonical

    bundle1 = bootstrap.build_bundle(value, runtime, shim)
    bundle2 = bootstrap.build_bundle(value, runtime, shim)
    assert bundle1 == bundle2
    validated = bootstrap.validate_bundle(bundle1)
    assert validated.manifest == value
    assert validated.bundle_sha256 == hashlib.sha256(bundle1).hexdigest()
    assert validated.manifest_sha256 == value.sha256


def test_target_config_false_policy_is_canonical_data_not_authority():
    target = target_config(
        changes={"bootstrap_enabled": False, "bootstrap_authorized": False}
    )
    payload = json.loads(target.to_private_bytes())

    assert payload["bootstrap_enabled"] is False
    assert payload["bootstrap_authorized"] is False
    assert bootstrap.BootstrapTargetConfig.from_bytes(target.to_private_bytes()) == target


def test_target_config_digest_binds_target_fields_but_excludes_full_plan_fields():
    base = target_config()
    changed = (
        target_config(changes={"receiver_sha256": SHA_B}),
        target_config(changes={"bootstrap_enabled": False}),
        target_config(changes={"supervisor_id": "other-controller"}),
    )
    assert all(item.target_config_sha256 != base.target_config_sha256 for item in changed)

    execution = bootstrap_execution()
    original_plan = bootstrap.build_bootstrap_plan(execution, plan_manifest())
    artifact_changed = bootstrap.build_bootstrap_plan(
        execution, replace(plan_manifest(), runtime_sha256=SHA_B)
    )
    catalog_changed = bootstrap.build_bootstrap_plan(
        bootstrap_execution(operations=("other-operation", "controller-bootstrap")),
        plan_manifest(),
    )
    for changed_plan in (artifact_changed, catalog_changed):
        assert original_plan.plan_sha256 != changed_plan.plan_sha256
        assert (
            bootstrap.bootstrap_target_config_sha256(original_plan)
            == bootstrap.bootstrap_target_config_sha256(changed_plan)
        )


def test_terminal_and_recovery_receipt_matrix():
    values = [
        receipt(
            bootstrap.BootstrapPhase.PLANNED,
            outcome=bootstrap.BootstrapOutcome.SUCCESS,
            acceptance=bootstrap.AcceptanceStatus.NOT_CHECKED,
            rollback=bootstrap.RollbackStatus.NOT_REQUIRED,
            operation_id=None,
        ),
        receipt(
            bootstrap.BootstrapPhase.ACCEPTED,
            outcome=bootstrap.BootstrapOutcome.SUCCESS,
            acceptance=bootstrap.AcceptanceStatus.ACCEPTED,
            rollback=bootstrap.RollbackStatus.NOT_REQUIRED,
        ),
        receipt(
            bootstrap.BootstrapPhase.ROLLBACK_STARTED,
            outcome=bootstrap.BootstrapOutcome.PENDING,
            acceptance=bootstrap.AcceptanceStatus.REJECTED,
            rollback=bootstrap.RollbackStatus.PENDING,
            error=bootstrap.BootstrapErrorCode.ACCEPTANCE_FAILED,
        ),
        receipt(
            bootstrap.BootstrapPhase.ROLLED_BACK,
            outcome=bootstrap.BootstrapOutcome.ERROR,
            acceptance=bootstrap.AcceptanceStatus.REJECTED,
            rollback=bootstrap.RollbackStatus.VERIFIED,
            error=bootstrap.BootstrapErrorCode.ACCEPTANCE_FAILED,
        ),
        receipt(
            bootstrap.BootstrapPhase.MANUAL_RECOVERY,
            outcome=bootstrap.BootstrapOutcome.ERROR,
            acceptance=bootstrap.AcceptanceStatus.NOT_CHECKED,
            rollback=bootstrap.RollbackStatus.UNAVAILABLE,
            error=bootstrap.BootstrapErrorCode.INSTALL_FAILED,
        ),
        receipt(
            bootstrap.BootstrapPhase.REFUSED,
            outcome=bootstrap.BootstrapOutcome.ERROR,
            acceptance=bootstrap.AcceptanceStatus.NOT_CHECKED,
            rollback=bootstrap.RollbackStatus.NOT_REQUIRED,
            error=bootstrap.BootstrapErrorCode.PRECONDITION_FAILED,
            operation_id=None,
        ),
        receipt(
            bootstrap.BootstrapPhase.CLEANUP_FAILED,
            outcome=bootstrap.BootstrapOutcome.ERROR,
            acceptance=bootstrap.AcceptanceStatus.ACCEPTED,
            rollback=bootstrap.RollbackStatus.NOT_REQUIRED,
            error=bootstrap.BootstrapErrorCode.CLEANUP_FAILED,
        ),
        receipt(
            bootstrap.BootstrapPhase.CLEANUP_FAILED,
            outcome=bootstrap.BootstrapOutcome.ERROR,
            acceptance=bootstrap.AcceptanceStatus.REJECTED,
            rollback=bootstrap.RollbackStatus.VERIFIED,
            error=bootstrap.BootstrapErrorCode.CLEANUP_FAILED,
            trigger=bootstrap.BootstrapErrorCode.ACCEPTANCE_FAILED,
        ),
    ]
    for value in values:
        assert set(value.to_dict()) == set(bootstrap.BootstrapReceipt.from_dict(value.to_dict()).to_dict())


def test_bundle_rejects_hash_mismatch_and_noncanonical_metadata():
    runtime = wheel_bytes()
    shim = b"fixed\n"
    raw = bootstrap.build_bundle(manifest(runtime, shim), runtime, shim)
    wrong_payload = rewrite_bundle(
        raw,
        mutate_payload=lambda name, payload: payload + b"x" if name == "bootstrap_shim.py" else payload,
    )
    with pytest.raises(bootstrap.BootstrapContractError) as caught:
        bootstrap.validate_bundle(wrong_payload)
    assert caught.value.code == "digest-mismatch"

    mutations = [
        lambda name, info: setattr(info, "date_time", (1981, 1, 1, 0, 0, 0)) if name == "runtime.whl" else None,
        lambda name, info: setattr(info, "external_attr", (stat.S_IFREG | 0o644) << 16) if name == "runtime.whl" else None,
        lambda name, info: setattr(info, "extra", b"\x01\x00\x00\x00") if name == "runtime.whl" else None,
        lambda name, info: setattr(info, "comment", b"comment") if name == "runtime.whl" else None,
        lambda name, info: setattr(info, "compress_type", zipfile.ZIP_DEFLATED) if name == "runtime.whl" else None,
        lambda name, info: setattr(info, "external_attr", (stat.S_IFLNK | 0o777) << 16) if name == "runtime.whl" else None,
    ]
    for mutation in mutations:
        with pytest.raises(bootstrap.BootstrapContractError):
            bootstrap.validate_bundle(rewrite_bundle(raw, mutate_info=mutation))
    with pytest.raises(bootstrap.BootstrapContractError):
        bootstrap.validate_bundle(rewrite_bundle(raw, archive_comment=b"comment"))


def test_preflight_containment_accepts_child_and_rejects_escape_and_link(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    assert bootstrap.preflight_contained_path(root, "generation/file") == root / "generation/file"
    with pytest.raises(bootstrap.BootstrapContractError):
        bootstrap.preflight_contained_path(root, "../escape")

    outside = tmp_path / "outside"
    outside.mkdir()
    link = root / "link"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symlink creation is unavailable")
    with pytest.raises(bootstrap.BootstrapContractError, match="unsafe"):
        bootstrap.preflight_contained_path(root, "link/file")
