import json
import subprocess

import pytest

from anvil_serving import docker_build_history as history

REVISION = 'a' * 40
REF = 'example/example0/' + 'b' * 25
SECRET_NAME = 'do-not-retain-build-name'


@pytest.fixture
def setup(tmp_path):
    plan = {'schema': 'anvil-serving.build-history-plan/v1', 'context': 'example', 'builder': 'example',
            'expected_engine_id': 'engine-1', 'expected_buildx_revision': REVISION,
            'started_after': '2026-01-01T00:00:00Z', 'started_before': '2026-01-01T01:00:00Z', 'ref': REF}
    path = tmp_path / 'plan.json'
    path.write_text(json.dumps(plan))
    return plan, path


class FakeDocker:
    def __init__(self):
        self.calls = []
        self.builder = {'Name': 'example', 'Driver': 'docker', 'Nodes': [{'Name': 'example0', 'Endpoint': 'example', 'Status': 'running'}]}
        self.engine = 'engine-1'
        self.version = f'github.com/docker/buildx v0.37.0 {REVISION}'
        # Shape is the exact lsContext.MarshalJSON output: full reference, raw time and transient name.
        self.record = {'ref': REF, 'name': SECRET_NAME, 'status': 'Error',
                       'created_at': '2026-01-01T00:30:00.123456789Z', 'completed_at': '2026-01-01T00:31:00Z',
                       'total_steps': 3, 'completed_steps': 2, 'cached_steps': 1}
        self.drift = False
        self.bindings = 0
    def __call__(self, argv, **kwargs):
        self.calls.append(argv)
        assert 0 < kwargs['timeout'] <= 30 and kwargs['max_output_bytes'] == 1024 * 1024
        assert argv[:3] == ['docker', '--context', 'example']
        args = argv[3:]
        if args == ['buildx', 'version']:
            data = self.version
        elif args[:2] == ['buildx', 'ls']:
            self.bindings += 1
            row = self.builder if not self.drift or self.bindings == 1 else {**self.builder, 'Driver': 'remote'}
            data = json.dumps(row)
        elif args[0] == 'info': data = json.dumps(self.engine)
        elif args[:5] == ['buildx', '--builder', 'example', 'history', 'ls']:
            assert args[5:] == ['--no-trunc', '--filter', 'startedAt>2026-01-01T00:00:00Z', '--filter', 'startedAt<2026-01-01T01:00:00Z', '--format', history._LIST_FORMAT]
            data = json.dumps(self.record)
        else: pytest.fail(f'unexpected command: {args}')
        return subprocess.CompletedProcess(argv, 0, data.encode(), b'')


def test_exact_producer_shape_discards_name_and_never_inspects(setup):
    _, path = setup
    docker = FakeDocker()
    result = history.inspect_history(path, runner=docker)
    assert result['records'] == [{k: v for k, v in docker.record.items() if k != 'name'}]
    assert SECRET_NAME not in json.dumps(result) and 'success' in result['limitation']
    assert len(docker.calls) == 7 and docker.bindings == 2
    assert all('inspect' not in call and 'logs' not in call for call in docker.calls)


@pytest.mark.parametrize(('key', 'value'), [('context', '../unsafe'), ('ref', 'short'),
    ('started_after', '2026-01-01'), ('started_before', '2025-01-01T00:00:00Z'),
    ('schema', 'other'), ('expected_buildx_revision', 'short')])
def test_bad_plan_never_invokes_docker(setup, key, value):
    plan, path = setup
    plan[key] = value
    path.write_text(json.dumps(plan))
    def forbidden(*args, **kwargs): pytest.fail('invalid plan invoked Docker')
    with pytest.raises(ValueError): history.inspect_history(path, runner=forbidden)


@pytest.mark.parametrize('defect', ['wrong_engine', 'builder', 'drift', 'version', 'short_id', 'wrong_node',
    'outside', 'negative_steps', 'missing_completed', 'unknown_field', 'name_type', 'status'])
def test_wrong_or_malformed_history_fails_closed(setup, defect):
    _, path = setup
    docker = FakeDocker()
    if defect == 'wrong_engine': docker.engine = 'other'
    if defect == 'builder': docker.builder['Nodes'][0]['Endpoint'] = 'elsewhere'
    if defect == 'drift': docker.drift = True
    if defect == 'version': docker.version = 'github.com/docker/buildx v0.37.0 ' + 'c' * 40
    if defect == 'short_id': docker.record['ref'] = 'short'
    if defect == 'wrong_node': docker.record['ref'] = 'example/other/' + 'b' * 25
    if defect == 'outside': docker.record['created_at'] = '2026-01-01T01:00:00Z'
    if defect == 'negative_steps': docker.record['completed_steps'] = -1
    if defect == 'missing_completed': del docker.record['completed_at']
    if defect == 'unknown_field': docker.record['environment'] = 'hidden'
    if defect == 'name_type': docker.record['name'] = None
    if defect == 'status': docker.record['status'] = 'unexpected'
    with pytest.raises(ValueError): history.inspect_history(path, runner=docker)


def test_running_record_and_nanosecond_boundary(setup):
    plan, path = setup
    del plan['ref']
    path.write_text(json.dumps(plan))
    docker = FakeDocker()
    docker.record.update(status='Running', created_at='2026-01-01T00:00:00.000000001Z')
    del docker.record['completed_at']
    assert history.inspect_history(path, runner=docker)['records'][0]['status'] == 'Running'
    docker.record['created_at'] = '2026-01-01T00:00:00Z'
    with pytest.raises(ValueError): history.inspect_history(path, runner=docker)


def test_command_manifest_declares_read_only():
    from anvil_serving.commands import render_manifest
    row = next(row for row in json.loads(render_manifest())['commands'] if row['path'] == 'host docker-image build-history')
    assert row['mutation_class'] == 'read'


def test_actual_cli_dispatch_does_not_return_name(setup, monkeypatch, capsys):
    from anvil_serving import cli
    _, path = setup
    docker = FakeDocker()
    native = history.inspect_history
    monkeypatch.setattr(history, 'inspect_history', lambda p: native(p, runner=docker))
    assert cli.main(['host', 'docker-image', 'build-history', '--plan', str(path), '--transport', 'local']) == 0
    output = capsys.readouterr().out
    assert 'anvil-serving.build-history/v1' in output and SECRET_NAME not in output


def test_missing_duplicate_and_selected_reference_fail(setup):
    plan, path = setup
    docker = FakeDocker()
    def list_output(output):
        def run(argv, **kwargs):
            if argv[3:8] == ['buildx', '--builder', 'example', 'history', 'ls']:
                return subprocess.CompletedProcess(argv, 0, output, b'')
            return docker(argv, **kwargs)
        return run
    with pytest.raises(ValueError, match='missing'):
        history.inspect_history(path, runner=list_output(b''))
    text = json.dumps(docker.record).encode()
    with pytest.raises(ValueError, match='duplicate'):
        history.inspect_history(path, runner=list_output(text + b'\n' + text))
    plan['ref'] = 'example/example0/' + 'c' * 25
    path.write_text(json.dumps(plan))
    with pytest.raises(ValueError, match='missing'):
        history.inspect_history(path, runner=docker)


def test_engine_and_version_drift_rejected(setup):
    _, path = setup
    for change in ('engine', 'version'):
        docker = FakeDocker()
        def run(argv, **kwargs):
            if docker.bindings > 1 and argv[3] == 'info' and change == 'engine': docker.engine = 'other'
            if docker.bindings > 0 and argv[3:5] == ['buildx', 'version'] and change == 'version':
                docker.version = 'github.com/docker/buildx v0.38.0 ' + REVISION
            return docker(argv, **kwargs)
        with pytest.raises(ValueError): history.inspect_history(path, runner=run)


def test_duplicate_json_plan_and_excessive_window_fail_before_dispatch(setup):
    plan, path = setup
    path.write_text('{"schema":"x","schema":"y"}')
    def forbidden(*args, **kwargs): pytest.fail('invalid plan invoked Docker')
    with pytest.raises(ValueError): history.inspect_history(path, runner=forbidden)
    plan['started_before'] = '2026-01-09T00:00:00Z'
    path.write_text(json.dumps(plan))
    with pytest.raises(ValueError, match='window'): history.inspect_history(path, runner=forbidden)
