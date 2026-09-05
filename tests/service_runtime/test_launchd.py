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


def test_describe_returns_only_pinned_identity_hash_engine_hint_and_ports(tmp_path):
    definition = tmp_path / "voice.plist"
    digest = _write_plist(definition)

    described = Adapter().describe(_binding(definition, digest))

    assert described == {
        "manager": "launchd",
        "identity": f"gui/{os.getuid()}/{LABEL}",
        "label": LABEL,
        "definition_sha256": digest,
        "engine_hint": "mlx_lm",
        "ports": [48123],
    }


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


def test_inspect_reports_running_registered_identity_and_disabled_state(tmp_path):
    definition = tmp_path / "voice.plist"
    digest = _write_plist(definition)
    binding = _binding(definition, digest)
    target = f"gui/{os.getuid()}/{LABEL}"
    printed = "\n".join(
        [
            f"{target} = {{",
            f"\tpath = {definition}",
            "\tstate = running",
            "\tpid = 4242",
            "}",
        ]
    )
    adapter = Adapter(
        run=_runner(
            print_result=subprocess.CompletedProcess([], 0, printed, ""),
            disabled=f'\tdisabled services = {{\n\t\t"{LABEL}" => true\n\t}}',
        )
    )

    observed = adapter.inspect(binding)

    assert observed == {
        "manager": "launchd",
        "identity": target,
        "registered": True,
        "running": True,
        "enabled": False,
        "pid": 4242,
        "state": "running",
    }


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


def test_inspect_maps_launchctl_not_running_state_to_idle(tmp_path):
    definition = tmp_path / "voice.plist"
    digest = _write_plist(definition)
    target = f"gui/{os.getuid()}/{LABEL}"
    printed = "\n".join(
        [
            f"{target} = {{",
            f"\tpath = {definition}",
            "\tstate = not running",
            "\truns = 0",
            "\tlast exit code = (never exited)",
            "}",
        ]
    )
    adapter = Adapter(
        run=_runner(print_result=subprocess.CompletedProcess([], 0, printed, ""))
    )

    observed = adapter.inspect(_binding(definition, digest))

    assert observed["registered"] is True
    assert observed["running"] is False
    assert observed["state"] == "waiting"
    assert observed["pid"] is None


def test_inspect_maps_top_level_spawn_scheduled_to_idle_despite_nested_active_state(
    tmp_path,
):
    definition = tmp_path / "voice.plist"
    digest = _write_plist(definition)
    target = f"gui/{os.getuid()}/{LABEL}"
    printed = "\n".join(
        [
            f"{target} = {{",
            f"\tpath = {definition}",
            "\tstate = spawn scheduled",
            "\tspawn type = daemon (3)",
            "\tproperties = {",
            "\t\tstate = active",
            "\t}",
            "}",
        ]
    )
    adapter = Adapter(
        run=_runner(print_result=subprocess.CompletedProcess([], 0, printed, ""))
    )

    observed = adapter.inspect(_binding(definition, digest))

    assert observed["registered"] is True
    assert observed["running"] is False
    assert observed["state"] == "waiting"
    assert observed["pid"] is None


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


def test_inspect_refuses_a_registered_definition_that_does_not_match(tmp_path):
    definition = tmp_path / "voice.plist"
    digest = _write_plist(definition)
    binding = _binding(definition, digest)

    target = f"gui/{os.getuid()}/{LABEL}"
    mismatched = Adapter(
        run=_runner(
            print_result=subprocess.CompletedProcess(
                [], 0, f"{target} = {{\npath = /private/other.plist\nstate = waiting\n}}", ""
            ),
        )
    )
    with pytest.raises(ServiceError) as identity:
        mismatched.inspect(binding)
    assert identity.value.code == "identity_mismatch"


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


def test_plan_relies_on_bootstrap_to_start_absent_run_at_load_service(tmp_path):
    definition = tmp_path / "voice.plist"
    digest = _write_plist(definition, run_at_load=True)

    plan = Adapter().plan(
        _binding(definition, digest),
        "up",
        {"registered": False, "running": False, "enabled": True},
    )

    assert plan == [
        ["launchctl", "bootstrap", f"gui/{os.getuid()}", str(definition)]
    ]


def test_plan_refuses_starting_a_disabled_service(tmp_path):
    definition = tmp_path / "voice.plist"
    digest = _write_plist(definition)

    with pytest.raises(ServiceError) as blocked:
        Adapter().plan(
            _binding(definition, digest),
            "up",
            {"registered": False, "running": False, "enabled": False},
        )

    assert blocked.value.code == "startup_disabled"


@pytest.mark.parametrize("action", ["up", "down", "restart", "enable", "disable"])
def test_plan_refuses_unknown_registration_state_before_mutation(tmp_path, action):
    definition = tmp_path / "voice.plist"
    digest = _write_plist(definition)

    with pytest.raises(ServiceError) as blocked:
        Adapter().plan(
            _binding(definition, digest),
            action,
            {"registered": None, "running": None, "enabled": None, "identity": None},
        )

    assert blocked.value.code == "state_unknown"


def test_plan_boots_out_a_registered_idle_service(tmp_path):
    definition = tmp_path / "voice.plist"
    digest = _write_plist(definition)
    identity = f"gui/{os.getuid()}/{LABEL}"

    plan = Adapter().plan(
        _binding(definition, digest),
        "down",
        {"registered": True, "running": False, "enabled": True, "identity": identity},
    )

    assert plan == [["launchctl", "bootout", identity]]


def test_plan_requires_the_exact_registered_launchd_identity(tmp_path):
    definition = tmp_path / "voice.plist"
    digest = _write_plist(definition)

    with pytest.raises(ServiceError) as mismatch:
        Adapter().plan(
            _binding(definition, digest),
            "down",
            {
                "registered": True,
                "running": True,
                "enabled": True,
                "identity": f"gui/{os.getuid()}/com.example.other",
            },
        )

    assert mismatch.value.code == "identity_mismatch"


def test_logs_reads_only_declared_regular_log_files_and_honors_tail(tmp_path):
    definition = tmp_path / "voice.plist"
    digest = _write_plist(definition)
    output = definition.with_suffix(".out.log")
    output.write_text("one\ntwo\nthree\n")
    output.chmod(0o600)
    error = definition.with_suffix(".err.log")
    error.write_text("four\n")
    error.chmod(0o600)

    lines = Adapter().logs(_binding(definition, digest), 2)

    assert lines == ["three", "four"]


def test_logs_bound_newline_free_records_by_encoded_bytes(tmp_path):
    definition = tmp_path / "voice.plist"
    digest = _write_plist(definition)
    output = definition.with_suffix(".out.log")
    output.write_text("start-" + "é" * 8_192 + "-end")
    output.chmod(0o600)

    lines = Adapter().logs(_binding(definition, digest), 1)

    assert len(lines) == 1
    assert len(lines[0].encode("utf-8")) <= 4 * 1024
    assert lines[0].endswith("-end")


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


def test_discover_keeps_registered_labels_without_definitions_ineligible(tmp_path):
    def run(argv, **_kwargs):
        assert argv == ["launchctl", "list"]
        return subprocess.CompletedProcess(
            argv,
            0,
            "PID\tStatus\tLabel\n4242\t0\tcom.example.anvil.voice\n-\t0\tunsafe label\n",
            "",
        )

    discovered = Adapter(run=run, discovery_root=tmp_path).discover()

    assert discovered == [
        {
            "manager": "launchd",
            "identity": f"gui/{os.getuid()}/{LABEL}",
            "label": LABEL,
            "pid": 4242,
            "state": "running",
            "registered": True,
            "running": True,
            "engine_hint": "unknown",
            "ports": [],
            "definition_sha256": None,
            "eligible_for_adoption": False,
        }
    ]


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


def test_discover_does_not_claim_unknown_registered_pid_is_stopped(tmp_path):
    label = "com.example.anvil.unknown"

    def run(argv, **_kwargs):
        return subprocess.CompletedProcess(argv, 0, f"not-a-pid\t0\t{label}\n", "")

    discovered = Adapter(run=run, discovery_root=tmp_path).discover()

    assert discovered[0]["registered"] is True
    assert discovered[0]["running"] is None
    assert discovered[0]["state"] == "unknown"
    assert discovered[0]["eligible_for_adoption"] is False


def test_discover_does_not_admit_an_unsafe_launchagent_definition(tmp_path):
    definition = tmp_path / f"{LABEL}.plist"
    _write_plist(definition)
    definition.chmod(0o666)

    def run(argv, **_kwargs):
        return subprocess.CompletedProcess(argv, 0, f"4242\t0\t{LABEL}\n", "")

    discovered = Adapter(run=run, discovery_root=tmp_path).discover()

    assert discovered[0]["registered"] is True
    assert discovered[0]["running"] is True
    assert discovered[0]["definition_sha256"] is None
    assert discovered[0]["eligible_for_adoption"] is False


def test_discover_exactly_inspects_definition_omitted_by_bounded_list(tmp_path):
    target_label = "com.example.zzz.target"
    definition = tmp_path / f"{target_label}.plist"
    digest = _write_plist(definition, label=target_label)
    prefix = "\n".join(
        f"-\t0\tcom.example.prefix.{index:03d}" for index in range(300)
    )
    target = f"gui/{os.getuid()}/{target_label}"
    calls: list[list[str]] = []

    def run(argv, **_kwargs):
        assert 0 < _kwargs["timeout"] <= 5
        calls.append(argv)
        if argv == ["launchctl", "list"]:
            return subprocess.CompletedProcess(argv, 0, prefix, "")
        if argv == ["launchctl", "print", target]:
            return subprocess.CompletedProcess(
                argv,
                0,
                f"path = {definition}\nstate = running\npid = 9123\n",
                "",
            )
        if argv == ["launchctl", "print-disabled", f"gui/{os.getuid()}"]:
            return subprocess.CompletedProcess(argv, 0, "", "")
        raise AssertionError(f"unexpected command: {argv!r}")

    discovered = Adapter(run=run, discovery_root=tmp_path).discover()
    target_record = next(record for record in discovered if record["label"] == target_label)

    assert target_record["registered"] is True
    assert target_record["running"] is True
    assert target_record["pid"] == 9123
    assert target_record["definition_sha256"] == digest
    assert target_record["eligible_for_adoption"] is True
    assert ["launchctl", "print", target] in calls


def test_discover_reports_exact_definition_inspection_failure_as_unknown(tmp_path):
    definition = tmp_path / f"{LABEL}.plist"
    _write_plist(definition)
    prefix = "\n".join(
        f"-\t0\tcom.example.prefix.{index:03d}" for index in range(300)
    )

    def run(argv, **_kwargs):
        if argv == ["launchctl", "list"]:
            return subprocess.CompletedProcess(argv, 0, prefix, "")
        if argv == ["launchctl", "print", f"gui/{os.getuid()}/{LABEL}"]:
            return subprocess.CompletedProcess(argv, 1, "", "Operation not permitted")
        raise AssertionError(f"unexpected command: {argv!r}")

    discovered = Adapter(run=run, discovery_root=tmp_path).discover()
    record = next(item for item in discovered if item["label"] == LABEL)

    assert len(discovered) == 256
    assert record["registered"] is None
    assert record["running"] is None
    assert record["state"] == "inaccessible"
    assert record["eligible_for_adoption"] is False
