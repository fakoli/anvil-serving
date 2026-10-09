"""Opt-in, synthetic retained owner qualification; no fleet services or data.

Uses the existing pinned Docker fixture image. Only setup/cleanup use Compose
directly; the retained stop/start must pass through the public CLI dispatcher.
Authorization digests below identify a synthetic test actor, never a human.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid

import pytest

from anvil_serving import router_manage
from anvil_serving.service_runtime.docker import _RETAINED_INSPECT_FORMAT
from anvil_serving.service_runtime.manifest import save_manifest
from tests.router.key_fixtures import tmp_path as tmp_path
from tests.router.test_container_owner_docker import docker_owner as docker_owner
from tests.service_runtime.test_docker import _sha
from tests.service_runtime.test_operations import retained_authorization

pytestmark = pytest.mark.skipif(
    os.environ.get("ANVIL_ROUTER_DOCKER_TESTS") != "1" or sys.platform != "linux",
    reason="explicit owned Linux Docker qualification required",
)

IDLE = "import signal; signal.signal(signal.SIGTERM, lambda *_: exit(0)); signal.pause()"


def test_retained_cli_same_incarnation_and_strict_storage_exclusion(docker_owner, tmp_path):
    f = docker_owner
    root = Path(__file__).resolve().parents[2]
    # This fixture needs only a running Compose-owned target and its real mounts,
    # not a router listener, ledger, credential store, or model backend.
    name = "retained-fixture-" + uuid.uuid4().hex
    compose = tmp_path / "consumer.json"
    home = tmp_path / "operator-home"
    home.mkdir(mode=0o700)
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(home),
           "ANVIL_SERVING_HOME": str(home), "COMPOSE_DISABLE_ENV_FILE": "1"}
    f["env"].clear()
    f["env"].update(env)
    f["main"].write_text(IDLE)
    router_id = f["start"](legacy=True)
    image = json.loads(f["compose"].read_text())["services"]["router"]["image"]
    healthcheck = {"test": ["CMD", "python", "-c", "pass"],
                   "interval": "1s", "timeout": "1s", "retries": 3}
    compose.write_text(json.dumps({"name": name, "services": {"consumer": {
        "image": image, "container_name": name, "entrypoint": ["python", "-c", IDLE],
        "user": "1000:1000", "network_mode": "none", "cpus": 1, "mem_limit": "128m",
        "pids_limit": 32, "read_only": True, "cap_drop": ["ALL"],
        "security_opt": ["no-new-privileges:true"], "restart": "no",
        "stop_grace_period": "2s", "healthcheck": healthcheck,
        "volumes": [{"type": "bind", "source": str(f["private"]), "target": "/state"}],
    }}}))
    compose_argv = ["docker", "compose", "--env-file", os.devnull, "-f", str(compose)]
    cid = None

    def docker(*args):
        return subprocess.check_output(["docker", *args], text=True, env=env, timeout=15).strip()

    def inspect():
        return json.loads(docker("inspect", "--format", _RETAINED_INSPECT_FORMAT, cid))

    def roster():
        return router_manage._offline_compose_roster(
            str(f["compose"]), "router", f["name"], _run=f["run"],
            execution_env=env, live_target=True, _metadata_only=True)

    try:
        # Create separately so a failed start still leaves an exact owned ID for cleanup.
        subprocess.run([*compose_argv, "create", "--pull", "never", "consumer"],
                       check=True, capture_output=True, env=env, timeout=30)
        cid = docker("inspect", "--format", "{{.Id}}", name)
        subprocess.run([*compose_argv, "start", "consumer"], check=True,
                       capture_output=True, env=env, timeout=15)
        deadline = time.monotonic() + 15
        while True:
            observed = inspect()
            if observed["State"]["HealthStatus"] == "healthy":
                break
            assert time.monotonic() < deadline, "synthetic consumer health did not converge"
            time.sleep(.1)
        assert observed["Config"]["Labels"]["com.docker.compose.project"] == name
        assert "io.anvil-serving.managed-by" not in observed["Config"]["Labels"]
        # Hash Docker's normalized object (nanoseconds and canonical field names),
        # not the Compose declaration. This exact CID is exclusively test-owned.
        native_healthcheck = json.loads(docker("inspect", "--format", "{{json .Config.Healthcheck}}", cid))
        assert native_healthcheck["Test"] == healthcheck["test"]
        writable = [{key: mount.get(key) for key in
                     ("Type", "Name", "Source", "Destination", "Driver", "RW", "Mode", "Propagation")}
                    for mount in observed["Mounts"] if mount["RW"]]
        writable.sort(key=lambda value: json.dumps(value, sort_keys=True, separators=(",", ":")))
        security = {
            "config": {key: observed["Config"].get(key) for key in ("User", "WorkingDir")},
            "host_config": {key: observed["HostConfig"].get(key) for key in (
                "ReadonlyRootfs", "Privileged", "CapAdd", "CapDrop", "SecurityOpt", "NetworkMode",
                "PidMode", "IpcMode", "UTSMode", "Devices", "PortBindings")},
        }
        definition = tmp_path / "definition.json"
        row = {"id": "consumer", "resource": "consumer", "manager": "docker", "engine": "none",
               "container": name, "container_id": cid, "image_id": observed["Image"],
               "identity_labels": observed["Config"]["Labels"], "retained_container": True,
               "restart_count": observed["RestartCount"], "restart_policy": "no",
               "restart_maximum_retry_count": 0, "shutdown_grace_seconds": 2,
               "writable_mounts_sha256": _sha(writable), "security_projection_sha256": _sha(security),
               "healthcheck_required": True, "healthcheck_sha256": _sha(native_healthcheck),
               "definition": str(definition)}
        definition.write_text(json.dumps({
            "schema": "anvil-retained-container-definition/v1", "service": row["id"],
            "container_id": cid, "image_id": row["image_id"],
            "source_kind": "reviewed-nonsecret-container-definition",
            "source_sha256": hashlib.sha256(compose.read_bytes()).hexdigest(),
            "review_sha256": _sha({"actor": "synthetic-test-only", "scope": name}),
            "healthcheck_sha256": row["healthcheck_sha256"], "contains_secrets": False,
        }))
        row["definition_sha256"] = hashlib.sha256(definition.read_bytes()).hexdigest()
        manifest = tmp_path / "services.toml"
        save_manifest(manifest, {"consumer": row}, expected_digest="")
        topology = tmp_path / "topology.toml"
        topology.write_text('''schema_version = 1
id = "fixture"
command_host = "host:fixture"
command_runtime = "runtime:fixture-native"
[[hosts]]
id = "fixture"
os = "linux"
roles = ["operator"]
[[runtimes]]
id = "fixture-native"
host = "fixture"
role = "native"
[[runtimes]]
id = "fixture-docker"
host = "fixture"
role = "docker"
[[resources]]
id = "fixture-host"
role = "host"
host = "fixture"
runtime = "fixture-native"
workload = "service"
[[resources]]
id = "consumer"
role = "controller"
host = "fixture"
runtime = "fixture-docker"
workload = "service"
''')

        def cli(action, *extra, selected_manifest=manifest, ok=True):
            result = subprocess.run([
                sys.executable, "-B", "-m", "anvil_serving.cli", "host", "services", action,
                "consumer", "--manifest", str(selected_manifest), "--topology", str(topology),
                "--command-host", "host:fixture", "--command-runtime", "runtime:fixture-native",
                "--target", "host:fixture", "--transport", "local", "--timeout-seconds", "30",
                "--json", *extra], cwd=root, env=env, capture_output=True, text=True, timeout=45)
            if not ok:
                assert result.returncode != 0
                return result
            assert result.returncode == 0, (result.stdout, result.stderr)
            return json.loads(result.stdout)["data"]

        with pytest.raises(ValueError, match="^router_offline_storage_busy$"):
            roster()
        # A valid manifest with wrong RW custody must refuse before dispatch.
        bad = tmp_path / "wrong-custody.toml"
        save_manifest(bad, {"consumer": {**row, "writable_mounts_sha256": "0" * 64}}, expected_digest="")
        before = inspect()
        refusal = cli("down", "--no-dry-run", "--confirm", selected_manifest=bad, ok=False)
        assert json.loads(refusal.stdout)["error"]["code"] == "identity_mismatch"
        assert inspect() == before

        for action in ("down", "up"):
            preview = cli(action)
            authorization = tmp_path / (action + "-synthetic-authorization.json")
            retained_authorization(authorization, preview, row, action)
            # Explicitly identify this generated test permission as synthetic.
            permission = json.loads(authorization.read_text())
            permission["human_approval_sha256"] = _sha({"actor": "synthetic-test-only", "action": action, "cid": cid})
            authorization.write_text(json.dumps(permission))
            result = cli(action, "--no-dry-run", "--confirm", "--expected-preview-sha256",
                         preview["retained_container"]["preview_sha256"],
                         "--operator-authorization-file", str(authorization))
            assert result["applied"] is True
            current = inspect()
            assert current["Id"] == cid and current["Image"] == observed["Image"]
            assert current["RestartCount"] == observed["RestartCount"]
            assert current["State"]["Running"] is (action == "up")
            if action == "down":
                assert current["State"]["Pid"] == 0
                assert roster()["target"]["container_id"] == router_id
            else:
                assert current["State"]["HealthStatus"] == "healthy"
                with pytest.raises(ValueError, match="^router_offline_storage_busy$"):
                    roster()
    finally:
        if cid is not None:
            # Disposal is limited to the exact ID created here. The qualification
            # runner separately captures fleet baselines before and after this test.
            subprocess.run(["docker", "rm", "-f", cid], stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, env=env, timeout=15, check=True)
