"""Construct the installed propagation owner from one protected, pinned profile."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import stat
import sys

from ..propagation import parse_contract, _digest, _id
from ..propagation_jobs import PropagationProfile, PropagationService
from .errors import ControllerError
from .propagation_active_identity import ObservedActiveIdentity
from .propagation_approval import PinnedApprovedContract
from .propagation_job_store import ExecutionProfile, JobStore
from .propagation_reader import FixedFleetReader
from .propagation_store import PropagationIntentStore
from .propagation_supervisor import PropagationSupervisor
from .propagation_workflow_client import WorkflowControlClient


_MAX_PROFILE = 64 * 1024
_FIELDS = {"schema", "mode", "ledger_path", "approved_contract_path", "approved_contract_sha256",
           "approval_ref", "activation", "execution_profile", "readback_profile",
           "executor_issuer", "workflow_control"}


def _pairs(rows):
    result = {}
    for key, value in rows:
        if key in result:
            raise ValueError()
        result[key] = value
    return result


def _trusted_parent(path: Path) -> None:
    if not path.is_absolute() or ".." in path.parts or path.parent.resolve(strict=True) != path.parent:
        raise ValueError()
    for ancestor in (path.parent, *path.parent.parents):
        info = os.lstat(ancestor)
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid not in {0, os.geteuid()}
                or info.st_mode & 0o022 and not (info.st_uid == 0 and info.st_mode & stat.S_ISVTX)):
            raise ValueError()


def _protected(path: Path, *, private: bool, limit: int) -> bytes:
    _trusted_parent(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid not in {0, os.geteuid()}
                or info.st_mode & (0o077 if private else 0o022) or info.st_size > limit):
            raise ValueError()
        raw = stream.read(limit + 1)
    if len(raw) > limit:
        raise ValueError()
    return raw


def _ledger(path: Path) -> None:
    _trusted_parent(path)
    directory = os.lstat(path.parent)
    if directory.st_uid != os.geteuid() or stat.S_IMODE(directory.st_mode) != 0o700:
        raise ValueError()
    info = os.lstat(path)
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) != 0o600):
        raise ValueError()


def _secured_artifacts(profile: ExecutionProfile) -> None:
    for raw in (profile.argv[0], *(item[0] for item in profile.artifact_pins)):
        selected = Path(raw)
        resolved = selected.resolve(strict=True)
        for candidate in (selected, resolved):
            _trusted_parent(candidate)
            info = os.lstat(candidate)
            if (info.st_uid not in {0, os.geteuid()}
                    or not (stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode))
                    or stat.S_ISREG(info.st_mode) and info.st_mode & 0o022):
                raise ValueError()
        if not stat.S_ISREG(os.lstat(resolved).st_mode):
            raise ValueError()
    if profile.cwd is not None:
        _trusted_parent(Path(profile.cwd) / "_profile_workdir")


def build_propagation_service(path: str, sha256: str) -> PropagationService:
    """Fail closed before advertising any owner operation when a binding is absent."""
    try:
        if sys.platform != "linux":
            raise ValueError()
        expected = _digest(sha256)
        raw = _protected(Path(path), private=True, limit=_MAX_PROFILE)
        if hashlib.sha256(raw).hexdigest() != expected:
            raise ValueError()
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs)
        if type(value) is not dict or set(value) != _FIELDS or value["schema"] != "anvil-serving.propagation-owner/v1":
            raise ValueError()
        ledger = Path(value["ledger_path"])
        _ledger(ledger)
        approved = PinnedApprovedContract(value["approved_contract_path"], value["approved_contract_sha256"])
        contract = parse_contract(approved.contract_lookup(value["approval_ref"]))
        if value["mode"] not in {"effects", "preview"}:
            raise ValueError()
        execution = ExecutionProfile.from_private_value(value["execution_profile"])
        readback = ExecutionProfile.from_private_value(value["readback_profile"])
        if ((contract.value["execution_profile_ref"], contract.value["execution_profile_digest"])
                != (execution.profile_id, execution.digest)):
            raise ValueError()
        _secured_artifacts(execution)
        _secured_artifacts(readback)
        execution.verify_executable()
        readback.verify_executable()
        identity = value["activation"]
        if type(identity) is not dict or set(identity) not in ({
                "activation_ref", "catalog_sha256", "owners", "router_base_url", "router_token_env"}, {
                "activation_ref", "catalog_sha256", "owners", "router_base_url", "router_token_env", "observer"}):
            raise ValueError()
        if "observer" in identity:
            from ..mcp.controller_client import resolve_controller_token_file
            from ..mcp.security import safe_controller_url
            observer = identity["observer"]
            _digest(observer["profile_sha256"])
            safe_controller_url(observer["controller_url"])
            resolve_controller_token_file(observer["token_file"])
        active = ObservedActiveIdentity(**identity)
        control = value["workflow_control"]
        if type(control) is not dict or set(control) != {
                "socket_path", "token_file", "expected_peer_uid", "expected_peer_gid"}:
            raise ValueError()
        client = WorkflowControlClient(**control)
        reader = FixedFleetReader(readback)
        reader.probe(execution.profile_id, contract.digest)
        issuer = _id(value["executor_issuer"])
        intents = PropagationIntentStore(ledger)
        jobs = JobStore(ledger, {execution.profile_id: execution})
        supervisor = PropagationSupervisor(jobs, reconcile=reader.reconcile)
        profile = PropagationProfile(execution.profile_id, execution.digest, issuer,
            approved.contract_lookup, approved.approval_lookup, active,
            reader.preview, reader.observe, resume_workflow=client.resume,
            cancel_workflow=client.cancel, recovery_evidence=reader.recovery_evidence,
            cancellation_evidence=reader.cancellation_evidence,
            workflow_status=client.status, mode=value["mode"],
            preview_contract=contract.canonical, owner_profile_digest=sha256)
        return PropagationService(intents, jobs, supervisor, profile)
    except Exception:
        raise ControllerError("propagation_profile_unavailable",
                              "protected propagation owner profile is unavailable") from None


def build_activation_observer(path: str, sha256: str) -> ObservedActiveIdentity:
    """Load the exact read-only activation pin into the resource controller."""
    try:
        if sys.platform != "linux":
            raise ValueError()
        expected = _digest(sha256)
        raw = _protected(Path(path), private=True, limit=_MAX_PROFILE)
        if hashlib.sha256(raw).hexdigest() != expected:
            raise ValueError()
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs)
        if type(value) is not dict or set(value) != {"schema", "activation"} or value["schema"] != "anvil-serving.propagation-activation-observer/v1":
            raise ValueError()
        activation = value["activation"]
        if type(activation) is not dict or set(activation) != {
                "activation_ref", "catalog_sha256", "owners", "router_base_url", "router_token_env"}:
            raise ValueError()
        return ObservedActiveIdentity(**activation)
    except Exception:
        raise ControllerError("propagation_observer_unavailable",
                              "protected activation observer is unavailable") from None
