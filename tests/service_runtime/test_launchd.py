from __future__ import annotations

import hashlib
import os
import plistlib
import subprocess
from pathlib import Path

import pytest

from anvil_serving.service_runtime.contracts import ServiceError
from anvil_serving.service_runtime.launchd import Adapter


LABEL = "com.example.anvil.voice"

if not hasattr(os, "getuid"):
    pytest.skip("launchd ownership and permissions require POSIX", allow_module_level=True)


def _write_plist(
    path: Path,
    *,
    arguments: list[str] | None = None,
    run_at_load: bool | None = None,
    label: str = LABEL,
    environment: dict[str, str] | None = None,
) -> str:
    payload = {
        "Label": label,
        "ProgramArguments": arguments
        or ["/usr/bin/python3", "-m", "mlx_lm.server", "--port", "48123"],
        "StandardOutPath": str(path.with_suffix(".out.log")),
        "StandardErrorPath": str(path.with_suffix(".err.log")),
    }
    if run_at_load is not None:
        payload["RunAtLoad"] = run_at_load
    if environment is not None:
        payload["EnvironmentVariables"] = environment
    path.write_bytes(plistlib.dumps(payload))
    path.chmod(0o600)
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _binding(path: Path, digest: str) -> dict:
    return {
        "id": "voice",
        "resource": "voice",
        "manager": "launchd",
        "engine": "mlx",
        "support": "supported",
        "label": LABEL,
        "definition": str(path),
        "definition_sha256": digest,
        "owner_uid": os.getuid(),
        "dependencies": [],
        "endpoint": "http://127.0.0.1:48123",
        "model": "example-model",
        "startup_policy": "enabled",
    }


def _runner(*, print_result: subprocess.CompletedProcess[str], disabled: str = ""):
    def run(argv, **_kwargs):
        if argv[:2] == ["launchctl", "print"] and len(argv) == 3:
            return print_result
        if argv[:2] == ["launchctl", "print-disabled"]:
            return subprocess.CompletedProcess(argv, 0, disabled, "")
        raise AssertionError(f"unexpected command: {argv!r}")

    return run


def test_describe_refuses_changed_hash_symlink_and_group_writable_definition(tmp_path):
    definition = tmp_path / "voice.plist"
    digest = _write_plist(definition)
    adapter = Adapter()
    binding = _binding(definition, digest)

    definition.write_text("changed")
    with pytest.raises(ServiceError) as changed:
        adapter.describe(binding)
    assert changed.value.code == "definition_changed"

    real = tmp_path / "real.plist"
    digest = _write_plist(real)
    symlink = tmp_path / "linked.plist"
    symlink.symlink_to(real)
    with pytest.raises(ServiceError) as linked:
        adapter.describe(_binding(symlink, digest))
    assert linked.value.code == "unsafe_definition"

    digest = _write_plist(definition)
    definition.chmod(0o660)
    with pytest.raises(ServiceError) as writable:
        adapter.describe(_binding(definition, digest))
    assert writable.value.code == "unsafe_definition"


@pytest.mark.parametrize(
    ("reported_policy", "expected_enabled"),
    [("enabled", True), ("disabled", False)],
)
def test_inspect_parses_launchctl_enabled_and_disabled_policy_words(
    tmp_path, reported_policy, expected_enabled
):
    definition = tmp_path / "voice.plist"
    digest = _write_plist(definition)
    target = f"gui/{os.getuid()}/{LABEL}"
    printed = f"{target} = {{\npath = {definition}\nstate = waiting\n}}"
    adapter = Adapter(
        run=_runner(
            print_result=subprocess.CompletedProcess([], 0, printed, ""),
            disabled=f'\tdisabled services = {{\n\t\t"{LABEL}" => {reported_policy}\n\t}}',
        )
    )

    assert adapter.inspect(_binding(definition, digest))["enabled"] is expected_enabled


def test_inspect_distinguishes_unloaded_from_permission_and_unknown_supervisor_states(tmp_path):
    definition = tmp_path / "voice.plist"
    digest = _write_plist(definition)
    binding = _binding(definition, digest)
    adapter = Adapter(
        run=_runner(
            print_result=subprocess.CompletedProcess([], 113, "", "Could not find service"),
        )
    )
    assert adapter.inspect(binding)["registered"] is False

    denied = Adapter(
        run=_runner(
            print_result=subprocess.CompletedProcess([], 1, "", "Operation not permitted"),
        )
    )
    assert denied.inspect(binding) == {
        "manager": "launchd",
        "identity": None,
        "registered": None,
        "running": None,
        "enabled": None,
        "pid": None,
        "state": "inaccessible",
    }

    unknown = Adapter(
        run=_runner(
            print_result=subprocess.CompletedProcess([], 1, "", "launchctl failed"),
        )
    )
    assert unknown.inspect(binding)["state"] == "unknown"


@pytest.mark.parametrize(
    ("action", "observed", "expected"),
    [
        ("up", {"registered": True, "running": True, "enabled": True}, []),
        (
            "up",
            {"registered": False, "running": False, "enabled": True},
            [
                ["launchctl", "bootstrap", f"gui/{os.getuid()}", "DEFINITION"],
                ["launchctl", "kickstart", f"gui/{os.getuid()}/{LABEL}"],
            ],
        ),
        (
            "up",
            {"registered": True, "running": False, "enabled": True},
            [["launchctl", "kickstart", f"gui/{os.getuid()}/{LABEL}"],],
        ),
        (
            "down",
            {"registered": True, "running": True, "enabled": True},
            [["launchctl", "bootout", f"gui/{os.getuid()}/{LABEL}"],],
        ),
        (
            "restart",
            {"registered": True, "running": True, "enabled": True},
            [["launchctl", "kickstart", "-k", f"gui/{os.getuid()}/{LABEL}"],],
        ),
        (
            "enable",
            {"registered": True, "running": False, "enabled": False},
            [["launchctl", "enable", f"gui/{os.getuid()}/{LABEL}"],],
        ),
        (
            "disable",
            {"registered": True, "running": True, "enabled": True},
            [["launchctl", "disable", f"gui/{os.getuid()}/{LABEL}"],],
        ),
    ],
)
def test_plan_maps_lifecycle_actions_without_running_subprocesses(tmp_path, action, observed, expected):
    definition = tmp_path / "voice.plist"
    digest = _write_plist(definition)
    binding = _binding(definition, digest)
    if observed["registered"]:
        observed = {**observed, "identity": f"gui/{os.getuid()}/{LABEL}"}
    calls: list[list[str]] = []
    adapter = Adapter(run=lambda argv, **_kwargs: calls.append(argv))

    plan = adapter.plan(binding, action, observed)

    if action == "up" and not observed["registered"]:
        expected[0][-1] = str(definition)
    assert plan == expected
    assert calls == []


def test_logs_redact_environment_secret_values_and_bearer_credentials(tmp_path, monkeypatch):
    definition = tmp_path / "voice.plist"
    digest = _write_plist(definition)
    output = definition.with_suffix(".out.log")
    output.write_text(
        "opaque raw-api-key-value\nAuthorization: Bearer raw-bearer-value\n"
    )
    output.chmod(0o600)
    monkeypatch.setenv("SERVICE_API_KEY", "raw-api-key-value")

    rendered = "\n".join(Adapter().logs(_binding(definition, digest), 2))

    assert "raw-api-key-value" not in rendered
    assert "raw-bearer-value" not in rendered
    assert "<redacted>" in rendered


def test_discover_merges_registered_and_unloaded_launchagent_definitions(tmp_path):
    idle_label = "com.example.anvil.idle"
    registered = tmp_path / f"{LABEL}.plist"
    registered_digest = _write_plist(registered)
    idle = tmp_path / f"{idle_label}.plist"
    idle_digest = _write_plist(
        idle,
        label=idle_label,
        arguments=["/usr/bin/python3", "-m", "mlx_vlm.server", "--port=48124"],
        run_at_load=False,
        environment={"SERVICE_API_KEY": "raw-discovery-secret"},
    )

    def run(argv, **_kwargs):
        if argv == ["launchctl", "list"]:
            return subprocess.CompletedProcess(
                argv, 0, f"PID\tStatus\tLabel\n4242\t0\t{LABEL}\n", ""
            )
        if argv == ["launchctl", "print", f"gui/{os.getuid()}/{LABEL}"]:
            return subprocess.CompletedProcess(
                argv,
                0,
                f"path = {registered}\nstate = running\npid = 4242\n",
                "",
            )
        if argv == ["launchctl", "print", f"gui/{os.getuid()}/{idle_label}"]:
            return subprocess.CompletedProcess(argv, 113, "", "Could not find service")
        if argv == ["launchctl", "print-disabled", f"gui/{os.getuid()}"]:
            return subprocess.CompletedProcess(argv, 0, "", "")
        raise AssertionError(f"unexpected command: {argv!r}")

    discovered = Adapter(run=run, discovery_root=tmp_path).discover()

    assert discovered == [
        {
            "manager": "launchd",
            "identity": f"gui/{os.getuid()}/{idle_label}",
            "label": idle_label,
            "pid": None,
            "state": "unloaded",
            "registered": False,
            "running": False,
            "engine_hint": "mlx_vlm",
            "ports": [48124],
            "definition_sha256": idle_digest,
            "eligible_for_adoption": True,
        },
        {
            "manager": "launchd",
            "identity": f"gui/{os.getuid()}/{LABEL}",
            "label": LABEL,
            "pid": 4242,
            "state": "running",
            "registered": True,
            "running": True,
            "engine_hint": "mlx_lm",
            "ports": [48123],
            "definition_sha256": registered_digest,
            "eligible_for_adoption": True,
        },
    ]
    assert "ProgramArguments" not in repr(discovered)
    assert "EnvironmentVariables" not in repr(discovered)
    assert "/usr/bin/python3" not in repr(discovered)
    assert "raw-discovery-secret" not in repr(discovered)
