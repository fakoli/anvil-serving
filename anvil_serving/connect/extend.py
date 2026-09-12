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
    """Run one managed admin operation with the request in role-owned state."""
    gateway_dir = _gateway_state_dir(data)
    gateway_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    request_path = gateway_dir / "extend-admin-request.json"
    request_path.write_text(json.dumps(request) + "\n", encoding="utf-8")
    os.chmod(request_path, 0o600)
    output_path = gateway_dir / "extend-admin-response.json" if output_name else None
    try:
        result = manage.admin(
            manifest_path,
            request_path=request_path,
            output_path=output_path,
            apply=True,
            runner=runner,
        )
    finally:
        try:
            request_path.unlink()
        except FileNotFoundError:
            pass
        if output_path is not None and output_name is None:
            try:
                output_path.unlink()
            except FileNotFoundError:
                pass
    if output_path is not None and output_path.is_file():
        value = json.loads(output_path.read_text(encoding="utf-8"))
        if output_name is None:
            try:
                output_path.unlink()
            except FileNotFoundError:
                pass
        return value
    return result


def _revoke_and_invite(
    data: dict[str, Any], manifest_path: str | Path, connector_id: str, declared: list[str], runner: Any
) -> Path:
    """Revoke the active installation, then invite with the full declared set."""
    _admin_exchange(data, manifest_path, {
        "operation": "installation-revoke", "installation": connector_id,
    }, output_name=None, runner=runner)
    response = _admin_exchange(data, manifest_path, {
        "operation": "invite",
        "installation": connector_id,
        "role": "connector",
        "resources": declared,
        "lifetime_seconds": 600,
    }, output_name="invitation", runner=runner)
    bundle_target = _connector_state_dir(data, connector_id) / "extend-invitation.json"
    bundle_target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    bundle_target.write_text(json.dumps(response) + "\n", encoding="utf-8")
    os.chmod(bundle_target, 0o600)
    uid = _connector_uid(data, connector_id)
    if os.geteuid() == 0:
        os.chown(bundle_target, uid, uid)
    return bundle_target


def _connector_uid(data: dict[str, Any], connector_id: str) -> int:
    identities = data.get("service_identities", {}).get("connectors", {})
    entry = identities.get(connector_id)
    if not isinstance(entry, dict) or not isinstance(entry.get("uid"), int):
        raise ExtendError(f"connector service identity is not declared: {connector_id}")
    return entry["uid"]


def _enroll_and_approve(
    data: dict[str, Any], manifest_path: str | Path, target: Target, bundle: Path, runner: Any
) -> str:
    """Redeem the invitation, read the fingerprint, approve it."""
    connector_id = target.name or ""
    manage.native_init(manifest_path, target, bundle=bundle, apply=True, runner=runner)
    try:
        bundle.unlink()
    except FileNotFoundError:
        pass
    identity = manage.identity(manifest_path, target, runner=runner)
    fingerprint = identity.get("fingerprint") if isinstance(identity, dict) else None
    if not isinstance(fingerprint, str) or not fingerprint:
        raise ExtendError("connector identity did not report a fingerprint after redemption")
    _admin_exchange(data, manifest_path, {
        "operation": "approve", "installation": connector_id, "fingerprint": fingerprint,
    }, output_name=None, runner=runner)
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
    if not preview["extendable"]:
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
    connector_id = target.name or ""
    connector_unit = _target_units((target,))[0]
    root, system_root = Path(data["config_root"]), Path(unit_root)
    checked = manage._validate_data(data, target, runner)
    with manage._deployment_lock(root):
        report = plan(data, root)
        if report["state"] not in {"update", "current"}:
            raise ExtendError("rendered ownership is not safe to extend")
        prior_connector = manage._unit_state(runner, connector_unit)
        gateway_units = _target_units((Target("gateway"),))
        prior_gateway = {unit: manage._unit_state(runner, unit) for unit in gateway_units}
        transaction: _Activation | None = None
        fingerprint: str | None = None
        try:
            staged = stage(data, root)
            transaction = _extend_activate_gateway(
                data, Path(staged["path"]), checked["digests"], runner, system_root,
                unit_root=system_root,
            )
            for unit in gateway_units:
                active, _ = prior_gateway[unit]
                if active:
                    manage._action(runner, (_SYSTEMCTL, "restart", unit), _SYSTEMD_TIMEOUT, "managed unit failed to restart")
                else:
                    manage._action(runner, (_SYSTEMCTL, "enable", "--now", unit), _SYSTEMD_TIMEOUT, "managed unit failed to start")
            manage._gateway_ready(data, runner)
            declared = list(preview["declared"])
            bundle = _revoke_and_invite(data, manifest_path, connector_id, declared, runner)
            fingerprint = _enroll_and_approve(data, manifest_path, target, bundle, runner)
            active_connector, _ = prior_connector
            if active_connector:
                manage._action(runner, (_SYSTEMCTL, "restart", connector_unit), _SYSTEMD_TIMEOUT, "connector failed to restart")
            else:
                manage._action(runner, (_SYSTEMCTL, "enable", "--now", connector_unit), _SYSTEMD_TIMEOUT, "connector failed to start")
            manage._started_units(runner, system_root, (connector_unit,))
            if transaction.pending_record is not None:
                _write_activation_record(root, transaction.pending_record)
            transaction.commit()
        except Exception as exc:
            if transaction is not None and not transaction.committed:
                try:
                    transaction.rollback(runner)
                except ManageError as rollback_error:
                    raise rollback_error from exc
                try:
                    _restore_gateway_units(runner, root, data, prior_gateway)
                except ManageError as restore_error:
                    raise restore_error from exc
            note = (
                "connector enrollment state may already be re-redeemed; re-run extend "
                "to complete the sequence"
            ) if fingerprint is not None else "no enrollment changes were committed"
            raise ExtendError(f"extend failed: {exc}; {note}", may_have_executed=True) from exc
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


def _restore_gateway_units(runner: Any, root: Path, data: dict[str, Any], prior: dict[str, tuple[bool, str]]) -> None:
    for unit, (active, _enabled) in prior.items():
        if active:
            manage._action(runner, (_SYSTEMCTL, "restart", unit), _SYSTEMD_TIMEOUT, "managed unit failed to restore")
        else:
            manage._action(runner, (_SYSTEMCTL, "stop", unit), _SYSTEMD_TIMEOUT, "managed unit failed to stop")