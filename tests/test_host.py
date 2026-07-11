"""Tests for `anvil-serving host` — WSL / Docker Desktop config with safety rails.

subprocess/`input`/`sys.platform`/`_wslconfig_path` are all injected or monkeypatched, so these run
on any OS with no docker, no WSL, no prompts.
"""
import os
import json
import subprocess
import types

import pytest

from anvil_serving import host


def proc(rc=0, out="", err=""):
    return types.SimpleNamespace(returncode=rc, stdout=out, stderr=err)


@pytest.fixture(autouse=True)
def _powershell_present(monkeypatch):
    # Default: pretend Windows PowerShell is on PATH so _ps() reaches the injected _run on any CI/OS
    # (CI has no real powershell/pwsh). The resolver tests override this to exercise the fallback.
    monkeypatch.setattr(host.shutil, "which",
                        lambda exe: (r"C:\ps\%s" % exe) if exe == "powershell" else None)


def _bytes(gb):
    return str(int(gb * 1024 ** 3))


# ---- recommend_wsl_memory_gb (the safe-cap math) -----------------------------

def test_recommend_leaves_a_windows_reserve():
    assert host.recommend_wsl_memory_gb(93.7) == 80        # 93.7 - 14 -> 79.7 -> 80 (the RIGHT answer)
    assert host.recommend_wsl_memory_gb(64.0) == 50        # 64 - 14
    assert host.recommend_wsl_memory_gb(None) is None
    assert host.recommend_wsl_memory_gb(12.0) is None      # too small to leave the 10 GB floor -> no rec


def test_recommend_never_suggests_a_value_wsl_config_would_refuse():
    # doctor must not recommend a value cmd_wsl_config then rejects (the small-host contradiction).
    for h in (11.0, 12.0, 13.5, 14.0, 16.0, 32.0, 64.0, 93.7, 128.0, 256.0):
        r = host.recommend_wsl_memory_gb(h)
        assert r is None or (h - r) >= host.MIN_WINDOWS_RESERVE_GB, "%s -> %s starves Windows" % (h, r)


# ---- set_wslconfig_values (pure transform) -----------------------------------

def test_changes_only_memory_and_preserves_custom_lines():
    cfg = ("[wsl2]\nmemory=64GB\nkernel=C:\\ZFSonWSL\\bzImage\nswap=16GB\n"
           "networkingMode=mirrored\n[experimental]\nhostAddressLoopback=true\n")
    new, changes = host.set_wslconfig_values(cfg, memory_gb=80)
    assert "memory=80GB" in new and "memory=64GB" not in new
    assert "kernel=C:\\ZFSonWSL\\bzImage" in new            # custom kernel preserved
    assert "networkingMode=mirrored" in new                # networking preserved
    assert "[experimental]" in new and "hostAddressLoopback=true" in new
    assert any("80GB" in c for c in changes)


def test_changes_swap_too():
    new, _ = host.set_wslconfig_values("[wsl2]\nmemory=64GB\nswap=16GB\n", memory_gb=80, swap_gb=32)
    assert "memory=80GB" in new and "swap=32GB" in new


def test_inserts_missing_key_under_wsl2():
    new, changes = host.set_wslconfig_values("[wsl2]\nswap=8GB\n", memory_gb=80)
    assert "memory=80GB" in new and "swap=8GB" in new
    assert any("memory=80GB" in c for c in changes)


def test_creates_section_when_empty():
    new, _ = host.set_wslconfig_values("", memory_gb=80)
    assert new.startswith("[wsl2]") and "memory=80GB" in new


def test_no_change_when_already_set():
    _, changes = host.set_wslconfig_values("[wsl2]\nmemory=80GB\n", memory_gb=80)
    assert changes == []


def test_handles_section_header_with_trailing_comment():
    # `[wsl2]  # comment` must be recognized (edit in place), not treated as absent -> duplicate section.
    new, _ = host.set_wslconfig_values("[wsl2]   # main\nmemory=64GB\nprocessors=8\n", memory_gb=80)
    assert new.count("[wsl2]") == 1                         # no duplicate section
    assert "memory=80GB" in new and "processors=8" in new
    assert "memory=64GB" not in new


# ---- cmd_wsl_config: safe cap + backup + revert ------------------------------

def _win(monkeypatch, tmp_path):
    monkeypatch.setattr(host.sys, "platform", "win32")
    p = tmp_path / ".wslconfig"
    monkeypatch.setattr(host, "_wslconfig_path", lambda: str(p))
    return p


def _host94(argv, **k):
    return proc(0, _bytes(93.7))          # Win32_ComputerSystem TotalPhysicalMemory


def test_refuses_memory_that_starves_windows(monkeypatch, tmp_path, capsys):
    p = _win(monkeypatch, tmp_path); p.write_text("[wsl2]\nmemory=64GB\n", encoding="utf-8")
    rc = host.cmd_wsl_config(memory_gb=84, _run=_host94)     # 84 leaves 9.7 < 10 floor
    assert rc == 2
    assert "REFUSING" in capsys.readouterr().err
    assert p.read_text(encoding="utf-8") == "[wsl2]\nmemory=64GB\n"     # untouched
    assert host._backups(str(p)) == []                                  # no backup on refusal


def test_force_overrides_the_refusal(monkeypatch, tmp_path):
    p = _win(monkeypatch, tmp_path); p.write_text("[wsl2]\nmemory=64GB\n", encoding="utf-8")
    rc = host.cmd_wsl_config(memory_gb=84, force=True, _run=_host94)
    assert rc == 0 and "memory=84GB" in p.read_text(encoding="utf-8")


def test_safe_value_backs_up_then_writes(monkeypatch, tmp_path):
    p = _win(monkeypatch, tmp_path); p.write_text("[wsl2]\nmemory=64GB\n", encoding="utf-8")
    rc = host.cmd_wsl_config(memory_gb=80, _run=_host94)
    assert rc == 0
    baks = host._backups(str(p))
    assert len(baks) == 1
    assert "memory=64GB" in open(baks[0], encoding="utf-8").read()      # backup = the ORIGINAL
    assert "memory=80GB" in p.read_text(encoding="utf-8")               # live = the new


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


def test_force_overrides_unreadable_host_ram(monkeypatch, tmp_path):
    p = _win(monkeypatch, tmp_path); p.write_text("[wsl2]\nmemory=64GB\n", encoding="utf-8")
    rc = host.cmd_wsl_config(memory_gb=200, force=True, _run=lambda argv, **k: proc(1, "", "boom"))
    assert rc == 0 and "memory=200GB" in p.read_text(encoding="utf-8")


def test_backup_numbering_survives_a_gap(monkeypatch, tmp_path):
    # Deleting a middle backup must NOT make the next write collide with / overwrite an existing one.
    p = _win(monkeypatch, tmp_path); p.write_text("[wsl2]\nmemory=64GB\n", encoding="utf-8")
    for m in (70, 72, 74):                       # accrue .bak.1/.2/.3
        host.cmd_wsl_config(memory_gb=m, _run=_host94)
    baks = host._backups(str(p))
    assert len(baks) == 3
    os.remove(baks[1])                           # prune the MIDDLE backup (.bak.2)
    host.cmd_wsl_config(memory_gb=76, _run=_host94)   # next write must go to .bak.4, not clobber .bak.3
    nums = sorted(int(b.rsplit(".", 1)[-1]) for b in host._backups(str(p)))
    assert nums == [1, 3, 4]                      # .bak.3 preserved; new backup is .bak.4


def test_wsl_config_rejected_off_windows(monkeypatch, capsys):
    monkeypatch.setattr(host.sys, "platform", "linux")
    assert host.cmd_wsl_config(memory_gb=80) == 2
    assert "not applicable" in capsys.readouterr().err


# ---- cmd_restart_docker: confirm / force -------------------------------------

def test_restart_docker_declined_without_force(monkeypatch, capsys):
    monkeypatch.setattr(host.sys, "platform", "win32")
    rc = host.cmd_restart_docker(force=False, _run=lambda *a, **k: proc(0), _input=lambda p: "n")
    assert rc == 1 and "aborted" in capsys.readouterr().out


def test_restart_docker_force_kills_and_relaunches(monkeypatch):
    monkeypatch.setattr(host.sys, "platform", "win32")
    monkeypatch.setattr(host.os.path, "exists", lambda p: True)
    calls = []
    rc = host.cmd_restart_docker(force=True, _run=lambda a, **k: calls.append(a) or proc(0, "killed"),
                                 _input=lambda p: "n")
    assert rc == 0
    flat = [" ".join(c) for c in calls]
    assert any("Stop-Process" in c and "Docker Desktop" in c for c in flat)   # stops the old/failed instance
    assert any("Start-Process" in c for c in flat)                            # relaunches Docker Desktop


def test_restart_docker_macos_timeout_returns_failure(monkeypatch):
    monkeypatch.setattr(host.sys, "platform", "darwin")

    def timeout(argv, **kwargs):
        assert kwargs["timeout"] == host.DEFAULT_PROBE_TIMEOUT_SECONDS
        raise subprocess.TimeoutExpired(argv, kwargs["timeout"])

    assert host.cmd_restart_docker(force=True, _run=timeout) == 1


def test_restart_docker_macos_launch_failure_is_not_success(monkeypatch):
    monkeypatch.setattr(host.sys, "platform", "darwin")
    calls = []

    def run(argv, **_kwargs):
        calls.append(argv)
        return proc(1 if argv[0] == "open" else 0)

    assert host.cmd_restart_docker(force=True, _run=run) == 1


# ---- _kill_process: locale-independent status parsing ------------------------

def test_kill_process_parses_status_tokens():
    for token in ("killed", "notfound", "denied"):
        assert host._kill_process("x", _run=lambda a, **k: proc(0, token + "\n")) == token
    assert host._kill_process("x", _run=lambda a, **k: proc(0, "weird")) == "error"   # unknown -> error
    def boom(a, **k):
        raise OSError("no powershell")
    assert host._kill_process("x", _run=boom) == "error"                              # launch failed


def test_powershell_exe_prefers_powershell_then_falls_back_to_pwsh(monkeypatch):
    monkeypatch.setattr(host.shutil, "which", lambda e: "x" if e == "powershell" else None)
    assert host._powershell_exe() == "powershell"                 # prefer Windows PowerShell
    monkeypatch.setattr(host.shutil, "which", lambda e: "x" if e == "pwsh" else None)
    assert host._powershell_exe() == "pwsh"                       # fall back to PowerShell 7
    monkeypatch.setattr(host.shutil, "which", lambda e: None)
    assert host._powershell_exe() is None                         # neither on PATH


def test_ps_short_circuits_without_powershell(monkeypatch):
    monkeypatch.setattr(host.shutil, "which", lambda e: None)     # no powershell AND no pwsh
    ran = []
    assert host._ps("anything", _run=lambda *a, **k: ran.append(a) or proc(0)) is None
    assert ran == []                                              # never attempted to run
    # and the pwsh chosen exe reaches _run when present:
    monkeypatch.setattr(host.shutil, "which", lambda e: "x" if e == "pwsh" else None)
    seen = []
    host._ps("cmd", _run=lambda a, **k: seen.append(a[0]) or proc(0))
    assert seen == ["pwsh"]


# ---- cmd_reset_wsl: un-wedge a hung WSL subsystem ----------------------------

def test_reset_wsl_declined_without_force(monkeypatch, capsys):
    monkeypatch.setattr(host.sys, "platform", "win32")
    rc = host.cmd_reset_wsl(force=False, _run=lambda *a, **k: proc(0), _input=lambda p: "n")
    assert rc == 1 and "aborted" in capsys.readouterr().out


def test_reset_wsl_force_kills_vm_and_frontends_then_restarts(monkeypatch):
    monkeypatch.setattr(host.sys, "platform", "win32")
    monkeypatch.setattr(host.os.path, "exists", lambda p: True)   # for the inner restart-docker exe check
    calls = []
    rc = host.cmd_reset_wsl(force=True, _run=lambda a, **k: calls.append(a) or proc(0, "killed"),
                            _input=lambda p: "n")
    assert rc == 0
    flat = [" ".join(c) for c in calls]
    assert any("Stop-Process" in c and "vmmemWSL" in c for c in flat)          # force-kills the WSL VM
    assert any("Stop-Process" in c and "-Name 'wsl'" in c for c in flat)       # clears hung wsl.exe front-ends
    assert any("Start-Process" in c and "Docker Desktop.exe" in c for c in flat)   # rebuilds the backend


def test_reset_wsl_access_denied_returns_nonzero_and_prints_fallback(monkeypatch, capsys):
    monkeypatch.setattr(host.sys, "platform", "win32")
    monkeypatch.setattr(host.os.path, "exists", lambda p: True)
    def denied(argv, **k):
        j = " ".join(argv)
        if "Stop-Process" in j and "vmmemWSL" in j:
            return proc(0, "denied")             # _kill_process maps ErrorCategory -> 'denied'
        return proc(0, "killed")
    rc = host.cmd_reset_wsl(force=True, _run=denied, _input=lambda p: "n")
    out = capsys.readouterr().out
    assert rc == 1                               # couldn't kill the VM -> must NOT report success
    assert "Restart-Service WSLService -Force" in out            # elevated fallback surfaced
    assert "access denied" in out.lower()


def test_reset_wsl_propagates_restart_docker_failure(monkeypatch):
    # Docker Desktop.exe missing -> inner restart-docker returns nonzero -> reset-wsl must NOT report success.
    monkeypatch.setattr(host.sys, "platform", "win32")
    monkeypatch.setattr(host.os.path, "exists", lambda p: False)   # exe missing -> restart fails
    rc = host.cmd_reset_wsl(force=True, _run=lambda a, **k: proc(0, "killed"), _input=lambda p: "n")
    assert rc != 0


def test_reset_wsl_rejected_off_windows(monkeypatch, capsys):
    monkeypatch.setattr(host.sys, "platform", "linux")
    assert host.cmd_reset_wsl(force=True) == 2
    assert "Windows only" in capsys.readouterr().err


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


def test_host_status_prints_structured_summary(monkeypatch, capsys):
    monkeypatch.setattr(host, "host_summary", lambda **_kwargs: {"mutates": False, "checks": []})
    assert host.cmd_status() == 0
    assert json.loads(capsys.readouterr().out) == {"checks": [], "mutates": False}


def test_host_status_probes_are_time_bounded():
    def timeout(argv, **kwargs):
        assert kwargs["timeout"] == host.DEFAULT_PROBE_TIMEOUT_SECONDS
        raise subprocess.TimeoutExpired(argv, kwargs["timeout"])

    assert host._wsl_vm_memory_gb(_run=timeout) is None
    assert host._gpus(_run=timeout) == []


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


def test_wsl_meminfo_parses_proc_meminfo():
    mem = host.wsl_meminfo(_run=lambda a, **k: proc(0, _meminfo_text(64, 8, 50)))
    assert round(mem["total_gb"]) == 64 and round(mem["cached_gb"]) == 50
    assert round(mem["available_gb"]) == 8 and round(mem["used_gb"]) == 56   # total - available
    assert host.wsl_meminfo(_run=lambda a, **k: proc(1, "", "no wsl")) is None
    def boom(a, **k):
        raise OSError("wsl.exe not found")
    assert host.wsl_meminfo(_run=boom) is None


def test_wsl_meminfo_targets_the_requested_distro():
    seen = []
    host.wsl_meminfo(_run=lambda a, **k: seen.append(a) or proc(0, _meminfo_text()), distro="Ubuntu")
    assert seen[0][:3] == ["wsl", "-d", "Ubuntu"]


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


def test_memory_rejected_off_windows(monkeypatch, capsys):
    monkeypatch.setattr(host.sys, "platform", "linux")
    assert host.cmd_memory() == 2
    assert "Windows only" in capsys.readouterr().err


def test_reclaim_rejected_off_windows(monkeypatch, capsys):
    monkeypatch.setattr(host.sys, "platform", "linux")
    assert host.cmd_reclaim(force=True) == 2
    assert "Windows only" in capsys.readouterr().err


def test_reclaim_refuses_while_a_load_is_streaming(monkeypatch, capsys):
    # Cached grows 2 GB across the 2 s sample window (1 GB/s > 0.25 floor) -> a checkpoint is
    # streaming -> refuse, run nothing.
    monkeypatch.setattr(host.sys, "platform", "win32")
    box = _WslBox([50, 50, 52])                     # before, sample A, sample B
    rc = host.cmd_reclaim(_run=box, _input=lambda p: "y", _sleep=lambda s: None)
    assert rc == 2
    assert "REFUSING" in capsys.readouterr().err
    assert box.drops == []                          # drop_caches NOT run


def test_reclaim_force_overrides_streaming_and_prompt(monkeypatch):
    # The SAME growing fixture that makes the un-forced path refuse (test above): if --force ever
    # stops bypassing the streaming check, this samples 50 -> 52 (1 GB/s), refuses, and fails here.
    monkeypatch.setattr(host.sys, "platform", "win32")
    box = _WslBox([50, 50, 52])
    rc = host.cmd_reclaim(force=True, _run=box, _input=lambda p: "n", _sleep=lambda s: None)
    assert rc == 0 and len(box.drops) == 1
    j = " ".join(box.drops[0])
    assert "-u root" in j and "drop_caches" in j and "sync" in j


def test_reclaim_reports_before_and_after_cache(monkeypatch, capsys):
    monkeypatch.setattr(host.sys, "platform", "win32")
    box = _WslBox([50, 5])                          # before, after
    rc = host.cmd_reclaim(force=True, _run=box, _input=lambda p: "n", _sleep=lambda s: None)
    assert rc == 0
    assert "50.0 GB -> 5.0 GB" in capsys.readouterr().out


def test_reclaim_declined_without_confirmation(monkeypatch, capsys):
    monkeypatch.setattr(host.sys, "platform", "win32")
    box = _WslBox([50])                             # stable cache -> passes the streaming check
    rc = host.cmd_reclaim(_run=box, _input=lambda p: "n", _sleep=lambda s: None)
    assert rc == 1 and box.drops == []
    assert "aborted" in capsys.readouterr().out


def test_reclaim_fails_when_wsl_unreachable(monkeypatch, capsys):
    monkeypatch.setattr(host.sys, "platform", "win32")
    rc = host.cmd_reclaim(force=True, _run=lambda a, **k: proc(1, "", "no wsl"))
    assert rc == 1
    assert "cannot read" in capsys.readouterr().err


def test_reclaim_dry_run_runs_nothing(monkeypatch, capsys):
    monkeypatch.setattr(host.sys, "platform", "win32")
    box = _WslBox([50])
    rc = host.cmd_reclaim(dry_run=True, _run=box)
    assert rc == 0 and box.drops == []
    assert "drop_caches" in capsys.readouterr().out


def test_reclaim_watch_requires_threshold(monkeypatch, capsys):
    monkeypatch.setattr(host.sys, "platform", "win32")
    assert host.cmd_reclaim(watch=True, force=True) == 2
    assert "--threshold-gb" in capsys.readouterr().err


def test_reclaim_rejects_nonpositive_watch_values(monkeypatch, capsys):
    # interval <= 0 reaches time.sleep -> ValueError (or a busy loop); threshold <= 0 drops every
    # interval. Both must be usage errors, not crashes.
    monkeypatch.setattr(host.sys, "platform", "win32")
    box = _WslBox([50])
    assert host.cmd_reclaim(watch=True, threshold_gb=40, interval_s=-5, force=True, _run=box) == 2
    assert "--interval" in capsys.readouterr().err
    assert host.cmd_reclaim(watch=True, threshold_gb=0, force=True, _run=box) == 2
    assert "--threshold-gb" in capsys.readouterr().err
    assert box.drops == []


def test_reclaim_rejects_watch_flags_without_watch(monkeypatch, capsys):
    # `reclaim --threshold-gb 40` (forgot --watch) must NOT silently do a one-shot drop.
    monkeypatch.setattr(host.sys, "platform", "win32")
    box = _WslBox([50])
    assert host.cmd_reclaim(threshold_gb=40, force=True, _run=box) == 2
    assert "--watch" in capsys.readouterr().err
    assert box.drops == []


def test_reclaim_ctrl_c_at_prompt_or_sample_aborts_cleanly(monkeypatch, capsys):
    monkeypatch.setattr(host.sys, "platform", "win32")
    def ctrl_c(prompt):
        raise KeyboardInterrupt
    rc = host.cmd_reclaim(_run=_WslBox([50]), _input=ctrl_c, _sleep=lambda s: None)
    assert rc == 1 and "aborted" in capsys.readouterr().out          # prompt Ctrl-C
    def ctrl_c_sleep(seconds):
        raise KeyboardInterrupt
    rc = host.cmd_reclaim(_run=_WslBox([50, 50]), _input=lambda p: "y", _sleep=ctrl_c_sleep)
    assert rc == 1 and "aborted" in capsys.readouterr().out          # growth-sample Ctrl-C


def test_reclaim_watch_drops_over_threshold_then_stops_on_interrupt(monkeypatch, capsys):
    monkeypatch.setattr(host.sys, "platform", "win32")
    box = _WslBox([50, 5])                          # 50 >= 40 -> drop; 5 = post-drop read
    def interrupt(seconds):                         # the interval sleep = the user's Ctrl-C
        raise KeyboardInterrupt
    rc = host.cmd_reclaim(watch=True, threshold_gb=40, force=True, _run=box, _sleep=interrupt)
    assert rc == 0 and len(box.drops) == 1
    out = capsys.readouterr().out
    assert "dropped: 50.0 GB" in out and "watchdog stopped" in out


def test_reclaim_watch_fails_closed_when_growth_sample_unreadable(monkeypatch, capsys):
    # Unattended watchdog + unreadable growth sample (likeliest DURING a disk-saturating load):
    # must skip the tick, NOT drop (guard.py: fail closed when safety state can't be read).
    monkeypatch.setattr(host.sys, "platform", "win32")
    class FlakyBox(_WslBox):
        meminfo_calls = 0
        def __call__(self, argv, **k):
            if "/proc/meminfo" in " ".join(argv):
                self.meminfo_calls += 1
                if self.meminfo_calls > 1:               # threshold read ok, then samples fail
                    return proc(1, "", "wsl timed out")
            return super().__call__(argv, **k)
    box = FlakyBox([50])
    def sleep(seconds):
        if seconds != host._STREAM_SAMPLE_SECONDS:       # the interval sleep -> stop the loop
            raise KeyboardInterrupt
    rc = host.cmd_reclaim(watch=True, threshold_gb=40, _run=box, _input=lambda p: "y", _sleep=sleep)
    assert rc == 0 and box.drops == []                   # tick skipped, nothing dropped
    assert "streaming check unavailable" in capsys.readouterr().out


def test_reclaim_one_shot_warns_when_growth_sample_unreadable(monkeypatch, capsys):
    # One-shot stays fail-open (human at the prompt) but must SAY the guard didn't run.
    monkeypatch.setattr(host.sys, "platform", "win32")
    class FlakyBox(_WslBox):
        def __call__(self, argv, **k):
            j = " ".join(argv)
            if "/proc/meminfo" in j and len(self.cached) < 3 and "drop" not in j:
                if not self.drops:                       # growth samples fail; before-read succeeded
                    return proc(1, "", "wsl timed out")
            return super().__call__(argv, **k)
    box = FlakyBox([50, 50, 5])                          # before ok, samples fail, after ok
    rc = host.cmd_reclaim(_run=box, _input=lambda p: "y", _sleep=lambda s: None)
    assert rc == 0 and len(box.drops) == 1               # proceeded (fail-open)...
    assert "streaming check unavailable" in capsys.readouterr().err   # ...but never silently


def test_reclaim_yes_skips_prompt_but_keeps_streaming_refusal(monkeypatch, capsys):
    # guard.py's --yes/--force split: --yes = don't prompt; only --force disarms the safety check.
    monkeypatch.setattr(host.sys, "platform", "win32")
    box = _WslBox([50, 50, 52])                          # growing -> streaming
    rc = host.cmd_reclaim(yes=True, _run=box, _input=lambda p: "n", _sleep=lambda s: None)
    assert rc == 2 and box.drops == []                   # refusal NOT bypassed by --yes
    assert "REFUSING" in capsys.readouterr().err
    box2 = _WslBox([50, 50, 50, 5])                      # stable -> passes the check
    rc = host.cmd_reclaim(yes=True, _run=box2, _input=lambda p: "n", _sleep=lambda s: None)
    assert rc == 0 and len(box2.drops) == 1              # prompt skipped without --force


def test_reclaim_watch_waits_out_a_streaming_load(monkeypatch, capsys):
    # Over threshold but growing fast -> the watchdog must WAIT for the load, not drop under it.
    monkeypatch.setattr(host.sys, "platform", "win32")
    box = _WslBox([50, 50, 52])                     # check, sample A, sample B (streaming)
    calls = {"n": 0}
    def sleep(seconds):
        calls["n"] += 1
        if calls["n"] >= 2:                         # sample sleep, then interval sleep -> stop
            raise KeyboardInterrupt
    rc = host.cmd_reclaim(watch=True, threshold_gb=40, _run=box, _input=lambda p: "y", _sleep=sleep)
    assert rc == 0 and box.drops == []
    assert "waiting" in capsys.readouterr().out


# ---- CLI dispatch ------------------------------------------------------------

def test_main_dispatches(monkeypatch):
    seen = {}
    monkeypatch.setattr(host, "cmd_wsl_config", lambda **k: seen.update(k) or 0)
    rc = host.main(["wsl-config", "--memory", "80", "--force"])
    assert rc == 0 and seen["memory_gb"] == 80 and seen["force"] is True


def test_main_dispatches_reclaim_flags(monkeypatch):
    seen = {}
    monkeypatch.setattr(host, "cmd_reclaim", lambda **k: seen.update(k) or 0)
    rc = host.main(["reclaim", "--watch", "--threshold-gb", "40", "--interval", "15", "--force"])
    assert rc == 0 and seen["watch"] is True and seen["threshold_gb"] == 40
    assert seen["interval_s"] == 15 and seen["force"] is True


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
