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


def test_manifest_mutation_changes_manifest_and_bundle_identity():
    runtime = wheel_bytes()
    shim = b"fixed shim\n"
    first = manifest(runtime, shim)
    second = replace(first, package_version="1.2.4")
    first_bundle = bootstrap.build_bundle(first, runtime, shim)
    second_bundle = bootstrap.build_bundle(second, runtime, shim)

    assert first.sha256 != second.sha256
    assert hashlib.sha256(first_bundle).digest() != hashlib.sha256(second_bundle).digest()


@pytest.mark.parametrize("platform", ("linux", "windows"))
def test_bootstrap_plan_from_resolved_topology_has_canonical_private_identity(platform):
    execution = bootstrap_execution(platform=platform)
    artifact = plan_manifest(platform=platform)
    first = bootstrap.build_bootstrap_plan(execution, artifact)
    second = bootstrap.BootstrapPlan(execution, artifact)

    assert first == second
    private = {
        "schema": bootstrap.PLAN_SCHEMA,
        "host": first.host,
        "execution_runtime": first.execution_runtime,
        "topology_sha256": first.topology_sha256,
        "manifest_sha256": first.manifest_sha256,
        "expected_node": first.expected_node,
        "platform": first.platform.value,
        "staging_root": first.staging_root,
        "install_root": first.install_root,
        "python_executable": first.python_executable,
        "receiver_path": first.receiver_path,
        "receiver_sha256": first.receiver_sha256,
        "install_adapter": first.install_adapter.value,
        "supervisor_adapter": first.supervisor_adapter.value,
        "install_root_class": first.install_root_class.value,
        "supervisor_id": first.supervisor_id,
        "bootstrap_enabled": True,
        "bootstrap_authorized": True,
        "expected_protocol_version": "2026-07-28",
        "expected_catalog_sha256": first.expected_catalog_sha256,
    }
    expected = json.dumps(
        private, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode()
    assert first.plan_sha256 == hashlib.sha256(expected).hexdigest()


def test_bootstrap_plan_public_projection_and_repr_are_private_value_free():
    value = bootstrap.build_bootstrap_plan(bootstrap_execution(), plan_manifest())
    payload = value.to_dict()
    rendered = json.dumps(payload, sort_keys=True) + repr(value)

    assert set(payload) == {
        "schema",
        "host",
        "topology_sha256",
        "plan_sha256",
        "manifest_sha256",
        "expected_node",
        "platform",
        "install_adapter",
        "supervisor_adapter",
        "expected_protocol_version",
        "expected_catalog_sha256",
    }
    for prohibited in (
        "private-stage",
        "private-install",
        "private-python",
        "private-receiver",
        "SYNTHETIC_BOOTSTRAP_TOKEN",
        "100.64.0.10",
        "bootstrap-controller",
    ):
        assert prohibited not in rendered


def test_bootstrap_plan_is_frozen_and_accepts_no_field_overrides():
    execution = bootstrap_execution()
    artifact = plan_manifest()
    value = bootstrap.BootstrapPlan(execution, artifact)
    with pytest.raises((AttributeError, TypeError)):
        value.host = "other"  # type: ignore[misc]
    with pytest.raises(TypeError):
        bootstrap.BootstrapPlan(execution, artifact, host="other")
    with pytest.raises(TypeError):
        bootstrap.build_bootstrap_plan(execution, artifact, host="other")
    assert not hasattr(bootstrap.BootstrapPlan, "from_dict")


def test_catalog_hash_is_sorted_canonical_json_and_accepts_256_operations():
    operations = (
        "z-operation",
        "controller-bootstrap",
        *(f"operation-{number:03d}" for number in range(254)),
    )
    envelope = {
        "schema": bootstrap.CONTROLLER_OPERATION_CATALOG_SCHEMA,
        "operations": sorted(operations),
    }
    expected = json.dumps(
        envelope, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode()

    assert len(operations) == 256
    assert bootstrap.controller_operation_catalog_sha256(operations) == hashlib.sha256(
        expected
    ).hexdigest()
    assert bootstrap.controller_operation_catalog_sha256(tuple(reversed(operations))) == (
        hashlib.sha256(expected).hexdigest()
    )


@pytest.mark.parametrize(
    "operations",
    (
        (),
        ("other",),
        ("controller-bootstrap", "controller-bootstrap"),
        ("controller-bootstrap", "bad.operation"),
        ["controller-bootstrap"],
        ("controller-bootstrap", *(f"operation-{number:03d}" for number in range(256))),
    ),
)
def test_catalog_hash_rejects_invalid_shape_members_and_bounds(operations):
    with pytest.raises(bootstrap.BootstrapContractError) as caught:
        bootstrap.controller_operation_catalog_sha256(operations)
    assert caught.value.code == "invalid-contract"


def test_bootstrap_plan_catalog_order_is_equivalent_but_membership_changes_identity():
    artifact = plan_manifest()
    first = bootstrap.build_bootstrap_plan(
        bootstrap_execution(operations=("z-operation", "controller-bootstrap")),
        artifact,
    )
    reordered = bootstrap.build_bootstrap_plan(
        bootstrap_execution(operations=("controller-bootstrap", "z-operation")),
        artifact,
    )
    changed = bootstrap.build_bootstrap_plan(
        bootstrap_execution(operations=("controller-bootstrap", "other-operation")),
        artifact,
    )
    assert first.expected_catalog_sha256 == reordered.expected_catalog_sha256
    assert first.topology_sha256 != reordered.topology_sha256
    assert first.plan_sha256 != reordered.plan_sha256
    assert first.plan_sha256 != changed.plan_sha256


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("staging_root", "/var/tmp/other-stage"),
        ("install_root", "/opt/other-install"),
        ("python_executable", "/usr/bin/other-python"),
        ("receiver_path", "/opt/other-receiver/bootstrap.py"),
        ("receiver_sha256", SHA_B),
        ("supervisor_id", "other-controller"),
    ),
)
def test_every_private_topology_value_changes_plan_identity(field, value):
    artifact = plan_manifest()
    original = bootstrap.build_bootstrap_plan(bootstrap_execution(), artifact)
    changed = bootstrap.build_bootstrap_plan(
        bootstrap_execution(bootstrap_changes={field: value}), artifact
    )
    assert original.plan_sha256 != changed.plan_sha256


def test_manifest_artifact_identity_changes_plan_identity():
    execution = bootstrap_execution()
    first = bootstrap.build_bootstrap_plan(execution, plan_manifest())
    changed = bootstrap.build_bootstrap_plan(
        execution, replace(plan_manifest(), runtime_sha256=SHA_B)
    )
    assert first.plan_sha256 != changed.plan_sha256


@pytest.mark.parametrize(
    "artifact",
    (
        replace(plan_manifest(), expected_node="other-node"),
        replace(
            plan_manifest(),
            controller_protocol_min="2025-11-25",
            controller_protocol_max="2026-08-01",
        ),
        replace(
            plan_manifest(),
            platform=bootstrap.BootstrapPlatform.WINDOWS,
            supervisor_adapter=bootstrap.SupervisorAdapter.WINDOWS_SCHEDULED_TASK,
        ),
    ),
)
def test_bootstrap_plan_refuses_manifest_target_protocol_and_platform_mismatch(artifact):
    with pytest.raises(bootstrap.BootstrapContractError) as caught:
        bootstrap.build_bootstrap_plan(bootstrap_execution(), artifact)
    assert caught.value.code == "precondition-failed"


def test_bootstrap_plan_refuses_false_policy_and_unsafe_replaced_path():
    execution = bootstrap_execution()
    denied = replace(
        execution,
        host_bootstrap=replace(execution.host_bootstrap, bootstrap_authorized=False),
        execution_host=replace(
            execution.execution_host,
            bootstrap=replace(execution.host_bootstrap, bootstrap_authorized=False),
        ),
    )
    with pytest.raises(bootstrap.BootstrapContractError) as caught:
        bootstrap.build_bootstrap_plan(denied, plan_manifest())
    assert caught.value.code == "authorization-denied"

    unsafe_bootstrap = replace(execution.host_bootstrap, receiver_path="/opt/../private")
    unsafe = replace(
        execution,
        host_bootstrap=unsafe_bootstrap,
        execution_host=replace(execution.execution_host, bootstrap=unsafe_bootstrap),
    )
    with pytest.raises(bootstrap.BootstrapContractError) as caught:
        bootstrap.build_bootstrap_plan(unsafe, plan_manifest())
    assert caught.value.code == "invalid-contract"


def test_bootstrap_plan_refuses_forged_resource_and_runtime_boundaries():
    execution = bootstrap_execution()
    for changed in (
        replace(execution, resource_endpoint="http://100.64.0.10:9999"),
        replace(execution, selected_target="host:other-node"),
        replace(execution, transport_auth_env=None),
        replace(execution, transport="ssh"),
        replace(execution, execution_runtime=replace(execution.execution_runtime, role="docker")),
    ):
        with pytest.raises(bootstrap.BootstrapContractError) as caught:
            bootstrap.build_bootstrap_plan(changed, plan_manifest())
        assert caught.value.code == "precondition-failed"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("schema", "wrong/v1"),
        ("package_version", "1.2"),
        ("package_version", "1.2.3.dev1"),
        ("source_commit", "A" * 40),
        ("runtime_sha256", "sha256:" + SHA_A),
        ("expected_node", "1node"),
        ("platform", "macos"),
        ("install_adapter", "pip"),
        ("supervisor_adapter", "docker"),
        ("install_root_class", "system"),
        ("controller_protocol_min", "2026-02-30"),
        ("controller_protocol_max", "1"),
    ],
)
def test_manifest_rejects_malformed_fields(field, value):
    raw = manifest().to_dict()
    raw[field] = value
    with pytest.raises(bootstrap.BootstrapContractError, match="invalid"):
        bootstrap.BootstrapManifest.from_dict(raw)


def test_manifest_rejects_unknown_missing_types_pairing_and_reversed_protocol():
    raw = manifest().to_dict()
    for changed in ({**raw, "endpoint": "secret"}, {key: value for key, value in raw.items() if key != "schema"}):
        with pytest.raises(bootstrap.BootstrapContractError):
            bootstrap.BootstrapManifest.from_dict(changed)
    raw["package_version"] = 123
    with pytest.raises(bootstrap.BootstrapContractError):
        bootstrap.BootstrapManifest.from_dict(raw)
    with pytest.raises(bootstrap.BootstrapContractError):
        replace(
            manifest(),
            platform=bootstrap.BootstrapPlatform.WINDOWS,
            supervisor_adapter=bootstrap.SupervisorAdapter.LINUX_SYSTEMD_USER,
        )
    with pytest.raises(bootstrap.BootstrapContractError):
        replace(
            manifest(),
            controller_protocol_min="2026-07-28",
            controller_protocol_max="2025-11-25",
        )


def test_canonical_json_rejects_duplicate_noncanonical_and_nonfinite():
    canonical = manifest().to_json_bytes()
    duplicate = canonical[:-1] + b',"schema":"anvil-serving.fleet-bootstrap-manifest/v1"}'
    for raw in (duplicate, b"{" + canonical[1:-1] + b" }", canonical + b"\n"):
        with pytest.raises(bootstrap.BootstrapContractError):
            bootstrap.BootstrapManifest.from_json_bytes(raw)
    with pytest.raises(bootstrap.BootstrapContractError):
        bootstrap.canonical_json_bytes({"value": float("nan")})
    with pytest.raises(bootstrap.BootstrapContractError):
        bootstrap.BootstrapManifest.from_json_bytes(b'{"x":NaN}')


@pytest.mark.parametrize(
    "phase",
    [
        bootstrap.BootstrapPhase.STAGED,
        bootstrap.BootstrapPhase.VERIFIED,
        bootstrap.BootstrapPhase.INSTALLED,
        bootstrap.BootstrapPhase.ACTIVATED,
        bootstrap.BootstrapPhase.RESTARTED,
    ],
)
def test_pending_receipt_phases_roundtrip(phase):
    value = receipt(
        phase,
        outcome=bootstrap.BootstrapOutcome.PENDING,
        acceptance=bootstrap.AcceptanceStatus.NOT_CHECKED,
        rollback=bootstrap.RollbackStatus.NOT_REQUIRED,
    )
    assert bootstrap.BootstrapReceipt.from_json_bytes(value.to_json_bytes()) == value


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


@pytest.mark.parametrize(
    "changes",
    [
        {"outcome": bootstrap.BootstrapOutcome.ERROR},
        {"operation_id": None},
        {"error_code": bootstrap.BootstrapErrorCode.INTERNAL_ERROR},
        {"trigger_error_code": bootstrap.BootstrapErrorCode.INTERNAL_ERROR},
        {"acceptance": bootstrap.AcceptanceStatus.ACCEPTED},
        {"rollback": bootstrap.RollbackStatus.PENDING},
    ],
)
def test_receipt_matrix_rejects_inconsistent_states(changes):
    value = receipt(
        bootstrap.BootstrapPhase.STAGED,
        outcome=bootstrap.BootstrapOutcome.PENDING,
        acceptance=bootstrap.AcceptanceStatus.NOT_CHECKED,
        rollback=bootstrap.RollbackStatus.NOT_REQUIRED,
    )
    with pytest.raises(bootstrap.BootstrapContractError):
        replace(value, **changes)


def test_cleanup_receipt_requires_trigger_for_prior_failure_and_forbids_recursive_trigger():
    base = receipt(
        bootstrap.BootstrapPhase.CLEANUP_FAILED,
        outcome=bootstrap.BootstrapOutcome.ERROR,
        acceptance=bootstrap.AcceptanceStatus.REJECTED,
        rollback=bootstrap.RollbackStatus.FAILED,
        error=bootstrap.BootstrapErrorCode.CLEANUP_FAILED,
        trigger=bootstrap.BootstrapErrorCode.ROLLBACK_FAILED,
    )
    for trigger in (None, bootstrap.BootstrapErrorCode.CLEANUP_FAILED):
        with pytest.raises(bootstrap.BootstrapContractError):
            replace(base, trigger_error_code=trigger)
    with pytest.raises(bootstrap.BootstrapContractError):
        replace(base, rollback=bootstrap.RollbackStatus.PENDING)
    with pytest.raises(bootstrap.BootstrapContractError):
        receipt(
            bootstrap.BootstrapPhase.MANUAL_RECOVERY,
            outcome=bootstrap.BootstrapOutcome.ERROR,
            acceptance=bootstrap.AcceptanceStatus.REJECTED,
            rollback=bootstrap.RollbackStatus.FAILED,
            error=bootstrap.BootstrapErrorCode.CLEANUP_FAILED,
        )


def test_receipt_early_refusal_allows_only_validated_values_and_safe_fields():
    value = bootstrap.BootstrapReceipt(
        operation_id=None,
        host=None,
        topology_sha256=None,
        plan_sha256=None,
        manifest_sha256=None,
        bundle_sha256=None,
        platform=None,
        install_adapter=None,
        supervisor_adapter=None,
        phase=bootstrap.BootstrapPhase.REFUSED,
        outcome=bootstrap.BootstrapOutcome.ERROR,
        created_at=CREATED,
        updated_at=CREATED,
        acceptance=bootstrap.AcceptanceStatus.NOT_CHECKED,
        rollback=bootstrap.RollbackStatus.NOT_REQUIRED,
        error_code=bootstrap.BootstrapErrorCode.INVALID_CONTRACT,
    )
    payload = value.to_dict()
    assert set(payload) == {
        "schema", "operation_id", "host", "topology_sha256", "plan_sha256",
        "manifest_sha256", "bundle_sha256", "platform", "install_adapter",
        "supervisor_adapter", "phase", "outcome", "created_at", "updated_at",
        "acceptance", "rollback", "error_code", "trigger_error_code",
    }
    assert not ({"endpoint", "path", "command", "message", "context"} & set(payload))
    with pytest.raises(bootstrap.BootstrapContractError):
        bootstrap.BootstrapReceipt.from_dict({**payload, "endpoint": "private"})
    missing = dict(payload)
    missing.pop("host")
    with pytest.raises(bootstrap.BootstrapContractError):
        bootstrap.BootstrapReceipt.from_dict(missing)


@pytest.mark.parametrize(
    "change",
    [
        {"operation_id": OPERATION_ID.upper()},
        {"operation_id": "12345678-1234-1234-9234-123456789abc"},
        {"created_at": "2026-09-05T12:00:00Z"},
        {"updated_at": "2026-09-05T11:00:00.000000Z"},
        {"host": "bad.host"},
        {"topology_sha256": "A" * 64},
    ],
)
def test_receipt_rejects_bad_uuid_timestamp_and_identity(change):
    value = receipt(
        bootstrap.BootstrapPhase.ACCEPTED,
        outcome=bootstrap.BootstrapOutcome.SUCCESS,
        acceptance=bootstrap.AcceptanceStatus.ACCEPTED,
        rollback=bootstrap.RollbackStatus.NOT_REQUIRED,
    )
    with pytest.raises(bootstrap.BootstrapContractError):
        replace(value, **change)


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


def test_bundle_rejects_order_missing_extra_and_duplicate_entries():
    runtime = wheel_bytes()
    shim = b"fixed\n"
    raw = bootstrap.build_bundle(manifest(runtime, shim), runtime, shim)
    bad_orders = [
        ("runtime.whl", "manifest.json", "bootstrap_shim.py"),
        ("manifest.json", "runtime.whl"),
        (*bootstrap.OUTER_BUNDLE_NAMES, "extra"),
        ("manifest.json", "runtime.whl", "runtime.whl"),
    ]
    for order in bad_orders:
        with pytest.raises(bootstrap.BootstrapContractError):
            bootstrap.validate_bundle(rewrite_bundle(raw, order=order))


def test_bundle_and_wheel_reject_encryption_flags():
    runtime = wheel_bytes()
    shim = b"fixed\n"
    raw = bootstrap.build_bundle(manifest(runtime, shim), runtime, shim)
    with pytest.raises(bootstrap.BootstrapContractError):
        bootstrap.validate_bundle(mark_first_entry_encrypted(raw))

    encrypted_wheel = mark_first_entry_encrypted(runtime)
    with pytest.raises(bootstrap.BootstrapContractError):
        bootstrap.build_bundle(
            manifest(encrypted_wheel, shim), encrypted_wheel, shim
        )


@pytest.mark.parametrize(
    "name",
    [
        "../escape.py", "a/../b", "a/./b", "/absolute", "C:/drive", "//server/share",
        "a\\b", "file:stream", "CON", "con.txt", "a/NUL.py", "trailing.",
        "trailing ", "a//b", "control\x00name", "e\u0301.py", "\ud800",
        "pkg/a?.py", "pkg/a*.py", "pkg/a|.py", "pkg/a<.py", "pkg/a>.py", 'pkg/a".py',
        "pkg/COM¹.txt", "pkg/COM².txt", "pkg/COM³.txt",
        "pkg/LPT¹.txt", "pkg/LPT².txt", "pkg/LPT³.txt",
        "CON .txt", "NUL .txt", "COM1 .txt", "LPT1 .txt",
        "pkg/nUl  .txt", "pkg/cOm¹ .py", "pkg/lPt² .py",
    ],
)
def test_archive_path_rejects_cross_platform_hazards_without_echo(name):
    with pytest.raises(bootstrap.BootstrapContractError) as caught:
        bootstrap.validate_archive_path(name)
    assert name not in str(caught.value)
    assert caught.value.__cause__ is None


def test_archive_path_bounds_and_safe_unicode():
    assert bootstrap.validate_archive_path("console .txt").as_posix() == "console .txt"
    assert bootstrap.validate_archive_path("pkg/é.py").as_posix() == "pkg/é.py"
    exact_name = "/".join(["a" * 204] * 5)
    assert len(exact_name.encode()) == bootstrap.MAX_ARCHIVE_NAME_BYTES
    assert bootstrap.validate_archive_path(exact_name).as_posix() == exact_name
    too_long_name = "b" + exact_name
    with pytest.raises(bootstrap.BootstrapContractError):
        bootstrap.validate_archive_path(too_long_name)
    with pytest.raises(bootstrap.BootstrapContractError):
        bootstrap.validate_archive_path("a" * 256)
    components = ["a" * 255] * 5
    with pytest.raises(bootstrap.BootstrapContractError):
        bootstrap.validate_archive_path("/".join(components))


def test_wheel_rejects_casefold_collision_traversal_link_and_unsupported_compression():
    collision = wheel_bytes((("pkg/A.py", b"a"), ("pkg/a.py", b"b")))
    traversal = wheel_bytes((("../escape.py", b"x"),))
    for runtime in (collision, traversal):
        with pytest.raises(bootstrap.BootstrapContractError):
            bootstrap.build_bundle(manifest(runtime), runtime, b"print('fixed')\n")

    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        info = zipfile.ZipInfo("pkg/link")
        info.create_system = 3
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        archive.writestr(info, b"target")
    linked = output.getvalue()
    with pytest.raises(bootstrap.BootstrapContractError):
        bootstrap.build_bundle(manifest(linked), linked, b"print('fixed')\n")

    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        info = zipfile.ZipInfo("pkg/reparse")
        info.create_system = 3
        info.external_attr = ((stat.S_IFREG | 0o644) << 16) | 0x400
        archive.writestr(info, b"target")
    reparse = output.getvalue()
    with pytest.raises(bootstrap.BootstrapContractError):
        bootstrap.build_bundle(manifest(reparse), reparse, b"print('fixed')\n")

    unsupported = wheel_bytes(compression=zipfile.ZIP_BZIP2)
    with pytest.raises(bootstrap.BootstrapContractError):
        bootstrap.build_bundle(manifest(unsupported), unsupported, b"print('fixed')\n")


@pytest.mark.parametrize(
    "entries",
    [
        (("pkg", b"file"), ("pkg/a.py", b"child")),
        (("pkg/a.py", b"child"), ("pkg", b"file")),
        (("PKG", b"file"), ("pkg/A.py", b"child")),
    ],
)
def test_wheel_rejects_file_directory_prefix_collisions_in_any_order(entries):
    runtime = wheel_bytes(entries)
    with pytest.raises(bootstrap.BootstrapContractError, match="collide"):
        bootstrap.build_bundle(manifest(runtime), runtime, b"print('fixed')\n")

    valid = wheel_bytes((("pkg.py", b"file"), ("pkg/a.py", b"child")))
    bootstrap.build_bundle(manifest(valid), valid, b"print('fixed')\n")


def test_wheel_rejects_original_name_truncation_during_build_and_validation():
    shim = b"print('fixed')\n"
    valid = wheel_bytes((("pkg/aZ.py", b"data"),))
    malformed = valid.replace(b"pkg/aZ.py", b"pkg/a\x00.py")
    assert malformed != valid and b"pkg/a\x00.py" in malformed

    with pytest.raises(bootstrap.BootstrapContractError, match="name"):
        bootstrap.build_bundle(manifest(malformed, shim), malformed, shim)

    valid_bundle = bootstrap.build_bundle(manifest(valid, shim), valid, shim)
    malformed_manifest = manifest(malformed, shim)
    malformed_bundle = rewrite_bundle(
        valid_bundle,
        mutate_payload=lambda name, payload: (
            malformed_manifest.to_json_bytes()
            if name == "manifest.json"
            else malformed
            if name == "runtime.whl"
            else payload
        ),
    )
    with pytest.raises(bootstrap.BootstrapContractError, match="name"):
        bootstrap.validate_bundle(malformed_bundle)


def test_malformed_deflate_is_a_fixed_contract_error_without_decoder_details():
    runtime = wheel_bytes((("pkg/data.bin", b"a" * 100),))
    malformed = corrupt_first_compressed_byte(runtime)
    with pytest.raises(bootstrap.BootstrapContractError, match="payload") as caught:
        bootstrap.build_bundle(manifest(malformed), malformed, b"print('fixed')\n")
    assert caught.value.code == "invalid-bundle"
    assert caught.value.__cause__ is None

    shim = b"print('fixed')\n"
    valid_bundle = bootstrap.build_bundle(manifest(runtime, shim), runtime, shim)
    malformed_manifest = manifest(malformed, shim)
    malformed_bundle = rewrite_bundle(
        valid_bundle,
        mutate_payload=lambda name, payload: (
            malformed_manifest.to_json_bytes()
            if name == "manifest.json"
            else malformed
            if name == "runtime.whl"
            else payload
        ),
    )
    with pytest.raises(bootstrap.BootstrapContractError, match="payload") as caught:
        bootstrap.validate_bundle(malformed_bundle)
    assert caught.value.code == "invalid-bundle"
    assert caught.value.__cause__ is None


def test_wheel_declared_expansion_limit_checked_before_payload_reads(monkeypatch):
    runtime = wheel_bytes((("pkg/huge.bin", b"x"),))
    original = zipfile.ZipFile.infolist

    def oversized(self):
        infos = original(self)
        if infos and infos[0].filename == "pkg/huge.bin":
            infos[0].file_size = bootstrap.MAX_WHEEL_EXPANDED_BYTES + 1
        return infos

    monkeypatch.setattr(zipfile.ZipFile, "infolist", oversized)
    with pytest.raises(bootstrap.BootstrapContractError, match="expansion"):
        bootstrap.build_bundle(manifest(runtime), runtime, b"print('fixed')\n")


def test_wheel_entry_count_limit_checked_before_payload_reads(monkeypatch):
    runtime = wheel_bytes()
    original = zipfile.ZipFile.infolist

    def too_many(self):
        infos = original(self)
        if infos and infos[0].filename == "anvil_serving/__init__.py":
            return infos * (bootstrap.MAX_WHEEL_ENTRIES + 1)
        return infos

    monkeypatch.setattr(zipfile.ZipFile, "infolist", too_many)
    with pytest.raises(bootstrap.BootstrapContractError, match="count"):
        bootstrap.build_bundle(manifest(runtime), runtime, b"print('fixed')\n")


def test_real_wheel_expansion_and_entry_count_boundaries():
    at_limit = wheel_bytes((("pkg/blob.bin", b"x" * bootstrap.MAX_WHEEL_EXPANDED_BYTES),))
    bootstrap.build_bundle(manifest(at_limit), at_limit, b"print('fixed')\n")
    above_limit = wheel_bytes(
        (("pkg/blob.bin", b"x" * (bootstrap.MAX_WHEEL_EXPANDED_BYTES + 1)),)
    )
    with pytest.raises(bootstrap.BootstrapContractError, match="expansion"):
        bootstrap.build_bundle(manifest(above_limit), above_limit, b"print('fixed')\n")

    entries = tuple((f"pkg/item-{number}.txt", b"") for number in range(bootstrap.MAX_WHEEL_ENTRIES))
    full = wheel_bytes(entries)
    bootstrap.build_bundle(manifest(full), full, b"print('fixed')\n")
    extra = wheel_bytes((*entries, ("pkg/extra.txt", b"")))
    with pytest.raises(bootstrap.BootstrapContractError):
        bootstrap.build_bundle(manifest(extra), extra, b"print('fixed')\n")


def test_raw_directory_preflight_cannot_be_bypassed_by_forged_entry_count():
    entries = tuple(
        (f"pkg/item-{number}.txt", b"")
        for number in range(bootstrap.MAX_WHEEL_ENTRIES + 1)
    )
    raw = bytearray(wheel_bytes(entries))
    eocd = raw.rfind(b"PK\x05\x06")
    assert eocd >= 0
    struct.pack_into("<H", raw, eocd + 8, 1)
    struct.pack_into("<H", raw, eocd + 10, 1)
    forged = bytes(raw)
    with pytest.raises(bootstrap.BootstrapContractError, match="directory"):
        bootstrap.build_bundle(manifest(forged), forged, b"print('fixed')\n")


def test_shim_size_boundary_and_outer_crc_failure():
    runtime = wheel_bytes()
    at_limit = b"x" * bootstrap.MAX_SHIM_BYTES
    bootstrap.build_bundle(manifest(runtime, at_limit), runtime, at_limit)
    above_limit = at_limit + b"x"
    with pytest.raises(bootstrap.BootstrapContractError, match="shim"):
        bootstrap.build_bundle(manifest(runtime, above_limit), runtime, above_limit)
    with pytest.raises(bootstrap.BootstrapContractError, match="shim"):
        bootstrap.build_bundle(manifest(runtime, b""), runtime, b"")

    shim = b"unique-fixed-shim-payload"
    raw = bootstrap.build_bundle(manifest(runtime, shim), runtime, shim)
    damaged = bytearray(raw)
    offset = damaged.find(shim)
    assert offset >= 0
    damaged[offset] ^= 1
    with pytest.raises(bootstrap.BootstrapContractError, match="payload"):
        bootstrap.validate_bundle(bytes(damaged))


def test_nested_wheel_crc_failure_is_refused():
    runtime = wheel_bytes((("pkg/unique.txt", b"unique-wheel-payload"),), compression=zipfile.ZIP_STORED)
    damaged = bytearray(runtime)
    offset = damaged.find(b"unique-wheel-payload")
    assert offset >= 0
    damaged[offset] ^= 1
    corrupted = bytes(damaged)
    with pytest.raises(bootstrap.BootstrapContractError, match="payload"):
        bootstrap.build_bundle(manifest(corrupted), corrupted, b"print('fixed')\n")


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


def test_preflight_rejects_regular_file_ancestor_and_link_root(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    (root / "file").write_text("not a directory", encoding="utf-8")
    with pytest.raises(bootstrap.BootstrapContractError, match="unsafe"):
        bootstrap.preflight_contained_path(root, "file/child")

    linked_root = tmp_path / "linked-root"
    try:
        linked_root.symlink_to(root, target_is_directory=True)
    except OSError:
        pytest.skip("symlink creation is unavailable")
    with pytest.raises(bootstrap.BootstrapContractError, match="unsafe"):
        bootstrap.preflight_contained_path(linked_root, "child")


def test_error_output_never_echoes_prohibited_input():
    seeded = "https://private.invalid/token-secret"
    with pytest.raises(bootstrap.BootstrapContractError) as caught:
        bootstrap.BootstrapManifest.from_dict({"endpoint": seeded})
    rendered = repr(caught.value) + str(caught.value)
    assert seeded not in rendered
    assert "endpoint" not in rendered


def test_direct_dicts_reject_hostile_schema_and_key_subclasses_without_invocation():
    seeded = "seeded-sensitive-detail"

    class HostileSchema(str):
        def __ne__(self, other):
            raise RuntimeError(seeded)

    class HostileKey(str):
        armed = False

        def __hash__(self):
            if self.armed:
                raise RuntimeError(seeded)
            return super().__hash__()

    accepted = receipt(
        bootstrap.BootstrapPhase.ACCEPTED,
        outcome=bootstrap.BootstrapOutcome.SUCCESS,
        acceptance=bootstrap.AcceptanceStatus.ACCEPTED,
        rollback=bootstrap.RollbackStatus.NOT_REQUIRED,
    )
    for parser, payload in (
        (bootstrap.BootstrapManifest.from_dict, manifest().to_dict()),
        (bootstrap.BootstrapReceipt.from_dict, accepted.to_dict()),
    ):
        hostile_schema = dict(payload)
        hostile_schema["schema"] = HostileSchema(hostile_schema["schema"])
        with pytest.raises(bootstrap.BootstrapContractError) as caught:
            parser(hostile_schema)
        assert seeded not in str(caught.value)

        hostile_key = HostileKey("schema")
        hostile_fields = dict(payload)
        schema = hostile_fields.pop("schema")
        hostile_fields[hostile_key] = schema
        hostile_key.armed = True
        with pytest.raises(bootstrap.BootstrapContractError) as caught:
            parser(hostile_fields)
        assert seeded not in str(caught.value)
