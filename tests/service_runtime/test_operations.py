"""Lifecycle effects require both exact ownership and an explicit apply gate."""
import os
import subprocess

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
            self.running = True
        if argv[-1] == "down" and not self.fail_stop:
            self.running, self.registered = False, False
        if argv[-1] in {"enable", "disable"}:
            self.enabled = argv[-1] == "enable"
        return subprocess.CompletedProcess(argv, 0, "", "")


@pytest.fixture
def setup(tmp_path):
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


def test_start_and_stop_are_verified_against_supervisor(setup):
    from anvil_serving.service_runtime.operations import execute
    adapter, options = setup
    started = execute("up", "events", confirm=True, dry_run=False, **options)
    assert started["applied"] is True
    assert started["services"][0]["after"]["running"] is True
    stopped = execute("down", "events", confirm=True, dry_run=False, **options)
    assert stopped["services"][0]["after"]["running"] is False


def test_failed_stop_is_not_reported_as_success(setup):
    from anvil_serving.service_runtime.operations import execute
    from anvil_serving.service_runtime.contracts import ServiceError
    adapter, options = setup
    adapter.running = adapter.fail_stop = True
    with pytest.raises(ServiceError, match="postcondition"):
        execute("down", "events", confirm=True, dry_run=False, **options)


def test_foreign_owner_is_refused_before_supervisor_access(setup):
    from anvil_serving.service_runtime.operations import execute
    from anvil_serving.service_runtime.contracts import ServiceError
    adapter, options = setup
    with pytest.raises(ServiceError):
        execute("up", "events", target="host:another", confirm=True, dry_run=False, **options)
    assert not adapter.commands


def test_disable_does_not_stop_running_service(setup):
    from anvil_serving.service_runtime.operations import execute
    adapter, options = setup
    adapter.running = True
    result = execute("disable", "events", confirm=True, dry_run=False, **options)
    assert result["services"][0]["after"]["running"] is True
    assert result["services"][0]["after"]["enabled"] is False


def test_controller_refuses_self_shutdown_over_remote_tool(setup, monkeypatch):
    from anvil_serving.service_runtime.operations import execute
    from anvil_serving.service_runtime.contracts import ServiceError
    adapter, options = setup
    data = options["manifest"].read_text().replace('engine = "none"', 'engine = "none"\nfeature = "controller"')
    options["manifest"].write_text(data)
    monkeypatch.setenv("ANVIL_SERVING_HOME", str(options.pop("manifest").parent))
    with pytest.raises(ServiceError, match="recovery"):
        execute("down", "events", remote=True, confirm=True, dry_run=False, **options)
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


def test_adoption_only_writes_binding(setup, tmp_path):
    from anvil_serving.service_runtime.operations import execute
    from anvil_serving.service_runtime.manifest import load_manifest
    adapter, options = setup
    row = load_manifest(options["manifest"])["events"]
    options["manifest"].unlink()
    source = tmp_path / "events.plist"
    source.write_text("existing definition")
    row.pop("definition_sha256")
    result = execute("adopt", "events", binding=row, confirm=True, dry_run=False, **options)
    assert result["applied"]
    assert load_manifest(options["manifest"])["events"]["definition_sha256"]
    assert not adapter.commands


def test_unknown_supervisor_state_is_not_absent(setup):
    from anvil_serving.service_runtime.operations import execute
    from anvil_serving.service_runtime.contracts import ServiceError
    adapter, options = setup
    adapter.registered = adapter.running = None
    with pytest.raises(ServiceError, match="state"):
        execute("up", "events", confirm=True, dry_run=False, **options)
    assert not adapter.commands


def test_restart_requires_replacement_evidence(setup):
    from anvil_serving.service_runtime.operations import execute
    from anvil_serving.service_runtime.contracts import ServiceError
    adapter, options = setup
    adapter.running = True
    with pytest.raises(ServiceError, match="postcondition"):
        execute("restart", "events", confirm=True, dry_run=False, **options)


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


def test_recipe_model_mismatch_refused_before_mutation(setup):
    from anvil_serving.service_runtime.operations import execute
    from anvil_serving.service_runtime.contracts import ServiceError
    adapter, options = setup
    with pytest.raises(ServiceError, match="model differs"):
        execute("up", "events", expected_model="different-model", confirm=True, dry_run=False, **options)
    assert not adapter.commands


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
    return adapter, dict(manifest=path, topology=topo, _adapters={"launchd": adapter}, _run=adapter.run,
                         _sleep=lambda _: None, timeout_seconds=.1, _host_os="macos", confirm=True, dry_run=False)


def test_dependencies_start_in_order_and_protect_running_dependents(tmp_path):
    from anvil_serving.service_runtime.operations import execute
    from anvil_serving.service_runtime.contracts import ServiceError
    adapter, options = fleet(tmp_path)
    execute("up", "worker", **options)
    assert adapter.commands == [["up", "events"], ["up", "worker"]]
    with pytest.raises(ServiceError, match="dependent"):
        execute("down", "events", **options)


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


def test_planned_bind_conflict_refuses_before_start(tmp_path):
    from anvil_serving.service_runtime.operations import execute
    from anvil_serving.service_runtime.contracts import ServiceError
    adapter, options = fleet(tmp_path)
    adapter.conflict = True
    with pytest.raises(ServiceError, match="bind"):
        execute("up", "worker", **options)
    assert not adapter.commands


@pytest.mark.parametrize("host_os", ["windows", "macos"])
def test_docker_linux_guest_preserves_declared_physical_host(host_os):
    from anvil_serving.service_runtime.operations import _owner
    from anvil_serving.topology import resolve_command_identity
    topo = parse_topology({"schema_version": 1, "id": "local", "command_host": "host:machine",
        "command_runtime": "runtime:docker", "hosts": [{"id": "machine", "os": host_os, "roles": ["operator"]}],
        "runtimes": [{"id": "docker", "host": "machine", "role": "docker"}],
        "resources": [{"id": "events", "role": "events", "host": "machine", "runtime": "docker", "workload": "service"}]})
    owner = _owner({"resource": "events", "manager": "docker", "engine": "none"}, topo,
                   resolve_command_identity(topo), None, "linux", "status")
    assert owner.host == "machine"


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
        container="aux", image_id="sha256:" + "a" * 64, identity_labels={"io.anvil-serving.managed-by": "fixture"})
    save_manifest(path, rows, expected_digest=digest(path))
    assert execute("up", "events", confirm=True, dry_run=False, **options)["applied"]
    status = execute("status", **options)
    foreign = next(row for row in status["services"] if row["id"] == "container")
    assert foreign["supervisor"]["running"] is None
    assert foreign["supervisor"]["state"] == "requires_owner_runtime"


def test_known_engine_hint_cannot_be_relabelled_during_adoption(setup, tmp_path):
    from dataclasses import replace
    from anvil_serving.service_runtime.operations import execute
    from anvil_serving.service_runtime.manifest import load_manifest
    from anvil_serving.service_runtime.contracts import ServiceError
    adapter, options = setup
    row = load_manifest(options["manifest"])["events"]
    options["manifest"].unlink()
    (tmp_path / "events.plist").write_text("pinned existing definition")
    row.update(engine="kokoro", support="legacy")
    topo = options["topology"]
    options["topology"] = replace(topo, resources=(replace(topo.resources[0], workload="tts"),))
    adapter.describe = lambda row: {"engine_hint": "parakeet", "ports": []}
    with pytest.raises(ServiceError, match="engine"):
        execute("adopt", "events", binding=row, confirm=True, dry_run=False, **options)
    assert not options["manifest"].exists()
    assert not adapter.commands
