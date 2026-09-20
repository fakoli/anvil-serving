from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess

import pytest

from anvil_serving.workbench_app.pi_runner import PiContextMount, PiRunnerPolicy


def test_container_launch_keeps_secret_values_out_of_argv(tmp_path: Path) -> None:
    policy = PiRunnerPolicy(
        engine_argv=("/usr/bin/docker",),
        image="anvil-serving-pi-runner:0.85.1",
        worktree=tmp_path / "worktree",
        agent_dir=tmp_path / "agent",
        uid=12345,
        gid=12346,
        container_name="pi-1",
        session_id="s1",
    )
    with pytest.raises(ValueError, match="trusted gateway"):
        policy.launch(tmp_path / "session", credential_environment={"PROVIDER_TOKEN": "not-in-runner"})
    launch = policy.launch(tmp_path / "session", credential_environment={})
    assert launch.environment == {}
    assert "12345:12346" in launch.argv
    assert "--cap-drop" in launch.argv
    assert launch.argv[launch.argv.index("--log-driver") + 1] == "none"


def test_linked_git_worktree_is_not_a_runner_checkout(tmp_path: Path) -> None:
    (tmp_path / ".git").write_text("gitdir: /outside", encoding="utf-8")
    with pytest.raises(ValueError, match="isolated full clone"):
        PiRunnerPolicy(
            engine_argv=("/usr/bin/docker",), image="image", worktree=tmp_path,
            agent_dir=tmp_path / "agent", uid=12345, gid=12345,
            container_name="pi-1", session_id="s1",
        )


def _inspect_fixture(policy, directory):
    mounts = [("/sessions", directory, True), ("/workspace", policy.worktree, True),
              ("/home/pi/.pi/agent", policy.agent_dir, True)]
    mounts.extend((f"/context/{item.root_id}", item.source, False) for item in policy.context_mounts)
    return {"Image": "sha256:fixture", "Config": {"Image": policy.image, "Labels": {"anvil.pi.session": policy.session_id, "anvil.pi.policy": policy.policy_digest(directory)}, "Cmd": list(policy.native_args), "Entrypoint": ["/opt/pi-runner/node_modules/.bin/pi"], "WorkingDir": "/workspace", "User": f"{policy.uid}:{policy.gid}", "Env": ["PI_CODING_AGENT_DIR=/home/pi/.pi/agent"]}, "HostConfig": {"LogConfig": {"Type": "none"}, "ReadonlyRootfs": True, "Privileged": False, "CapAdd": None, "CapDrop": ["ALL"], "SecurityOpt": ["no-new-privileges"], "NanoCpus": int(policy.cpu_limit * 10**9), "Memory": policy.memory_limit_bytes, "PidsLimit": policy.pids_limit, "NetworkMode": "none", "Tmpfs": {"/tmp": "rw,nosuid,nodev,size=64m"}}, "Mounts": [{"Type": "bind", "Destination": target, "Source": str(source), "RW": writable} for target, source, writable in mounts], "State": {"Running": True}}


def _policy(tmp_path):
    return PiRunnerPolicy(engine_argv=("/usr/bin/docker",), image="runner@sha256:" + "a" * 64, worktree=tmp_path / "work", agent_dir=tmp_path / "agent", uid=10001, gid=10001, container_name="anvil-pi-test", session_id="test", native_args=("--mode", "rpc", "--session-id", "test"))


def test_inspection_distinguishes_absence_from_daemon_failure(tmp_path):
    from subprocess import CompletedProcess
    from anvil_serving.workbench_app.pi_runner import PiContainerInspector
    policy = _policy(tmp_path)
    for error, expected in (("Cannot connect to the Docker daemon", "unavailable"), ("permission denied", "unavailable"), ("Error: No such container: anvil-pi-test", "absent"), ("Error: No such container: some-other-container", "unavailable")):
        inspector = PiContainerInspector("docker", run=lambda *a, **k: CompletedProcess(a, 1, "", error))
        assert inspector.state(policy, tmp_path / "sessions") == expected


@pytest.mark.parametrize("section,key,value", [
    ("Config", "Image", "other@sha256:" + "b" * 64),
    ("Config", "Cmd", ["sh"]),
    ("Config", "User", "0:0"),
    ("Config", "Entrypoint", ["sh"]),
    ("Config", "Env", ["PI_CODING_AGENT_DIR=/home/pi/.pi/agent", "HTTPS_PROXY=http://uncontrolled:8080"]),
    ("HostConfig", "ReadonlyRootfs", False),
    ("HostConfig", "CapDrop", []),
    ("HostConfig", "SecurityOpt", []),
    ("HostConfig", "Privileged", True),
    ("HostConfig", "Memory", 0),
    ("HostConfig", "NanoCpus", 0),
    ("HostConfig", "PidsLimit", -1),
    ("HostConfig", "NetworkMode", "bridge"),
    ("HostConfig", "DeviceRequests", [{"Capabilities": [["gpu"]]}]),
])
def test_inspection_requires_full_effective_runtime_policy(tmp_path, section, key, value):
    import json
    from subprocess import CompletedProcess
    from anvil_serving.workbench_app.pi_runner import PiContainerInspector
    policy, directory = _policy(tmp_path), tmp_path / "sessions"
    item = _inspect_fixture(policy, directory)
    def run(argv, **kwargs):
        return CompletedProcess(argv, 0, json.dumps([{"Id": "sha256:fixture"}] if argv[1] == "image" else [item]), "")
    inspector = PiContainerInspector("docker", run=run)
    assert inspector.state(policy, directory) == "running"
    item[section][key] = value
    assert inspector.state(policy, directory) == "unsafe"


def test_branch_native_arguments_and_policy_digest_are_bound(tmp_path):
    from dataclasses import replace
    policy = replace(_policy(tmp_path), native_args=("--mode", "rpc", "--fork", "/sessions/branch-source.jsonl", "--session-id", "new-child"))
    argv = policy.argv(tmp_path / "sessions", credential_names=())
    assert argv[-len(policy.native_args):] == policy.native_args
    assert "--session" not in argv
    assert f"anvil.pi.policy={policy.policy_digest(tmp_path / 'sessions')}" in argv
    assert replace(policy, memory_limit_bytes=1024**3).policy_digest(tmp_path / "sessions") != policy.policy_digest(tmp_path / "sessions")


def test_legacy_policy_digest_omits_empty_context_mounts(tmp_path):
    policy, directory = _policy(tmp_path), tmp_path / "sessions"
    expected = {"image": policy.image, "worktree": str(policy.worktree.resolve()), "agent_dir": str(policy.agent_dir.resolve()),
                "session_dir": str(directory.resolve()), "uid": policy.uid, "gid": policy.gid,
                "cpu": policy.cpu_limit, "memory": policy.memory_limit_bytes, "pids": policy.pids_limit,
                "network": policy.network, "proxy": policy.proxy_url, "network_id": policy.network_id,
                "session": policy.session_id, "native_args": policy.native_args,
                "credential_names": policy.credential_names}
    assert policy.policy_digest(directory) == hashlib.sha256(json.dumps(expected, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def test_context_mounts_cannot_overlap_each_other_or_the_session(tmp_path):
    from dataclasses import replace

    first = tmp_path / "first"
    first.mkdir()
    with pytest.raises(ValueError, match="distinct directories"):
        replace(_policy(tmp_path), context_mounts=(PiContextMount("one", first), PiContextMount("two", first)))
    policy = replace(_policy(tmp_path), context_mounts=(PiContextMount("one", first),))
    with pytest.raises(ValueError, match="session mount"):
        policy.argv(first / "session", credential_names=())
    invalid = tmp_path / "bad,mount"
    invalid.mkdir()
    policy = replace(_policy(tmp_path), context_mounts=(PiContextMount("invalid", invalid),))
    with pytest.raises(ValueError, match="mount path"):
        policy.argv(tmp_path / "sessions", credential_names=())


def test_context_snapshot_mount_is_read_only_and_inspector_enforces_it(tmp_path):
    import json
    from subprocess import CompletedProcess
    from dataclasses import replace
    from anvil_serving.workbench_app.pi_runner import PiContainerInspector

    context = tmp_path / "context"
    context.mkdir()
    (context / "readme.txt").write_text("bounded context", encoding="utf-8")
    policy = replace(_policy(tmp_path), context_mounts=(PiContextMount("docs", context),))
    argv = policy.argv(tmp_path / "sessions", credential_names=())
    assert f"type=bind,source={context},target=/context/docs,readonly" in argv
    item = _inspect_fixture(policy, tmp_path / "sessions")
    inspector = PiContainerInspector("docker", run=lambda argv, **_k: CompletedProcess(argv, 0, json.dumps([{"Id": "sha256:fixture"}] if argv[1] == "image" else [item]), ""))
    assert inspector.state(policy, tmp_path / "sessions") == "running"
    next(mount for mount in item["Mounts"] if mount["Destination"] == "/context/docs")["RW"] = True
    assert inspector.state(policy, tmp_path / "sessions") == "unsafe"


def test_disposable_cpu_runner_reads_only_the_snapshot_context(tmp_path):
    """Exercise the reviewed local test image; no service, GPU, or network."""
    import shutil

    engine = shutil.which("docker")
    image = "anvil-pi-runner:0.85.1"
    if not engine or subprocess.run((engine, "image", "inspect", image), capture_output=True, timeout=10).returncode:
        pytest.skip("requires the local Pi runner test image")
    snapshot = tmp_path / "snapshot"
    snapshot.mkdir(mode=0o755)
    (snapshot / "readme.txt").write_text("bounded context\n", encoding="utf-8")
    (snapshot / "readme.txt").chmod(0o444)
    # These paths exist beside the snapshot but are never mounted into it.
    outside = tmp_path / "outside"
    outside.mkdir(mode=0o755)
    (outside / "leak.txt").write_text("outside", encoding="utf-8")
    command = (
        engine, "run", "--rm", "--network", "none", "--read-only", "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges", "--tmpfs", "/tmp:rw,nosuid,nodev,size=16m",
        "--mount", f"type=bind,source={snapshot},target=/context/docs,readonly", "--entrypoint", "/bin/sh",
        image, "-ceu",
        "test \"$(cat /context/docs/readme.txt)\" = 'bounded context'; "
        "test ! -e /context/docs/.env; test ! -e /context/docs/.git; "
        "test ! -e /context/docs/escape; test ! -e /context/docs/leak.txt; "
        "if printf x > /context/docs/write.txt 2>/dev/null; then exit 1; fi; printf pass",
    )
    result = subprocess.run(command, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert result.stdout == "pass"


def test_stop_uses_retained_launch_policy_after_current_binding_is_lost(tmp_path, monkeypatch):
    """Synthetic stop proof: a current binding failure cannot strand a runner."""
    from types import SimpleNamespace
    from anvil_serving.workbench_app import pi_runner, service as service_module
    from anvil_serving.workbench_app.pi_sessions import PiSession, PiTaskBinding
    from anvil_serving.workbench_app.service import WorkbenchService

    work, agent, sessions, context = (tmp_path / item for item in ("work", "agent", "sessions", "context"))
    for path in (work, agent, sessions, context):
        path.mkdir()
    policy = PiRunnerPolicy(engine_argv=("docker",), image="runner@sha256:" + "a" * 64, worktree=work,
                            agent_dir=agent, uid=1000, gid=1000, container_name="retained", session_id="retained",
                            native_args=("--mode", "rpc"), context_mounts=(PiContextMount("docs", context),))
    session = PiSession("retained", PiTaskBinding("owner", "project", "task", "lease", "runner", "provider", "a" * 64), 1, 1, "start", container_name="retained", launch_policy=WorkbenchService._retain_launch_policy(policy))

    class Store:
        def get(self, _key): return session
        def session_dir(self, _key): return sessions

    observed, removed = [], []
    class Inspector:
        def __init__(self, *_args): pass
        def state(self, seen, *_args, **_kwargs):
            observed.append(seen)
            return "running" if len(observed) == 1 else "absent"

    service = object.__new__(WorkbenchService)
    service.config = {"pi": {"engine_binary": "docker"}}
    service.pi_store = Store()
    service._pi_absence = lambda _session: False
    service._policy_for = lambda _session: (_ for _ in ()).throw(AssertionError("current binding must not be used to stop"))
    service._remove_pi_gateway = lambda item: removed.append(item.session_id)
    monkeypatch.setattr(pi_runner, "PiContainerInspector", Inspector)
    monkeypatch.setattr(service_module.subprocess, "run", lambda *_args, **_kwargs: SimpleNamespace(returncode=0))
    service._stop_runner(session)
    assert removed == ["retained"] and observed[0].policy_digest(sessions) == policy.policy_digest(sessions)
