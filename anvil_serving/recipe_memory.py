"""Explicit local Linux/WSL recipe memory containment and bounded observations."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess
import sys

MIB = 1024 * 1024
FIELDS = ('memory_limit_mib', 'memory_swap_limit_mib', 'host_memory_reserve_mib')


def _memory_facts(text):
    facts = {}
    for line in text.splitlines():
        key, _, value = line.partition(':')
        if key in {'MemTotal', 'MemAvailable', 'SwapFree'}:
            parts = value.split()
            if len(parts) != 2 or parts[1] != 'kB' or not parts[0].isdecimal() or key in facts:
                raise ValueError('invalid host memory observation')
            facts[key] = int(parts[0]) * 1024
    if (not {'MemTotal', 'MemAvailable', 'SwapFree'} <= facts.keys()
            or facts['MemTotal'] <= 0 or facts['MemAvailable'] > facts['MemTotal']):
        raise ValueError('invalid or incomplete host memory observation')
    return facts


def _desktop_facts(memory, reserve, _run):
    from . import host

    info = json.loads(_run(['docker', 'info', '--format', '{{json .}}'], check=True,
                          capture_output=True, text=True, timeout=10).stdout)
    if (not isinstance(info, dict) or info.get('OSType') != 'linux'
            or info.get('OperatingSystem') != 'Docker Desktop'
            or 'microsoft' not in str(info.get('KernelVersion', '')).lower()
            or type(info.get('MemTotal')) is not int or info['MemTotal'] <= 0):
        raise ValueError('bounded Windows loads require the local Docker Desktop WSL Linux engine')
    observed = _run(host._wsl_argv(['-e', 'cat', '/proc/meminfo'], 'docker-desktop'),
                    check=True, capture_output=True, text=True, timeout=15)
    facts = _memory_facts(observed.stdout)
    if facts.get('MemTotal') != info['MemTotal']:
        raise ValueError('Docker and local WSL memory identities do not match')
    observed = host._ps(
        "$m = Get-CimInstance Win32_OperatingSystem; "
        "@{total=$m.TotalVisibleMemorySize; available=$m.FreePhysicalMemory} | ConvertTo-Json -Compress",
        _run)
    if observed is None or observed.returncode:
        raise ValueError('Windows physical memory observation is unavailable')
    physical = json.loads(observed.stdout)
    if (not isinstance(physical, dict) or any(type(physical.get(key)) is not int
            or physical[key] <= 0 for key in ('total', 'available'))
            or physical['available'] > physical['total']):
        raise ValueError('invalid Windows physical memory observation')
    windows_reserve = max(reserve * MIB, host.RECOMMENDED_WINDOWS_RESERVE_GB * 1024**3)
    if (physical['available'] * 1024 < memory * MIB + windows_reserve
            or physical['total'] * 1024 - info['MemTotal'] < windows_reserve):
        raise ValueError('candidate or WSL memory ceiling would consume the Windows host reserve')
    return facts


def limits(serve: dict) -> tuple[int, int, int] | None:
    if not any(name in serve for name in FIELDS):
        return None  # Existing known-good recipes remain reproducible.
    values = tuple(serve.get(name) for name in FIELDS)
    if any(type(value) is not int or not 1 <= value <= 16777216 for value in values):
        raise ValueError('recipe memory limits require all three positive integer MiB fields')
    memory, total, reserve = values
    if memory < 6 or total < memory or reserve < 1024:
        raise ValueError('recipe memory requires RAM >= 6 MiB, RAM+swap >= RAM, host reserve >= 1024 MiB')
    return memory, total, reserve


def check_host(serve: dict, *, _run=subprocess.run, meminfo=Path('/proc/meminfo')) -> None:
    bounds = limits(serve)
    if bounds is None:
        return
    memory, total, reserve = bounds
    context = os.environ.get('DOCKER_CONTEXT', '')
    if context and (len(context) > 256 or context.startswith('-') or any(ord(c) < 32 for c in context)):
        raise ValueError('invalid Docker context selection')
    endpoint = None if context else os.environ.get('DOCKER_HOST')
    if not endpoint:
        endpoint = _run(['docker', 'context', 'inspect', *([context] if context else []), '--format', '{{json .Endpoints.docker.Host}}'], check=True, capture_output=True, text=True, timeout=10).stdout
        endpoint = json.loads(endpoint)
    desktop = sys.platform == 'win32' and endpoint in {
        'npipe:////./pipe/dockerDesktopLinuxEngine', 'npipe:////./pipe/docker_engine'}
    if not desktop and (not isinstance(endpoint, str) or not endpoint.startswith('unix://') or not meminfo.is_file()):
        raise ValueError('bounded recipe loads require a local Linux Docker endpoint')
    capabilities = _run(['docker', 'info', '--format', '{{json .MemoryLimit}} {{json .SwapLimit}} {{json .CgroupVersion}}'], check=True, capture_output=True, text=True, timeout=10).stdout.strip()
    if capabilities != 'true true "2"':
        raise ValueError('bounded recipe loads require enforced memory/swap limits and cgroup v2')
    facts = _desktop_facts(memory, reserve, _run) if desktop else _memory_facts(meminfo.read_text())
    if facts.get('MemAvailable', 0) < (memory + reserve) * MIB:
        raise ValueError('candidate RAM limit would consume the declared available host reserve')
    if facts.get('SwapFree', 0) < (total - memory) * MIB:
        raise ValueError('candidate swap allowance exceeds currently free host swap')


def observation(row: dict, *, proc=Path('/proc'), cgroup=Path('/sys/fs/cgroup')) -> dict:
    host, state = row.get('HostConfig') or {}, row.get('State') or {}
    result = {'limit_bytes': host.get('Memory'), 'ram_plus_swap_limit_bytes': host.get('MemorySwap'),
              'oom_killed': state.get('OOMKilled'), 'exit_code': state.get('ExitCode'),
              'current_bytes': None, 'peak_bytes': None, 'events': None,
              'effective_limit_bytes': None, 'effective_swap_limit_bytes': None}
    pid, identity = state.get('Pid'), row.get('Id')
    if type(pid) is not int or pid <= 0 or not isinstance(identity, str) or not re.fullmatch(r'[0-9a-f]{64}', identity):
        return result
    try:
        rows = (proc / str(pid) / 'cgroup').read_text().splitlines()
        paths = [line[3:] for line in rows if line.startswith('0::/')]
        if len(paths) != 1:
            return result
        relative = Path(paths[0].lstrip('/'))
        if '..' in relative.parts or relative.name not in {identity, 'docker-' + identity + '.scope'}:
            return result
        root = cgroup / relative
        for key, name in (('current_bytes', 'memory.current'), ('peak_bytes', 'memory.peak'),
                          ('effective_limit_bytes', 'memory.max'), ('effective_swap_limit_bytes', 'memory.swap.max')):
            value = (root / name).read_text().strip()
            result[key] = int(value) if value.isdecimal() else None
        events = {}
        for line in (root / 'memory.events').read_text().splitlines():
            key, value = line.split()
            if key in {'low', 'high', 'max', 'oom', 'oom_kill', 'oom_group_kill'} and value.isdecimal():
                events[key] = int(value)
        result['events'] = events
    except (OSError, ValueError):
        pass  # Stopped containers retain Docker OOM/exit state; counters may be gone.
    return result
