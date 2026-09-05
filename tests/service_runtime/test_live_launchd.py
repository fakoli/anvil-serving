"""Opt-in real supervisor smoke; only an isolated temporary sleep job is touched."""
import hashlib
import os
from pathlib import Path
import plistlib
import sys
import uuid

import pytest

from anvil_serving.service_runtime.manifest import save_manifest
from anvil_serving.service_runtime.operations import execute
from anvil_serving.topology import parse_topology


@pytest.mark.skipif(sys.platform != "darwin" or os.environ.get("ANVIL_TEST_LAUNCHD") != "1",
                    reason="requires explicit opt-in on a macOS desktop session")
def test_isolated_launchd_lifecycle(tmp_path):
    label = "com.example.anvil-smoke-" + uuid.uuid4().hex[:12]
    destination = Path.home() / "Library" / "LaunchAgents" / (label + ".plist")
    source = tmp_path / "source.plist"
    raw = plistlib.dumps({"Label": label, "ProgramArguments": ["/bin/sleep", "600"],
        "RunAtLoad": False, "KeepAlive": False,
        "StandardOutPath": str(tmp_path / "stdout.log"), "StandardErrorPath": str(tmp_path / "stderr.log")})
    source.write_bytes(raw)
    manifest = tmp_path / "services.toml"
    save_manifest(manifest, {"smoke": {"id": "smoke", "resource": "smoke", "manager": "launchd", "engine": "none",
        "owner_uid": os.getuid(), "label": label, "definition": str(destination), "source_definition": str(source),
        "definition_sha256": hashlib.sha256(raw).hexdigest()}}, expected_digest="")
    topology = parse_topology({"schema_version": 1, "id": "smoke", "command_host": "host:mac",
        "command_runtime": "runtime:native", "hosts": [{"id": "mac", "os": "macos", "roles": ["operator"]}],
        "runtimes": [{"id": "native", "host": "mac", "role": "native"}],
        "resources": [{"id": "smoke", "role": "smoke", "host": "mac", "runtime": "native", "workload": "service"}]})
    options = dict(manifest=manifest, topology=topology, confirm=True, dry_run=False, timeout_seconds=15)
    try:
        assert execute("install", "smoke", **options)["applied"]
        initial = execute("status", "smoke", **options)["services"][0]
        assert not initial["supervisor"]["registered"]
        assert execute("up", "smoke", **options)["services"][0]["after"]["running"]
        status = execute("status", "smoke", **options)["services"][0]
        assert status["supervisor"]["running"]
        assert status["engine"]["model_state"] == "unknown"
        assert execute("logs", "smoke", **options)["lines"] == []
        assert execute("restart", "smoke", **options)["services"][0]["after"]["pid"] != status["supervisor"]["pid"]
        disabled = execute("disable", "smoke", **options)["services"][0]["after"]
        assert disabled["running"] and not disabled["enabled"]
        assert execute("enable", "smoke", **options)["services"][0]["after"]["enabled"]
        stopped = execute("down", "smoke", **options)["services"][0]["after"]
        assert not stopped["registered"] and not stopped["running"]
        with pytest.raises(ProcessLookupError):
            os.kill(disabled["pid"], 0)
        assert execute("down", "smoke", **options)["applied"]
    finally:
        if destination.exists():
            execute("down", "smoke", **options)
            destination.unlink()
