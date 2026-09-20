"""Extend one enrolled connector's resource set through the managed surface.

The deployment manifest is validated as a closed whole and activations swap
the rendered tree atomically, so a connector's resource set cannot be widened
by the ordinary up/down surface alone: the gateway must learn the resource
before the connector can be re-enrolled, while the connector refuses to start
on a resource set it has not enrolled. `extend` performs the proven sequence
as one managed operation — gateway-scoped activation of the new generation,
native admin revoke/invite, managed bundle redemption and fingerprint
approval, then the connector-only restart — inside the deployment lock with
rollback of the rendered tree and gateway units.

Invitation material lives only in role-owned 0700 state directories and is
deleted by `init` on redemption. Principal resource lists are deliberately
NOT extended here: `human-set` remains an operator decision per principal
(see the resource-extension ticket for the related gap).
"""

from __future__ import annotations

import json
import os
import secrets
import stat
from pathlib import Path
from typing import Any

from . import manage
from .manage import (
    ManageError,
    Target,
    _Activation,
    _MAX_OUTPUT,
    _SYSTEMCTL,
    _SYSTEMD_TIMEOUT,
    _bound_active,
    _make_public,
    _read_regular,
    _require_supported_platform,
    _target_units,
    _unit_metadata,
    _unit_sources,
    _verify_owned_tree,
    _write_activation_record,
    _write_atomic,
    plan,
    read_manifest,
    render_config,
    require_isolated,
    stage,
)

_CONNECTOR_RESOURCE_LIMIT = 64
_RECOVERY_SCHEMA = "anvil-connect.extend-recovery/v1"
_RECOVERY_PHASES = {
    "revoke-pending", "revoked", "invite-pending", "invited", "re-enroll-pending", "init-pending",
    "redeemed", "approve-pending", "approved",
}


class ExtendError(ManageError):
    """The declared extension is not usable; `may_have_executed` when a phase ran."""

    def __init__(self, message: str, *, may_have_executed: bool = False, plan: dict[str, Any] | None = None) -> None:
        super().__init__(message, may_have_executed=may_have_executed)
        self.plan = plan


def _resource_ids(entries: Any) -> set[str]:
    """Collect resource IDs across the three recorded entry shapes.

    Rendered connector files wrap the rule in an envelope; gateway files wrap
    it directly; some records carry a flat id. All three are accepted.
    """
    ids: set[str] = set()
    for entry in entries or ():
        if not isinstance(entry, dict):
            continue
        for candidate in (
            entry.get("rule"),
            (entry.get("envelope") or {}).get("rule") if isinstance(entry.get("envelope"), dict) else None,
            entry,
        ):
            if isinstance(candidate, dict) and isinstance(candidate.get("id"), str):
                ids.add(candidate["id"])
                break
    return ids


def _rendered_connector_ids(data: dict[str, Any], connector_id: str) -> set[str]:
    root = Path(data["config_root"])
    path = root / "connectors" / f"{connector_id}.json"
    value = json.loads(_read_regular(path, _MAX_OUTPUT) or b"{}")
    return _resource_ids(value.get("resources"))


def _rendered_gateway_ids(data: dict[str, Any]) -> set[str]:
    root = Path(data["config_root"])
    value = json.loads(_read_regular(root / "gateway.json", _MAX_OUTPUT) or b"{}")
    return _resource_ids(value.get("gateway", {}).get("resources"))


def _connector_state_dir(data: dict[str, Any], connector_id: str) -> Path:
    for connector in data.get("connectors", ()):
        if isinstance(connector, dict) and connector.get("id") == connector_id:
            return Path(connector["state_directory"])
    raise ExtendError(f"connector is not declared: {connector_id}")


def _gateway_state_dir(data: dict[str, Any]) -> Path:
    return Path(data["gateway"]["state_directory"])


def _sidecar_root(data: dict[str, Any], *, create: bool = False) -> Path:
    """Return the manager-owned sidecar for transient extension state.

    Gateway and connector state directories are writable by their service
    users.  The manager keeps recovery records outside those directories and
    only hands exact, nonce-named files to a role through its own subdirectory.
    """
    root = Path(data["config_root"])
    manage._safe_dir(root.parent)
    sidecar = root.parent / ("." + root.name + ".anvil-connect-extend")
    if create:
        sidecar.mkdir(mode=0o711, exist_ok=True)
    try:
        info = sidecar.lstat()
    except FileNotFoundError:
        return sidecar
    if (not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode)
            or info.st_uid != os.geteuid() or info.st_mode & 0o022):
        raise ExtendError("extension sidecar directory is unsafe")
    os.chmod(sidecar, 0o711)
    return sidecar


def _recovery_path(data: dict[str, Any], connector_id: str, *, create: bool = False) -> Path:
    return _sidecar_root(data, create=create) / (connector_id + ".recovery.json")


def _role_identity(data: dict[str, Any], role: str, identifier: str | None = None) -> Any:
    return manage._role_service_identity(data, role, identifier)


def _connector_handoff_needs_migration(
    observed: tuple[int, int, int], target: tuple[int, int, int], manager: tuple[int, int], *, created: bool,
) -> bool:
    """Accept only the final or descriptor-recoverable connector sidecar states."""
    if observed == target:
        return False
    manager_uid, manager_gid = manager
    _, target_gid, _ = target
    legacy = (manager_uid, target_gid, 0o730)
    role_intermediate = (manager_uid, target_gid, 0o700)
    fresh_intermediate = (manager_uid, manager_gid, 0o700)
    if observed in {legacy, role_intermediate, fresh_intermediate}:
        return True
    # Freshly-created directories may still reflect umask before this
    # descriptor has published their exact handoff mode.
    if created and observed[0] == manager_uid:
        return True
    raise ExtendError("extension connector sidecar is unsafe")


def _role_directory(data: dict[str, Any], role: str, identifier: str | None = None) -> tuple[Path, Any]:
    """Return the bounded handoff directory for an exact service role.

    Gateway requests remain manager-owned: the native admin reader accepts a
    root-owned declaration through the role group.  Connector bundle inputs
    are read by the native private-file reader, which intentionally requires
    an exact role-owned 0700 parent.  Migrate only the legacy connector
    handoff directory (root:role 0730) in place, so a retained invitation is
    still usable after a manager upgrade.
    """
    identity = _role_identity(data, role, identifier)
    suffix = role if identifier is None else role + "-" + identifier
    directory = _sidecar_root(data, create=True) / suffix
    if role != "connector":
        directory.mkdir(mode=0o730, exist_ok=True)
        info = directory.lstat()
        if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode) or info.st_uid != os.geteuid():
            raise ExtendError("extension role sidecar is unsafe")
        if identity is not None:
            os.chown(directory, os.geteuid(), identity.gid)
        os.chmod(directory, 0o730)
        return directory, identity

    created = False
    try:
        directory.mkdir(mode=0o700)
        created = True
    except FileExistsError:
        pass
    descriptor = None
    try:
        descriptor = os.open(directory, os.O_RDONLY | os.O_CLOEXEC | os.O_DIRECTORY | os.O_NOFOLLOW)
        info = os.fstat(descriptor)
        if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode):
            raise ExtendError("extension connector sidecar is unsafe")
        target_uid = identity.uid if identity is not None else os.geteuid()
        target_gid = identity.gid if identity is not None else os.getegid()
        target = (target_uid, target_gid, 0o700)
        observed = (info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode))
        needs_migration = _connector_handoff_needs_migration(
            observed, target, (os.geteuid(), os.getegid()), created=created,
        )
        if not needs_migration:
            return directory, identity
        # The descriptor keeps the selected directory pinned.  First close the
        # manager-owned legacy directory to 0700, then hand it to the role.
        # A failure between those steps leaves a closed manager-owned 0700
        # intermediate, which a later managed retry can finish without losing
        # its retained invitation.
        if observed[2] != 0o700:
            os.fchmod(descriptor, 0o700)
        os.fchown(descriptor, target_uid, target_gid)
        final = os.fstat(descriptor)
        if (final.st_uid, final.st_gid, stat.S_IMODE(final.st_mode)) != target:
            raise ExtendError("extension connector sidecar is unsafe")
        return directory, identity
    except OSError as exc:
        raise ExtendError("extension connector sidecar is unsafe") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _write_handoff(directory: Path, name: str, data: bytes, identity: Any) -> Path:
    """Publish a bounded role-readable file only after descriptor ownership is set."""
    if not name or "/" in name or len(data) > 32 * 1024:
        raise ExtendError("extension sidecar file is invalid")
    descriptor = os.open(directory, os.O_RDONLY | os.O_CLOEXEC | os.O_DIRECTORY | os.O_NOFOLLOW)
    temporary = "." + name + "." + secrets.token_hex(16) + ".new"
    file_descriptor: int | None = None
    try:
        file_descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW,
            0o600,
            dir_fd=descriptor,
        )
        os.fchmod(file_descriptor, 0o600)
        if identity is not None:
            os.fchown(file_descriptor, identity.uid, identity.gid)
        offset = 0
        while offset < len(data):
            written = os.write(file_descriptor, data[offset:])
            if written <= 0:
                raise OSError("short sidecar write")
            offset += written
        os.fsync(file_descriptor)
        os.close(file_descriptor)
        file_descriptor = None
        try:
            os.link(temporary, name, src_dir_fd=descriptor, dst_dir_fd=descriptor, follow_symlinks=False)
        except FileExistsError as exc:
            raise ExtendError("extension sidecar file already exists") from exc
        os.unlink(temporary, dir_fd=descriptor)
    except OSError as exc:
        raise ExtendError("extension sidecar file write failed") from exc
    finally:
        if file_descriptor is not None:
            os.close(file_descriptor)
        try:
            os.unlink(temporary, dir_fd=descriptor)
        except FileNotFoundError:
            pass
        os.close(descriptor)
    return directory / name


def _read_role_file(path: Path, identity: Any) -> bytes | None:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return None
    if (not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode)
            or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1 or info.st_size > 32 * 1024
            or (identity is not None and info.st_uid != identity.uid)):
        raise ExtendError("extension sidecar response is unsafe")
    return _read_regular(path, 32 * 1024)


def _closed_installation_status(value: Any, connector_id: str) -> dict[str, Any]:
    fields = {"id", "status", "fingerprint", "epoch", "generation", "resources"}
    if (not isinstance(value, dict) or set(value) != fields
            or value.get("id") != connector_id or value.get("status") not in {"active", "invited", "pending", "revoked"}
            or not isinstance(value.get("epoch"), str) or len(value["epoch"]) != 64
            or any(char not in "0123456789abcdef" for char in value["epoch"])
            or type(value.get("generation")) is not int or not 1 <= value["generation"] <= (2**64 - 1)
            or not isinstance(value.get("resources"), list) or value["resources"] != sorted(set(value["resources"]))
            or any(not isinstance(item, str) or manage._ID.fullmatch(item) is None for item in value["resources"])
            or not isinstance(value.get("fingerprint"), str)):
        raise ExtendError("installation recovery status is invalid")
    fingerprint = value["fingerprint"]
    if value["status"] in {"active", "pending"}:
        if (len(fingerprint) != 43
                or any(char not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_" for char in fingerprint)):
            raise ExtendError("installation recovery status is invalid")
    elif value["status"] == "revoked":
        # Revoke preserves an active fingerprint, while revoke of an invited
        # generation preserves that generation's empty fingerprint.
        if fingerprint and (len(fingerprint) != 43
                            or any(char not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_" for char in fingerprint)):
            raise ExtendError("installation recovery status is invalid")
    elif fingerprint:
        raise ExtendError("installation recovery status is invalid")
    return value


def _gateway_output_directory(data: dict[str, Any], identity: Any) -> Path:
    """Validate the native owner's exact private output directory contract."""
    directory = _gateway_state_dir(data)
    try:
        info = directory.lstat()
    except OSError as exc:
        raise ExtendError("gateway private output directory is unavailable") from exc
    expected_uid = identity.uid if identity is not None else os.geteuid()
    if (not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode)
            or info.st_uid != expected_uid or stat.S_IMODE(info.st_mode) != 0o700):
        raise ExtendError("gateway private output directory is unsafe")
    return directory


def _write_recovery(
    data: dict[str, Any], connector_id: str, declared: list[str], phase: str, bundle: str | None = None,
    prior: dict[str, Any] | None = None, generation: int | None = None, fingerprint: str = "",
) -> None:
    """Persist the non-secret enrollment phase before crossing an authority boundary."""
    if phase not in _RECOVERY_PHASES:
        raise ExtendError("extension recovery phase is invalid")
    if bundle is not None and (not bundle.startswith("invitation-") or not bundle.endswith(".json") or len(bundle) > 128):
        raise ExtendError("extension recovery bundle is invalid")
    if prior is None or generation is None:
        raise ExtendError("extension recovery binding is unavailable")
    prior = _closed_installation_status(prior, connector_id)
    if type(generation) is not int or not 1 <= generation <= (2**64 - 1):
        raise ExtendError("extension recovery generation is invalid")
    path = _recovery_path(data, connector_id, create=True)
    _write_atomic(path, (json.dumps({
        "schema": _RECOVERY_SCHEMA,
        "connector": connector_id,
        "resources": declared,
        "phase": phase,
        "bundle": bundle,
        "prior": prior,
        "generation": generation,
        "fingerprint": fingerprint,
    }, sort_keys=True) + "\n").encode("utf-8"), mode=0o600)


def _read_recovery(data: dict[str, Any], connector_id: str, declared: list[str]) -> dict[str, Any] | None:
    """Return one exact retained recovery phase, rejecting config drift or junk."""
    raw = _read_regular(_recovery_path(data, connector_id), 16 * 1024)
    if raw is None:
        return None
    value = manage._strict_json(raw, "extension recovery state is invalid")
    if (set(value) != {"schema", "connector", "resources", "phase", "bundle", "prior", "generation", "fingerprint"}
            or value.get("schema") != _RECOVERY_SCHEMA
            or value.get("connector") != connector_id
            or value.get("resources") != declared
            or value.get("phase") not in _RECOVERY_PHASES
            or (value.get("bundle") is not None and (
                not isinstance(value.get("bundle"), str)
                or not value["bundle"].startswith("invitation-")
                or not value["bundle"].endswith(".json")
                or len(value["bundle"]) > 128))
            or type(value.get("generation")) is not int or not 1 <= value["generation"] <= (2**64 - 1)):
        raise ExtendError("extension recovery state does not match this declaration")
    _closed_installation_status(value.get("prior"), connector_id)
    if (not isinstance(value.get("fingerprint"), str)
            or (value["fingerprint"] and (len(value["fingerprint"]) != 43
                or any(char not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_" for char in value["fingerprint"])) )
            or (value["phase"] in {"redeemed", "approve-pending", "approved"} and not value["fingerprint"])):
        raise ExtendError("extension recovery state does not match this declaration")
    return value


def _clear_recovery(data: dict[str, Any], connector_id: str, bundle: str | None = None) -> None:
    if bundle is not None:
        directory, _ = _role_directory(data, "connector", connector_id)
        try:
            (directory / bundle).unlink()
        except FileNotFoundError:
            pass
    try:
        _recovery_path(data, connector_id).unlink()
    except FileNotFoundError:
        pass


def _retained_bundle(data: dict[str, Any], connector_id: str, name: str | None) -> Path | None:
    if name is None:
        return None
    directory, identity = _role_directory(data, "connector", connector_id)
    path = directory / name
    return path if _read_role_file(path, identity) is not None else None


def _reenrollment_prior(data: dict[str, Any], connector_id: str, prior: dict[str, Any]) -> Path:
    """Hand the native owner the exact non-secret local identity it must replace."""
    identity = _role_identity(data, "connector", connector_id)
    directory, _ = _role_directory(data, "connector", connector_id)
    value = _closed_installation_status(prior, connector_id)
    payload = {
        "id": value["id"], "fingerprint": value["fingerprint"], "epoch": value["epoch"],
        "generation": value["generation"], "resources": value["resources"],
    }
    return _write_handoff(
        directory, "prior-" + secrets.token_hex(16) + ".json",
        (json.dumps(payload, sort_keys=True) + "\n").encode("utf-8"), identity,
    )


def _stop_connector(runner: Any, connector_unit: str) -> None:
    """Stop the native state owner before it atomically replaces its identity."""
    manage._action(runner, (_SYSTEMCTL, "stop", connector_unit), _SYSTEMD_TIMEOUT, "managed connector stop failed")
    if manage._unit_state(runner, connector_unit)[0]:
        raise ExtendError("managed connector did not stop before re-enrollment")


def _start_connector(runner: Any, connector_unit: str) -> None:
    """Resume a stopped connector without changing its enablement policy."""
    manage._action(runner, (_SYSTEMCTL, "start", connector_unit), _SYSTEMD_TIMEOUT, "managed connector start failed")


def extend_plan(data: dict[str, Any], target: Target) -> dict[str, Any]:
    """Read-only comparison of the enrolled connector with its declaration."""
    if target.kind != "connector":
        raise ExtendError("extend targets one connector; pass --service connector:<id>")
    connector_id = target.name or ""
    declared_ids = _resource_ids(
        [
            resource.get("envelope", resource)
            for resource in next(
                (c for c in data.get("connectors", ()) if isinstance(c, dict) and c.get("id") == connector_id),
                {},
            ).get("resources", ())
        ]
    )
    if not declared_ids:
        raise ExtendError(f"connector declares no resources: {connector_id}")
    if len(declared_ids) > _CONNECTOR_RESOURCE_LIMIT:
        raise ExtendError("connector resource sets are limited to 64 entries")
    enrolled = _rendered_connector_ids(data, connector_id)
    gateway_known = _rendered_gateway_ids(data)
    added = sorted(declared_ids - enrolled)
    removed = sorted(enrolled - declared_ids)
    gateway_missing = sorted(declared_ids - gateway_known)
    report: dict[str, Any] = {
        "schema": "anvil-connect.extend-plan/v1",
        "target": target.text(),
        "declared": sorted(declared_ids),
        "enrolled": sorted(enrolled),
        "added": added,
        "removed": removed,
        "gateway_missing": gateway_missing,
        "extendable": bool(added) and not removed,
    }
    if removed:
        report["removal_guidance"] = (
            "resource removal changes trust scope; use installation-revoke plus a "
            "full re-declaration instead of extend"
        )
    return report


def _extend_activate_gateway(
    data: dict[str, Any],
    stage_path: Path,
    digests: dict[str, str],
    runner: Any,
    system_root: Path,
    unit_root: Path | None = None,
) -> _Activation:
    """Swap to the new generation but restart only gateway units.

    The ordinary activation refuses this shape because the connector's
    rendered file also changes; extend is the purpose-built exception whose
    connector compensation runs in phase 2. The sequence mirrors _activate.
    """
    root = Path(data["config_root"])
    _verify_owned_tree(stage_path, strict=False)
    old_generation, _ = _verify_owned_tree(root)
    prior_record_bytes = _read_regular(manage._activation_record(root), _MAX_OUTPUT)
    _bound_active(data, Target("gateway"), digests, allow_unpinned_gateway=True)
    desired_sources = _unit_sources(stage_path)
    gateway_units = tuple(
        unit for unit in _target_units((Target("gateway"),)) if unit in desired_sources
    )
    unit_root = unit_root or Path("/etc/systemd/system")
    for unit in gateway_units:
        _unit_metadata(runner, unit_root, (unit,), present=(unit_root / unit).exists())
    prior_units = {unit: _read_regular(unit_root / unit, _MAX_OUTPUT) for unit in gateway_units}
    transaction = _Activation(root, unit_root, prior_units, prior_record_bytes, None, False)
    try:
        backup = Path(__import__("tempfile").mkdtemp(prefix="." + root.name + ".anvil-connect-rollback-", dir=root.parent))
        os.rmdir(backup)
        transaction.backup = backup
        os.replace(root, backup)
        transaction.root_moved = True
        _make_public(stage_path)
        os.replace(stage_path, root)
        transaction.new_root = True
        for unit in gateway_units:
            _write_atomic(unit_root / unit, desired_sources[unit])
        _fail_run(runner, "systemd did not accept the owned generation")
        for unit in gateway_units:
            _unit_metadata(runner, unit_root, (unit,), present=True)
        transaction.pending_record = {
            "schema": "anvil-connect.activation/v1",
            "generation": render_config(data)["generation"],
            "native_sha256": digests["native"],
            "components": {key: digests[key] for key in ("caddy", "authelia")},
        }
        _unused(old_generation)
        return transaction
    except Exception as exc:
        try:
            transaction.rollback(runner)
        except ManageError as rollback_error:
            raise rollback_error from exc
        raise ExtendError(f"gateway-scoped activation failed: {exc}", may_have_executed=True) from exc


def _fail_run(runner: Any, message: str) -> None:
    manage._fail(manage._run(runner, (_SYSTEMCTL, "daemon-reload"), _SYSTEMD_TIMEOUT), message)


def _unused(*_: Any) -> None:
    """Keep the ownership-proof read: the value is validated by _verify_owned_tree."""
    return None


def _admin_exchange(
    data: dict[str, Any],
    manifest_path: str | Path,
    request: dict[str, Any],
    *,
    output_name: str | None,
    runner: Any,
) -> Any:
    """Run one managed admin operation through a manager-owned sidecar."""
    gateway_dir, identity = _role_directory(data, "gateway")
    suffix = secrets.token_hex(16)
    request_bytes = (json.dumps(request, sort_keys=True) + "\n").encode("utf-8")
    if len(request_bytes) > 32 * 1024:
        raise ExtendError("administrative extension request exceeds its bound")
    request_path = _write_handoff(gateway_dir, "request-" + suffix + ".json", request_bytes, identity)
    output_path = (_gateway_output_directory(data, identity) / ("extend-response-" + suffix + ".json")
                   if output_name else None)
    value = None
    try:
        result = manage.admin(
            manifest_path,
            request_path=request_path,
            output_path=output_path,
            apply=True,
            runner=runner,
        )
        if output_path is not None:
            raw = _read_role_file(output_path, identity)
            if raw is None:
                raise ExtendError("administrative invitation response is unavailable")
            value = manage._strict_json(raw, "administrative invitation response is invalid")
    finally:
        try:
            request_path.unlink()
        except FileNotFoundError:
            pass
        if output_path is not None:
            try:
                output_path.unlink()
            except FileNotFoundError:
                pass
    return value if value is not None else result


def _revoke(data: dict[str, Any], manifest_path: str | Path, connector_id: str, runner: Any) -> None:
    """Revoke one installation. The caller persists recovery immediately after it returns."""
    _admin_exchange(data, manifest_path, {
        "operation": "installation-revoke", "installation": connector_id,
    }, output_name=None, runner=runner)


def _invite(
    data: dict[str, Any], manifest_path: str | Path, connector_id: str, declared: list[str], runner: Any
) -> Path:
    """Issue and retain one private bundle for a connector already marked revoked."""
    response = _admin_exchange(data, manifest_path, {
        "operation": "invite",
        "installation": connector_id,
        "role": "connector",
        "resources": declared,
        "lifetime_seconds": 600,
    }, output_name="invitation", runner=runner)
    bundle_directory, identity = _role_directory(data, "connector", connector_id)
    bundle_name = "invitation-" + secrets.token_hex(16) + ".json"
    encoded = (json.dumps(response, sort_keys=True) + "\n").encode("utf-8")
    if len(encoded) > 32 * 1024:
        raise ExtendError("administrative invitation response exceeds its bound")
    return _write_handoff(bundle_directory, bundle_name, encoded, identity)


def _connector_identity(manifest_path: str | Path, target: Target, runner: Any) -> dict[str, Any]:
    """Extract the closed native identity payload returned by ``manage.identity``."""
    identity = manage.identity(manifest_path, target, runner=runner)
    payload = identity.get("identity") if isinstance(identity, dict) else None
    if not isinstance(payload, dict):
        raise ExtendError("connector identity did not report a fingerprint after redemption")
    fingerprint = payload.get("fingerprint")
    if not isinstance(fingerprint, str) or not fingerprint:
        raise ExtendError("connector identity did not report a fingerprint after redemption")
    return payload


def _identity_fingerprint(manifest_path: str | Path, target: Target, runner: Any) -> str:
    return str(_connector_identity(manifest_path, target, runner)["fingerprint"])


def _installation_status(
    data: dict[str, Any], manifest_path: str | Path, connector_id: str, runner: Any,
) -> dict[str, Any]:
    """Read one closed installation status without retaining authority output."""
    response = _admin_exchange(data, manifest_path, {
        "operation": "installation-status", "installation": connector_id,
    }, output_name="status", runner=runner)
    status = response.get("status") if isinstance(response, dict) else None
    return _closed_installation_status(status, connector_id)


def _resolve_recovery_phase(
    data: dict[str, Any], manifest_path: str | Path, connector_id: str, declared: list[str], runner: Any,
) -> dict[str, Any]:
    """Resolve only an ambiguous authority outcome before replaying a command."""
    recovery = _read_recovery(data, connector_id, declared)
    if recovery is None:
        raise ExtendError("extension recovery state is unavailable")
    phase = recovery["phase"]
    status = _installation_status(data, manifest_path, connector_id, runner)
    prior, generation, fingerprint = recovery["prior"], recovery["generation"], recovery["fingerprint"]

    def exact(expected_status: str, expected_resources: list[str], expected_generation: int,
              expected_fingerprint: str) -> bool:
        return (
            status["status"] == expected_status
            and status["epoch"] == prior["epoch"]
            and status["generation"] == expected_generation
            and status["resources"] == expected_resources
            and status["fingerprint"] == expected_fingerprint
        )

    if phase == "revoked":
        if not exact("revoked", prior["resources"] if fingerprint else declared, generation, fingerprint):
            raise ExtendError("installation status cannot prove the retained revoke outcome")
        return recovery
    if phase in {"invited", "re-enroll-pending"}:
        if not exact("invited", declared, generation, ""):
            raise ExtendError("installation status cannot prove the retained invitation outcome")
        return recovery
    if phase == "init-pending":
        # This phase is deliberately resolved in _resume_enrollment: an
        # invited record permits one safe replacement after local identity
        # fails, while a pending record must agree with that local identity.
        if (exact("invited", declared, generation, "")
                or (status["status"] == "pending" and status["epoch"] == prior["epoch"]
                    and status["generation"] == generation and status["resources"] == declared)):
            return recovery
        raise ExtendError("installation status cannot prove the retained initialization outcome")
    if phase == "redeemed":
        if not exact("pending", declared, generation, fingerprint):
            raise ExtendError("installation status cannot prove the retained native enrollment")
        return recovery
    if phase == "approved":
        if not exact("active", declared, generation, fingerprint):
            raise ExtendError("installation status cannot prove the retained approval")
        return recovery

    if phase == "revoke-pending":
        if status == prior:
            _revoke(data, manifest_path, connector_id, runner)
            status = _installation_status(data, manifest_path, connector_id, runner)
            if not exact("revoked", prior["resources"], generation + 1, fingerprint):
                raise ExtendError("installation status cannot prove the retained revoke outcome")
            _write_recovery(data, connector_id, declared, "revoked", prior=prior, generation=status["generation"], fingerprint=fingerprint)
        elif exact("revoked", prior["resources"], generation + 1, fingerprint):
            _write_recovery(data, connector_id, declared, "revoked", prior=prior, generation=status["generation"], fingerprint=fingerprint)
        else:
            raise ExtendError("installation status cannot prove the retained revoke outcome")
    elif phase == "invite-pending":
        if exact("revoked", prior["resources"], generation, fingerprint):
            _write_recovery(data, connector_id, declared, "revoked", prior=prior, generation=generation, fingerprint=fingerprint)
        elif exact("invited", declared, generation + 1, ""):
            # The bearer response was lost. Revoke this bounded generation and
            # issue one fresh invitation; never guess or replay a bearer.
            _revoke(data, manifest_path, connector_id, runner)
            status = _installation_status(data, manifest_path, connector_id, runner)
            if not exact("revoked", declared, generation + 2, ""):
                raise ExtendError("installation status cannot prove the retained invitation outcome")
            _write_recovery(data, connector_id, declared, "revoked", prior=prior, generation=status["generation"], fingerprint="")
        elif status["status"] == "pending" and exact("pending", declared, generation + 1, status["fingerprint"]):
            _write_recovery(data, connector_id, declared, "init-pending", recovery["bundle"], prior, generation + 1, status["fingerprint"])
        elif status["status"] == "active" and exact("active", declared, generation + 1, status["fingerprint"]):
            _write_recovery(data, connector_id, declared, "approved", recovery["bundle"], prior, generation + 1, status["fingerprint"])
        else:
            raise ExtendError("installation status cannot prove the retained invitation outcome")
    else:
        if exact("active", declared, generation, fingerprint):
            _write_recovery(data, connector_id, declared, "approved", recovery["bundle"], prior, generation, fingerprint)
        elif exact("pending", declared, generation, fingerprint):
            _write_recovery(data, connector_id, declared, "redeemed", recovery["bundle"], prior, generation, fingerprint)
        else:
            raise ExtendError("installation status cannot prove the retained approval outcome")
    return _read_recovery(data, connector_id, declared) or recovery


def _resume_enrollment(
    data: dict[str, Any],
    manifest_path: str | Path,
    target: Target,
    connector_unit: str,
    prior_connector: tuple[bool, str],
    declared: list[str],
    runner: Any,
    system_root: Path,
) -> str:
    """Complete exactly the private phase retained after a revoke.

    A retained ``invited`` phase may have completed native redemption before a
    process crash.  In that case identity is the owner-provided proof and no
    second invitation is issued.
    """
    connector_id = target.name or ""
    recovery = _resolve_recovery_phase(data, manifest_path, connector_id, declared, runner)
    phase, bundle_name = recovery["phase"], recovery["bundle"]
    prior, generation = recovery["prior"], recovery["generation"]
    fingerprint = recovery["fingerprint"]
    if phase == "revoked":
        _write_recovery(data, connector_id, declared, "invite-pending", prior=prior, generation=generation, fingerprint=recovery["fingerprint"])
        try:
            bundle = _invite(data, manifest_path, connector_id, declared, runner)
        except ManageError as invite_error:
            if not invite_error.may_have_executed:
                _write_recovery(data, connector_id, declared, "revoked", prior=prior, generation=generation, fingerprint=recovery["fingerprint"])
            raise
        bundle_name = bundle.name
        status = _installation_status(data, manifest_path, connector_id, runner)
        if (status["status"] != "invited" or status["epoch"] != prior["epoch"]
                or status["generation"] != generation + 1 or status["resources"] != declared or status["fingerprint"]):
            raise ExtendError("installation status cannot prove the issued invitation")
        generation = status["generation"]
        _write_recovery(data, connector_id, declared, "invited", bundle_name, prior, generation)
        phase = "invited"
    if phase == "invited":
        _write_recovery(data, connector_id, declared, "re-enroll-pending", bundle_name, prior, generation)
        phase = "re-enroll-pending"
    if phase == "re-enroll-pending":
        bundle = _retained_bundle(data, connector_id, bundle_name)
        if bundle is None:
            raise ExtendError("retained invitation is unavailable for connector re-enrollment")
        prior_path = _reenrollment_prior(data, connector_id, prior)
        try:
            _stop_connector(runner, connector_unit)
            manage.native_reenroll(manifest_path, target, prior=prior_path, bundle=bundle, apply=True, runner=runner)
        finally:
            try:
                prior_path.unlink()
            except FileNotFoundError:
                pass
        _write_recovery(data, connector_id, declared, "init-pending", bundle_name, prior, generation)
        manage.native_init(manifest_path, target, bundle=bundle, apply=True, runner=runner)
        try:
            bundle.unlink()
        except FileNotFoundError:
            pass
        phase = "init-pending"
    if phase == "init-pending":
        # Re-enrollment has already atomically staged the new pending local
        # identity.  A still-invited authority proves ordinary init never
        # redeemed the bundle, so retry that one operation only.  Any missing
        # or drifted local identity is held without touching authority.
        try:
            identity = _connector_identity(manifest_path, target, runner)
        except ManageError as exc:
            raise ExtendError("connector identity is unavailable for retained re-enrollment") from exc
        status = _installation_status(data, manifest_path, connector_id, runner)
        fingerprint = identity["fingerprint"]
        local_pending = (
            identity.get("status") == "pending" and identity.get("id") == connector_id
            and identity.get("epoch") == prior["epoch"] and identity.get("generation") == generation
            and identity.get("resources") == declared
        )
        if (status["status"] == "invited" and status["epoch"] == prior["epoch"]
                and status["generation"] == generation and status["resources"] == declared
                and not status["fingerprint"]):
            if not local_pending:
                raise ExtendError("connector identity cannot prove the retained replacement stage")
            bundle = _retained_bundle(data, connector_id, bundle_name)
            if bundle is None:
                raise ExtendError("retained invitation is unavailable for pending connector enrollment")
            manage.native_init(manifest_path, target, bundle=bundle, apply=True, runner=runner)
            try:
                bundle.unlink()
            except FileNotFoundError:
                pass
            return _resume_enrollment(
                data, manifest_path, target, connector_unit, prior_connector, declared, runner, system_root,
            )
        if (status["status"] != "pending" or status["epoch"] != prior["epoch"]
                or status["generation"] != generation or status["resources"] != declared
                or status["fingerprint"] != fingerprint):
            raise ExtendError("installation status cannot prove native enrollment")
        _write_recovery(data, connector_id, declared, "redeemed", bundle_name, prior, generation, fingerprint)
        phase = "redeemed"
    if phase in {"redeemed", "approved"}:
        observed = _identity_fingerprint(manifest_path, target, runner)
        if fingerprint and observed != fingerprint:
            raise ExtendError("connector identity does not match retained approval")
        fingerprint = observed
    if phase == "redeemed":
        _write_recovery(data, connector_id, declared, "approve-pending", bundle_name, prior, generation, fingerprint)
        _admin_exchange(data, manifest_path, {
            "operation": "approve", "installation": connector_id, "fingerprint": fingerprint,
        }, output_name=None, runner=runner)
        _write_recovery(data, connector_id, declared, "approved", bundle_name, prior, generation, fingerprint)
    elif phase != "approved":
        raise ExtendError("extension recovery phase is not actionable")
    _start_connector(runner, connector_unit)
    manage._started_units(runner, system_root, (connector_unit,))
    _clear_recovery(data, connector_id, bundle_name)
    return fingerprint


def extend(
    manifest_path: str | Path,
    target: Target,
    *,
    confirm: bool,
    runner: Any = None,
    unit_root: str | Path = "/etc/systemd/system",
) -> dict[str, Any]:
    """Run the managed resource-extension sequence for one connector."""
    _require_supported_platform()
    data = read_manifest(manifest_path)
    require_isolated(data)
    preview = extend_plan(data, target)
    connector_id = target.name or ""
    recovery = _read_recovery(data, connector_id, list(preview["declared"]))
    if not preview["extendable"] and recovery is None:
        guidance = preview.get("removal_guidance", "nothing to add")
        raise ExtendError(
            f"declaration is not a pure resource addition; {guidance}", plan=preview,
        )
    result: dict[str, Any] = {
        "schema": "anvil-connect.extend/v1",
        "action": "extend",
        "target": target.text(),
        "applied": bool(confirm),
        "dry_run": not confirm,
        "plan": preview,
    }
    if not confirm:
        return result
    connector_unit = _target_units((target,))[0]
    root, system_root = Path(data["config_root"]), Path(unit_root)
    with manage._deployment_lock(root):
        # Re-plan inside the lock: another activation may have converged the
        # same extension while this call waited for the deployment lock.
        preview = extend_plan(data, target)
        declared = list(preview["declared"])
        previous = list(preview["enrolled"])
        recovery = _read_recovery(data, connector_id, declared)
        if recovery is not None:
            report = plan(data, root)
            if report["state"] != "current":
                raise ExtendError("rendered ownership is not current for retained extension recovery")
            checked = manage._validate_data(data, (Target("gateway"), target), runner)
            manage._bound_active(data, Target("gateway"), checked["digests"])
            manage._gateway_ready(data, runner)
            prior_connector = manage._unit_state(runner, connector_unit)
            try:
                fingerprint = _resume_enrollment(
                    data, manifest_path, target, connector_unit, prior_connector, declared, runner, system_root,
                )
            except Exception as exc:
                raise ExtendError(
                    f"extension recovery is retained at phase {recovery['phase']}: {exc}; "
                    "re-run extend after correcting the failure",
                    may_have_executed=True,
                ) from exc
            result.update({"recovered": True, "fingerprint": fingerprint})
            return result
        if not preview["extendable"]:
            result.update({"applied": False, "already_extended": True})
            return result
        report = plan(data, root)
        if report["state"] not in {"update", "current"}:
            raise ExtendError("rendered ownership is not safe to extend")
        # Gateway activation consumes its pinned native binary plus Caddy and
        # Authelia before any connector enrollment changes. Validate that full
        # closed role set up front, rather than accepting connector-only pins.
        checked = manage._validate_data(data, (Target("gateway"), target), runner)
        prior_connector = manage._unit_state(runner, connector_unit)
        gateway_units = _target_units((Target("gateway"),))
        prior_gateway = {unit: manage._unit_state(runner, unit) for unit in gateway_units}
        transaction: _Activation | None = None
        fingerprint: str | None = None
        authority_changed = False
        try:
            staged = stage(data, root)
            transaction = _extend_activate_gateway(
                data, Path(staged["path"]), checked["digests"], runner, system_root,
                unit_root=system_root,
            )
            for unit in gateway_units:
                active, unit_file_state = prior_gateway[unit]
                _apply_unit_lifecycle(runner, unit, active, unit_file_state, start=True)
            manage._gateway_ready(data, runner)
            if transaction.pending_record is not None:
                _write_activation_record(root, transaction.pending_record)
                transaction.pending_record = None
            # The candidate root is now active and its activation record is
            # published.  Native admin rejects an otherwise-valid status read
            # until both binding proofs agree on that same generation.
            prior_status = _installation_status(data, manifest_path, connector_id, runner)
            if prior_status["status"] != "active" or prior_status["resources"] != previous:
                raise ExtendError("installation state is not the declared prior connector enrollment")
            _write_recovery(
                data, connector_id, declared, "revoke-pending", prior=prior_status,
                generation=prior_status["generation"], fingerprint=prior_status["fingerprint"],
            )
            try:
                _revoke(data, manifest_path, connector_id, runner)
            except ManageError as revoke_error:
                if revoke_error.may_have_executed:
                    authority_changed = True
                else:
                    _clear_recovery(data, connector_id)
                raise
            authority_changed = True
            status = _installation_status(data, manifest_path, connector_id, runner)
            if (status["status"] != "revoked" or status["epoch"] != prior_status["epoch"]
                    or status["generation"] != prior_status["generation"] + 1
                    or status["resources"] != prior_status["resources"]
                    or status["fingerprint"] != prior_status["fingerprint"]):
                raise ExtendError("installation status cannot prove the completed revoke")
            _write_recovery(
                data, connector_id, declared, "revoked", prior=prior_status,
                generation=status["generation"], fingerprint=status["fingerprint"],
            )
            fingerprint = _resume_enrollment(
                data, manifest_path, target, connector_unit, prior_connector, declared, runner, system_root,
            )
            transaction.commit()
        except Exception as exc:
            if authority_changed:
                # Revoke changes the gateway-owned installation before an
                # invitation can be issued.  Keep the new generation and its
                # retained private phase; rollback would restore a connector
                # declaration whose authority was deliberately revoked.
                if transaction is not None and not transaction.committed:
                    transaction.commit()
                retained = _read_recovery(data, connector_id, declared)
                phase = retained["phase"] if retained is not None else "unknown"
                if phase in {"revoke-pending", "re-enroll-pending", "init-pending"}:
                    guidance = (
                        "installation-status check before a new invitation is issued"
                        if phase == "revoke-pending"
                        else ("retry of the exact retained connector replacement"
                              if phase == "re-enroll-pending"
                              else "connector identity verification before any invitation is replayed")
                    )
                    raise ExtendError(
                        f"extension {phase} outcome is uncertain; recovery state is retained for an {guidance}",
                        may_have_executed=True,
                    ) from exc
                try:
                    fingerprint = _resume_enrollment(
                        data, manifest_path, target, connector_unit, prior_connector,
                        declared, runner, system_root,
                    )
                except Exception as recovery_error:
                    raise ExtendError(
                        f"extension authority changed and recovery is retained at phase {phase}: "
                        f"{recovery_error}; re-run extend after correcting the failure",
                        may_have_executed=True,
                    ) from exc
                result["completed_after_error"] = str(exc)
                result["fingerprint"] = fingerprint
                return result
            if transaction is not None and not transaction.committed:
                try:
                    transaction.rollback(runner)
                except ManageError as rollback_error:
                    raise rollback_error from exc
                try:
                    _restore_gateway_units(runner, root, data, prior_gateway)
                except ManageError as restore_error:
                    raise restore_error from exc
            raise ExtendError(
                f"extend failed: {exc}; no enrollment changes were committed",
                may_have_executed=True,
            ) from exc
    readback = extend_plan(read_manifest(manifest_path), target)
    result.update({
        "activated": True,
        "fingerprint": fingerprint,
        "resources": readback["declared"],
        "extendable_after": readback["extendable"],
        "principals_note": (
            "existing principals keep their prior resource lists until human-set "
            "extends them; see the resource-extension ticket"
        ),
    })
    return result


def _apply_unit_lifecycle(
    runner: Any, unit: str, was_active: bool, unit_file_state: str, *, start: bool
) -> None:
    """Drive one unit to its exact prior lifecycle during rollback.

    ``enable --now`` used for startup makes a previously disabled unit
    boot-persistent; rollback therefore disables it again when the prior
    unit-file state was disabled, and never leaves an inactive unit enabled.
    """
    if start:
        if was_active:
            manage._action(runner, (_SYSTEMCTL, "restart", unit), _SYSTEMD_TIMEOUT, "managed unit failed to restart")
        else:
            manage._action(runner, (_SYSTEMCTL, "enable", "--now", unit), _SYSTEMD_TIMEOUT, "managed unit failed to start")
        return
    if was_active:
        manage._action(runner, (_SYSTEMCTL, "restart", unit), _SYSTEMD_TIMEOUT, "managed unit failed to restore")
    else:
        if unit_file_state == "disabled":
            manage._action(runner, (_SYSTEMCTL, "disable", unit), _SYSTEMD_TIMEOUT, "managed unit failed to disable")
        manage._action(runner, (_SYSTEMCTL, "stop", unit), _SYSTEMD_TIMEOUT, "managed unit failed to stop")


def _restore_gateway_units(runner: Any, root: Path, data: dict[str, Any], prior: dict[str, tuple[bool, str]]) -> None:
    for unit, (active, unit_file_state) in prior.items():
        _apply_unit_lifecycle(runner, unit, active, unit_file_state, start=False)
