"""Tests for `anvil-serving host` — WSL / Docker Desktop config with safety rails.

subprocess/`input`/`sys.platform`/`_wslconfig_path` are all injected or monkeypatched, so these run
on any OS with no docker, no WSL, no prompts.
"""
import json
import subprocess

import pytest

from anvil_serving import host
from tests.conftest import proc


@pytest.fixture(autouse=True)
def _powershell_present(monkeypatch):
    # Default: pretend Windows PowerShell is on PATH so _ps() reaches the injected _run on any CI/OS
    # (CI has no real powershell/pwsh). The resolver tests override this to exercise the fallback.
    monkeypatch.setattr(host.shutil, "which",
                        lambda exe: (r"C:\ps\%s" % exe) if exe == "powershell" else None)


def _bytes(gb):
    return str(int(gb * 1024 ** 3))


class _SharedMemoryRun:
    def __init__(self, scans, *, containers=None):
        self.scans = list(scans)
        self.containers = containers or []
        self.calls = []

    def __call__(self, argv, **_kwargs):
        self.calls.append(argv)
        if argv[:3] == ["docker", "ps", "-q"]:
            return proc(0, "\n".join(row["Id"] for row in self.containers))
        if argv[:2] == ["docker", "inspect"]:
            container_id = argv[2]
            row = next(row for row in self.containers if row["Id"] == container_id)
            return proc(0, json.dumps([row]))
        if argv[0] == "wsl" and "sh" in argv:
            return proc(0, self.scans.pop(0))
        if argv[0] == "wsl" and "rm" in argv:
            return proc(0)
        raise AssertionError("unexpected command: %r" % argv)


# ---- recommend_wsl_memory_gb (the safe-cap math) -----------------------------


# ---- set_wslconfig_values (pure transform) -----------------------------------


# ---- cmd_wsl_config: safe cap + backup + revert ------------------------------

def _win(monkeypatch, tmp_path):
    monkeypatch.setattr(host.sys, "platform", "win32")
    p = tmp_path / ".wslconfig"
    monkeypatch.setattr(host, "_wslconfig_path", lambda: str(p))
    return p


def _host94(argv, **k):
    return proc(0, _bytes(93.7))          # Win32_ComputerSystem TotalPhysicalMemory


def test_revert_restores_the_newest_backup(monkeypatch, tmp_path):
    p = _win(monkeypatch, tmp_path); p.write_text("[wsl2]\nmemory=64GB\n", encoding="utf-8")
    host.cmd_wsl_config(memory_gb=80, _run=_host94)
    rc = host.cmd_wsl_config(revert=True)
    assert rc == 0 and "memory=64GB" in p.read_text(encoding="utf-8")


def test_dry_run_writes_nothing(monkeypatch, tmp_path):
    p = _win(monkeypatch, tmp_path); p.write_text("[wsl2]\nmemory=64GB\n", encoding="utf-8")
    host.cmd_wsl_config(memory_gb=80, dry_run=True, _run=_host94)
    assert p.read_text(encoding="utf-8") == "[wsl2]\nmemory=64GB\n" and host._backups(str(p)) == []


def test_fails_closed_when_host_ram_unreadable(monkeypatch, tmp_path, capsys):
    # host RAM unreadable (PowerShell/WMI fails -> None) must REFUSE, not skip the cap (fail-open).
    p = _win(monkeypatch, tmp_path); p.write_text("[wsl2]\nmemory=64GB\n", encoding="utf-8")
    def unreadable(argv, **k):
        return proc(1, "", "boom")           # non-zero -> _host_total_gb returns None
    rc = host.cmd_wsl_config(memory_gb=200, _run=unreadable)
    assert rc == 2
    assert "cannot read host RAM" in capsys.readouterr().err
    assert p.read_text(encoding="utf-8") == "[wsl2]\nmemory=64GB\n"     # NOT written
    assert host._backups(str(p)) == []


# ---- cmd_restart_docker: confirm / force -------------------------------------


def test_restart_docker_macos_timeout_returns_failure(monkeypatch):
    monkeypatch.setattr(host.sys, "platform", "darwin")

    def timeout(argv, **kwargs):
        assert kwargs["timeout"] == host.DEFAULT_PROBE_TIMEOUT_SECONDS
        raise subprocess.TimeoutExpired(argv, kwargs["timeout"])

    assert host.cmd_restart_docker(force=True, _run=timeout) == 1


class _ReadinessClock:
    now = 0.0

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


@pytest.mark.parametrize(("command", "platform"), [
    (host.cmd_restart_docker, "win32"),
    (host.cmd_restart_docker, "darwin"),
    (host.cmd_reset_wsl, "win32"),
])
def test_desktop_recovery_waits_for_engine_after_launch(monkeypatch, command, platform):
    monkeypatch.setattr(host.sys, "platform", platform)
    monkeypatch.setattr(host, "_docker_desktop_executable", lambda: r"C:\Docker\Docker Desktop.exe")
    monkeypatch.setenv("DOCKER_HOST", "tcp://192.0.2.10:2376")
    monkeypatch.setenv("DOCKER_CONTEXT", "remote-fixture")
    clock = _ReadinessClock()
    probes = []

    def run(argv, **kwargs):
        if argv[0] != "docker":
            return proc(0, "killed")
        assert argv == ["docker", "--context", "desktop-linux", "info", "--format", "{{.OSType}}"]
        assert 0 < kwargs["timeout"] <= host.DEFAULT_PROBE_TIMEOUT_SECONDS
        probes.append(clock.now)
        return proc(1, err="engine starting") if len(probes) < 3 else proc(0, "linux\n")

    assert command(force=True, _run=run, _sleep=clock.sleep, _monotonic=clock.monotonic) == 0
    assert probes == [0, 2, 4]


@pytest.mark.parametrize(("command", "platform"), [
    (host.cmd_restart_docker, "win32"),
    (host.cmd_restart_docker, "darwin"),
    (host.cmd_reset_wsl, "win32"),
])
def test_desktop_recovery_launch_success_cannot_mask_unavailable_engine(
    monkeypatch, capsys, command, platform,
):
    monkeypatch.setattr(host.sys, "platform", platform)
    monkeypatch.setattr(host, "_docker_desktop_executable", lambda: r"C:\Docker\Docker Desktop.exe")
    clock = _ReadinessClock()
    probes = []

    def run(argv, **kwargs):
        if argv[0] != "docker":
            return proc(0, "killed")
        probes.append(kwargs["timeout"])
        clock.sleep(kwargs["timeout"])
        raise subprocess.TimeoutExpired(argv, kwargs["timeout"])

    assert command(force=True, _run=run, _sleep=clock.sleep, _monotonic=clock.monotonic) == 1
    assert clock.now == host.DOCKER_DESKTOP_READY_TIMEOUT_SECONDS
    assert probes and all(0 < timeout <= host.DEFAULT_PROBE_TIMEOUT_SECONDS for timeout in probes)
    assert probes[-1] < host.DEFAULT_PROBE_TIMEOUT_SECONDS
    assert "did not become ready" in capsys.readouterr().err


# ---- _kill_process: locale-independent status parsing ------------------------


# ---- cmd_reset_wsl: un-wedge a hung WSL subsystem ----------------------------

def test_reset_wsl_declined_without_force(monkeypatch, capsys):
    monkeypatch.setattr(host.sys, "platform", "win32")
    rc = host.cmd_reset_wsl(force=False, _run=lambda *a, **k: proc(0), _input=lambda p: "n")
    assert rc == 1 and "aborted" in capsys.readouterr().out


def test_reset_wsl_access_denied_returns_nonzero_and_prints_fallback(monkeypatch, capsys):
    monkeypatch.setattr(host.sys, "platform", "win32")
    monkeypatch.setattr(host.os.path, "isfile", lambda p: True)
    def denied(argv, **k):
        j = " ".join(argv)
        if "Stop-Process" in j and "vmmemWSL" in j:
            return proc(0, "denied")             # _kill_process maps ErrorCategory -> 'denied'
        return proc(0, "linux" if argv[0] == "docker" else "killed")
    rc = host.cmd_reset_wsl(force=True, _run=denied, _input=lambda p: "n")
    out = capsys.readouterr().out
    assert rc == 1                               # couldn't kill the VM -> must NOT report success
    assert "Restart-Service WSLService -Force" in out            # elevated fallback surfaced
    assert "access denied" in out.lower()


@pytest.mark.parametrize(
    ("action", "platform"),
    [("restart-docker", "win32"), ("restart-docker", "darwin"), ("reset-wsl", "win32")],
)
def test_disruptive_host_dry_run_never_launches(monkeypatch, capsys, action, platform):
    monkeypatch.setattr(host.sys, "platform", platform)
    def runner(*_args, **_kwargs):
        pytest.fail("host dry-run launched a process")
    if action == "restart-docker":
        rc = host.cmd_restart_docker(dry_run=True, _run=runner)
    else:
        rc = host.cmd_reset_wsl(dry_run=True, _run=runner)
    assert rc == 0
    assert json.loads(capsys.readouterr().out)["dry_run"] is True


# ---- cmd_doctor --------------------------------------------------------------

def test_doctor_reports_and_recommends(monkeypatch, capsys):
    monkeypatch.setattr(host.sys, "platform", "win32")     # use the powershell (DI'd) host-RAM path
    def fake(argv, **k):
        j = " ".join(argv)
        if "Win32_ComputerSystem" in j:
            return proc(0, _bytes(93.7))
        if argv[:2] == ["docker", "info"]:
            return proc(0, _bytes(62.8))
        if argv[0] == "nvidia-smi":
            return proc(0, "0, RTX 5090, 28330, 32607\n1, RTX PRO 6000, 87026, 97887\n")
        return proc(0)
    rc = host.cmd_doctor(_run=fake)
    out = capsys.readouterr().out
    assert rc == 0
    assert "RECOMMENDED WSL memory: 80 GB" in out           # the safe value, not 84
    assert "RTX PRO 6000" in out and "GPU 1" in out


# ---- wsl_meminfo / cmd_memory / cmd_reclaim (page-cache watchdog) -------------

def _meminfo_text(total_gb=64, avail_gb=8, cached_gb=50):
    def kb(gb):
        return int(gb * 1024 ** 2)
    return ("MemTotal:       %d kB\nMemFree:        %d kB\nMemAvailable:   %d kB\n"
            "Buffers:        1000 kB\nCached:         %d kB\n"
            % (kb(total_gb), kb(1), kb(avail_gb), kb(cached_gb)))


class _WslBox:
    """Injectable _run for the WSL page-cache commands: serves a sequence of Cached values to
    meminfo reads, records drop_caches invocations."""
    def __init__(self, cached_seq):
        self.cached = list(cached_seq)
        self.drops = []

    def __call__(self, argv, **k):
        j = " ".join(argv)
        if "drop_caches" in j:
            self.drops.append(argv)
            return proc(0)
        if "/proc/meminfo" in j:
            c = self.cached[0] if len(self.cached) == 1 else self.cached.pop(0)
            return proc(0, _meminfo_text(cached_gb=c))
        return proc(0)


def test_memory_reports_wsl_and_page_cache(monkeypatch, capsys):
    monkeypatch.setattr(host.sys, "platform", "win32")
    def fake(argv, **k):
        j = " ".join(argv)
        if "Win32_ComputerSystem" in j:
            return proc(0, _bytes(93.7))
        if "/proc/meminfo" in j:
            return proc(0, _meminfo_text(64, 8, 50))
        if argv[0] == "nvidia-smi":
            return proc(0, "0, RTX 5090, 28330, 32607\n")
        return proc(0)
    rc = host.cmd_memory(_run=fake)
    out = capsys.readouterr().out
    assert rc == 0
    assert "page cache:     50.0 GB" in out
    assert "RTX 5090" in out
    assert "host reclaim --confirm" in out          # cache > half the VM -> points at the fix


def test_reclaim_declined_without_confirmation(monkeypatch, capsys):
    monkeypatch.setattr(host.sys, "platform", "win32")
    box = _WslBox([50])                             # stable cache -> passes the streaming check
    rc = host.cmd_reclaim(_run=box, _input=lambda p: "n", _sleep=lambda s: None)
    assert rc == 1 and box.drops == []
    assert "aborted" in capsys.readouterr().out


def test_reclaim_dry_run_runs_nothing(monkeypatch, capsys):
    monkeypatch.setattr(host.sys, "platform", "win32")
    box = _WslBox([50])
    rc = host.cmd_reclaim(dry_run=True, _run=box)
    assert rc == 0 and box.drops == []
    assert "drop_caches" in capsys.readouterr().out


def test_reclaim_watch_drops_over_threshold_then_stops_on_interrupt(monkeypatch, capsys):
    monkeypatch.setattr(host.sys, "platform", "win32")
    box = _WslBox([50, 5])                          # 50 >= 40 -> drop; 5 = post-drop read
    def interrupt(seconds):                         # the interval sleep = the user's Ctrl-C
        raise KeyboardInterrupt
    rc = host.cmd_reclaim(watch=True, threshold_gb=40, force=True, _run=box, _sleep=interrupt)
    assert rc == 0 and len(box.drops) == 1
    out = capsys.readouterr().out
    assert "dropped: 50.0 GB" in out and "watchdog stopped" in out


# ---- vLLM native KV-offload shared-memory lifecycle ------------------------


def test_shared_memory_inspection_blocks_mapped_or_active_files(monkeypatch):
    monkeypatch.setattr(host.sys, "platform", "win32")
    path = "/dev/shm/vllm_offload_live.mmap"
    container_id = "a" * 12
    run = _SharedMemoryRun(
        [path + "\t1024\t763,764\n"],
        containers=[{
            "Id": container_id,
            "Name": "/deepseek",
            "Config": {"Env": ["KV_OFFLOADING_SIZE=8"], "Cmd": []},
            "Args": [],
        }],
    )

    report = host.inspect_vllm_offload_shared_memory(_run=run)

    assert report["available"] is True
    assert report["reclaimable_files"] == []
    assert report["active_containers"] == [{"id": container_id, "name": "deepseek"}]
    assert "native KV offload container is running" in report["blocked_reasons"]
    assert "offload mmap is mapped by a live process" in report["blocked_reasons"]


def test_shared_memory_reclaim_never_deletes_unrelated_paths(monkeypatch):
    monkeypatch.setattr(host.sys, "platform", "win32")
    offload_path = "/dev/shm/vllm_offload_orphan.mmap"
    unrelated_path = "/dev/shm/operator-owned.mmap"
    row = offload_path + "\t4096\t\n"
    run = _SharedMemoryRun([row, row, ""])

    result = host.reclaim_vllm_offload_shared_memory(confirm=True, _run=run)

    assert result["outcome"] == "reclaimed"
    rm = next(call for call in run.calls if "rm" in call)
    assert rm[-2:] == ["--", offload_path]
    assert unrelated_path not in rm


def test_shared_memory_command_honors_top_level_confirmation_scope(monkeypatch):
    monkeypatch.setattr(host.sys, "platform", "win32")
    path = "/dev/shm/vllm_offload_orphan.mmap"
    row = path + "\t4096\t\n"
    run = _SharedMemoryRun([row, row, ""])

    with host.guard.confirmation_scope(True):
        rc = host.cmd_shared_memory_reclaim(_run=run)

    assert rc == 0
    assert any("rm" in call for call in run.calls)


def test_container_native_offload_detection_is_explicit():
    row = {
        "Config": {"Env": ["KV_OFFLOADING_SIZE=8"], "Cmd": ["serve"]},
        "Args": [],
    }
    assert host.container_uses_native_kv_offload(
        "candidate", _run=lambda *_a, **_k: proc(0, json.dumps([row]))
    ) is True
    row["Config"]["Env"] = ["KV_OFFLOADING_SIZE=0"]
    assert host.container_uses_native_kv_offload(
        "candidate", _run=lambda *_a, **_k: proc(0, json.dumps([row]))
    ) is False


# ---- CLI dispatch ------------------------------------------------------------


def test_main_dispatches_status_and_disruptive_dry_run(monkeypatch):
    seen = []
    monkeypatch.setattr(host, "cmd_status", lambda: seen.append("status") or 0)
    monkeypatch.setattr(
        host,
        "cmd_restart_docker",
        lambda **kwargs: seen.append(("restart", kwargs)) or 0,
    )
    assert host.main(["status"]) == 0
    assert host.main(["restart-docker", "--dry-run"]) == 0
    assert seen == ["status", ("restart", {"force": False, "dry_run": True})]


def test_main_dispatches_shared_memory_commands(monkeypatch):
    seen = []
    monkeypatch.setattr(
        host, "cmd_shared_memory_status",
        lambda **kwargs: seen.append(("status", kwargs)) or 0,
    )
    monkeypatch.setattr(
        host, "cmd_shared_memory_reclaim",
        lambda **kwargs: seen.append(("reclaim", kwargs)) or 0,
    )
    assert host.main(["shared-memory-status"]) == 0
    assert host.main(["shared-memory-reclaim", "--confirm"]) == 0
    assert seen == [
        ("status", {"distro": "docker-desktop"}),
        ("reclaim", {"confirm": True, "distro": "docker-desktop"}),
    ]


# ---- persistent lifecycle cache-reclaim policy ------------------------------


def _auto_policy(**overrides):
    policy = {
        "enabled": True,
        "distro": "docker-desktop",
        "threshold_gb": 16.0,
        "source_path": "host.toml",
        "configured": True,
        "applicable": True,
        "schema_version": 1,
    }
    policy.update(overrides)
    return policy


def _sample(cached):
    return {"total_gb": 64.0, "used_gb": 40.0, "available_gb": 24.0,
            "cached_gb": cached}


def test_automatic_reclaim_readiness_timeout_skips_sampling():
    def runner(*_args, **_kwargs):
        pytest.fail("readiness timeout probed WSL")
    result = host.automatic_cache_reclaim(
        _auto_policy(), _sample(10), operation="load", readiness=False, _run=runner,
    )
    assert result["outcome"] == "readiness-timeout"
    assert result["before_cache_gb"] == 10


def test_automatic_reclaim_unavailable_below_threshold_and_no_growth():
    unavailable = host.automatic_cache_reclaim(
        _auto_policy(), None, operation="pull",
        _run=lambda *_args, **_kwargs: proc(1),
    )
    assert unavailable["outcome"] == "unavailable"

    below = _WslBox([12])
    result = host.automatic_cache_reclaim(
        _auto_policy(), _sample(10), operation="pull", _run=below,
    )
    assert result["outcome"] == "below-threshold"
    assert result["growth_gb"] == 2

    unchanged = _WslBox([20])
    result = host.automatic_cache_reclaim(
        _auto_policy(), _sample(19.5), operation="up", _run=unchanged,
    )
    assert result["outcome"] == "no-operation-growth"
    assert unchanged.drops == []


def test_automatic_reclaim_reports_drop_failure_and_renderer_warns(capsys):
    class FailedDrop(_WslBox):
        def __call__(self, argv, **kwargs):
            if "drop_caches" in " ".join(argv):
                self.drops.append(argv)
                return proc(1, err="denied")
            return super().__call__(argv, **kwargs)

    result = host.automatic_cache_reclaim(
        _auto_policy(), _sample(10), operation="load",
        _run=FailedDrop([20, 20]), _sleep=lambda _seconds: None,
    )
    assert result["outcome"] == "failed"
    host.render_cache_reclaim_result(result)
    assert "WARNING" in capsys.readouterr().err


# --------------------------------------------------------------------------- #
# host_summary — macOS unified-memory shape (2026-08-08)
# --------------------------------------------------------------------------- #

_DARWIN_VM_STAT = """Mach Virtual Memory Statistics: (page size of 16384 bytes)
Pages free:                              100000.
Pages inactive:                           50000.
Pages speculative:                        10000.
Pages active:                            200000.
"""


def _darwin_runner(outputs):
    """Runner stub keyed on the exact argv; unknown commands fail like a missing tool."""

    def run(argv, capture_output=True, text=True, timeout=None, check=False):
        key = " ".join(argv)
        out = outputs.get(key)

        class _Completed:
            returncode = 0 if out is not None else 1
            stdout = out or ""
            stderr = "" if out is not None else "missing"

        return _Completed()

    return run


def test_host_summary_darwin_reports_unified_memory(monkeypatch):
    monkeypatch.setattr(host.sys, "platform", "darwin")
    monkeypatch.setattr(host, "cache_reclaim_policy_summary", lambda path=None: {"applicable": False})
    outputs = {
        "sysctl -n hw.memsize": str(48 * 1024**3),
        "vm_stat": _DARWIN_VM_STAT,
        "memory_pressure -Q": "System-wide memory free percentage: 59%",
        "sysctl vm.swapusage": "vm.swapusage: total = 2048.00M  used = 512.00M  free = 1536.00M  (encrypted)",
        "hostname": "mac.example",
        "scutil --get ComputerName": "Mac",
        "scutil --get LocalHostName": "Mac",
        "sysctl -n hw.model": "Mac16,5",
        "sw_vers -productVersion": "27.0",
    }
    summary = host.host_summary(_run=_darwin_runner(outputs))
    assert summary["platform"] == "macos"
    assert summary["host_ram_gb"] == 48.0
    # memory_pressure -Q overrides the vm_stat-derived ratio: 100 - 59 = 41.0
    assert summary["unified_memory"]["memory_pressure_percent"] == 41.0
    assert summary["unified_memory"]["swap_used_gb"] == 0.5
    assert summary["identity"]["hardware_model"] == "Mac16,5"
    # Windows-only concepts are explicit nulls, never absent (cmd_doctor indexes them).
    assert summary["wsl_vm_memory_gb"] is None
    assert summary["gpus"] == []
    assert summary["windows_reserve"]["ceiling_gb"] is None
    checks = {c["name"]: c for c in summary["checks"]}
    assert checks["host_ram"]["ok"] is True
    assert checks["unified_memory_sample"]["ok"] is True
