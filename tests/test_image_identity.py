import json
import subprocess

import pytest

from anvil_serving.image_identity import DEFAULT_LABELS, inspect_image_identity
from anvil_serving.docker_images import DockerImageCleanupError


IMAGE_ID = 'sha256:' + 'a' * 64
DIGEST = 'registry.example/runtime@sha256:' + 'b' * 64


def fixture_row(**changes):
    row = {'image_id': IMAGE_ID, 'repo_digests': [DIGEST], 'os': 'linux',
           'architecture': 'amd64', 'labels': dict.fromkeys(DEFAULT_LABELS, None)}
    row.update(changes)
    return row


def test_selected_labels_only_and_one_read_only_inspection():
    calls = []
    row = fixture_row()
    row['labels']['ai.release.cache-schema'] = 'v81'

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, json.dumps(row), '')

    result = inspect_image_identity(DIGEST, labels=['ai.release.cache-schema'], runner=run)
    assert result['labels']['ai.release.cache-schema'] == 'v81'
    assert result['requested_identity'] == DIGEST
    assert len(calls) == 1
    argv, kwargs = calls[0]
    assert argv[:4] == ['docker', 'image', 'inspect', '--format']
    assert argv[-1] == DIGEST
    assert '.Env' not in argv[4]
    assert '{{json .Config.Labels}}' not in argv[4]
    assert kwargs['timeout'] == 30


@pytest.mark.parametrize('image,labels', [
    ('registry.example/runtime:latest', []), (IMAGE_ID, ['x\nsecret']),
    ('repo\x00name@sha256:' + 'b' * 64, []),
    (IMAGE_ID, [f'label{i}' for i in range(65)]),
])
def test_invalid_request_never_calls_docker(image, labels):
    def run(*args, **kwargs):
        pytest.fail('invalid input invoked Docker')
    with pytest.raises(DockerImageCleanupError):
        inspect_image_identity(image, labels=labels, runner=run)


@pytest.mark.parametrize('row', [
    fixture_row(repo_digests=[]), fixture_row(image_id='short'),
    fixture_row(os=''), fixture_row(labels={'unselected': 'secret'}),
    fixture_row(labels={**dict.fromkeys(DEFAULT_LABELS), DEFAULT_LABELS[0]: 'x' * 4097}),
    fixture_row(extra='unexpected'),
])
def test_mismatched_or_malformed_identity_fails_closed(row):
    def run(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 0, json.dumps(row), '')
    with pytest.raises(DockerImageCleanupError):
        inspect_image_identity(DIGEST, runner=run)


def test_docker_failure_does_not_echo_unbounded_error_output():
    def run(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 1, '', 'sensitive-unbounded-error')
    with pytest.raises(DockerImageCleanupError) as caught:
        inspect_image_identity(IMAGE_ID, runner=run)
    assert 'sensitive' not in str(caught.value)


def test_full_id_must_resolve_exactly():
    def run(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 0, json.dumps(fixture_row()), '')
    with pytest.raises(DockerImageCleanupError, match='does not match'):
        inspect_image_identity('sha256:' + 'c' * 64, runner=run)
