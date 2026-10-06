import errno
import json
import os
from pathlib import Path
import subprocess
from types import SimpleNamespace

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
    def __init__(
        self, *, container_state=None, configured_tags=None, child=False, drift=False,
        post_remove_inspect_error=None,
    ):
        self.container_state = container_state
        self.configured_tags = configured_tags or ["repo/app:old"]
        self.child = child
        self.drift = drift
        self.post_remove_inspect_error = post_remove_inspect_error
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
                    return _completed(
                        argv,
                        returncode=1,
                        stderr=self.post_remove_inspect_error or "No such image",
                    )
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
        if command[:1] == ["compose"]:
            path = Path(command[command.index("--file") + 1])
            raw = path.read_text(encoding="utf-8")
            if "[\n" in raw:
                return _completed(argv, returncode=1, stderr="invalid compose YAML")
            if "${" in raw:
                image = "${IMAGE}"
            elif "repo/app:old" in raw:
                image = "repo/app:old"
            else:
                image = TARGET_DIGEST
            return _completed(argv, stdout=json.dumps({
                "services": {"app": {"image": image}},
            }))
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


def test_nested_operational_recipe_reference_blocks_exact_image_removal(tmp_path):
    recipe = tmp_path / "candidates" / "candidate" / "serve-recipes.toml"
    recipe.parent.mkdir(parents=True)
    recipe.write_text('[recipe.serve]\nimage = "%s"\n' % TARGET_DIGEST, encoding="utf-8")
    fixture = DockerFixture(configured_tags=[])

    result = docker_images.remove_docker_image(
        TARGET_ID, confirm=True, config_home=tmp_path, runner=fixture
    )

    assert result["outcome"] == "blocked"
    assert result["inspection"]["references"]["configured"] == [{
        "path": "candidates/candidate/serve-recipes.toml",
        "field": "recipe.serve.image",
        "value": TARGET_DIGEST,
    }]


def test_nested_workbench_json_image_reference_blocks_exact_image_removal(tmp_path):
    config = tmp_path / "workbench" / "bootstrap.json"
    config.parent.mkdir()
    config.write_text(json.dumps({"pi": {"image": TARGET_DIGEST}}), encoding="utf-8")
    fixture = DockerFixture(configured_tags=[])

    result = docker_images.remove_docker_image(
        TARGET_ID, confirm=True, config_home=tmp_path, runner=fixture
    )

    assert result["outcome"] == "blocked"
    assert result["inspection"]["references"]["configured"] == [{
        "path": "workbench/bootstrap.json",
        "field": "pi.image",
        "value": TARGET_DIGEST,
    }]


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


def test_malformed_selected_json_config_fails_closed(tmp_path):
    (tmp_path / "candidate-stack.json").write_text("not json", encoding="utf-8")
    fixture = DockerFixture(configured_tags=[])

    result = docker_images.remove_docker_image(
        TARGET_ID, confirm=True, config_home=tmp_path, runner=fixture
    )

    errors = result["inspection"]["references"]["config_audit_errors"]
    assert result["outcome"] == "blocked"
    assert errors[0].startswith("candidate-stack.json:")


def test_unreadable_selected_json_config_fails_closed(tmp_path, monkeypatch):
    config = tmp_path / "candidate-stack.json"
    config.write_text(json.dumps({"candidate": {"image": TARGET_DIGEST}}), encoding="utf-8")
    original = type(config).read_bytes

    def unreadable(path):
        if path == config:
            raise OSError("permission denied")
        return original(path)

    monkeypatch.setattr(type(config), "read_bytes", unreadable)
    fixture = DockerFixture(configured_tags=[])

    result = docker_images.remove_docker_image(
        TARGET_ID, confirm=True, config_home=tmp_path, runner=fixture
    )

    errors = result["inspection"]["references"]["config_audit_errors"]
    assert result["outcome"] == "blocked"
    assert errors == ["candidate-stack.json: permission denied"]


def test_unreadable_config_directory_fails_closed(tmp_path, monkeypatch):
    blocked = tmp_path / "candidate-stack"

    def inaccessible(root, *, followlinks, onerror):
        assert root == tmp_path
        assert followlinks is False
        onerror(OSError(errno.EACCES, "Permission denied", str(blocked)))
        return []

    monkeypatch.setattr(docker_images.os, "walk", inaccessible)
    fixture = DockerFixture(configured_tags=[])

    result = docker_images.remove_docker_image(
        TARGET_ID, confirm=True, config_home=tmp_path, runner=fixture
    )

    errors = result["inspection"]["references"]["config_audit_errors"]
    assert result["outcome"] == "blocked"
    assert errors == ["candidate-stack: [Errno 13] Permission denied: '%s'" % blocked]


def test_artifact_and_dependency_trees_do_not_block_config_audit(tmp_path):
    for relative in (
        "benchmark-harness-cache/run/bad.json",
        "evidence/run/bad.json",
        "candidates/candidate/venv/bad.json",
        "pi-web/current/node_modules/package/bad.json",
    ):
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("not json", encoding="utf-8")
    fixture = DockerFixture(configured_tags=[])

    result = docker_images.remove_docker_image(
        TARGET_ID, dry_run=True, config_home=tmp_path, runner=fixture
    )

    assert result["outcome"] == "preview"
    assert result["inspection"]["references"]["config_audit_errors"] == []


@pytest.mark.parametrize("filename", ["candidate-stack.yaml", "candidate-stack.json"])
def test_arbitrary_config_filename_image_reference_blocks_exact_image_removal(
    tmp_path, filename,
):
    path = tmp_path / filename
    if path.suffix == ".json":
        path.write_text(json.dumps({"candidate": {"image": TARGET_DIGEST}}), encoding="utf-8")
    else:
        path.write_text("services:\n  app:\n    image: %s\n" % TARGET_DIGEST, encoding="utf-8")
    fixture = DockerFixture(configured_tags=[])

    result = docker_images.remove_docker_image(
        TARGET_ID, confirm=True, config_home=tmp_path, runner=fixture
    )

    assert result["outcome"] == "blocked"
    assert result["inspection"]["references"]["configured"][0]["path"] == filename


def test_flow_yaml_image_reference_blocks_exact_image_removal(tmp_path):
    (tmp_path / "candidate-stack.yaml").write_text(
        "services: {app: {image: %s}}\n" % TARGET_DIGEST,
        encoding="utf-8",
    )
    fixture = DockerFixture(configured_tags=[])

    result = docker_images.remove_docker_image(
        TARGET_ID, confirm=True, config_home=tmp_path, runner=fixture
    )

    configured = result["inspection"]["references"]["configured"]
    assert result["outcome"] == "blocked"
    assert configured == [{
        "path": "candidate-stack.yaml",
        "field": "services.app.image",
        "value": TARGET_DIGEST,
    }]
    compose = next(call for call in fixture.calls if call[1:2] == ["compose"])
    assert compose[1:] == [
        "compose", "--env-file", os.devnull,
        "--project-directory", str(tmp_path), "--file",
        str(tmp_path / "candidate-stack.yaml"),
        "config", "--format", "json", "--no-interpolate",
        "--no-env-resolution", "--no-path-resolution",
    ]
    assert not any(call[1:3] == ["image", "rm"] for call in fixture.calls)


@pytest.mark.parametrize("key", ['"image"', r"im\u0061ge"])
def test_quoted_or_escaped_yaml_image_key_is_validated(tmp_path, key):
    (tmp_path / "candidate-stack.yaml").write_text(
        "metadata:\n  %s: %s\n" % (key, TARGET_DIGEST),
        encoding="utf-8",
    )
    fixture = DockerFixture(configured_tags=[])

    result = docker_images.remove_docker_image(
        TARGET_ID, confirm=True, config_home=tmp_path, runner=fixture
    )

    assert result["outcome"] == "blocked"
    assert any(call[1:2] == ["compose"] for call in fixture.calls)


def test_flow_yaml_tag_reference_blocks_exact_image_removal(tmp_path):
    (tmp_path / "candidate-stack.yaml").write_text(
        "{services: {app: {image: repo/app:old}}}\n", encoding="utf-8"
    )
    fixture = DockerFixture()

    result = docker_images.remove_docker_image(
        TARGET_ID, confirm=True, config_home=tmp_path, runner=fixture
    )

    assert result["outcome"] == "blocked"
    assert result["inspection"]["references"]["configured"][0]["value"] == "repo/app:old"


@pytest.mark.parametrize("yaml", [
    "services:\n  app:\n    image: ${IMAGE}\n",
    "{services: {app: {image: ${IMAGE}}}}\n",
])
def test_unresolved_yaml_image_reference_fails_closed(tmp_path, yaml):
    (tmp_path / "candidate-stack.yaml").write_text(yaml, encoding="utf-8")
    fixture = DockerFixture(configured_tags=[])

    result = docker_images.remove_docker_image(
        TARGET_ID, dry_run=True, config_home=tmp_path, runner=fixture
    )

    errors = result["inspection"]["references"]["config_audit_errors"]
    assert result["outcome"] == "blocked"
    assert errors == ["candidate-stack.yaml: unresolved image reference at services.app.image"]


def test_non_compose_yaml_without_image_reference_does_not_block_audit(tmp_path):
    (tmp_path / "searxng-settings.yml").write_text(
        "use_default_settings: true\nsearch:\n  safe_search: 0\n  image_proxy: false\n",
        encoding="utf-8",
    )
    (tmp_path / "prometheus.yml").write_text(
        "global:\n  scrape_interval: 15s\nscrape_configs: []\n",
        encoding="utf-8",
    )
    fixture = DockerFixture(configured_tags=[])

    result = docker_images.remove_docker_image(
        TARGET_ID, dry_run=True, config_home=tmp_path, runner=fixture
    )

    assert result["outcome"] == "preview"
    assert result["inspection"]["references"]["config_audit_errors"] == []
    assert not any(call[1:2] == ["compose"] for call in fixture.calls)


def test_potential_yaml_image_key_is_validated_and_blocks_exact_image_removal(tmp_path):
    (tmp_path / "prometheus.yml").write_text(
        "static_metadata:\n  immutable_image: %s\n" % TARGET_DIGEST,
        encoding="utf-8",
    )
    fixture = DockerFixture(configured_tags=[])

    result = docker_images.remove_docker_image(
        TARGET_ID, confirm=True, config_home=tmp_path, runner=fixture
    )

    configured = result["inspection"]["references"]["configured"]
    assert result["outcome"] == "blocked"
    assert configured[0]["path"] == "prometheus.yml"
    assert configured[0]["value"] == TARGET_DIGEST
    assert any(call[1:2] == ["compose"] for call in fixture.calls)


def test_plain_yaml_immutable_reference_blocks_without_compose_parse(tmp_path):
    (tmp_path / "metadata.yml").write_text(
        "immutable_ref: %s\n" % TARGET_DIGEST,
        encoding="utf-8",
    )
    fixture = DockerFixture(configured_tags=[])

    result = docker_images.remove_docker_image(
        TARGET_ID, confirm=True, config_home=tmp_path, runner=fixture
    )

    configured = result["inspection"]["references"]["configured"]
    assert result["outcome"] == "blocked"
    assert configured[0]["path"] == "metadata.yml"
    assert configured[0]["value"] == TARGET_DIGEST
    assert not any(call[1:2] == ["compose"] for call in fixture.calls)


def test_malformed_yaml_config_fails_closed(tmp_path):
    (tmp_path / "candidate-stack.yaml").write_text("services:\n  app: [\n", encoding="utf-8")
    fixture = DockerFixture(configured_tags=[])

    result = docker_images.remove_docker_image(
        TARGET_ID, confirm=True, config_home=tmp_path, runner=fixture
    )

    errors = result["inspection"]["references"]["config_audit_errors"]
    assert result["outcome"] == "blocked"
    assert errors == ["candidate-stack.yaml: invalid compose YAML"]


def test_compose_yaml_validation_work_is_bounded(tmp_path, monkeypatch):
    (tmp_path / "first.yaml").write_text(
        "services:\n  first:\n    image: %s\n" % TARGET_DIGEST,
        encoding="utf-8",
    )
    (tmp_path / "second.yaml").write_text(
        "services:\n  second:\n    image: %s\n" % TARGET_DIGEST,
        encoding="utf-8",
    )
    monkeypatch.setattr(docker_images, "MAX_COMPOSE_CONFIG_FILES", 1)
    fixture = DockerFixture(configured_tags=[])

    result = docker_images.remove_docker_image(
        TARGET_ID, dry_run=True, config_home=tmp_path, runner=fixture
    )

    errors = result["inspection"]["references"]["config_audit_errors"]
    assert result["outcome"] == "blocked"
    assert errors == ["second.yaml: too many Compose YAML files (maximum 1)"]
    assert len([call for call in fixture.calls if call[1:2] == ["compose"]]) == 1


def test_symlinked_config_directory_fails_closed(tmp_path, monkeypatch):
    recipes = tmp_path / "recipes"
    recipes.mkdir()
    (recipes / "image.toml").write_text(
        'image = "%s"\n' % TARGET_DIGEST,
        encoding="utf-8",
    )
    original = docker_images._path_is_link_like
    monkeypatch.setattr(
        docker_images,
        "_path_is_link_like",
        lambda path: path == recipes or original(path),
    )
    fixture = DockerFixture(configured_tags=[])

    result = docker_images.remove_docker_image(
        TARGET_ID, confirm=True, config_home=tmp_path, runner=fixture
    )

    errors = result["inspection"]["references"]["config_audit_errors"]
    assert result["outcome"] == "blocked"
    assert errors == ["recipes: symlinked directory is not audited"]
    assert not any(call[1:3] == ["image", "rm"] for call in fixture.calls)


def test_windows_python311_junction_is_link_like():
    class JunctionPath:
        @staticmethod
        def is_symlink():
            return False

        @staticmethod
        def lstat():
            return SimpleNamespace(
                st_file_attributes=docker_images.FILE_ATTRIBUTE_REPARSE_POINT
            )

    assert docker_images._path_is_link_like(JunctionPath()) is True


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


def test_post_removal_inspect_error_is_not_accepted_as_absence(tmp_path):
    fixture = DockerFixture(
        configured_tags=[],
        post_remove_inspect_error="permission denied while connecting to Docker",
    )

    result = docker_images.remove_docker_image(
        TARGET_ID, confirm=True, config_home=tmp_path, runner=fixture
    )

    assert result["outcome"] == "failed"
    assert result["applied"] is False
    assert result["removal_attempted"] is True
    assert "permission denied" in result["error"]
