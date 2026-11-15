"""Lifecycle effects require both exact ownership and an explicit apply gate."""
import os
import subprocess
import sys

import pytest

from anvil_serving.service_runtime.manifest import save_manifest
from anvil_serving.topology import parse_topology


def topology():
    return parse_topology({"schema_version": 1, "id": "local", "command_host": "host:mac",
        "command_runtime": "runtime:mac-native", "hosts": [{"id": "mac", "os": "macos", "roles": ["operator"]}],
        "runtimes": [{"id": "mac-native", "host": "mac", "role": "native"}],
        "resources": [{"id": "events", "role": "events", "host": "mac", "runtime": "mac-native", "workload": "service"}]})


class Supervisor:
    def __init__(self, *, running=False):
        self.running, self.registered, self.enabled = running, True, True
        self.commands = []
        self.fail_stop = False

    def inspect(self, binding):
        return {"manager": "launchd", "registered": self.registered, "running": self.running,
                "enabled": self.enabled, "identity": "gui/501/com.example.events", "pid": 456 if self.running else None,
                "state": "running" if self.running else "waiting"}

    def describe(self, binding):
        return {"identity": "gui/501/com.example.events", "engine_hint": "none", "ports": []}

    def plan(self, binding, action, observed):
        if action == "up" and observed["running"] or action == "down" and not observed["registered"]:
            return []
        return [["fixture-supervisor", action]]

    def run(self, argv, **kwargs):
        self.commands.append(argv)
        if argv[-1] in {"up", "restart"}:
            self.running = self.registered = True
        if argv[-1] == "down" and not self.fail_stop:
            self.running, self.registered = False, False
        if argv[-1] in {"enable", "disable"}:
            self.enabled = argv[-1] == "enable"
        return subprocess.CompletedProcess(argv, 0, "", "")


class DockerSupervisor:
    def __init__(self):
        self.inspections = []
        self.log_requests = []

    def verify_context(self):
        return None

    def discover(self):
        return [
            {
                "container": "hindsight",
                "image_id": "sha256:" + "a" * 64,
                "identity_labels": {"io.anvil-serving.managed-by": "models-recipes"},
            }
        ]

    def describe(self, binding):
        return {"identity": binding["container"], "image_id": binding["image_id"], "engine_hint": "none", "ports": []}

    def inspect(self, binding):
        self.inspections.append(binding["id"])
        return {
            "manager": "docker",
            "registered": True,
            "running": True,
            "enabled": True,
            "identity": binding["container"],
            "pid": 456,
            "state": "running",
        }

    def logs(self, binding, tail, *, since=None, until=None, timestamps=False, expected_identity=None):
        self.log_requests.append((binding["id"], tail, since, until, timestamps, expected_identity))
        return ['2026-10-16T12:00:01Z {"event":"admission"}']


def native_docker_topology():
    return parse_topology(
        {
            "schema_version": 1,
            "id": "local",
            "command_host": "host:dark",
            "command_runtime": "runtime:dark-native",
            "hosts": [{"id": "dark", "os": "linux", "roles": ["operator"]}],
            "runtimes": [
                {"id": "dark-native", "host": "dark", "role": "native"},
                {"id": "dark-docker", "host": "dark", "role": "docker"},
            ],
            "resources": [
                {
                    "id": "hindsight",
                    "role": "memory",
                    "host": "dark",
                    "runtime": "dark-docker",
                    "workload": "service",
                }
            ],
        }
    )


def docker_binding():
    return {
        "id": "hindsight",
        "resource": "hindsight",
        "manager": "docker",
        "engine": "none",
        "container": "hindsight",
        "image_id": "sha256:" + "a" * 64,
        "identity_labels": {"io.anvil-serving.managed-by": "models-recipes"},
    }


@pytest.fixture
def setup(tmp_path, monkeypatch):
    from itertools import count
    from types import SimpleNamespace
    from anvil_serving.service_runtime import operations

    # Fake supervisors test lifecycle outcomes, not host scheduling latency.
    ticks = count(0, .001)
    monkeypatch.setattr(operations, "time", SimpleNamespace(monotonic=lambda: next(ticks)))
    binding = dict(id="events", resource="events", manager="launchd", engine="none",
                   definition=str(tmp_path / "events.plist"), definition_sha256="a" * 64,
                   label="com.example.events", owner_uid=501)
    path = tmp_path / "services.toml"
    save_manifest(path, {"events": binding}, expected_digest="")
    adapter = Supervisor()
    options = dict(manifest=path, topology=topology(), _adapters={"launchd": adapter}, _run=adapter.run,
                   _host_os="macos", timeout_seconds=.1, _sleep=lambda _: None)
    return adapter, options


@pytest.mark.parametrize("confirm,dry_run", [(False, False), (True, True)])
def test_confirmation_and_preview_never_mutate(setup, confirm, dry_run):
    from anvil_serving.service_runtime.operations import execute
    adapter, options = setup
    result = execute("up", "events", confirm=confirm, dry_run=dry_run, **options)
    assert not result["applied"]
    assert not adapter.commands


def test_foreign_owner_is_refused_before_supervisor_access(setup):
    from anvil_serving.service_runtime.operations import execute
    from anvil_serving.service_runtime.contracts import ServiceError
    adapter, options = setup
    with pytest.raises(ServiceError):
        execute("up", "events", target="host:another", confirm=True, dry_run=False, **options)
    assert not adapter.commands


@pytest.mark.skipif(os.name == "nt", reason="launchd installation requires POSIX directory permissions")
def test_install_pinned_definition_does_not_register_or_start(setup, tmp_path):
    import hashlib
    from anvil_serving.service_runtime.operations import execute
    from anvil_serving.service_runtime.manifest import load_manifest, digest
    adapter, options = setup
    adapter.registered = False
    source = tmp_path / "staged.plist"
    source.write_bytes(b"pinned fixture definition")
    manifest = options["manifest"]
    rows = load_manifest(manifest)
    rows["events"].update(source_definition=str(source), definition_sha256=hashlib.sha256(source.read_bytes()).hexdigest())
    save_manifest(manifest, rows, expected_digest=digest(manifest))
    destination = tmp_path / "events.plist"
    preview = execute("install", "events", **options)
    assert not preview["applied"] and not destination.exists()
    result = execute("install", "events", confirm=True, dry_run=False, **options)
    assert result["applied"] and destination.read_bytes() == source.read_bytes()
    assert not adapter.commands


def test_start_polls_endpoint_until_ready(setup):
    from anvil_serving.service_runtime.operations import execute
    from anvil_serving.service_runtime.manifest import load_manifest, digest
    adapter, options = setup
    # The real loopback bind probe can consume its 200 ms socket timeout on
    # Windows before the mocked engine readiness polling starts.
    options["timeout_seconds"] = 5
    path = options["manifest"]
    rows = load_manifest(path)
    rows["events"]["endpoint"] = "http://127.0.0.1:65534"
    save_manifest(path, rows, expected_digest=digest(path))
    calls = []
    def health(row, **kwargs):
        calls.append(1)
        return {"ready": len(calls) >= 3}
    result = execute("up", "events", confirm=True, dry_run=False, _engine=health, **options)
    assert result["applied"] and len(calls) >= 3


def fleet(tmp_path):
    from anvil_serving.service_runtime.manifest import save_manifest
    class Fleet:
        def __init__(self):
            self.running = {"events": False, "worker": False}
            self.commands = []
            self.conflict = False
            self.fail_worker = False
        def describe(self, row):
            return {"engine_hint": "none", "ports": [54321] if self.conflict else []}
        def inspect(self, row):
            running = self.running[row["id"]]
            return dict(manager="launchd", identity=row["id"], registered=running, running=running,
                        enabled=True, pid=(100 if row["id"] == "events" else 200) if running else None)
        def plan(self, row, action, state):
            return [["fixture", action, row["id"]]]
        def run(self, argv, **kwargs):
            self.commands.append(argv[1:])
            if argv[1:] == ["up", "worker"] and self.fail_worker:
                return subprocess.CompletedProcess(argv, 1, "", "")
            self.running[argv[2]] = argv[1] == "up"
            return subprocess.CompletedProcess(argv, 0, "", "")
    adapter = Fleet()
    topo = parse_topology({"schema_version": 1, "id": "local", "command_host": "host:mac",
        "command_runtime": "runtime:mac-native", "hosts": [{"id": "mac", "os": "macos", "roles": ["operator"]}],
        "runtimes": [{"id": "mac-native", "host": "mac", "role": "native"}],
        "resources": [{"id": name, "role": name, "host": "mac", "runtime": "mac-native", "workload": "service"}
                      for name in ("events", "worker")]})
    rows = {name: dict(id=name, resource=name, manager="launchd", engine="none", label="com.example." + name,
                owner_uid=501, definition=str(tmp_path / (name + ".plist")), definition_sha256="a" * 64,
                dependencies=["events"] if name == "worker" else []) for name in ("events", "worker")}
    path = tmp_path / "services.toml"
    save_manifest(path, rows, expected_digest="")
    # These scenarios assert dependency order, rollback, and conflict handling,
    # not elapsed time. A slow Windows runner can consume the shared .1 s setup
    # fixture budget during manifest and ownership checks before any command.
    return adapter, dict(manifest=path, topology=topo, _adapters={"launchd": adapter}, _run=adapter.run,
                         _sleep=lambda _: None, timeout_seconds=5, _host_os="macos", confirm=True, dry_run=False)


def test_failed_start_rolls_back_only_new_instances(tmp_path):
    from anvil_serving.service_runtime.operations import execute
    from anvil_serving.service_runtime.contracts import ServiceError
    adapter, options = fleet(tmp_path)
    adapter.running["events"] = True
    adapter.fail_worker = True
    with pytest.raises(ServiceError):
        execute("up", "worker", **options)
    assert adapter.running["events"]
    assert ["down", "events"] not in adapter.commands


def test_failed_start_of_registered_idle_job_preserves_definition_and_reports_partial_rollback(setup, monkeypatch):
    from itertools import count
    from types import SimpleNamespace
    from anvil_serving.service_runtime import operations
    from anvil_serving.service_runtime.operations import execute
    from anvil_serving.service_runtime.contracts import ServiceError
    adapter, options = setup
    definition = options["manifest"].parent / "events.plist"
    original = b"pinned installed fixture definition"
    definition.write_bytes(original)
    assert adapter.registered and not adapter.running and adapter.enabled
    # This tests rollback after readiness failure, not runner scheduling within
    # the fixture's 100 ms deadline. Keep the failure path deterministic on CI.
    ticks = count(0, .001)
    monkeypatch.setattr(operations, "time", SimpleNamespace(monotonic=lambda: next(ticks)))

    def failed_readiness(row, **kwargs):
        raise ServiceError("readiness_failed", "fixture endpoint failed readiness")

    # The .1 s fixture deadline is a busy-spin ceiling for postcondition tests;
    # a slow Windows runner can consume it before the plan loop starts, which
    # would surface operation_timeout instead of this test's readiness_failed.
    options["timeout_seconds"] = 5
    with pytest.raises(ServiceError) as raised:
        execute("up", "events", confirm=True, dry_run=False, _engine=failed_readiness, **options)

    assert raised.value.code == "readiness_failed"
    assert raised.value.details["rollback"] == [
        {"id": "events", "stopped": True, "registration_restored": False},
    ]
    assert not adapter.running and not adapter.registered
    assert adapter.enabled and definition.read_bytes() == original
    assert adapter.commands == [["fixture-supervisor", "up"], ["fixture-supervisor", "down"]]
    recovered = execute("up", "events", confirm=True, dry_run=False, **options)
    assert recovered["services"][0]["after"]["running"]
    assert recovered["services"][0]["after"]["registered"]


def test_logs_passes_interval_to_declared_owner_and_returns_runtime_identity(tmp_path):
    from anvil_serving.service_runtime.operations import execute

    manifest = tmp_path / "services.toml"
    save_manifest(manifest, {"hindsight": docker_binding()}, expected_digest="")
    docker = DockerSupervisor()
    result = execute(
        "logs", "hindsight", manifest=manifest, topology=native_docker_topology(),
        _adapters={"docker": docker}, _host_os="linux", tail=3,
        since="2026-10-16T12:00:00Z", until="2026-10-16T12:01:00Z", timestamps=True,
    )
    assert docker.log_requests == [("hindsight", 3, "2026-10-16T12:00:00Z", "2026-10-16T12:01:00Z", True, "hindsight")]
    assert result["observed_runtime"] == {
        "manager": "docker", "identity": "hindsight", "image_id": "sha256:" + "a" * 64,
        "observed_at": result["observed_runtime"]["observed_at"], "runtime_interval_continuity": "unknown",
    }
    assert result["requested_interval"] == {
        "since": "2026-10-16T12:00:00Z", "until": "2026-10-16T12:01:00Z", "timestamps": True,
    }


def test_native_identity_adopts_same_host_docker_service(tmp_path):
    from anvil_serving.service_runtime.operations import execute

    docker = DockerSupervisor()
    result = execute(
        "adopt",
        "hindsight",
        manifest=tmp_path / "services.toml",
        topology=native_docker_topology(),
        binding={key: value for key, value in docker_binding().items() if key not in {"image_id", "identity_labels"}},
        _adapters={"docker": docker},
        _host_os="linux",
    )

    assert result["applied"] is False
    assert result["services"][0]["id"] == "hindsight"
    assert docker.inspections == ["hindsight"]


def test_mixed_runtime_manifest_does_not_block_local_operation(setup):
    from dataclasses import replace
    from anvil_serving.topology import Runtime, Resource
    from anvil_serving.service_runtime.manifest import load_manifest, digest
    from anvil_serving.service_runtime.operations import execute
    adapter, options = setup
    topo = options["topology"]
    options["topology"] = replace(topo, runtimes=topo.runtimes + (Runtime("docker", "mac", "docker"),),
                                  resources=topo.resources + (Resource("container", "aux", "mac", "docker"),))
    path = options["manifest"]
    rows = load_manifest(path)
    rows["container"] = dict(id="container", resource="container", manager="docker", engine="none",
        container="aux", image_id="sha256:" + "a" * 64, identity_labels={"io.anvil-serving.managed-by": "models-recipes"})
    save_manifest(path, rows, expected_digest=digest(path))
    docker = DockerSupervisor()
    docker.describe = lambda binding: {"identity": binding["container"], "engine_hint": "none", "ports": []}
    docker.inspect = lambda binding: {
        "manager": "docker", "registered": True, "running": True, "enabled": True,
        "identity": binding["container"], "pid": 789, "state": "running",
    }
    options["_adapters"]["docker"] = docker
    assert execute("up", "events", confirm=True, dry_run=False, **options)["applied"]
    status = execute("status", **options)
    container = next(row for row in status["services"] if row["id"] == "container")
    assert container["supervisor"]["running"] is True


class RetainedSupervisor:
    def __init__(self, custody):
        self.running = False
        self.custody = custody
        self.cycle = 0
        self.fail_health = False
        self.commands = []
        self.command_options = []

    def verify_context(self):
        return None

    def describe(self, binding):
        return {"manager": "docker", "retained_container": True, "engine_hint": "none", "ports": []}

    def inspect(self, binding):
        return {"manager": "docker", "registered": True, "running": self.running,
                "enabled": True, "identity": binding["container_id"],
                "pid": 777 if self.running else None, "state": "running" if self.running else "exited",
                "retained_container": True, "custody_sha256": self.custody,
                "restart_count": 0, "writable_mounts_sha256": binding["writable_mounts_sha256"],
                "security_projection_sha256": binding["security_projection_sha256"],
                "health_status": (
                    "starting" if self.running and self.fail_health
                    else "healthy" if self.running else "not_running"
                ),
                "started_at": f"2026-10-10T04:00:{self.cycle:02d}Z",
                "finished_at": f"2026-10-10T04:01:{self.cycle:02d}Z"}

    def plan(self, binding, action, observed):
        if action not in {"up", "down"} or observed["identity"] != binding["container_id"]:
            raise AssertionError("invalid retained lifecycle plan")
        if observed["running"] is (action == "up"):
            return []
        return [["docker", "start", binding["container_id"]]] if action == "up" else [[
            "docker", "stop", "--timeout", str(binding["shutdown_grace_seconds"]), binding["container_id"]
        ]]

    def run(self, argv, **kwargs):
        self.commands.append(list(argv))
        self.command_options.append(dict(kwargs))
        self.running = argv[1] == "start"
        self.cycle += 1
        return subprocess.CompletedProcess(argv, 0, "", "")


def retained_setup(tmp_path, *, shutdown_grace_seconds=1):
    import hashlib
    from anvil_serving.service_runtime.docker import _retained_custody_sha256

    definition = tmp_path / "reviewed-retained-definition.json"
    row = {
        "id": "hindsight", "resource": "hindsight", "manager": "docker", "engine": "none",
        "container": "retained-service", "image_id": "sha256:" + "a" * 64,
        "identity_labels": {"owner": "reviewed"}, "retained_container": True,
        "container_id": "b" * 64, "restart_count": 0, "restart_policy": "unless-stopped",
        "restart_maximum_retry_count": 0, "writable_mounts_sha256": "c" * 64,
        "security_projection_sha256": "d" * 64, "definition": str(definition),
        "definition_sha256": "0" * 64,
        "shutdown_grace_seconds": shutdown_grace_seconds,
        "healthcheck_required": True, "healthcheck_sha256": "e" * 64,
    }
    definition.write_text(__import__("json").dumps({
        "schema": "anvil-retained-container-definition/v1", "service": row["id"],
        "container_id": row["container_id"], "image_id": row["image_id"],
        "source_kind": "reviewed-nonsecret-container-definition", "source_sha256": "f" * 64,
        "review_sha256": "1" * 64, "healthcheck_sha256": row["healthcheck_sha256"],
        "contains_secrets": False,
    }, sort_keys=True) + "\n")
    row["definition_sha256"] = hashlib.sha256(definition.read_bytes()).hexdigest()
    manifest = tmp_path / "services.toml"
    save_manifest(manifest, {row["id"]: row}, expected_digest="")
    adapter = RetainedSupervisor(_retained_custody_sha256(row))
    return row, adapter, {
        "manifest": manifest, "topology": native_docker_topology(), "_adapters": {"docker": adapter},
        "_run": adapter.run, "_host_os": "linux",
        "timeout_seconds": max(10, shutdown_grace_seconds + 5), "_sleep": lambda _: None,
        "_engine": lambda *_args, **_kwargs: {"ready": True},
    }


def retained_authorization(path, preview, row, action):
    from datetime import datetime, timedelta, timezone
    from anvil_serving.service_runtime.manifest import digest

    data = {
        "schema": "anvil-retained-container-authorization/v1",
        "scope": "retained-container-lifecycle", "service": row["id"], "action": action,
        "manifest_sha256": digest(path.parent / "services.toml"),
        "preview_sha256": preview["retained_container"]["preview_sha256"],
        "container_id": row["container_id"],
        "custody_sha256": preview["retained_container"]["custody_sha256"],
        "definition_sha256": row["definition_sha256"], "human_approval_sha256": "e" * 64,
        "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=2)).isoformat(),
    }
    if action == "down":
        data.update(
            storage_consumer_state="clear", storage_consumer_evidence_sha256="f" * 64,
            ingress_idle_state="unknown", ingress_evidence_sha256="1" * 64,
            drain_state="bounded", drain_evidence_sha256="2" * 64,
            uncertain_interruption_risk_accepted=True,
        )
    path.write_text(__import__("json").dumps(data, sort_keys=True))
    path.chmod(0o600)


retained_linux_custody = pytest.mark.skipif(
    sys.platform != "linux",
    reason="retained authorization custody requires native Linux POSIX ownership and modes",
)


@retained_linux_custody
def test_retained_start_and_stop_require_reviewed_preview_and_scoped_authorization(tmp_path):
    from anvil_serving.service_runtime.operations import execute

    row, adapter, options = retained_setup(tmp_path)
    preview = execute("up", row["id"], **options)
    assert preview["retained_container"]["rollback_timeout_seconds"] == 6
    assert preview["retained_container"]["maximum_total_seconds"] == 16
    auth = tmp_path / "up-authorization.json"
    retained_authorization(auth, preview, row, "up")
    result = execute("up", row["id"], confirm=True, dry_run=False,
                     expected_preview_sha256=preview["retained_container"]["preview_sha256"],
                     operator_authorization_file=auth, **options)
    assert result["applied"] is True
    assert adapter.commands == [["docker", "start", row["container_id"]]]

    preview = execute("down", row["id"], **options)
    auth = tmp_path / "down-authorization.json"
    retained_authorization(auth, preview, row, "down")
    result = execute("down", row["id"], confirm=True, dry_run=False,
                     expected_preview_sha256=preview["retained_container"]["preview_sha256"],
                     operator_authorization_file=auth, **options)
    assert result["services"][0]["after"]["registered"] is True
    assert adapter.commands[-1] == ["docker", "stop", "--timeout", "1", row["container_id"]]


@retained_linux_custody
def test_retained_failed_start_reserves_full_declared_stop_grace_for_rollback(tmp_path, monkeypatch):
    from itertools import count
    from types import SimpleNamespace

    from anvil_serving.service_runtime import operations
    from anvil_serving.service_runtime.contracts import ServiceError
    from anvil_serving.service_runtime.operations import execute

    row, adapter, options = retained_setup(tmp_path, shutdown_grace_seconds=20)
    adapter.fail_health = True
    ticks = count(0, .25)
    monkeypatch.setattr(operations, "time", SimpleNamespace(monotonic=lambda: next(ticks)))
    preview = execute("up", row["id"], **options)
    auth = tmp_path / "authorization.json"
    retained_authorization(auth, preview, row, "up")
    with pytest.raises(ServiceError, match="postcondition") as raised:
        execute("up", row["id"], confirm=True, dry_run=False,
                expected_preview_sha256=preview["retained_container"]["preview_sha256"],
                operator_authorization_file=auth, **options)
    assert adapter.commands == [
        ["docker", "start", row["container_id"]],
        ["docker", "stop", "--timeout", "20", row["container_id"]],
    ]
    assert adapter.command_options[-1]["timeout"] >= row["shutdown_grace_seconds"]
    assert raised.value.details["rollback"] == [
        {"id": row["id"], "stopped": True, "registration_restored": True},
    ]


@retained_linux_custody
def test_retained_authorization_marker_refuses_same_path_reuse_when_state_repeats(tmp_path):
    from anvil_serving.service_runtime.contracts import ServiceError
    from anvil_serving.service_runtime.operations import execute

    row, adapter, options = retained_setup(tmp_path)
    preview = execute("up", row["id"], **options)
    auth = tmp_path / "authorization.json"
    retained_authorization(auth, preview, row, "up")
    approval = dict(confirm=True, dry_run=False,
                    expected_preview_sha256=preview["retained_container"]["preview_sha256"],
                    operator_authorization_file=auth)
    execute("up", row["id"], **approval, **options)
    adapter.running = False
    adapter.cycle = 0
    with pytest.raises(ServiceError, match="already consumed"):
        execute("up", row["id"], **approval, **options)
    assert adapter.commands == [["docker", "start", row["container_id"]]]


@retained_linux_custody
def test_copied_retained_authorization_cannot_replay_after_manual_state_cycle(tmp_path):
    from anvil_serving.service_runtime.contracts import ServiceError
    from anvil_serving.service_runtime.operations import execute

    row, adapter, options = retained_setup(tmp_path)
    preview = execute("up", row["id"], **options)
    auth = tmp_path / "authorization.json"
    copied = tmp_path / "copied-authorization.json"
    retained_authorization(auth, preview, row, "up")
    copied.write_bytes(auth.read_bytes())
    copied.chmod(0o600)
    execute("up", row["id"], confirm=True, dry_run=False,
            expected_preview_sha256=preview["retained_container"]["preview_sha256"],
            operator_authorization_file=auth, **options)
    adapter.running = False
    adapter.cycle += 1
    with pytest.raises(ServiceError, match="current reviewed preview"):
        execute("up", row["id"], confirm=True, dry_run=False,
                expected_preview_sha256=preview["retained_container"]["preview_sha256"],
                operator_authorization_file=copied, **options)
    assert adapter.commands == [["docker", "start", row["container_id"]]]


@retained_linux_custody
def test_retained_unknown_ingress_requires_explicit_interruption_authority(tmp_path):
    import json
    from anvil_serving.service_runtime.contracts import ServiceError
    from anvil_serving.service_runtime.operations import execute

    row, adapter, options = retained_setup(tmp_path)
    adapter.running = True
    preview = execute("down", row["id"], **options)
    auth = tmp_path / "authorization.json"
    retained_authorization(auth, preview, row, "down")
    data = json.loads(auth.read_text())
    data["uncertain_interruption_risk_accepted"] = False
    auth.write_text(json.dumps(data, sort_keys=True))
    with pytest.raises(ServiceError, match="explicit authorization"):
        execute("down", row["id"], confirm=True, dry_run=False,
                expected_preview_sha256=preview["retained_container"]["preview_sha256"],
                operator_authorization_file=auth, **options)
    assert adapter.commands == []


def test_retained_stop_requires_deadline_beyond_declared_grace(tmp_path):
    from anvil_serving.service_runtime.contracts import ServiceError
    from anvil_serving.service_runtime.operations import execute

    row, adapter, options = retained_setup(tmp_path)
    options["timeout_seconds"] = row["shutdown_grace_seconds"] + 4
    with pytest.raises(ServiceError, match="reserve five seconds"):
        execute("down", row["id"], **options)
    assert adapter.commands == []
