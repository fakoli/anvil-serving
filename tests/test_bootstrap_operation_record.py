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


@pytest.mark.parametrize("field", ("expected_node", "operation_id", "plan_sha256", "target_config_sha256"))
def test_retry_identity_mismatches_refuse(field: str) -> None:
    record, raw, frame = _record()
    values = frame.to_dict()
    values[field] = "Node_2" if field == "expected_node" else "d" * 64
    if field == "operation_id":
        values[field] = "12345678-1234-4234-9234-123456789abd"
    changed = bootstrap.BootstrapReceiverFrame.from_dict(values)

    _assert_refusal(
        bootstrap.BootstrapErrorCode.RECEIVER_MISMATCH,
        lambda: bootstrap.match_operation_record(changed, record),
    )
    assert raw


def test_stage_retry_binds_artifact_even_after_advance() -> None:
    record, raw, frame = _record()
    advanced = record.transition(bootstrap.BootstrapPhase.VERIFIED)
    assert bootstrap.match_operation_record(frame, advanced) is advanced
    changed = replace(frame, bundle_sha256="d" * 64)
    _assert_refusal(
        bootstrap.BootstrapErrorCode.RECEIVER_MISMATCH,
        lambda: bootstrap.match_operation_record(changed, advanced),
    )
    assert raw


def test_from_stage_reuses_frame_and_bundle_validation() -> None:
    raw = _bundle(node="Node_2")
    mismatch = _stage(raw)
    _assert_refusal(
        bootstrap.BootstrapErrorCode.RECEIVER_MISMATCH,
        lambda: bootstrap.BootstrapOperationRecord.from_stage(mismatch, raw),
    )
    valid = _bundle()
    frame = _stage(valid)
    _assert_refusal(
        bootstrap.BootstrapErrorCode.DIGEST_MISMATCH,
        lambda: bootstrap.BootstrapOperationRecord.from_stage(
            replace(frame, bundle_sha256="d" * 64), valid
        ),
    )


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


def test_response_projection_uses_existing_receiver_state_contract() -> None:
    record, _, frame = _record()
    staged = record.to_receiver_result(frame)
    assert staged.operation is bootstrap.ReceiverOperation.STAGE
    assert staged.phase is bootstrap.BootstrapPhase.STAGED
    activate = bootstrap.BootstrapReceiverFrame(
        operation=bootstrap.ReceiverOperation.ACTIVATE,
        expected_node=frame.expected_node,
        operation_id=frame.operation_id,
        plan_sha256=frame.plan_sha256,
        target_config_sha256=frame.target_config_sha256,
    )
    _assert_refusal(
        bootstrap.BootstrapErrorCode.PRECONDITION_FAILED,
        lambda: record.to_receiver_result(activate),
    )
    rollback = bootstrap.BootstrapReceiverFrame(
        operation=bootstrap.ReceiverOperation.ROLLBACK,
        expected_node=frame.expected_node,
        operation_id=frame.operation_id,
        plan_sha256=frame.plan_sha256,
        target_config_sha256=frame.target_config_sha256,
        trigger_error_code=bootstrap.BootstrapErrorCode.INSTALL_FAILED,
    )
    _assert_refusal(
        bootstrap.BootstrapErrorCode.PRECONDITION_FAILED,
        lambda: record.to_receiver_result(rollback),
    )


@pytest.mark.parametrize(
    "raw",
    (
        b"",
        b'{"schema":"anvil-serving.fleet-bootstrap-operation/v1"}',
        b'{"schema":"anvil-serving.fleet-bootstrap-operation/v1","schema":"x"}',
        b"{" + b"x" * bootstrap.MAX_RECEIVER_RESULT_BYTES,
    ),
)
def test_record_json_is_exact_bounded_and_duplicate_free(raw: bytes) -> None:
    _assert_refusal(
        bootstrap.BootstrapErrorCode.INVALID_CONTRACT,
        lambda: bootstrap.BootstrapOperationRecord.from_json_bytes(raw),
    )


def test_record_revalidates_tampering_and_subclasses_without_echoing_values() -> None:
    record, _, _ = _record()
    object.__setattr__(record, "bundle_length", True)
    _assert_refusal(bootstrap.BootstrapErrorCode.INVALID_CONTRACT, record.to_dict)

    class Derived(bootstrap.BootstrapOperationRecord):
        pass

    valid, _, _ = _record()
    _assert_refusal(
        bootstrap.BootstrapErrorCode.INVALID_CONTRACT,
        lambda: Derived(
            expected_node=valid.expected_node,
            operation_id=valid.operation_id,
            plan_sha256=valid.plan_sha256,
            target_config_sha256=valid.target_config_sha256,
            bundle_sha256=valid.bundle_sha256,
            bundle_length=valid.bundle_length,
            manifest_sha256=valid.manifest_sha256,
            phase=valid.phase,
            error_code=valid.error_code,
            trigger_error_code=valid.trigger_error_code,
        ),
    )
    with pytest.raises(bootstrap.BootstrapContractError) as raised:
        repr(record)
    assert "True" not in str(raised.value)
