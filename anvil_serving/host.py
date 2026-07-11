"""anvil-serving host - own the HOST (WSL / Docker Desktop) config, not just the containers.

Closes the "reach for raw `wsl` / hand-edit `.wslconfig` / restart Docker Desktop" gap so anvil is
the one-stop shop. Bakes in the safety rails that a 2026-07-04 incident taught the hard way (a
hand-set `memory=84GB` on a 93.7 GB host starved Windows, Docker Desktop failed to start, and a
`wsl --shutdown` retry loop wedged the WSL subsystem):

  doctor          inspect host RAM / GPUs / the WSL-VM memory cap, and RECOMMEND a SAFE WSL memory
                  (host - a Windows reserve) - enough headroom for a big-model load without OOM'ing Windows.
  wsl-config      edit `%USERPROFILE%\\.wslconfig` `memory`/`swap` (Windows). BACKS UP first, changes ONLY
                  those lines (preserves a custom kernel/networking), and REFUSES a value that leaves less
                  than the Windows floor (unless --force). `--revert` restores the newest backup.
  restart-docker  restart Docker Desktop - the RIGHT way to apply a WSL-backend memory change. NOT
                  `wsl --shutdown` (it does not cycle the docker-desktop distro and, in a retry loop, can
                  wedge WSL). Confirms unless --force.
  reset-wsl       un-wedge a HUNG WSL subsystem (`wsl` times out, Docker Desktop can't start): force-kill
                  the WSL VM (vmmemWSL) + hung `wsl.exe`, then restart Docker Desktop. Codifies the manual
                  Task-Manager 'End task on vmmemWSL' recovery; prints the elevated fallback if kill is denied.
  memory          show host RAM, the WSL VM's used/page-cache/available (via `/proc/meminfo` inside the
                  distro), and GPU VRAM. The page-cache line is the one that matters during bakeoffs:
                  repeated 60-90 GB weight streams balloon it until Windows starves.
  reclaim         drop the WSL VM's page cache (`sync && echo 3 > /proc/sys/vm/drop_caches` as root inside
                  the distro) - the safe manual remediation from the 2026-07-10/11 Blackwell bakeoff,
                  promoted per "operational utilities belong in anvil-serving". Confirms unless
                  --confirm/--force; REFUSES while a model load is actively streaming (page cache growing
                  fast) unless --force. `--watch --threshold-gb N [--interval S]` runs it as a foreground
                  watchdog because `autoMemoryReclaim=gradual` lags load bursts.

Cross-cutting standards (the user's directive): back up before any change, offer a revert, confirm
before a disruptive action + a --force for autonomous runs. stdlib-only; subprocess/`input` are
dependency-injected so tests run with no docker, no WSL, no prompts.
"""
import argparse
import os
import shutil
import subprocess
import sys
import time
from . import guard

# Leave AT LEAST this much for Windows (hard floor: refuse a WSL memory that leaves less, unless --force).
MIN_WINDOWS_RESERVE_GB = 10
# The doctor's RECOMMENDED reserve (more generous - room for AV scans / Windows Update / cache spikes).
RECOMMENDED_WINDOWS_RESERVE_GB = 14


# --------------------------------------------------------------------------- #
# resource inspection
# --------------------------------------------------------------------------- #

def _host_total_gb(_run=subprocess.run):
    """Total physical host RAM in GB, or None if it can't be read."""
    if sys.platform == "win32":
        r = _ps("(Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory", _run)
        if r is not None and r.returncode == 0:
            try:
                return int((r.stdout or "").strip()) / (1024 ** 3)
            except ValueError:
                return None
        return None
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    return int(line.split()[1]) / (1024 ** 2)  # kB -> GB
    except OSError:
        return None
    return None


def _wsl_vm_memory_gb(_run=subprocess.run):
    """The WSL/docker VM's current memory cap (docker info MemTotal), or None. On WSL2 this reflects
    the `.wslconfig` memory the docker-desktop distro actually booted with."""
    try:
        r = _run(["docker", "info", "--format", "{{.MemTotal}}"], capture_output=True, text=True)
        if r.returncode == 0:
            val = int((r.stdout or "").strip()) / (1024 ** 3)
            return val if val > 0 else None   # 0 => docker/WSL not up yet
    except (OSError, ValueError):
        pass
    return None


def _gpus(_run=subprocess.run):
    """[(index, name, used_gb, total_gb)] from nvidia-smi, or []."""
    try:
        r = _run(["nvidia-smi", "--query-gpu=index,name,memory.used,memory.total",
                  "--format=csv,noheader,nounits"], capture_output=True, text=True)
    except OSError:
        return []
    out = []
    for line in (r.stdout or "").splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) >= 4:
            try:
                out.append((parts[0], parts[1], int(parts[2]) / 1024, int(parts[3]) / 1024))
            except ValueError:
                continue
    return out


# --------------------------------------------------------------------------- #
# WSL page-cache inspection + reclaim (the 2026-07-10/11 Blackwell-bakeoff watchdog)
# --------------------------------------------------------------------------- #

# `Cached` growing faster than this means a model checkpoint is STREAMING through the page cache
# RIGHT NOW (a 60-90 GB load runs well above it; idle churn is ~MB/s) -> reclaim refuses unless
# --force, because dropping caches mid-load evicts pages the loader is about to reuse.
# ponytail: fixed heuristic; make it a flag if a slow disk ever streams below 0.25 GB/s.
STREAMING_CACHE_GROWTH_GBPS = 0.25
_STREAM_SAMPLE_SECONDS = 2.0
DROP_CACHES_CMD = "sync && echo 3 > /proc/sys/vm/drop_caches"


def _wsl_argv(tail, distro=None):
    return ["wsl"] + (["-d", distro] if distro else []) + tail


def wsl_meminfo(_run=subprocess.run, distro=None):
    """{'total_gb','used_gb','available_gb','cached_gb'} from the WSL VM's `/proc/meminfo`, or None
    if WSL is unreachable. All distros share ONE VM/kernel, so any running distro sees VM-wide
    memory - the default distro is fine even though docker-desktop is a separate distro."""
    try:
        r = _run(_wsl_argv(["-e", "cat", "/proc/meminfo"], distro),
                 capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if r is None or r.returncode != 0:
        return None
    kb = {}
    for line in (r.stdout or "").splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0].endswith(":"):
            try:
                kb[parts[0][:-1]] = int(parts[1])
            except ValueError:
                continue
    if "MemTotal" not in kb:
        return None
    def gb(key):
        return kb[key] / (1024 ** 2) if key in kb else None
    total, avail = gb("MemTotal"), gb("MemAvailable")
    return {
        "total_gb": total,
        "used_gb": None if avail is None else total - avail,
        "available_gb": avail,
        "cached_gb": gb("Cached"),
    }


def _cache_growth_gbps(_run=subprocess.run, distro=None, _sleep=time.sleep):
    """GB/s the WSL page cache grew over a short window - the load-in-flight signal. None if
    meminfo is unreadable (callers must decide fail-open vs fail-closed explicitly)."""
    a = wsl_meminfo(_run=_run, distro=distro)
    if a is None or a["cached_gb"] is None:
        return None
    _sleep(_STREAM_SAMPLE_SECONDS)
    b = wsl_meminfo(_run=_run, distro=distro)
    if b is None or b["cached_gb"] is None:
        return None
    return (b["cached_gb"] - a["cached_gb"]) / _STREAM_SAMPLE_SECONDS


def _drop_caches(distro, _run):
    """`sync && echo 3 > /proc/sys/vm/drop_caches` as root inside the distro. True on success.
    Only clean (already-written) cache pages are evicted, so this is data-safe; `sync` first
    flushes any dirty pages. Generous timeout: sync can take a while under write load."""
    try:
        r = _run(_wsl_argv(["-u", "root", "-e", "sh", "-c", DROP_CACHES_CMD], distro),
                 capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return r is not None and r.returncode == 0


def recommend_wsl_memory_gb(host_gb, reserve_gb=RECOMMENDED_WINDOWS_RESERVE_GB):
    """A SAFE WSL memory: host - a Windows reserve, rounded to the nearest 2 GB, and CLAMPED to the
    largest value wsl-config will accept (host - the hard floor). None if the host is too small to
    leave even the hard floor - so `doctor` never recommends a value wsl-config would refuse. On a
    93.7 GB host with the default 14 GB reserve this yields 80 GB (not the 84 GB that broke it)."""
    if not host_gb:
        return None
    ceiling = int(host_gb - MIN_WINDOWS_RESERVE_GB)   # largest value wsl-config won't refuse
    if ceiling < 4:
        return None                                    # host too small to leave even the hard floor
    val = round((host_gb - reserve_gb) / 2) * 2        # nearest 2 GB
    return max(4, min(int(val), ceiling))


def _fmt(gb):
    return "?" if gb is None else ("%.1f GB" % gb)


def host_summary(_run=subprocess.run):
    """Return read-only WSL/Docker/GPU host checks as structured data."""
    host = _host_total_gb(_run=_run)
    wsl = _wsl_vm_memory_gb(_run=_run)
    gpus = _gpus(_run=_run)
    rec = recommend_wsl_memory_gb(host)
    reserve = {
        "minimum_gb": MIN_WINDOWS_RESERVE_GB,
        "recommended_gb": RECOMMENDED_WINDOWS_RESERVE_GB,
        "recommended_would_leave_gb": None if rec is None or host is None else round(host - rec, 1),
        "ceiling_gb": None if host is None else int(host - MIN_WINDOWS_RESERVE_GB),
    }
    gpu_rows = [
        {
            "index": index,
            "name": name,
            "memory_used_gb": used,
            "memory_total_gb": total,
        }
        for index, name, used, total in gpus
    ]
    checks = [
        {"name": "host_ram", "ok": host is not None, "value_gb": host},
        {"name": "docker_wsl_memory", "ok": wsl is not None, "value_gb": wsl},
        {"name": "gpu_inventory", "ok": bool(gpus), "count": len(gpus)},
    ]
    return {
        "mutates": False,
        "host_ram_gb": host,
        "wsl_vm_memory_gb": wsl,
        "docker": {
            "available": wsl is not None,
            "memory_cap_gb": wsl,
        },
        "gpus": gpu_rows,
        "recommended_wsl_memory_gb": rec,
        "windows_reserve": reserve,
        "checks": checks,
    }


# --------------------------------------------------------------------------- #
# process control (PowerShell — consistent with _host_total_gb, and locale-independent:
# outcomes come from PowerShell's ErrorCategory enum, not taskkill's localized text)
# --------------------------------------------------------------------------- #

def _powershell_exe():
    """The PowerShell executable to use: Windows PowerShell (`powershell`) or PowerShell 7 (`pwsh`),
    or None if neither is on PATH. Resolving both means the host verb works on a box where only pwsh
    is installed (legacy Windows PowerShell removed) instead of just failing closed."""
    for exe in ("powershell", "pwsh"):
        if shutil.which(exe):
            return exe
    return None


def _ps(script, _run=subprocess.run, timeout=15):
    """Run a PowerShell one-liner via `powershell` or `pwsh`. Returns the CompletedProcess, or None if
    neither PowerShell is on PATH or the call fails/times out."""
    exe = _powershell_exe()
    if exe is None:
        return None
    try:
        return _run([exe, "-NoProfile", "-NonInteractive", "-Command", script],
                    capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        return None


def _kill_process(name, _run=subprocess.run):
    """Force-kill every process whose image name is `name` (no `.exe`). Returns one of
    'killed' | 'notfound' | 'denied' | 'error'. Detection is via PowerShell's ErrorCategory (an enum),
    so it is LOCALE-INDEPENDENT — unlike parsing taskkill's 'Access is denied' text."""
    script = ("try { Stop-Process -Name '%s' -Force -ErrorAction Stop; 'killed' } "
              "catch { switch ($_.CategoryInfo.Category) "
              "{ 'PermissionDenied' { 'denied' } 'ObjectNotFound' { 'notfound' } default { 'error' } } }"
              % name)
    r = _ps(script, _run)
    if r is None:
        return "error"
    token = ((r.stdout or "").strip().splitlines() or ["error"])[-1].strip()
    return token if token in ("killed", "notfound", "denied") else "error"


# --------------------------------------------------------------------------- #
# .wslconfig editing (Windows) - pure transform + backup/revert
# --------------------------------------------------------------------------- #

def _wslconfig_path():
    return os.path.join(os.path.expanduser("~"), ".wslconfig")


def set_wslconfig_values(content, memory_gb=None, swap_gb=None):
    """Return `content` with ONLY the `memory=`/`swap=` lines under `[wsl2]` changed (or inserted if
    absent), preserving every other line (custom kernel, networkingMode, [experimental], comments).
    Returns (new_content, changes: list[str])."""
    lines = content.splitlines()
    changes = []
    want = {}
    if memory_gb is not None:
        want["memory"] = "memory=%dGB" % int(memory_gb)
    if swap_gb is not None:
        want["swap"] = "swap=%dGB" % int(swap_gb)
    seen = set()
    out = []
    in_wsl2 = False
    wsl2_idx = None
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("[") and "]" in stripped:
            # match the leading [token] only, tolerating a trailing comment/space after the ']'
            section = stripped[1:].split("]", 1)[0].strip().lower()
            in_wsl2 = (section == "wsl2")
            if in_wsl2:
                wsl2_idx = len(out)
        key = stripped.split("=", 1)[0].strip().lower() if "=" in stripped else None
        if in_wsl2 and key in want and not stripped.startswith("#"):
            if line != want[key]:
                changes.append("%s -> %s" % (line.strip(), want[key]))
            out.append(want[key])
            seen.add(key)
            continue
        out.append(line)
    # Insert any missing keys right under [wsl2] (or create the section).
    missing = [want[k] for k in want if k not in seen]
    if missing:
        if wsl2_idx is None:
            out = ["[wsl2]"] + missing + out
        else:
            out[wsl2_idx + 1:wsl2_idx + 1] = missing
        changes += ["+ " + m for m in missing]
    return "\n".join(out) + ("\n" if content.endswith("\n") else ""), changes


# Backup naming/numbering now lives in the shared guard module (guard.py) so
# every config-writing verb uses the same .anvil.bak.N convention; these thin
# aliases keep host.py's internal call sites and tests stable.
def _backups(path):
    return guard.backups(path)


def _next_backup(path):
    return guard.next_backup(path)


# --------------------------------------------------------------------------- #
# commands
# --------------------------------------------------------------------------- #

def cmd_doctor(_run=subprocess.run):
    summary = host_summary(_run=_run)
    host = summary["host_ram_gb"]
    wsl = summary["wsl_vm_memory_gb"]
    print("host RAM (physical):   %s" % _fmt(host))
    print("WSL VM memory cap:     %s" % (
        "unavailable (docker/WSL not running)" if wsl is None
        else "%s  (what .wslconfig `memory` booted with)" % _fmt(wsl)))
    for gpu in summary["gpus"]:
        print("GPU %s (%s): %.1f / %.1f GB" % (
            gpu["index"], gpu["name"], gpu["memory_used_gb"], gpu["memory_total_gb"]))
    rec = summary["recommended_wsl_memory_gb"]
    if rec is not None:
        floor = summary["windows_reserve"]["ceiling_gb"]
        print("\nRECOMMENDED WSL memory: %d GB  (leaves ~%d GB for Windows)" %
              (rec, int(host - rec)))
        print("  ceiling before refusal: %d GB (must leave >= %d GB for Windows; --force to override)"
              % (floor, MIN_WINDOWS_RESERVE_GB))
        print("  apply with:  anvil-serving host wsl-config --memory %d   then   host restart-docker" % rec)
        print("  NOTE: models live in GPU VRAM, so WSL needs this RAM only DURING a checkpoint LOAD;"
              " a bigger cap mainly cuts disk thrashing (it can't fully hold a checkpoint > host-reserve).")
    return 0


def _confirm(prompt, force, _input):
    # Delegates to the shared gate (guard.confirm) — same [y/N] + EOF->No contract.
    return guard.confirm(prompt, force=force, _input=_input)


def cmd_memory(distro=None, _run=subprocess.run):
    """Show host RAM, the WSL VM's memory (incl. the page cache that model-weight streams
    balloon), and GPU VRAM - the read side of `host reclaim`."""
    if sys.platform != "win32":
        print("host memory reads the WSL2 VM (Windows only); on %s read /proc/meminfo directly."
              % sys.platform, file=sys.stderr)
        return 2
    print("host RAM (physical):   %s" % _fmt(_host_total_gb(_run=_run)))
    mem = wsl_meminfo(_run=_run, distro=distro)
    if mem is None:
        print("WSL VM:                unavailable (WSL not installed/running?)")
    else:
        print("WSL VM total:          %s" % _fmt(mem["total_gb"]))
        print("WSL VM used:           %s" % _fmt(mem["used_gb"]))
        print("WSL VM page cache:     %s  <- repeated model-weight streams balloon this" %
              _fmt(mem["cached_gb"]))
        print("WSL VM available:      %s" % _fmt(mem["available_gb"]))
    for index, name, used, total in _gpus(_run=_run):
        print("GPU %s (%s): %.1f / %.1f GB" % (index, name, used, total))
    if mem and mem["cached_gb"] is not None and mem["total_gb"] and \
            mem["cached_gb"] > mem["total_gb"] / 2:
        print("\npage cache is over half the VM - free it with:  anvil-serving host reclaim --confirm")
    return 0


def cmd_reclaim(force=False, watch=False, threshold_gb=None, interval_s=30.0, distro=None,
                dry_run=False, _run=subprocess.run, _input=input, _sleep=time.sleep):
    """Drop the WSL VM's page cache. One-shot by default (confirm-gated, refuses mid-load);
    `--watch --threshold-gb N` is the foreground watchdog for bakeoff sessions where
    `autoMemoryReclaim=gradual` lags the load bursts."""
    if sys.platform != "win32":
        print("host reclaim drops the WSL2 VM page cache (Windows only); on %s run:  %s"
              % (sys.platform, DROP_CACHES_CMD), file=sys.stderr)
        return 2
    if watch and threshold_gb is None:
        print("reclaim --watch needs --threshold-gb <GB> (drop when the page cache exceeds it).",
              file=sys.stderr)
        return 2
    if dry_run:
        print("would run in WSL%s (as root):  %s" %
              (" distro %s" % distro if distro else "", DROP_CACHES_CMD))
        return 0

    if watch:
        if not _confirm("Start the reclaim watchdog? Drops the WSL page cache whenever it exceeds "
                        "%.0f GB (checking every %.0fs) until Ctrl-C." % (threshold_gb, interval_s),
                        force, _input):
            print("aborted (no --confirm/--force / declined).")
            return 1
        print("watching the WSL page cache (threshold %.0f GB, every %.0fs) - Ctrl-C to stop."
              % (threshold_gb, interval_s))
        try:
            while True:
                mem = wsl_meminfo(_run=_run, distro=distro)
                if mem is None or mem["cached_gb"] is None:
                    print("  meminfo unreadable - retrying next interval.")
                elif mem["cached_gb"] < threshold_gb:
                    print("  cache %.1f GB < %.0f GB - ok." % (mem["cached_gb"], threshold_gb))
                else:
                    growth = None if force else _cache_growth_gbps(_run=_run, distro=distro, _sleep=_sleep)
                    if growth is not None and growth > STREAMING_CACHE_GROWTH_GBPS:
                        # a load is streaming: don't yank pages it's about to reuse; wait it out
                        print("  cache %.1f GB over threshold but growing %.2f GB/s (model load "
                              "streaming) - waiting." % (mem["cached_gb"], growth))
                    elif _drop_caches(distro, _run):
                        after = wsl_meminfo(_run=_run, distro=distro)
                        print("  dropped: %.1f GB -> %s cached" %
                              (mem["cached_gb"], _fmt(None if after is None else after["cached_gb"])))
                    else:
                        print("  drop_caches failed (WSL unreachable / root exec denied) - "
                              "retrying next interval.", file=sys.stderr)
                _sleep(interval_s)
        except KeyboardInterrupt:
            print("\nwatchdog stopped.")
            return 0

    before = wsl_meminfo(_run=_run, distro=distro)
    if before is None:
        print("cannot read the WSL VM's /proc/meminfo (WSL not installed/running?).", file=sys.stderr)
        return 1
    if not force:
        growth = _cache_growth_gbps(_run=_run, distro=distro, _sleep=_sleep)
        if growth is not None and growth > STREAMING_CACHE_GROWTH_GBPS:
            print("REFUSING: the page cache is growing %.2f GB/s - a model checkpoint looks like "
                  "it is streaming RIGHT NOW, and dropping caches would evict pages the loader is "
                  "about to reuse. Wait for the load to finish, or --force." % growth,
                  file=sys.stderr)
            return 2
    if not _confirm("Drop the WSL page cache (%s)? Data-safe - only clean cache pages are "
                    "evicted - but the next model load re-reads weights from disk." % DROP_CACHES_CMD,
                    force, _input):
        print("aborted (no --confirm/--force / declined).")
        return 1
    if not _drop_caches(distro, _run):
        print("drop_caches failed (WSL unreachable or root exec denied).", file=sys.stderr)
        return 1
    after = wsl_meminfo(_run=_run, distro=distro)
    print("page cache: %s -> %s" %
          (_fmt(before["cached_gb"]), _fmt(None if after is None else after["cached_gb"])))
    return 0


def cmd_wsl_config(memory_gb=None, swap_gb=None, revert=False, force=False, dry_run=False,
                   _run=subprocess.run, _input=input):
    if sys.platform != "win32":
        print("host wsl-config manages Windows `.wslconfig`; not applicable on %s." % sys.platform,
              file=sys.stderr)
        return 2
    path = _wslconfig_path()

    if revert:
        newest = guard.latest_backup(path)
        if not newest:
            print("no anvil backup of %s to revert to." % path, file=sys.stderr)
            return 1
        with open(newest, encoding="utf-8") as f:
            restored = f.read()
        if dry_run:
            print("would restore %s <- %s" % (path, newest))
            return 0
        with open(path, "w", encoding="utf-8") as f:
            f.write(restored)
        print("reverted %s <- %s" % (path, os.path.basename(newest)))
        print("  restart Docker Desktop (host restart-docker) to apply.")
        return 0

    if memory_gb is None and swap_gb is None:
        print("wsl-config needs --memory <GB> and/or --swap <GB> (or --revert).", file=sys.stderr)
        return 2

    # SAFE-CAP CHECK: refuse a memory that starves Windows (the 84GB-on-93.7GB incident) unless --force.
    if memory_gb is not None:
        host = _host_total_gb(_run=_run)
        if host is None:
            # FAIL CLOSED: can't read host RAM -> can't verify the Windows floor -> refuse, don't skip.
            # (Skipping is the exact fail-open that would silently reproduce the starvation incident.)
            if not force:
                print("REFUSING: cannot read host RAM to verify memory=%dGB leaves the %d GB Windows "
                      "floor (PowerShell/WMI unavailable). Re-run when it's readable (`host doctor`) or "
                      "pass --force to override." % (int(memory_gb), MIN_WINDOWS_RESERVE_GB),
                      file=sys.stderr)
                return 2
        elif host - memory_gb < MIN_WINDOWS_RESERVE_GB and not force:
            print("REFUSING: memory=%dGB leaves only %.1f GB for Windows on a %.1f GB host "
                  "(< %d GB floor). This can prevent Docker Desktop/WSL from starting. Use a "
                  "lower value (try `host doctor`) or --force." % (
                      int(memory_gb), host - memory_gb, host, MIN_WINDOWS_RESERVE_GB), file=sys.stderr)
            return 2

    try:
        with open(path, encoding="utf-8") as f:
            content = f.read()
    except FileNotFoundError:
        content = "[wsl2]\n"
    new_content, changes = set_wslconfig_values(content, memory_gb=memory_gb, swap_gb=swap_gb)
    if not changes:
        print("no change (%s already has those values)." % path)
        return 0
    print("changes to %s:" % path)
    for c in changes:
        print("  " + c)
    if dry_run:
        print("(dry-run - not written)")
        return 0

    if os.path.exists(path):
        bak = _next_backup(path)
        with open(bak, "x", encoding="utf-8") as f:   # "x": fail loud, never silently clobber a backup
            f.write(content)
        print("backed up -> %s" % os.path.basename(bak))
    with open(path, "w", encoding="utf-8") as f:
        f.write(new_content)
    print("wrote %s" % path)
    print("  NOT LIVE YET: a WSL-backend memory change needs a Docker Desktop RESTART (not "
          "`wsl --shutdown`). Run:  anvil-serving host restart-docker")
    return 0


def cmd_restart_docker(force=False, _run=subprocess.run, _input=input):
    """Restart Docker Desktop so the WSL backend re-reads `.wslconfig`. This is the RIGHT lever:
    `wsl --shutdown` does NOT cycle the docker-desktop distro and, hammered in a loop, wedges WSL."""
    if sys.platform not in ("win32", "darwin"):
        print("host restart-docker targets Docker Desktop (Windows/macOS); on %s use your service "
              "manager (e.g. systemctl restart docker)." % sys.platform, file=sys.stderr)
        return 2
    if not _confirm("Restart Docker Desktop? This stops the engine + all containers briefly "
                    "(unless-stopped ones auto-restart).", force, _input):
        print("aborted (no --force / declined).")
        return 1
    exe = os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"),
                       "Docker", "Docker", "Docker Desktop.exe")
    if sys.platform == "win32":
        _kill_process("Docker Desktop", _run)   # stop the (possibly failed) instance
        if not os.path.exists(exe):
            print("Docker Desktop.exe not found at %s - start it from the Start menu." % exe,
                  file=sys.stderr)
            return 1
        if _ps("Start-Process '%s'" % exe, _run) is None:
            print("could not launch Docker Desktop (PowerShell unavailable).", file=sys.stderr)
            return 1
    else:  # darwin
        _run(["osascript", "-e", 'quit app "Docker Desktop"'], capture_output=True, text=True)
        _run(["open", "-a", "Docker"])
    print("Docker Desktop restarting - the engine + unless-stopped containers take ~1-2 min to return.")
    print("  verify with:  anvil-serving router status   and   anvil-serving serves status")
    return 0


def cmd_reset_wsl(force=False, _run=subprocess.run, _input=input):
    """Un-wedge a HUNG WSL2 subsystem (`wsl` commands time out, Docker Desktop can't start, hundreds of
    stuck `wsl.exe` pile up). Codifies the manual Task-Manager 'End task on vmmemWSL' recovery
    (2026-07-04): force-kill the WSL VM's backing process + the hung `wsl.exe` front-ends, then restart
    Docker Desktop so it rebuilds the backend. Deliberately does NOT use `wsl --shutdown` - when the
    subsystem is already wedged the CLI front-end blocks (that loop is what wedged it in the first place).
    Non-elevated best-effort; if the kill is denied it prints the elevated `Restart-Service` fallback."""
    if sys.platform != "win32":
        print("host reset-wsl un-wedges a hung WSL2 subsystem (Windows only).", file=sys.stderr)
        return 2
    if not _confirm("Reset the WSL subsystem? Force-kills the WSL VM (vmmemWSL) + hung wsl.exe, then "
                    "restarts Docker Desktop (engine + all containers cycle; unless-stopped auto-restart).",
                    force, _input):
        print("aborted (no --force / declined).")
        return 1
    kill_failed = False   # vmmemWSL could NOT be killed (denied / error) -> WSL may still be wedged
    denied = False
    for name in ("vmmemWSL", "wsl"):   # the VM backing process, then the piled-up front-ends
        status = _kill_process(name, _run)   # 'killed' | 'notfound' | 'denied' | 'error'
        if name == "vmmemWSL" and status in ("denied", "error"):
            kill_failed = True            # 'notfound' is benign (VM already gone; Docker rebuilds it)
        if status == "denied":
            denied = True
        print("  kill %-10s -> %s" % (name, status))
    print("restarting Docker Desktop to rebuild the WSL backend...")
    restart_rc = cmd_restart_docker(force=True, _run=_run, _input=_input)

    # Propagate failure so `reset-wsl --force` automation can detect an INCOMPLETE reset.
    rc = 0
    if kill_failed:
        print("\nCould not force-kill the WSL VM (vmmemWSL)%s. If WSL is still wedged, run in an "
              "ELEVATED PowerShell, then rebuild the backend:" % (" - access denied" if denied else ""))
        print("  Restart-Service WSLService -Force    (then:  anvil-serving host restart-docker)")
        rc = 1
    elif denied:
        print("\n(some wsl.exe front-ends could not be killed - access denied; harmless once the VM is gone.)")
    if restart_rc != 0:
        print("\nDocker Desktop could not be restarted (exit %d) - launch it manually (or "
              "`anvil-serving host restart-docker`) to rebuild the backend." % restart_rc)
        rc = rc or restart_rc
    print("\nThen verify:  anvil-serving host doctor   (WSL cap reads live; GPUs repopulate as models reload)")
    return rc


def _build_parser():
    p = argparse.ArgumentParser(
        prog="anvil-serving host",
        description="Own the host (WSL / Docker Desktop) config, with backup/revert + safe caps.")
    sub = p.add_subparsers(dest="action", required=True)
    sub.add_parser("doctor", help="inspect + recommend safe WSL memory")

    wsl = sub.add_parser("wsl-config", help="edit .wslconfig memory/swap settings")
    wsl.add_argument("--memory", type=int, help="WSL memory cap in GB.")
    wsl.add_argument("--swap", type=int, help="WSL swap in GB.")
    wsl.add_argument("--revert", action="store_true", help="restore the newest anvil backup.")
    wsl.add_argument("--force", action="store_true", help="skip the safe-cap refusal.")
    wsl.add_argument("--dry-run", action="store_true", help="show the change, write nothing.")

    restart = sub.add_parser("restart-docker", help="restart Docker Desktop to apply host changes")
    restart.add_argument("--force", action="store_true", help="skip the confirm prompt.")

    reset = sub.add_parser("reset-wsl", help="reset a wedged WSL subsystem")
    reset.add_argument("--force", action="store_true", help="skip the confirm prompt.")

    mem = sub.add_parser("memory", help="show host RAM / WSL VM memory (incl. page cache) / GPUs")
    mem.add_argument("--distro", help="WSL distro to query (default: the default distro).")

    rec = sub.add_parser("reclaim", help="drop the WSL VM page cache (sync && drop_caches=3)")
    rec.add_argument("--force", action="store_true",
                     help="skip the confirm prompt AND the streaming-load refusal.")
    rec.add_argument("--watch", action="store_true",
                     help="foreground watchdog: drop whenever the cache exceeds --threshold-gb.")
    rec.add_argument("--threshold-gb", type=float,
                     help="(--watch) drop when the page cache exceeds this many GB.")
    rec.add_argument("--interval", type=float, default=30.0,
                     help="(--watch) seconds between checks (default 30).")
    rec.add_argument("--distro", help="WSL distro to run in (default: the default distro).")
    rec.add_argument("--dry-run", action="store_true", help="show the command, run nothing.")
    return p


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    p = _build_parser()
    a = p.parse_args(argv)

    if a.action == "doctor":
        return cmd_doctor()
    if a.action == "wsl-config":
        return cmd_wsl_config(memory_gb=a.memory, swap_gb=a.swap, revert=a.revert,
                              force=a.force, dry_run=a.dry_run)
    if a.action == "restart-docker":
        return cmd_restart_docker(force=a.force)
    if a.action == "reset-wsl":
        return cmd_reset_wsl(force=a.force)
    if a.action == "memory":
        return cmd_memory(distro=a.distro)
    if a.action == "reclaim":
        return cmd_reclaim(force=a.force, watch=a.watch, threshold_gb=a.threshold_gb,
                           interval_s=a.interval, distro=a.distro, dry_run=a.dry_run)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
