"""Short operator commands using the owner's authenticated MCP contract."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
import tomllib

from . import mcp
from .control_plane.mcp.auth_file import AuthFileError, read_private_auth_file
from .control_plane.mcp.arguments import validate_schema_value
from .control_plane.mcp.controller_client import remote_controller_request, resolve_controller_token_file
from .control_plane.mcp.errors import ToolError
from .control_plane.mcp.protocol import CLIENT_CAPABILITIES_META_KEY, CLIENT_INFO_META_KEY, PROTOCOL_VERSION_META_KEY
from .control_plane.propagation import (
    MAX_TARGETS, PropagationContractError, _digest, _id, capability_declaration,
)
from .operator_output import CommandResult, PartialResultError, SafetyError, TransportError, UsageError


_OPERATIONS = {
    "start": "propagation.accept.v1",
    "status": "propagation.status.v1",
    "resume": "propagation.resume.v1",
    "cancel": "propagation.cancel.v1",
}
_SCHEMAS = {item["name"]: item for item in capability_declaration()["operations"]}


class _Parser(argparse.ArgumentParser):
    def error(self, _message):
        raise UsageError("invalid workflow arguments", code="invalid_workflow_arguments")


def _unique_pairs(pairs):
    value = dict(pairs)
    if len(value) != len(pairs):
        raise ValueError("duplicate release field")
    return value


def _read_config() -> dict:
    root = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    path = root / "anvil-serving" / "workflows.toml"
    try:
        value = tomllib.loads(read_private_auth_file(path, max_bytes=4096).decode("utf-8"))
        if (not {"schema", "controller_url", "auth_file"} <= set(value)
                or set(value) - {"schema", "controller_url", "auth_file", "recovery_auth_file",
                                 "release_dir", "release_digest", "native_storage_root"}
                or value["schema"] != "anvil-serving.workflows-operator/v1"
                or type(value["controller_url"]) is not str
                or type(value["auth_file"]) is not str
                or not Path(value["auth_file"]).is_absolute()):
            raise ValueError()
        return value
    except (OSError, ValueError, TypeError, UnicodeDecodeError, AuthFileError, ToolError):
        raise SafetyError("workflow operator configuration is unavailable", code="workflow_config_unavailable") from None


def _config(*, recovery: bool = False) -> tuple[str, str]:
    value = _read_config()
    try:
        auth_file = value["recovery_auth_file"] if recovery else value["auth_file"]
        if type(auth_file) is not str or not Path(auth_file).is_absolute():
            raise ValueError()
        return value["controller_url"], resolve_controller_token_file(auth_file)
    except (KeyError, ValueError, ToolError):
        raise SafetyError("workflow credential is unavailable", code="workflow_credential_unavailable") from None


def _local_preview(profile: str) -> dict:
    if profile != "propagation-v1":
        raise UsageError("unsupported workflow profile", code="invalid_workflow_profile")
    if not hasattr(os, "O_NOFOLLOW"):
        raise SafetyError("local workflow release is unavailable", code="workflow_release_unavailable")
    config = _read_config()
    try:
        directory = Path(config["release_dir"])
        expected = _digest(config["release_digest"])
        if not directory.is_absolute():
            raise ValueError()
        manifest_path = directory / "manifest.json"
        descriptor = os.open(manifest_path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > 65_536:
                raise ValueError()
            with os.fdopen(os.dup(descriptor), "rb") as stream:
                raw = stream.read(65_537)
        finally:
            os.close(descriptor)
        if len(raw) > 65_536 or hashlib.sha256(raw).hexdigest() != expected:
            raise ValueError()
        manifest = json.loads(raw, object_pairs_hook=_unique_pairs)
        if (type(manifest) is not dict or set(manifest) != {"schema", "profile_id", "profile_digest", "components"}
                or manifest["schema"] != "anvil-workflows.release/v1"
                or manifest["profile_id"] != profile or type(manifest["components"]) is not list
                or not 1 <= len(manifest["components"]) <= 8):
            raise ValueError()
        _digest(manifest["profile_digest"])
        components = []
        names = set()
        for item in manifest["components"]:
            if (type(item) is not dict or set(item) != {"name", "file", "sha256"}
                    or type(item["name"]) is not str or type(item["file"]) is not str
                    or not item["file"].isascii()
                    or any(ord(char) < 33 or ord(char) == 127 or char == "\\" for char in item["file"])
                    or Path(item["file"]).name != item["file"] or item["file"] in {"", ".", ".."}
                    or item["name"] in names):
                raise ValueError()
            _id(item["name"])
            names.add(item["name"])
            expected_file = _digest(item["sha256"])
            descriptor = os.open(directory / item["file"], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            try:
                metadata = os.fstat(descriptor)
                if not stat.S_ISREG(metadata.st_mode):
                    raise ValueError()
                with os.fdopen(os.dup(descriptor), "rb") as stream:
                    actual = hashlib.file_digest(stream, "sha256").hexdigest()
            finally:
                os.close(descriptor)
            if actual != expected_file:
                raise ValueError()
            components.append({"name": item["name"], "sha256": expected_file, "bytes": metadata.st_size})
        return {"profile_id": profile, "profile_digest": manifest["profile_digest"],
                "release_digest": expected,
                "release_path_digest": hashlib.sha256(os.fsencode(directory.resolve(strict=True))).hexdigest(),
                "components": components, "effects": []}
    except (KeyError, OSError, ValueError, TypeError, UnicodeError, PropagationContractError):
        raise SafetyError("local workflow release is unavailable", code="workflow_release_unavailable") from None


def _arguments(argv: list[str]) -> tuple[str, dict]:
    if not argv:
        raise UsageError("choose a workflow action", code="workflow_action_required")
    if argv[0] in {"deployment", "recovery"}:
        if len(argv) < 2:
            raise UsageError("choose a workflow action", code="workflow_action_required")
        action, rest = argv[0] + "_" + argv[1], argv[2:]
    else:
        action, rest = argv[0], argv[1:]
    if action not in {*_OPERATIONS, "capabilities", "deployment_preview", "deployment_verify", "recovery_verify", "recovery_snapshot-journal"}:
        raise UsageError("unsupported workflow action", code="workflow_action_required")
    parser = _Parser(add_help=False, allow_abbrev=False)
    if action == "start":
        parser.add_argument("--approval-ref", required=True)
        parser.add_argument("--request-id", required=True)
    elif action in {"status", "resume", "cancel"}:
        parser.add_argument("--intent-id", required=True)
        if action in {"resume", "cancel"}:
            parser.add_argument("--expected-digest", required=True)
    elif action == "recovery_snapshot-journal":
        parser.add_argument("--profile", required=True)
        parser.add_argument("--output", required=True)
    elif action != "capabilities":
        parser.add_argument("--profile", required=True)
    if action not in {"status", "capabilities", "deployment_preview", "deployment_verify", "recovery_verify"}:
        parser.add_argument("--confirm", action="store_true")
    values = vars(parser.parse_args(rest))
    if action not in {"status", "capabilities", "deployment_preview", "deployment_verify", "recovery_verify"} and not values.pop("confirm"):
        raise SafetyError("confirm the approved owner request", code="confirmation_required")
    try:
        for key, value in values.items():
            if key != "output":
                (_digest if key == "expected_digest" else _id)(value)
    except PropagationContractError:
        raise UsageError("invalid workflow identity", code="invalid_workflow_arguments") from None
    if action == "start":
        arguments = {"approval_ref": values["approval_ref"], "request_id": values["request_id"]}
    elif action == "status":
        arguments = {"intent_id": values["intent_id"], "cursor": None}
    elif action == "capabilities":
        arguments = {}
    elif action in {"deployment_preview", "deployment_verify", "recovery_verify"}:
        if values["profile"] != "propagation-v1":
            raise UsageError("unsupported workflow profile", code="invalid_workflow_profile")
        arguments = {"profile_id": values["profile"]}
    elif action == "recovery_snapshot-journal":
        arguments = {"profile_id": values["profile"], "output": values["output"]}
    else:
        arguments = {"intent_id": values["intent_id"], "expected_digest": values["expected_digest"]}
    if action in _OPERATIONS:
        try:
            validate_schema_value(arguments, _SCHEMAS[_OPERATIONS[action]]["input_schema"], "arguments")
        except ToolError:
            raise UsageError("invalid workflow identity", code="invalid_workflow_arguments") from None
    return action, arguments


def _call(url: str, token: str, name: str, arguments: dict) -> dict:
    response = remote_controller_request(url, {
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": name, "arguments": arguments,
                   "_meta": {PROTOCOL_VERSION_META_KEY: mcp.PROTOCOL_VERSION,
                             CLIENT_CAPABILITIES_META_KEY: {},
                             CLIENT_INFO_META_KEY: {"name": "anvil-workflows-cli", "version": mcp.SERVER_INFO["version"]}}},
    }, token, timeout=15, max_response_bytes=131_072)
    result = response.get("result")
    content = result.get("structuredContent") if isinstance(result, dict) else None
    if not isinstance(content, dict) or content.get("ok") is not True or not isinstance(content.get("data"), dict):
        raise SafetyError("workflow owner refused the operation", code="workflow_owner_refused")
    data = content["data"]
    if name == "propagation_capabilities":
        expected = capability_declaration()
        for operation in expected["operations"]:
            operation["available"] = True
        if data != expected:
            raise SafetyError("workflow owner capabilities differ from this release", code="workflow_owner_capabilities_missing")
        return {"schema": data["schema"], "state": "ready",
                "operations": [operation["name"] for operation in data["operations"]], "effects": []}
    validate_schema_value(data, _SCHEMAS[name]["result_schema"], "result")
    return data


def native_current_authority(url: str, token: str, *, intent_id: str, job_id: str,
                             contract_digest: str, generation: int, target_id: str,
                             resource_id: str) -> bool:
    """Read the authenticated owner inside a native file fence callback."""
    name = "fleet.propagation.current.v1"
    arguments = {"intent_id": intent_id, "job_id": job_id,
                 "contract_digest": contract_digest, "generation": generation,
                 "target_id": target_id, "resource_id": resource_id}
    validate_schema_value(arguments, _SCHEMAS[name]["input_schema"], "arguments")
    return _call(url, token, name, arguments)["current"] is True


def _status(url: str, token: str, arguments: dict) -> dict:
    targets, seen_cursors = [], set()
    first = None
    for _ in range((MAX_TARGETS + 7) // 8):
        page = _call(url, token, _OPERATIONS["status"], arguments)
        if first is None:
            first = page
        elif {key: value for key, value in page.items() if key not in {"targets", "next_cursor"}} != {
                key: value for key, value in first.items() if key not in {"targets", "next_cursor"}}:
            raise SafetyError("workflow status pages disagree", code="workflow_status_incomplete")
        targets.extend(page["targets"])
        cursor = page["next_cursor"]
        if cursor is None:
            break
        if cursor in seen_cursors:
            raise SafetyError("workflow status cursor repeated", code="workflow_status_incomplete")
        seen_cursors.add(cursor)
        arguments = {**arguments, "cursor": cursor}
    if (first is None or cursor is not None or len(targets) != first["target_count"]
            or len(targets) != first["total_items"]
            or len({row["target_id"] for row in targets}) != len(targets)):
        raise SafetyError("workflow status is incomplete", code="workflow_status_incomplete")
    return {**first, "targets": targets, "next_cursor": None}


def main(argv: list[str] | None = None) -> CommandResult:
    try:
        action, arguments = _arguments(list(argv or []))
        if action == "deployment_preview":
            result = _local_preview(arguments["profile_id"])
        elif action == "recovery_snapshot-journal":
            from .commands.workflows_recovery import snapshot_journal
            try:
                result = snapshot_journal(_read_config()["native_storage_root"], arguments["profile_id"], arguments["output"])
            except (KeyError, OSError, ValueError, TypeError):
                raise SafetyError("native journal snapshot is unavailable", code="workflow_recovery_unavailable") from None
        elif action == "deployment_verify":
            result = _local_preview(arguments["profile_id"])
            url, token = _config()
            owner = _call(url, token, "propagation.profile.v1", {})
            result = {**result, "owner": owner,
                      "state": "matched" if owner["installed"]
                      and (owner["profile_id"], owner["profile_digest"]) ==
                      (result["profile_id"], result["profile_digest"]) else "drift"}
            if result["state"] != "matched":
                return CommandResult(data=result, error=PartialResultError(
                    "installed workflow profile differs from release", code="workflow_profile_drift"))
        else:
            url, token = _config(recovery=True) if action == "recovery_verify" else _config()
            result = (_status(url, token, arguments) if action == "status"
                      else _call(url, token, "propagation_capabilities" if action == "capabilities"
                                 else "propagation.recovery.verify.v1" if action == "recovery_verify"
                                 else _OPERATIONS[action], arguments))
            if (action == "recovery_verify" and result["state"] != "passed"
                    or action == "resume" and result["state"] == "refused"
                    or action == "cancel" and result["state"] == "uncertain"):
                return CommandResult(data=result, error=PartialResultError(
                    "workflow operation requires attention", code="workflow_outcome_incomplete"))
        return CommandResult(data=result)
    except (SafetyError, UsageError, TransportError, PartialResultError) as error:
        return CommandResult(error=error)
    except ToolError as error:
        return CommandResult(error=TransportError("workflow owner unavailable", code=error.code))
