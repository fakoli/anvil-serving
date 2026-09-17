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
    meminfo.write_text('MemAvailable: 2000000 kB\nSwapFree: 0 kB\n')
    calls = []
    def run(argv, **kwargs):
        calls.append(argv)
        return SimpleNamespace(stdout='"unix:///var/run/docker.sock"' if argv[1] == 'context' else 'true true "2"')
    rm.check_host(serve(), _run=run, meminfo=meminfo)
    meminfo.write_text('MemAvailable: 1000000 kB\nSwapFree: 0 kB\n')
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
