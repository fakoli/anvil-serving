import json
from types import SimpleNamespace

import pytest

from anvil_serving import recipe_memory as rm, serve_recipes as sr


def serve():
    return dict(image='example/runtime', memory_limit_mib=64, memory_swap_limit_mib=64, host_memory_reserve_mib=1024)


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
    ('windows-pressure-boundary', None),
    ('windows-pressure-below', 'Windows host reserve'),
    ('windows-cap-boundary', None),
    ('windows-cap-below', 'Windows host reserve'),
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
    if fault in {'windows-pressure-boundary', 'windows-pressure-below'}:
        physical['available'] = (14 * 1024 + 64) * 1024 - (fault == 'windows-pressure-below')
    if fault in {'windows-cap-boundary', 'windows-cap-below'}:
        physical['total'] = 78 * 1024**2 - (fault == 'windows-cap-below')
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
        with pytest.raises(ValueError, match=expected) as caught:
            rm.check_host(bounded, _run=run)
        if expected == 'Windows host reserve':
            reserve = max(bounded['host_memory_reserve_mib'] * rm.MIB, 14 * 1024**3)
            assert str(caught.value).endswith(
                f'available_bytes={physical["available"] * 1024}, '
                f'required_available_bytes={64 * rm.MIB + reserve}; '
                f'physical_total_bytes={physical["total"] * 1024}, '
                f'wsl_ceiling_bytes={info["MemTotal"]}, '
                f'required_outside_wsl_bytes={reserve}'
            )
    else:
        rm.check_host(bounded, _run=run)
        assert any(argv[0] == 'wsl' for argv in calls)


def _live_row():
    return {'Id': 'b' * 64, 'HostConfig': {'Memory': 67108864, 'MemorySwap': 67108864},
            'State': {'Pid': 123, 'Running': True, 'OOMKilled': False, 'ExitCode': 0}}


def _desktop_probe(monkeypatch, *, fault=None, driver='cgroupfs'):
    import subprocess

    monkeypatch.setattr(rm.sys, 'platform', 'win32')
    monkeypatch.delenv('DOCKER_CONTEXT', raising=False)
    monkeypatch.setenv('DOCKER_HOST', 'npipe:////./pipe/dockerDesktopLinuxEngine')
    info = dict(OSType='linux', OperatingSystem='Docker Desktop',
                KernelVersion='6.6-microsoft-standard-WSL2', MemTotal=64 * 1024**3,
                CgroupVersion='2', CgroupDriver=driver)
    if fault == 'engine':
        info['OperatingSystem'] = 'remote'
    if fault == 'vm':
        info['MemTotal'] -= 1024
    if fault == 'v1':
        info['CgroupVersion'] = '1'
    payload = ('memory.current\n1024\nmemory.peak\n2048\nmemory.max\n67108864\n'
               'memory.swap.max\n0\nmemory.events\nlow 0\nhigh 0\nmax 3\noom 1\noom_kill 1\n')
    if fault == 'negative':
        payload = payload.replace('1024', '-1')
    if fault == 'duplicate':
        payload += 'oom 2\n'
    if fault == 'missing-oom':
        payload = payload.replace('oom_kill 1\n', '')
    if fault == 'truncated':
        payload = payload[:35]
    if fault == 'oversized':
        payload += 'x' * 16384
    if fault == 'unlimited':
        payload = payload.replace('67108864', 'max').replace('memory.swap.max\n0', 'memory.swap.max\nmax')
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        assert kwargs['check'] and kwargs['capture_output'] and kwargs['text']
        assert 0 < kwargs['timeout'] <= 15
        if argv[:2] == ['docker', 'context']:
            text = json.dumps('ssh://remote.invalid')
        elif argv[:2] == ['docker', 'info']:
            text = json.dumps(info)
        elif argv == ['wsl', '-d', 'docker-desktop', '-e', 'cat', '/proc/meminfo']:
            text = f'MemTotal: {64 * 1024**2} kB\nMemAvailable: 1000 kB\nSwapFree: 0 kB\n'
        else:
            assert argv[:6] == ['wsl', '-d', 'docker-desktop', '-e', 'sh', '-c']
            assert argv[6] == rm._CGROUP_READ
            assert argv[7] == 'anvil-recipe-memory'
            expected = ('/sys/fs/cgroup/docker/' + 'b' * 64 if driver == 'cgroupfs'
                        else '/sys/fs/cgroup/system.slice/docker-' + 'b' * 64 + '.scope')
            assert argv[8] == expected
            assert kwargs['timeout'] == 5
            if fault == 'timeout':
                raise subprocess.TimeoutExpired(argv, 5)
            if fault == 'exit-race':
                raise subprocess.CalledProcessError(1, argv)
            text = payload
        return SimpleNamespace(stdout=text, returncode=0)
    return run, calls


@pytest.mark.parametrize('driver', ['cgroupfs', 'systemd'])
def test_windows_exact_id_cgroup_observation_has_no_container_exec(monkeypatch, driver):
    run, calls = _desktop_probe(monkeypatch, driver=driver)
    result = rm.observation(_live_row(), _run=run)
    assert result['current_bytes'] == 1024
    assert result['peak_bytes'] == 2048
    assert result['effective_limit_bytes'] == 67108864
    assert result['effective_swap_limit_bytes'] == 0
    assert result['events'] == {'low': 0, 'high': 0, 'max': 3, 'oom': 1, 'oom_kill': 1}
    assert not any(call[:2] == ['docker', 'exec'] for call in calls)
    # Observation still works when the VM has little free memory; it is not admission.
    assert len(calls) == 3


@pytest.mark.parametrize('total,actual_ram,actual_swap,valid', [
    (64, 64 * rm.MIB, 0, True),
    (65, 64 * rm.MIB, rm.MIB, True),
    (64, 64 * rm.MIB, None, False),
    (64, 64 * rm.MIB, 1, False),
    (64, None, 0, False),
    (64, 63 * rm.MIB, 0, False),
    (65, 64 * rm.MIB, 0, False),
    (64, 64 * rm.MIB, False, False),
])
def test_effective_limits_are_authoritative(total, actual_ram, actual_swap, valid):
    configured = dict(serve(), memory_swap_limit_mib=total)
    memory, wire_total = rm.docker_limits(configured)
    observed = dict(limit_bytes=memory, ram_plus_swap_limit_bytes=wire_total,
                    effective_limit_bytes=actual_ram, effective_swap_limit_bytes=actual_swap)
    assert (rm.containment_error(observed, configured) is None) == valid
    assert (rm.containment_error(observed) is None) == valid
    assert rm.containment_error({}, configured) is not None
    assert rm.containment_error({}) is None


@pytest.mark.parametrize('fault', [None, 'unlimited', 'missing', 'ram', 'identity', 'inspect', 'stop'])
def test_bounded_load_verifies_and_stops_only_its_created_id(monkeypatch, fault):
    import subprocess

    recipe = {'model': 'example/model', 'serve': serve()}
    cid = 'd' * 64
    row = _live_row()
    row.update(Id=cid, Config={'Labels': {
        sr.RECIPE_MANAGED_LABEL: sr.RECIPE_MANAGED_VALUE,
        sr.RECIPE_DIGEST_LABEL: sr.recipe_digest(recipe)}})
    if fault == 'identity':
        row['Id'] = 'e' * 64
    observed = dict(limit_bytes=64 * rm.MIB, ram_plus_swap_limit_bytes=64 * rm.MIB + 1,
                    effective_limit_bytes=64 * rm.MIB, effective_swap_limit_bytes=0)
    if fault in {'unlimited', 'stop'}:
        observed['effective_swap_limit_bytes'] = None
    if fault == 'ram':
        observed['effective_limit_bytes'] -= 1
    if fault == 'missing':
        observed = {}
    monkeypatch.setattr(rm, 'check_host', lambda *_args, **_kwargs: None)
    monkeypatch.setattr(rm, 'observation', lambda *_args, **_kwargs: observed)
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        if argv[:2] == ['docker', 'run']:
            assert kwargs['stdout'] == subprocess.PIPE and kwargs['text'] is True
            return SimpleNamespace(returncode=0, stdout=cid + '\n')
        if argv == ['docker', 'inspect', cid]:
            if fault == 'inspect':
                raise subprocess.CalledProcessError(1, argv, output='private-inspect-payload')
            return SimpleNamespace(returncode=0, stdout=json.dumps([row]))
        assert argv == ['docker', 'stop', '--time', '10', cid]
        return SimpleNamespace(returncode=int(fault == 'stop'))

    if fault:
        with pytest.raises(sr.RecipeError, match='containment refused') as caught:
            sr.load_recipe(recipe, 'candidate', _run=run)
        message = str(caught.value)
        assert 'private-inspect-payload' not in message
        assert 'host_memory=' in message and cid in message
        evidence = json.loads(message.split(rm.FAILURE_PREFIX)[1])
        assert evidence['container_id'] == cid
        assert evidence['cleanup'] == ('stop_failed' if fault == 'stop' else 'stopped_logs_retained')
        assert ('stop failed' if fault == 'stop' else 'stopped; container and logs retained') in message
        assert calls[-1] == ['docker', 'stop', '--time', '10', cid]
    else:
        assert sr.load_recipe(recipe, 'candidate', _run=run)[1] == 0
        assert len(calls) == 2


@pytest.mark.parametrize('returncode,stdout', [(1, 'e' * 64), (0, '')])
def test_failed_or_unknown_creation_never_stops_existing_name(monkeypatch, returncode, stdout):
    monkeypatch.setattr(rm, 'check_host', lambda *_args, **_kwargs: None)
    calls = []
    def run(argv, **kwargs):
        calls.append(argv)
        return SimpleNamespace(returncode=returncode, stdout=stdout)
    recipe = {'model': 'example/model', 'serve': serve()}
    if returncode:
        assert sr.load_recipe(recipe, 'existing-name', _run=run)[1] == returncode
    else:
        with pytest.raises(sr.RecipeError, match='ownership unknown'):
            sr.load_recipe(recipe, 'existing-name', _run=run)
    assert len(calls) == 1 and calls[0][:2] == ['docker', 'run']


def test_recipe_inspection_deadline_fails_before_health(monkeypatch):
    import subprocess
    from anvil_serving import models

    real_identity = models._recipe_container_identity
    seen = []
    def run(argv, **kwargs):
        seen.append(kwargs['timeout'])
        raise subprocess.TimeoutExpired(argv, kwargs['timeout'], output='must-not-leak')
    def identity(recipe, container, **kwargs):
        assert kwargs['require_recipe_digest'] is True
        return real_identity(recipe, container, _run=run, **kwargs)
    monkeypatch.setattr(models, '_recipe_container_identity', identity)
    clock = iter([0, 0.25])
    ready, reason = models._await_recipe_container_healthy(
        {'model': 'example/model', 'serve': serve()}, 'candidate', {'port': 30123}, 1, 1,
        _health=lambda *_: pytest.fail('no HTTP after inspection timeout'),
        _monotonic=lambda: next(clock))
    assert not ready and 'identity unavailable' in reason
    assert seen == [0.75]


def test_readiness_identity_requires_recipe_digest_but_legacy_unload_identity_does_not():
    from anvil_serving import models

    recipe = {'model': 'example/model', 'serve': serve()}
    row = _live_row()
    row['Config'] = {'Labels': {sr.RECIPE_MANAGED_LABEL: sr.RECIPE_MANAGED_VALUE,
                               sr.RECIPE_MODEL_LABEL: recipe['model'],
                               sr.RECIPE_DIGEST_LABEL: 'f' * 64}}
    def run(argv, **kwargs):
        assert kwargs['timeout'] == 10
        return SimpleNamespace(returncode=0, stdout=json.dumps([row]))
    with pytest.raises(sr.RecipeError, match='recipe digest label'):
        models._recipe_container_identity(recipe, 'candidate', _run=run, require_recipe_digest=True)
    assert models._recipe_container_identity(recipe, 'candidate', _run=run)['container_id'] == row['Id']
