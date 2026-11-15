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


@pytest.mark.parametrize('repository', ['registry.example/runtime', 'registry.example:5000/runtime'])
def test_tagged_digest_matches_repository_and_digest_without_losing_registry_port(repository):
    digest = repository + '@sha256:' + 'b' * 64
    reference = repository + ':release' + digest[digest.index('@'):]

    def run(argv, **kwargs):
        assert argv[-1] == reference
        return subprocess.CompletedProcess(argv, 0, json.dumps(fixture_row(repo_digests=[digest])), '')

    assert inspect_image_identity(reference, runner=run)['requested_identity'] == reference

    def wrong_digest(argv, **kwargs):
        row = fixture_row(repo_digests=[repository + '@sha256:' + 'c' * 64])
        return subprocess.CompletedProcess(argv, 0, json.dumps(row), '')

    with pytest.raises(DockerImageCleanupError, match='does not match'):
        inspect_image_identity(reference, runner=wrong_digest)
