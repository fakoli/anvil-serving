"""No-GPU tests for the read-only GPU-sharing capability inspector."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

from anvil_serving import cli, gpu_sharing


ROOT = Path(__file__).resolve().parents[1]


GPU_CSV = (
    "0, GPU-04d3b6e7-5691-3e86-1d34-c37999440cf1, NVIDIA GeForce RTX 5090\n"
    "1, GPU-d0f446cf-1771-414c-e116-a39138798a8c, NVIDIA RTX PRO 6000 Blackwell\n"
)
DETAIL_CSV = (
    "0, GPU-04d3b6e7-5691-3e86-1d34-c37999440cf1, NVIDIA GeForce RTX 5090, 12.0, 610.62\n"
    "1, GPU-d0f446cf-1771-414c-e116-a39138798a8c, NVIDIA RTX PRO 6000 Blackwell, 12.0, 610.62\n"
)
SM_CSV = (
    "0, GPU-04d3b6e7-5691-3e86-1d34-c37999440cf1, 170\n"
    "1, GPU-d0f446cf-1771-414c-e116-a39138798a8c, 188\n"
)
ROLES = (
    {"id": "fast", "uuid": "GPU-04d3b6e7-5691-3e86-1d34-c37999440cf1"},
    {"id": "heavy", "uuid": "GPU-d0f446cf-1771-414c-e116-a39138798a8c"},
)


def _gpu_run(*_args, **kwargs):
    assert kwargs["timeout"] <= gpu_sharing.MAX_TIMEOUT_SECONDS
    return GPU_CSV


def _symbols(runtime=True, driver=True):
    return lambda: {
        "runtime": {
            "library": "libcudart.so.13",
            "symbol": "cudaGreenCtxCreate",
            "present": runtime,
            "error": None,
        },
        "driver": {
            "library": "libcuda.so.1",
            "symbol": "cuGreenCtxCreate",
            "present": driver,
            "error": None,
        },
    }


class FakeCommands:
    def __init__(
        self,
        *,
        toolkit="13.1",
        driver_cuda="13.1",
        details=DETAIL_CSV,
        mps_servers=(0, "", ""),
        mps_partitions=(0, "GPU free used", ""),
        extended_failure=None,
    ):
        self.toolkit = toolkit
        self.driver_cuda = driver_cuda
        self.details = details
        self.mps_servers = mps_servers
        self.mps_partitions = mps_partitions
        self.extended_failure = extended_failure
        self.calls = []

    def __call__(self, argv, **kwargs):
        self.calls.append((tuple(argv), kwargs.get("input"), kwargs["timeout"]))
        assert kwargs["capture_output"] is True
        assert kwargs["text"] is True
        assert kwargs["check"] is False
        if argv[0] == "nvidia-smi" and len(argv) > 1:
            if "multiprocessor_count" in argv[1]:
                return SimpleNamespace(returncode=0, stdout=SM_CSV, stderr="")
            if self.extended_failure == "timeout":
                raise subprocess.TimeoutExpired(argv, kwargs["timeout"])
            return SimpleNamespace(returncode=0, stdout=self.details, stderr="")
        if argv == ["nvidia-smi"]:
            return SimpleNamespace(
                returncode=0,
                stdout=f"NVIDIA-SMI 610.62 CUDA Version: {self.driver_cuda}",
                stderr="",
            )
        if argv[-1:] == ["--version"]:
            return SimpleNamespace(
                returncode=0,
                stdout=f"Cuda compilation tools, release {self.toolkit}, V{self.toolkit}.0",
                stderr="",
            )
        if argv[0] == "/usr/bin/docker":
            return SimpleNamespace(returncode=0, stdout='"28.0.0"', stderr="")
        if argv == ["/usr/bin/nvidia-cuda-mps-control", "--help"]:
            return SimpleNamespace(
                returncode=0, stdout="--static-partitioning sm_partition lspart", stderr=""
            )
        if argv == ["/usr/bin/nvidia-cuda-mps-control"]:
            result = (
                self.mps_servers
                if kwargs.get("input") == "get_server_list\n"
                else self.mps_partitions
            )
            return SimpleNamespace(returncode=result[0], stdout=result[1], stderr=result[2])
        raise AssertionError(f"unexpected command: {argv!r}")


def _which(name):
    return {
        "nvcc": "/usr/bin/nvcc",
        "docker": "/usr/bin/docker",
        "nvidia-cuda-mps-control": "/usr/bin/nvidia-cuda-mps-control",
    }.get(name)


def _inspect(commands=None, **kwargs):
    commands = commands or FakeCommands()
    result = gpu_sharing.inspect_gpu_sharing(
        roles=ROLES,
        system="Linux",
        wsl=False,
        inside_container=False,
        _run=commands,
        _gpu_run=_gpu_run,
        _which=_which,
        _find_spec=lambda name: object() if name == "torch" else None,
        _symbol_probe=_symbols(),
        **kwargs,
    )
    return result, commands


def test_supported_runtime_and_static_mps_are_reported_with_stable_roles():
    result, _commands = _inspect()

    assert result["schema_version"] == 1
    assert result["operation"] == "gpu_sharing_inspect"
    assert result["mutated_state"] is False
    assert [(row["role"], row["uuid"], row["sm_count"]) for row in result["gpus"]] == [
        ("fast", ROLES[0]["uuid"], 170),
        ("heavy", ROLES[1]["uuid"], 188),
    ]
    assert {row["green_context"]["status"] for row in result["gpus"]} == {"supported"}
    assert {row["mps_static_partitioning"]["status"] for row in result["gpus"]} == {
        "supported"
    }
    assert result["frameworks"]["pytorch_green_context"]["status"] == "supported"
    assert result["frameworks"]["flashinfer_green_context"]["status"] == "unavailable"


def test_gpu_roles_follow_uuid_when_runtime_indexes_reorder():
    reordered_inventory = (
        "0, GPU-d0f446cf-1771-414c-e116-a39138798a8c, NVIDIA RTX PRO 6000 Blackwell\n"
        "1, GPU-04d3b6e7-5691-3e86-1d34-c37999440cf1, NVIDIA GeForce RTX 5090\n"
    )

    def reordered_gpu_run(*_args, **_kwargs):
        return reordered_inventory

    result = gpu_sharing.inspect_gpu_sharing(
        roles=ROLES,
        system="Linux",
        wsl=False,
        inside_container=False,
        _run=FakeCommands(),
        _gpu_run=reordered_gpu_run,
        _which=_which,
        _symbol_probe=_symbols(),
    )
    assert {row["uuid"]: row["role"] for row in result["gpus"]} == {
        ROLES[0]["uuid"]: "fast",
        ROLES[1]["uuid"]: "heavy",
    }


@pytest.mark.parametrize("toolkit", ["12.8", "13.0"])
def test_old_cuda_runtime_is_blocked(toolkit):
    result, _commands = _inspect(FakeCommands(toolkit=toolkit))
    assert {row["green_context"]["status"] for row in result["gpus"]} == {
        "blocked_by_runtime_version"
    }


def test_missing_green_context_symbol_is_unavailable():
    commands = FakeCommands()
    result = gpu_sharing.inspect_gpu_sharing(
        system="Linux",
        wsl=False,
        inside_container=False,
        _run=commands,
        _gpu_run=_gpu_run,
        _which=_which,
        _symbol_probe=_symbols(runtime=False),
    )
    assert {row["green_context"]["status"] for row in result["gpus"]} == {"unavailable"}


def test_missing_cuda_runtime_evidence_stays_unknown():
    commands = FakeCommands(toolkit="not-a-version")
    result = gpu_sharing.inspect_gpu_sharing(
        system="Linux",
        wsl=False,
        inside_container=False,
        _run=commands,
        _gpu_run=_gpu_run,
        _which=lambda name: None if name == "nvcc" else _which(name),
        _symbol_probe=_symbols(runtime=None),
    )
    assert result["environment"]["host_cuda_runtime"]["status"] == "unknown"
    assert {row["green_context"]["status"] for row in result["gpus"]} == {"unknown"}


def test_driver_runtime_mismatch_blocks_green_context():
    result, _commands = _inspect(FakeCommands(toolkit="13.2", driver_cuda="13.1"))
    assert {row["green_context"]["status"] for row in result["gpus"]} == {
        "blocked_by_runtime_version"
    }
    assert any("exceeds" in warning for warning in result["warnings"])


def test_mps_absent_stopped_and_unreadable_are_structured():
    absent = FakeCommands()
    result = gpu_sharing.inspect_gpu_sharing(
        system="Linux",
        wsl=False,
        inside_container=False,
        _run=absent,
        _gpu_run=_gpu_run,
        _which=lambda name: None if name == "nvidia-cuda-mps-control" else _which(name),
        _symbol_probe=_symbols(),
    )
    assert result["mps"]["daemon_status"] == "unavailable"
    assert {row["mps_static_partitioning"]["status"] for row in result["gpus"]} == {
        "unavailable"
    }

    stopped_commands = FakeCommands(
        mps_servers=(1, "", "Cannot find MPS control daemon process")
    )
    stopped, _ = _inspect(stopped_commands)
    assert stopped["mps"]["daemon_status"] == "stopped"

    class PermissionCommands(FakeCommands):
        def __call__(self, argv, **kwargs):
            if argv == ["/usr/bin/nvidia-cuda-mps-control"]:
                raise PermissionError("denied")
            return super().__call__(argv, **kwargs)

    unreadable, _ = _inspect(PermissionCommands())
    assert unreadable["mps"]["daemon_status"] == "unreadable"
    assert "MPS daemon query permission_denied" in unreadable["warnings"]


def test_wsl_is_unknown_and_native_windows_is_environment_blocked():
    commands = FakeCommands()
    wsl = gpu_sharing.inspect_gpu_sharing(
        system="Linux",
        wsl=True,
        inside_container=False,
        _run=commands,
        _gpu_run=_gpu_run,
        _which=_which,
        _symbol_probe=_symbols(),
    )
    assert {row["green_context"]["status"] for row in wsl["gpus"]} == {"unknown"}
    assert {row["mps_static_partitioning"]["status"] for row in wsl["gpus"]} == {
        "unknown"
    }

    windows = gpu_sharing.inspect_gpu_sharing(
        system="Windows",
        wsl=False,
        inside_container=False,
        _run=FakeCommands(),
        _gpu_run=_gpu_run,
        _which=_which,
        _symbol_probe=_symbols(),
    )
    assert {row["mps_static_partitioning"]["status"] for row in windows["gpus"]} == {
        "blocked_by_environment"
    }


def test_malformed_and_timed_out_gpu_details_degrade_without_traceback():
    malformed, _ = _inspect(FakeCommands(details="bad,row"))
    assert malformed["gpus"][0]["compute_capability"] is None
    assert any("malformed GPU row" in warning for warning in malformed["warnings"])

    timed_out, _ = _inspect(FakeCommands(extended_failure="timeout"))
    assert len(timed_out["gpus"]) == 2
    assert "extended NVIDIA GPU query timeout" in timed_out["warnings"]


def test_only_bounded_non_mutating_commands_are_invoked_and_json_is_stable():
    result, commands = _inspect()
    for argv, input_text, timeout in commands.calls:
        assert 0 < timeout <= gpu_sharing.MAX_TIMEOUT_SECONDS
        joined = " ".join(argv).lower()
        assert not any(token in joined for token in (" -d", " start", " quit", " add", " rm"))
        if "nvidia-cuda-mps-control" in argv[0] and input_text is not None:
            assert input_text in {"get_server_list\n", "lspart\n"}

    first = json.dumps(result, indent=2, sort_keys=True, ensure_ascii=True)
    second, _ = _inspect()
    assert first == json.dumps(second, indent=2, sort_keys=True, ensure_ascii=True)


def test_timeout_bounds_and_cli_help_contract(capsys):
    with pytest.raises(ValueError, match="timeout"):
        gpu_sharing.inspect_gpu_sharing(timeout=61)

    assert cli.main(["host", "gpu-sharing", "inspect", "--help"]) == 0
    output = capsys.readouterr().out
    assert "--timeout" in output
    assert "--topology" in output

    assert cli.main(["host", "gpu-sharing", "probe", "--help"]) == 0
    output = capsys.readouterr().out
    assert "--gpu-uuid" in output
    assert "--compose-file" in output
    assert "--confirm" in output


def _rendered_probe_service(gpu_uuid=ROLES[0]["uuid"]):
    return {
        "services": {
            "gpu-sharing-inspect": {
                "profiles": ["gpu-sharing-probe"],
                "image": gpu_sharing.DEFAULT_PROBE_IMAGE,
                "platform": "linux/amd64",
                "entrypoint": ["/bin/bash", "-lc"],
                "read_only": True,
                "restart": "no",
                "cap_drop": ["ALL"],
                "security_opt": ["no-new-privileges:true"],
                "environment": {"CUDA_VISIBLE_DEVICES": gpu_uuid},
                "deploy": {
                    "resources": {
                        "reservations": {
                            "devices": [{"device_ids": [gpu_uuid]}]
                        }
                    }
                },
                "volumes": [
                    {
                        "type": "bind",
                        "source": str(ROOT / "examples/fakoli-dark/gpu-sharing"),
                        "target": "/opt/anvil-gpu-sharing",
                        "read_only": True,
                    }
                ],
                "command": [
                    "nvcc -std=c++17 -O2 /opt/anvil-gpu-sharing/inspect.cu "
                    "-lcuda -ldl -o /run/anvil/gpu-sharing-inspect "
                    "&& /run/anvil/gpu-sharing-inspect"
                ],
            }
        }
    }


class FakeProbeCommands:
    def __init__(self, rendered=None):
        self.rendered = rendered or _rendered_probe_service()
        self.calls = []

    def __call__(self, argv, **kwargs):
        self.calls.append((list(argv), kwargs))
        assert kwargs["env"]["FAST_GPU_UUID"] == ROLES[0]["uuid"]
        assert kwargs["cwd"] == str(ROOT / "examples/fakoli-dark")
        if "config" in argv:
            return SimpleNamespace(returncode=0, stdout=json.dumps(self.rendered), stderr="")
        result = {
            "ok": True,
            "mutated_state": False,
            "created_context": False,
            "launched_workload": False,
            "cuda_visible_devices": ROLES[0]["uuid"],
            "gpu": {"uuid": ROLES[0]["uuid"]},
        }
        return SimpleNamespace(returncode=0, stdout=json.dumps(result), stderr="")


def test_product_probe_previews_then_runs_reviewed_compose_contract():
    compose_file = ROOT / "examples/fakoli-dark/docker-compose.experiment.yml"
    commands = FakeProbeCommands()

    preview = gpu_sharing.probe_gpu_sharing(
        compose_file=compose_file,
        gpu_uuid=ROLES[0]["uuid"],
        dry_run=True,
        _run=commands,
        _which=lambda name: "docker" if name == "docker" else None,
    )
    assert preview["ok"] is True
    assert preview["executed"] is False
    assert preview["safety_contract"]["primary_display_safe_only_for_inspection"] is True
    assert len(commands.calls) == 1

    live = gpu_sharing.probe_gpu_sharing(
        compose_file=compose_file,
        gpu_uuid=ROLES[0]["uuid"],
        dry_run=False,
        _run=commands,
        _which=lambda name: "docker" if name == "docker" else None,
    )
    assert live["ok"] is True
    assert live["executed"] is True
    assert live["result"]["created_context"] is False
    run_argv = commands.calls[-1][0]
    assert run_argv[-4:] == ["run", "--rm", "--no-deps", "gpu-sharing-inspect"]


def test_product_probe_refuses_uuid_drift_or_weakened_container_safety():
    compose_file = ROOT / "examples/fakoli-dark/docker-compose.experiment.yml"
    rendered = _rendered_probe_service("GPU-11111111-1111-1111-1111-111111111111")
    rendered["services"]["gpu-sharing-inspect"]["read_only"] = False

    with pytest.raises(ValueError, match="unsafe GPU-sharing probe configuration"):
        gpu_sharing.probe_gpu_sharing(
            compose_file=compose_file,
            gpu_uuid=ROLES[0]["uuid"],
            _run=FakeProbeCommands(rendered),
            _which=lambda name: "docker" if name == "docker" else None,
        )


def test_product_probe_refuses_unreviewed_image_or_command():
    compose_file = ROOT / "examples/fakoli-dark/docker-compose.experiment.yml"
    rendered = _rendered_probe_service()
    service = rendered["services"]["gpu-sharing-inspect"]
    service["image"] = "nvidia/cuda:latest"
    service["command"] = [service["command"][0] + " && echo unsafe"]

    with pytest.raises(ValueError, match="exact reviewed digest"):
        gpu_sharing.probe_gpu_sharing(
            compose_file=compose_file,
            gpu_uuid=ROLES[0]["uuid"],
            _run=FakeProbeCommands(rendered),
            _which=lambda name: "docker" if name == "docker" else None,
        )


def test_compose_probe_is_profile_gated_uuid_pinned_and_non_mutating():
    compose = (ROOT / "examples/fakoli-dark/docker-compose.experiment.yml").read_text(
        encoding="utf-8"
    )
    source = (ROOT / "examples/fakoli-dark/gpu-sharing/inspect.cu").read_text(
        encoding="utf-8"
    )

    service = compose.split("  gpu-sharing-inspect:\n", 1)[1].split(
        "\n  # Managed voice-latency candidates", 1
    )[0]
    assert 'profiles: ["gpu-sharing-probe"]' in service
    assert "platform: linux/amd64" in service
    assert "FAST_GPU_UUID:-GPU-04d3b6e7-5691-3e86-1d34-c37999440cf1" in service
    assert "read_only: true" in service
    assert "cap_drop:" in service and '["ALL"]' in service
    assert "ports:" not in service
    assert "inspect.cu" in service

    assert "cuInit(0)" in source
    assert "created_context\\\": false" in source
    assert "launched_workload\\\": false" in source
    for forbidden_call in (
        "cudaGreenCtxCreate(",
        "cuGreenCtxCreate(",
        "cudaExecutionCtxStreamCreate(",
        "cuGreenCtxStreamCreate(",
    ):
        assert forbidden_call not in source
