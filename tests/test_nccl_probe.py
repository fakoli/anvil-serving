import json
import subprocess
from types import SimpleNamespace

import pytest

from anvil_serving import cli, nccl_probe as probe


GPUS = ['GPU-00000000-0000-0000-0000-000000000001',
        'GPU-00000000-0000-0000-0000-000000000002']
IMAGE = 'registry.example/runtime@sha256:' + '1' * 64


@pytest.fixture(autouse=True)
def native_owner(monkeypatch):
    """Model the resource owner independently of the machine running pytest."""
    host = {'system': 'Linux', 'release': '6.8.0', 'container': False}
    real_path = probe.Path

    def host_path(value):
        if value == '/.dockerenv':
            return SimpleNamespace(exists=lambda: host['container'])
        return real_path(value)

    monkeypatch.setattr(probe, 'platform', SimpleNamespace(
        system=lambda: host['system'], release=lambda: host['release']))
    monkeypatch.setattr(probe, 'Path', host_path)
    return host


class Runtime:
    def __init__(self, *, busy=False, timeout=False, wrong_owner=False, p2p=False,
                 bad_result=False, display='Disabled', absent_error='Error: No such object: probe'):
        self.calls = []
        self.busy, self.timeout, self.wrong_owner = busy, timeout, wrong_owner
        self.p2p, self.bad_result, self.display = p2p, bad_result, display
        self.removed = False
        self.label = None
        self.absent_error = absent_error

    def __call__(self, argv, **kwargs):
        self.calls.append(argv)
        assert 0 < kwargs['timeout'] <= 180
        assert kwargs['env']['DOCKER_HOST'] == 'unix:///var/run/docker.sock'
        assert 'DOCKER_CONTEXT' not in kwargs['env']
        out, err, rc = '', '', 0
        if argv[0] == 'nvidia-smi':
            if '--query-compute-apps=gpu_uuid,pid' in argv:
                out = GPUS[0] + ', 123\n' if self.busy else ''
            elif 'topo' in argv:
                out = 'GPU0 GPU1 PHB\n'
            else:
                out = ''.join(f'{gpu}, Test GPU, 00000000:0{i+1}:00.0, 24000, 0, {self.display}, 595.0\n'
                              for i, gpu in enumerate(GPUS))
        elif 'image' in argv:
            out = json.dumps('sha256:' + '2' * 64)
        elif 'create' in argv:
            self.label = argv[argv.index('--label') + 1].split('=', 1)[1]
            out = '3' * 64
        elif 'start' in argv:
            if self.timeout:
                raise subprocess.TimeoutExpired(argv, kwargs['timeout'])
        elif 'logs' in argv:
            transport = 'P2P/IPC' if self.p2p else 'SHM/direct'
            ranks = [dict(rank=rank, uuid=gpu, correct=True, measurements=[
                dict(bytes=size, iterations=20, correct=True, median_ms=1.0)
                for size in [8, 1024, 65536, 16 * 1024 * 1024]])
                for rank, gpu in enumerate(GPUS)]
            if self.bad_result:
                ranks[1]['measurements'] = []
            out = ('[0] NCCL INFO NCCL version 2.30.7+cuda13.3\n'
                   f'Channel 00 : 0[0] -> 1[1] via {transport}\n'
                   f'Channel 00 : 1[1] -> 0[0] via {transport}\n' +
                   probe.MARKER + json.dumps({'ranks': ranks}) + '\n')
        elif 'rm' in argv:
            self.removed = True
        elif 'inspect' in argv:
            if self.removed:
                rc, err = 1, self.absent_error
            elif '{{json .State}}' in argv:
                out = json.dumps({'Running': self.timeout, 'ExitCode': 0})
            else:
                out = json.dumps({probe.LABEL: 'someone-else' if self.wrong_owner else self.label})
        else:
            raise AssertionError(argv)
        return subprocess.CompletedProcess(argv, rc, out, err)


def run(runtime, **changes):
    return probe.probe(image=IMAGE, gpu_uuids=GPUS, _runner=runtime,
                       _domains=lambda _: dict.fromkeys(GPUS, 'none'), **changes)


def test_timeout_still_collects_logs_and_removes_only_owned_container():
    runtime = Runtime(timeout=True)
    result = run(runtime, dry_run=False)
    assert not result['ok'] and 'timed out' in result['error']
    assert result['cleanup']['ok'] and runtime.removed and result['logs']


def test_empty_measurements_and_shm_fallback_do_not_qualify_p2p():
    assert not run(Runtime(bad_result=True), dry_run=False)['ok']
    assert not run(Runtime(), dry_run=False, p2p='enabled')['ok']


def test_cli_confirmation_help_and_packaged_source(capsys):
    assert cli.main(['host', 'nccl', '--help']) == 0
    output = capsys.readouterr().out
    assert '--confirm' in output and '--gpu-uuid' in output and '--output' in output
    assert probe.SOURCE.is_file()
    compile(probe.SOURCE.read_text(), str(probe.SOURCE), 'exec')
