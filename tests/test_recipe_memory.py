import json
from types import SimpleNamespace

import pytest

from anvil_serving import recipe_memory as rm, serve_recipes as sr


def serve():
    return dict(image='example/runtime', memory_limit_mib=64, memory_swap_limit_mib=64, host_memory_reserve_mib=1024)


@pytest.mark.parametrize('field,value', [('memory_limit_mib', 0), ('memory_limit_mib', True), ('memory_limit_mib', '64'), ('memory_swap_limit_mib', -1), ('memory_swap_limit_mib', 63), ('host_memory_reserve_mib', 0)])
def test_invalid_bounds_refused_even_on_registry_validation(field, value):
    data = serve()
    data[field] = value
    with pytest.raises(sr.RecipeError):
        sr.validate_recipe({'model': 'example/model', 'serve': data})


def test_renderer_applies_both_limits_and_legacy_remains_reproducible():
    recipe = {'model': 'example/model', 'serve': serve()}
    argv = sr.docker_run_argv(recipe)
    assert argv[argv.index('--memory') + 1] == str(64 * rm.MIB)
    assert argv[argv.index('--memory-swap') + 1] == str(64 * rm.MIB)
    assert '--memory' not in sr.docker_run_argv({'model': 'example/model', 'serve': {'image': 'example/runtime'}})
    del recipe['serve']['memory_swap_limit_mib']
    with pytest.raises(sr.RecipeError):
        sr.docker_run_argv(recipe)


def test_host_reserve_and_capability_fail_before_docker_start(tmp_path, monkeypatch):
    monkeypatch.delenv('DOCKER_HOST', raising=False)
    monkeypatch.delenv('DOCKER_CONTEXT', raising=False)
    meminfo = tmp_path / 'meminfo'
    meminfo.write_text('MemTotal: 2000000 kB\nMemAvailable: 2000000 kB\nSwapFree: 0 kB\n')
    calls = []
    def run(argv, **kwargs):
        calls.append(argv)
        return SimpleNamespace(stdout='"unix:///var/run/docker.sock"' if argv[1] == 'context' else 'true true "2"')
    rm.check_host(serve(), _run=run, meminfo=meminfo)
    meminfo.write_text('MemTotal: 2000000 kB\nMemAvailable: 1000000 kB\nSwapFree: 0 kB\n')
    with pytest.raises(ValueError, match='reserve'):
        rm.check_host(serve(), _run=run, meminfo=meminfo)
    monkeypatch.setenv('DOCKER_HOST', 'ssh://remote.invalid')
    with pytest.raises(ValueError, match='local Linux'):
        rm.check_host(serve(), _run=run, meminfo=meminfo)
    monkeypatch.setenv('DOCKER_HOST', 'unix:///var/run/docker.sock')
    monkeypatch.setenv('DOCKER_CONTEXT', 'remote')
    with pytest.raises(ValueError, match='local Linux'):
        rm.check_host(serve(), _run=lambda *a, **kw: SimpleNamespace(stdout='\"ssh://remote.invalid\"'), meminfo=meminfo)
    monkeypatch.setattr(rm, 'check_host', lambda *args, **kwargs: (_ for _ in ()).throw(ValueError('reserve')))
    with pytest.raises(sr.RecipeError, match='containment refused'):
        sr.load_recipe({'model': 'example/model', 'serve': serve()}, 'candidate', _run=lambda *a, **kw: pytest.fail('Docker must not start'))


def test_cgroup_observation_and_stopped_oom(tmp_path):
    cid = 'a' * 64
    proc, cgroup = tmp_path / 'proc', tmp_path / 'cgroup'
    (proc / '123').mkdir(parents=True)
    relative = 'system.slice/docker-' + cid + '.scope'
    (proc / '123/cgroup').write_text('0::/' + relative + '\n')
    root = cgroup / relative
    root.mkdir(parents=True)
    (root / 'memory.current').write_text('1000')
    (root / 'memory.peak').write_text('67108864')
    (root / 'memory.max').write_text('67108864')
    (root / 'memory.swap.max').write_text('0')
    (root / 'memory.events').write_text('oom 1\noom_kill 1\nmax 42\n')
    row = {'Id': cid, 'HostConfig': {'Memory': 67108864, 'MemorySwap': 67108864}, 'State': {'Pid': 123, 'OOMKilled': False, 'ExitCode': 0}}
    result = rm.observation(row, proc=proc, cgroup=cgroup)
    assert result['events']['oom_kill'] == 1
    assert result['peak_bytes'] == 67108864
    assert result['effective_limit_bytes'] == 67108864
    assert result['effective_swap_limit_bytes'] == 0
    row['State'] = {'Pid': 0, 'OOMKilled': True, 'ExitCode': 137}
    result = rm.observation(row, proc=proc, cgroup=cgroup)
    assert result['oom_killed'] is True and result['exit_code'] == 137
    assert result['peak_bytes'] is None
    assert json.loads(json.dumps(result)) == result


@pytest.mark.parametrize('fault,expected', [
    (None, None),
    ('remote-pipe', 'local Linux'),
    ('wrong-engine', 'Docker Desktop'),
    ('vm-mismatch', 'identities'),
    ('windows-pressure', 'Windows host reserve'),
    ('windows-cap', 'Windows host reserve'),
    ('vm-pressure', 'available host reserve'),
    ('invalid-memory', 'invalid host memory'),
    ('missing-swap', 'incomplete host memory'),
    ('inconsistent-vm', 'incomplete host memory'),
    ('missing-physical', 'invalid Windows'),
    ('cgroup-v1', 'cgroup v2'),
    ('missing-swap-limit', 'cgroup v2'),
    ('declared-windows-reserve', 'Windows host reserve'),
])
def test_windows_desktop_containment_checks_both_memory_boundaries(monkeypatch, fault, expected):
    from anvil_serving import host

    monkeypatch.setattr(rm.sys, 'platform', 'win32')
    monkeypatch.delenv('DOCKER_CONTEXT', raising=False)
    monkeypatch.setenv('DOCKER_HOST', 'npipe:////./pipe/' + (
        'remote-proxy' if fault == 'remote-pipe' else 'dockerDesktopLinuxEngine'))
    monkeypatch.setattr(host, '_powershell_exe', lambda: 'powershell.exe')
    info = dict(OSType='linux', OperatingSystem='Docker Desktop',
                KernelVersion='6.6-microsoft-standard-WSL2', MemTotal=64 * 1024**3)
    if fault == 'wrong-engine':
        info['OperatingSystem'] = 'Other engine'
    if fault == 'vm-mismatch':
        info['MemTotal'] -= 1024
    physical = dict(total=96 * 1024**2, available=32 * 1024**2)
    if fault == 'windows-pressure':
        physical['available'] = 1024**2
    if fault == 'windows-cap':
        physical['total'] = 70 * 1024**2
    if fault == 'missing-physical':
        del physical['available']
    if fault == 'declared-windows-reserve':
        physical['available'] = 18 * 1024**2
    available = '10' if fault == 'vm-pressure' else str(32 * 1024**2)
    if fault == 'invalid-memory':
        available = '-1'
    if fault == 'inconsistent-vm':
        available = str(65 * 1024**2)
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        if argv[:2] == ['docker', 'info']:
            text = json.dumps(info) if argv[-1] == '{{json .}}' else 'true true "2"'
            if argv[-1] != '{{json .}}' and fault == 'cgroup-v1':
                text = 'true true "1"'
            if argv[-1] != '{{json .}}' and fault == 'missing-swap-limit':
                text = 'true false "2"'
        elif argv[0] == 'wsl':
            assert argv == ['wsl', '-d', 'docker-desktop', '-e', 'cat', '/proc/meminfo']
            text = f'MemTotal: {64 * 1024**2} kB\nMemAvailable: {available} kB\nSwapFree: 0 kB\n'
            if fault == 'missing-swap':
                text = text.replace('SwapFree: 0 kB\n', '')
        else:
            assert argv[0] == 'powershell.exe'
            text = json.dumps(physical)
        return SimpleNamespace(stdout=text, returncode=0)

    bounded = serve()
    if fault == 'declared-windows-reserve':
        bounded['host_memory_reserve_mib'] = 20 * 1024
    if expected:
        with pytest.raises(ValueError, match=expected):
            rm.check_host(bounded, _run=run)
    else:
        rm.check_host(bounded, _run=run)
        assert any(argv[0] == 'wsl' for argv in calls)
