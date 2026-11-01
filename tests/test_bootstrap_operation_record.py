from __future__ import annotations

import hashlib
import io
import json
import stat
import zipfile
from dataclasses import replace

import pytest

from anvil_serving import fleet_bootstrap as bootstrap


SHA_A = "a" * 64
SHA_B = "b" * 64
COMMIT = "c" * 40
OPERATION_ID = "12345678-1234-4234-9234-123456789abc"


def _wheel() -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_STORED) as archive:
        info = zipfile.ZipInfo("anvil_serving/__init__.py")
        info.create_system = 3
        info.external_attr = (stat.S_IFREG | 0o644) << 16
        archive.writestr(info, b"")
    return output.getvalue()


def _bundle(*, node: str = "Node_1") -> bytes:
    runtime = _wheel()
    shim = b"print('fixed')\n"
    manifest = bootstrap.BootstrapManifest(
        package_version="1.2.3",
        source_commit=COMMIT,
        runtime_sha256=hashlib.sha256(runtime).hexdigest(),
        shim_sha256=hashlib.sha256(shim).hexdigest(),
        expected_node=node,
        platform=bootstrap.BootstrapPlatform.LINUX,
        install_adapter=bootstrap.InstallAdapter.PYTHON_WHEEL_VENV,
        supervisor_adapter=bootstrap.SupervisorAdapter.LINUX_SYSTEMD_USER,
        install_root_class=bootstrap.InstallRootClass.USER,
        controller_protocol_min="2025-01-01",
        controller_protocol_max="2025-12-31",
    )
    return bootstrap.build_bundle(manifest, runtime, shim)


def _stage(raw: bytes, **changes: object) -> bootstrap.BootstrapReceiverFrame:
    values: dict[str, object] = {
        "operation": bootstrap.ReceiverOperation.STAGE,
        "expected_node": "Node_1",
        "operation_id": OPERATION_ID,
        "plan_sha256": SHA_A,
        "target_config_sha256": SHA_B,
        "bundle_sha256": hashlib.sha256(raw).hexdigest(),
        "bundle_length": len(raw),
    }
    values.update(changes)
    return bootstrap.BootstrapReceiverFrame(**values)  # type: ignore[arg-type]


def _record() -> tuple[bootstrap.BootstrapOperationRecord, bytes, bootstrap.BootstrapReceiverFrame]:
    raw = _bundle()
    frame = _stage(raw)
    return bootstrap.BootstrapOperationRecord.from_stage(frame, raw), raw, frame


def _assert_refusal(code: bootstrap.BootstrapErrorCode, call: object) -> None:
    with pytest.raises(bootstrap.BootstrapContractError) as raised:
        call()  # type: ignore[operator]
    assert raised.value.code == code.value


def test_stage_record_binds_actual_bundle_and_canonical_json() -> None:
    record, raw, frame = _record()

    assert record.phase is bootstrap.BootstrapPhase.STAGED
    assert record.bundle_sha256 == hashlib.sha256(raw).hexdigest()
    assert record.manifest_sha256 == bootstrap.validate_bundle(raw).manifest_sha256
    assert record.to_dict() == {
        "schema": bootstrap.OPERATION_RECORD_SCHEMA,
        "expected_node": "Node_1",
        "operation_id": OPERATION_ID,
        "plan_sha256": SHA_A,
        "target_config_sha256": SHA_B,
        "bundle_sha256": frame.bundle_sha256,
        "bundle_length": len(raw),
        "manifest_sha256": record.manifest_sha256,
        "phase": "staged",
        "error_code": None,
        "trigger_error_code": None,
    }
    encoded = record.to_json_bytes()
    assert bootstrap.BootstrapOperationRecord.from_json_bytes(encoded) == record
    assert json.loads(encoded) == record.to_dict()


def test_legal_transition_and_rollback_provenance_matrix() -> None:
    record, _, _ = _record()
    verified = record.transition(bootstrap.BootstrapPhase.VERIFIED)
    installed = verified.transition(bootstrap.BootstrapPhase.INSTALLED)
    activated = installed.transition(bootstrap.BootstrapPhase.ACTIVATED)
    assert activated.transition(bootstrap.BootstrapPhase.RESTARTED).phase is bootstrap.BootstrapPhase.RESTARTED

    rollback = installed.transition(
        bootstrap.BootstrapPhase.ROLLBACK_STARTED,
        error_code=bootstrap.BootstrapErrorCode.INSTALL_FAILED,
    )
    assert rollback.transition(
        bootstrap.BootstrapPhase.ROLLBACK_STARTED,
        error_code=bootstrap.BootstrapErrorCode.INSTALL_FAILED,
    ) is rollback
    rolled_back = rollback.transition(bootstrap.BootstrapPhase.ROLLED_BACK)
    cleanup = rolled_back.transition(bootstrap.BootstrapPhase.CLEANUP_FAILED)
    assert cleanup.error_code is bootstrap.BootstrapErrorCode.CLEANUP_FAILED
    assert cleanup.trigger_error_code is bootstrap.BootstrapErrorCode.INSTALL_FAILED

    for phase, error in (
        (bootstrap.BootstrapPhase.INSTALLED, None),
        (bootstrap.BootstrapPhase.PLANNED, None),
        (bootstrap.BootstrapPhase.ACCEPTED, None),
        (bootstrap.BootstrapPhase.ROLLBACK_STARTED, None),
        (bootstrap.BootstrapPhase.ROLLBACK_STARTED, bootstrap.BootstrapErrorCode.CLEANUP_FAILED),
    ):
        _assert_refusal(
            bootstrap.BootstrapErrorCode.PRECONDITION_FAILED,
            lambda phase=phase, error=error: record.transition(phase, error_code=error),
        )


def test_rollback_match_retains_original_trigger() -> None:
    record, _, frame = _record()
    cleanup = (
        record.transition(
            bootstrap.BootstrapPhase.ROLLBACK_STARTED,
            error_code=bootstrap.BootstrapErrorCode.ACTIVATION_FAILED,
        )
        .transition(bootstrap.BootstrapPhase.MANUAL_RECOVERY)
        .transition(bootstrap.BootstrapPhase.CLEANUP_FAILED)
    )
    rollback = bootstrap.BootstrapReceiverFrame(
        operation=bootstrap.ReceiverOperation.ROLLBACK,
        expected_node=frame.expected_node,
        operation_id=frame.operation_id,
        plan_sha256=frame.plan_sha256,
        target_config_sha256=frame.target_config_sha256,
        trigger_error_code=bootstrap.BootstrapErrorCode.ACTIVATION_FAILED,
    )
    assert bootstrap.match_operation_record(rollback, cleanup) is cleanup
    _assert_refusal(
        bootstrap.BootstrapErrorCode.RECEIVER_MISMATCH,
        lambda: bootstrap.match_operation_record(
            replace(rollback, trigger_error_code=bootstrap.BootstrapErrorCode.INSTALL_FAILED), cleanup
        ),
    )
