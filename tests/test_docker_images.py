import json
import subprocess

import pytest

from anvil_serving import docker_images


TARGET_HEX = "a" * 64
TARGET_ID = "sha256:" + TARGET_HEX
TARGET_DIGEST = "repo/app@sha256:" + "b" * 64
CHILD_HEX = "c" * 64
CHILD_ID = "sha256:" + CHILD_HEX
CONTAINER_ID = "d" * 64


def _completed(argv, *, returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(argv, returncode, stdout=stdout, stderr=stderr)


class DockerFixture:
    def __init__(self, *, container_state=None, configured_tags=None, child=False, drift=False):
        self.container_state = container_state
        self.configured_tags = configured_tags or ["repo/app:old"]
        self.child = child
        self.drift = drift
        self.removed = False
        self.calls = []
        self.target_single_inspections = 0

    def _target(self, *, drifted=False):
        return {
            "Id": TARGET_ID,
            "Parent": "",
            "RepoTags": self.configured_tags + (["repo/app:drifted"] if drifted else []),
            "RepoDigests": [TARGET_DIGEST],
            "Size": 1_000_000_000,
            "RootFS": {"Layers": ["sha256:layer-one"]},
        }

    @staticmethod
    def _child():
        return {
            "Id": CHILD_ID,
            "Parent": TARGET_ID,
            "RepoTags": ["repo/child:latest"],
            "RepoDigests": [],
            "Size": 1_100_000_000,
            "RootFS": {"Layers": ["sha256:layer-one", "sha256:layer-two"]},
        }

    def __call__(self, argv, **_kwargs):
        self.calls.append(list(argv))
        command = argv[1:]
        if command[:2] == ["image", "inspect"]:
            refs = command[2:]
            if len(refs) == 1 and refs[0] in {TARGET_ID, TARGET_HEX, TARGET_DIGEST}:
                self.target_single_inspections += 1
                if self.removed:
                    return _completed(argv, returncode=1, stderr="No such image")
                drifted = self.drift and self.target_single_inspections >= 2
                return _completed(argv, stdout=json.dumps([self._target(drifted=drifted)]))
            rows = []
            for reference in refs:
                if reference in {TARGET_ID, TARGET_HEX} and not self.removed:
                    rows.append(self._target())
                elif reference in {CHILD_ID, CHILD_HEX} and self.child:
                    rows.append(self._child())
            return _completed(argv, stdout=json.dumps(rows))
        if command == ["image", "ls", "--all", "--quiet", "--no-trunc"]:
            ids = [TARGET_ID]
            if self.child:
                ids.append(CHILD_ID)
            return _completed(argv, stdout="\n".join(ids) + "\n")
        if command == ["container", "ls", "--all", "--quiet", "--no-trunc"]:
            output = CONTAINER_ID + "\n" if self.container_state else ""
            return _completed(argv, stdout=output)
        if command[:2] == ["container", "inspect"]:
            row = {
                "Id": CONTAINER_ID,
                "Image": TARGET_ID,
                "Name": "/candidate",
                "State": {"Status": self.container_state},
            }
            return _completed(argv, stdout=json.dumps([row]))
        if command == ["system", "df", "--verbose"]:
            output = (
                "Images space usage:\n\n"
                "REPOSITORY  TAG  IMAGE ID      CREATED    SIZE    SHARED SIZE  "
                "UNIQUE SIZE  CONTAINERS\n"
                f"repo/app    old  {TARGET_HEX[:12]}  1 day ago  1GB     0B           "
                "1GB          0\n\n"
                "Containers space usage:\n"
            )
            return _completed(argv, stdout=output)
        if command == ["image", "rm", "--no-prune", TARGET_ID]:
            self.removed = True
            return _completed(argv, stdout="Deleted: " + TARGET_ID + "\n")
        raise AssertionError("unexpected docker command: %r" % argv)


@pytest.mark.parametrize("value", ["repo/app:latest", TARGET_HEX[:12], "--all", ""])
def test_exact_image_cleanup_rejects_tags_and_abbreviated_ids(value):
    with pytest.raises(docker_images.DockerImageCleanupError, match="immutable"):
        docker_images.normalize_immutable_image_reference(value)


def test_unattached_exact_image_dry_run_and_confirmed_removal(tmp_path):
    fixture = DockerFixture(configured_tags=[])

    preview = docker_images.remove_docker_image(
        TARGET_ID, dry_run=True, config_home=tmp_path, runner=fixture
    )

    assert preview["outcome"] == "preview"
    assert preview["inspection"]["estimated_reclaimable_bytes"] == 1_000_000_000
    assert not any(call[1:3] == ["image", "rm"] for call in fixture.calls)

    applied = docker_images.remove_docker_image(
        TARGET_ID, confirm=True, config_home=tmp_path, runner=fixture
    )

    assert applied["outcome"] == "removed"
    assert applied["applied"] is True
    assert applied["removed_image_id"] == TARGET_ID
    assert ["docker", "image", "rm", "--no-prune", TARGET_ID] in fixture.calls


@pytest.mark.parametrize("state", ["running", "exited"])
def test_container_reference_blocks_exact_image_removal(tmp_path, state):
    fixture = DockerFixture(container_state=state, configured_tags=[])

    result = docker_images.remove_docker_image(
        TARGET_ID, confirm=True, config_home=tmp_path, runner=fixture
    )

    assert result["outcome"] == "blocked"
    containers = result["inspection"]["references"]["containers"]
    assert containers == [{
        "container_id": CONTAINER_ID,
        "name": "candidate",
        "state": state,
    }]
    assert not any(call[1:3] == ["image", "rm"] for call in fixture.calls)


def test_declared_recipe_reference_blocks_exact_image_removal(tmp_path):
    (tmp_path / "serve-recipes.toml").write_text(
        '[recipe.serve]\nimage = "%s"\n' % TARGET_DIGEST,
        encoding="utf-8",
    )
    fixture = DockerFixture(configured_tags=[])

    result = docker_images.remove_docker_image(
        TARGET_ID, confirm=True, config_home=tmp_path, runner=fixture
    )

    configured = result["inspection"]["references"]["configured"]
    assert result["outcome"] == "blocked"
    assert configured[0]["path"] == "serve-recipes.toml"
    assert configured[0]["field"] == "recipe.serve.image"


def test_declared_rollback_reference_blocks_exact_image_removal(tmp_path):
    (tmp_path / "rollback.json").write_text(
        json.dumps({"rollback": {"image": TARGET_DIGEST}}),
        encoding="utf-8",
    )
    fixture = DockerFixture(configured_tags=[])

    result = docker_images.remove_docker_image(
        TARGET_ID, confirm=True, config_home=tmp_path, runner=fixture
    )

    configured = result["inspection"]["references"]["configured"]
    assert result["outcome"] == "blocked"
    assert configured[0]["field"] == "rollback.image"


def test_dependent_child_image_blocks_exact_image_removal(tmp_path):
    fixture = DockerFixture(configured_tags=[], child=True)

    result = docker_images.remove_docker_image(
        TARGET_ID, confirm=True, config_home=tmp_path, runner=fixture
    )

    children = result["inspection"]["references"]["dependent_images"]
    assert result["outcome"] == "blocked"
    assert children[0]["image_id"] == CHILD_ID


def test_identity_drift_between_inspection_and_removal_fails_closed(tmp_path):
    fixture = DockerFixture(configured_tags=[], child=True, drift=True)
    fixture.child = False

    result = docker_images.remove_docker_image(
        TARGET_ID, confirm=True, config_home=tmp_path, runner=fixture
    )

    assert result["outcome"] == "identity-drift"
    assert not any(call[1:3] == ["image", "rm"] for call in fixture.calls)
