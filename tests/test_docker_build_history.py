import json
import subprocess

import pytest

from anvil_serving import docker_build_history as history


@pytest.fixture
def setup(tmp_path):
    plan = {'schema': 'anvil-serving.build-history-plan/v1', 'context': 'example', 'builder': 'example',
            'expected_engine_id': 'engine-1', 'started_after': '2026-01-01T00:00:00Z',
            'started_before': '2026-01-01T01:00:00Z', 'ref': 'a' * 25}
    path = tmp_path / 'plan.json'
    path.write_text(json.dumps(plan))
    return plan, path


class FakeDocker:
    def __init__(self):
        self.calls = []
        self.builder = {'Name': 'example', 'Driver': 'docker', 'Nodes': [{'Endpoint': 'example', 'Status': 'running'}]}
        self.engine = 'engine-1'
        self.record = {'id': 'a' * 25, 'status': 'Completed', 'created_at': '2026-01-01T00:30:00Z', 'duration': '1.2s'}
        self.detail = {'ref': 'a' * 25, 'status': 'completed', 'started_at': '2026-01-01T00:30:00Z',
                       'completed_at': '2026-01-01T00:31:00Z', 'completed_steps': 2, 'total_steps': 3, 'duration': 60000000000}
        self.drift = False
        self.bindings = 0
    def __call__(self, argv, **kwargs):
        self.calls.append(argv)
        assert kwargs == {'timeout': 30, 'max_output_bytes': 1024 * 1024}
        assert argv[:3] == ['docker', '--context', 'example']
        args = argv[3:]
        if args[:2] == ['buildx', 'ls']:
            self.bindings += 1
            row = self.builder if not self.drift or self.bindings == 1 else {**self.builder, 'Driver': 'remote'}
            data = json.dumps(row)
        elif args[0] == 'info': data = json.dumps(self.engine)
        elif args[:5] == ['buildx', '--builder', 'example', 'history', 'ls']:
            assert args[5:] == ['--no-trunc', '--filter', 'startedAt>2026-01-01T00:00:00Z', '--filter', 'startedAt<2026-01-01T01:00:00Z', '--format', history._LIST_FORMAT]
            data = json.dumps(self.record)
        elif args[:5] == ['buildx', '--builder', 'example', 'history', 'inspect']:
            assert args[5:] == ['--format', history._DETAIL_FORMAT, 'a' * 25]
            data = json.dumps(self.detail)
        else: pytest.fail(f'unexpected command: {args}')
        return subprocess.CompletedProcess(argv, 0, data.encode(), b'')


def test_history_binds_and_filters_fields(setup):
    _, path = setup
    docker = FakeDocker()
    result = history.inspect_history(path, runner=docker)
    assert result['records'] == [docker.record] and result['detail'] == docker.detail
    assert len(docker.calls) == 6 and docker.bindings == 2
    assert all('Config' not in arg and 'Attachments' not in arg for call in docker.calls for arg in call)
    assert 'quiescence' in result['limitation']


@pytest.mark.parametrize(('key', 'value'), [('context', '../unsafe'), ('ref', 'short'),
    ('started_after', '2026-01-01'), ('started_before', '2025-01-01T00:00:00Z'), ('schema', 'other')])
def test_bad_plan_never_invokes_docker(setup, key, value):
    plan, path = setup
    plan[key] = value
    path.write_text(json.dumps(plan))
    def forbidden(*args, **kwargs): pytest.fail('invalid plan invoked Docker')
    with pytest.raises(ValueError): history.inspect_history(path, runner=forbidden)


@pytest.mark.parametrize('defect', ['wrong_engine', 'builder', 'drift', 'short_id', 'outside', 'detail_mismatch', 'negative_steps', 'missing_detail', 'unknown_field'])
def test_wrong_or_malformed_history_fails_closed(setup, defect):
    _, path = setup
    docker = FakeDocker()
    if defect == 'wrong_engine': docker.engine = 'other'
    if defect == 'builder': docker.builder['Nodes'][0]['Endpoint'] = 'elsewhere'
    if defect == 'drift': docker.drift = True
    if defect == 'short_id': docker.record['id'] = 'short'
    if defect == 'outside': docker.record['created_at'] = '2026-01-01T01:00:00Z'
    if defect == 'detail_mismatch': docker.detail['ref'] = 'b' * 25
    if defect == 'negative_steps': docker.detail['completed_steps'] = -1
    if defect == 'missing_detail': del docker.detail['completed_at']
    if defect == 'unknown_field': docker.record['environment'] = 'hidden'
    with pytest.raises((ValueError, KeyError)): history.inspect_history(path, runner=docker)


def test_list_only_and_nanosecond_boundary(setup):
    plan, path = setup
    del plan['ref']
    path.write_text(json.dumps(plan))
    docker = FakeDocker()
    docker.record['created_at'] = '2026-01-01T00:00:00.000000001Z'
    result = history.inspect_history(path, runner=docker)
    assert result['detail'] is None and len(docker.calls) == 5
    docker.record['created_at'] = '2026-01-01T00:00:00Z'
    with pytest.raises(ValueError): history.inspect_history(path, runner=docker)


def test_command_manifest_declares_read_only():
    from anvil_serving.commands import render_manifest
    commands = json.loads(render_manifest())['commands']
    row = next(row for row in commands if row['path'] == 'host docker-image build-history')
    assert row['mutation_class'] == 'read'


def test_actual_cli_dispatch_uses_selected_fields(setup, monkeypatch, capsys):
    from anvil_serving import cli
    _, path = setup
    docker = FakeDocker()
    native = history.inspect_history
    monkeypatch.setattr(history, 'inspect_history', lambda p: native(p, runner=docker))
    assert cli.main(['host', 'docker-image', 'build-history', '--plan', str(path), '--transport', 'local']) == 0
    output = capsys.readouterr().out
    assert 'anvil-serving.build-history/v1' in output and 'engine-1' in output
    assert 'environment' not in output and 'attachments' not in output


def test_missing_or_duplicate_records_fail(setup):
    _, path = setup
    docker = FakeDocker()
    def run_empty(argv, **kwargs):
        if argv[3:8] == ['buildx', '--builder', 'example', 'history', 'ls']:
            return subprocess.CompletedProcess(argv, 0, b'', b'')
        return docker(argv, **kwargs)
    with pytest.raises(ValueError, match='missing'):
        history.inspect_history(path, runner=run_empty)
    def run_duplicate(argv, **kwargs):
        if argv[3:8] == ['buildx', '--builder', 'example', 'history', 'ls']:
            text = json.dumps(docker.record).encode()
            return subprocess.CompletedProcess(argv, 0, text + b'\n' + text, b'')
        return docker(argv, **kwargs)
    with pytest.raises(ValueError, match='duplicate'):
        history.inspect_history(path, runner=run_duplicate)


def test_status_change_and_invalid_duration_fail(setup):
    _, path = setup
    docker = FakeDocker()
    docker.detail['status'] = 'running'
    with pytest.raises(ValueError, match='status changed'):
        history.inspect_history(path, runner=docker)
    docker = FakeDocker()
    docker.record['duration'] = 'not-a-duration'
    with pytest.raises(ValueError, match='duration'):
        history.inspect_history(path, runner=docker)


def test_engine_drift_between_reads_rejected(setup):
    _, path = setup
    docker = FakeDocker()
    def run(argv, **kwargs):
        if argv[3] == 'info' and docker.bindings > 1:
            docker.engine = 'different-engine'
        return docker(argv, **kwargs)
    with pytest.raises(ValueError, match='engine identity'):
        history.inspect_history(path, runner=run)


def test_duplicate_json_plan_and_excessive_window_fail_before_dispatch(setup):
    plan, path = setup
    path.write_text('{"schema":"x","schema":"y"}')
    def forbidden(*args, **kwargs): pytest.fail('invalid plan invoked Docker')
    with pytest.raises(ValueError): history.inspect_history(path, runner=forbidden)
    plan['started_before'] = '2026-01-09T00:00:00Z'
    path.write_text(json.dumps(plan))
    with pytest.raises(ValueError, match='window'): history.inspect_history(path, runner=forbidden)
