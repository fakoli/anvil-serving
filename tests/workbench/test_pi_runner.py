from __future__ import annotations

from pathlib import Path

import pytest

from anvil_serving.workbench_app.pi_runner import PiRunnerPolicy


def test_container_launch_keeps_secret_values_out_of_argv() -> None:
    policy = PiRunnerPolicy(
        engine_argv=("/usr/bin/docker",),
        image="anvil-serving-pi-runner:0.85.1",
        worktree=Path("/private/worktree"),
        agent_dir=Path("/private/agent"),
        uid=12345,
        gid=12346,
        container_name="pi-1",
        session_id="s1",
    )
    with pytest.raises(ValueError, match="trusted gateway"):
        policy.launch(Path("/private/session"), credential_environment={"PROVIDER_TOKEN": "not-in-runner"})
    launch = policy.launch(Path("/private/session"), credential_environment={})
    assert launch.environment == {}
    assert "12345:12346" in launch.argv
    assert "--cap-drop" in launch.argv
    assert launch.argv[launch.argv.index("--log-driver") + 1] == "none"


def test_linked_git_worktree_is_not_a_runner_checkout(tmp_path: Path) -> None:
    (tmp_path / ".git").write_text("gitdir: /outside", encoding="utf-8")
    with pytest.raises(ValueError, match="isolated full clone"):
        PiRunnerPolicy(
            engine_argv=("/usr/bin/docker",), image="image", worktree=tmp_path,
            agent_dir=Path("/private/agent"), uid=12345, gid=12345,
            container_name="pi-1", session_id="s1",
        )


def _inspect_fixture(policy, directory):
    return {"Image": "sha256:fixture", "Config": {"Image": policy.image, "Labels": {"anvil.pi.session": policy.session_id, "anvil.pi.policy": policy.policy_digest(directory)}, "Cmd": list(policy.native_args), "Entrypoint": ["/opt/pi-runner/node_modules/.bin/pi"], "WorkingDir": "/workspace", "User": f"{policy.uid}:{policy.gid}", "Env": ["PI_CODING_AGENT_DIR=/home/pi/.pi/agent"]}, "HostConfig": {"LogConfig": {"Type": "none"}, "ReadonlyRootfs": True, "Privileged": False, "CapAdd": None, "CapDrop": ["ALL"], "SecurityOpt": ["no-new-privileges"], "NanoCpus": int(policy.cpu_limit * 10**9), "Memory": policy.memory_limit_bytes, "PidsLimit": policy.pids_limit, "NetworkMode": "none", "Tmpfs": {"/tmp": "rw,nosuid,nodev,size=64m"}}, "Mounts": [{"Type": "bind", "Destination": target, "Source": str(source), "RW": True} for target, source in (("/sessions", directory), ("/workspace", policy.worktree), ("/home/pi/.pi/agent", policy.agent_dir))], "State": {"Running": True}}


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
