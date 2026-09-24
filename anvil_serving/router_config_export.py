"""Read one verified installed router config without traversing the operator home.

This is not a general filesystem export: the running container must own one
read-only regular-file bind at the fixed router configuration path. Whole-home
inventory and export keep their existing symlink/dependency refusal contract.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path
import re
import subprocess
import tomllib

from . import operator_config
from .operator_output import CommandResult, UsageError

MAX_BYTES = 1024 * 1024
INSTALLED_PATH = "/etc/anvil/config.toml"
_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")
_SHA = re.compile(r"[0-9a-f]{64}\Z")
_HASH_CODE = (
    "import hashlib,os,stat; p='/etc/anvil/config.toml'; "
    "s=os.lstat(p); assert stat.S_ISREG(s.st_mode) and s.st_size<=1048576; "
    "f=open(p,'rb'); b=f.read(1048577); f.close(); "
    "assert len(b)<=1048576; print(hashlib.sha256(b).hexdigest())"
)
_VALIDATE_CODE = """
import hashlib, pathlib, sys, tempfile
from anvil_serving.router.config import load_bytes, load_server_config
raw = sys.stdin.buffer.read(1048577)
assert len(raw) <= 1048576
load_bytes(raw)
with tempfile.TemporaryDirectory(prefix='anvil-router-validate-') as folder:
    snapshot = pathlib.Path(folder) / 'router.toml'
    snapshot.write_bytes(raw)
    load_server_config(str(snapshot))
print(hashlib.sha256(raw).hexdigest())
"""


def _refusal(stage="ownership") -> UsageError:
    # Do not include parser errors, source bytes, or Docker stderr in diagnostics.
    return UsageError("Installed router configuration export refused.", code="router_config_export_refused", details={"stage": stage})


def _secret_projection(parsed: dict) -> dict:
    """Recognize one numeric inference budget, never a credential string."""
    checked = copy.deepcopy(parsed)

    for tier in checked.get("router", {}).get("tiers", []):
        params = tier.get("extra_body", {})
        value = params.get("thinking_token_budget")
        if type(value) is int and 0 <= value <= 1048576:
            del params["thinking_token_budget"]
    return checked


def export_installed_config(container: str, expected_sha256: str, *, _run=subprocess.run) -> dict:
    """Return exact safe UTF-8 content only after source/runtime identity checks."""
    if not _NAME.fullmatch(container) or not _SHA.fullmatch(expected_sha256):
        raise _refusal()

    def run(argv: list[str], *, input_text=None) -> str:
        result = _run(argv, input=input_text, capture_output=True, text=True, encoding="utf-8", timeout=30)
        if result.returncode or len(result.stdout) > 128 * 1024:
            raise _refusal(stage)
        return result.stdout

    def inspect() -> dict:
        row = json.loads(run([
            "docker", "inspect", "--format",
            '{"id":{{json .Id}},"running":{{json .State.Running}},"mounts":{{json .Mounts}},'
            '"project":{{json (index .Config.Labels "com.docker.compose.project")}},'
            '"service":{{json (index .Config.Labels "com.docker.compose.service")}}}',
            container,
        ]))
        if not isinstance(row, dict) or row.get("running") is not True:
            raise _refusal()
        if not isinstance(row.get("id"), str) or not _SHA.fullmatch(row["id"]):
            raise _refusal()
        if row.get("project") != "anvil-serving" or row.get("service") != "router":
            raise _refusal()
        mounts = row.get("mounts")
        if not isinstance(mounts, list) or any(not isinstance(item, dict) for item in mounts):
            raise _refusal()
        matches = [item for item in mounts if item.get("Destination") == INSTALLED_PATH]
        if len(matches) != 1 or matches[0].get("Type") != "bind" or matches[0].get("RW") is not False:
            raise _refusal()
        return {"container_id": row["id"], "mount": matches[0], "project": row["project"], "service": row["service"]}

    stage = "ownership"
    try:
        before = inspect()
        source = Path(before["mount"]["Source"])
        if not source.is_absolute() or source.name != "router.toml":
            raise _refusal()
        # Reuse the exporter's nofollow, bounded regular-file and race checks.
        stage = "source_read"
        with operator_config._operator_home_anchor(source.parent) as (root, identity, _fd):
            raw = operator_config._read_bounded(source, max_bytes=MAX_BYTES, root=root, root_identity=identity)
            digest = hashlib.sha256(raw).hexdigest()
            if digest != expected_sha256:
                raise _refusal()
            content = raw.decode("utf-8")
            parsed = tomllib.loads(content)
            stage = "router_validation"
            # The installed runtime owns its schema. Validate the exact captured
            # bytes there, even when its schema is newer than the operator CLI.
            validated = run(["docker", "exec", "-i", before["container_id"], "python", "-c", _VALIDATE_CODE], input_text=content).strip()
            if validated != digest:
                raise _refusal(stage)
            stage = "secret_validation"
            operator_config._assert_text_config_safe(content, parser="toml", path="router.toml")
            operator_config._assert_no_secret_literals(_secret_projection(parsed), path="router.toml")
            # This operation exports only one installed artifact, never its
            # dependencies. Refuse versionable dependency declarations instead
            # of claiming an incomplete bundle is sufficient for deployment.
            def has_dependency(value):
                if isinstance(value, dict):
                    return any(k in operator_config._DEPENDENCY_KEYS or has_dependency(v) for k, v in value.items())
                return isinstance(value, list) and any(has_dependency(v) for v in value)
            server = parsed.get("server", {})
            if not isinstance(server, dict) or has_dependency(parsed) or server.get("authorization_policy_path") is not None:
                raise _refusal("dependency_validation")
            stage = "installed_identity"
            mounted_digest = run(["docker", "exec", before["container_id"], "python", "-c", _HASH_CODE]).strip()
            repeated = operator_config._read_bounded(source, max_bytes=MAX_BYTES, root=root, root_identity=identity)
            if mounted_digest != digest or repeated != raw or inspect() != before:
                raise _refusal()
        return {
            "schema": "installed-router-config-export/v1",
            "read_only": True,
            "container": container,
            "container_id": before["container_id"],
            "source": str(source),
            "installed_path": INSTALLED_PATH,
            "sha256": digest,
            "size_bytes": len(raw),
            "content": content,
        }
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
        raise _refusal(stage) from None


def dispatch(argv=None) -> CommandResult:
    parser = argparse.ArgumentParser(prog="anvil-serving router export-config")
    parser.add_argument("--container", default="anvil-router")
    parser.add_argument("--expected-sha256", required=True)
    args = parser.parse_args(argv)
    try:
        data = export_installed_config(args.container, args.expected_sha256)
    except UsageError as exc:
        return CommandResult(error=exc, human_stderr="router export-config: refused at " + exc.details["stage"] + "\n")
    return CommandResult(data=data, human_stdout=data["content"])
