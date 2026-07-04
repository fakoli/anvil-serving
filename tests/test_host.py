"""Tests for `anvil-serving host` — WSL / Docker Desktop config with safety rails.

subprocess/`input`/`sys.platform`/`_wslconfig_path` are all injected or monkeypatched, so these run
on any OS with no docker, no WSL, no prompts.
"""
import types

from anvil_serving import host


def proc(rc=0, out="", err=""):
    return types.SimpleNamespace(returncode=rc, stdout=out, stderr=err)


def _bytes(gb):
    return str(int(gb * 1024 ** 3))


# ---- recommend_wsl_memory_gb (the safe-cap math) -----------------------------

def test_recommend_leaves_a_windows_reserve():
    assert host.recommend_wsl_memory_gb(93.7) == 80        # 93.7 - 14 -> 79.7 -> 80 (the RIGHT answer)
    assert host.recommend_wsl_memory_gb(64.0) == 50        # 64 - 14
    assert host.recommend_wsl_memory_gb(None) is None
    assert host.recommend_wsl_memory_gb(16.0) >= 4          # floor


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
    rc = host.cmd_restart_docker(force=True, _run=lambda a, **k: calls.append(a) or proc(0),
                                 _input=lambda p: "n")
    assert rc == 0
    assert any("taskkill" in c for c in calls)              # stops the failed/old instance
    assert any("start" in c for c in calls)                 # relaunches Docker Desktop


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


# ---- CLI dispatch ------------------------------------------------------------

def test_main_dispatches(monkeypatch):
    seen = {}
    monkeypatch.setattr(host, "cmd_wsl_config", lambda **k: seen.update(k) or 0)
    rc = host.main(["wsl-config", "--memory", "80", "--force"])
    assert rc == 0 and seen["memory_gb"] == 80 and seen["force"] is True
