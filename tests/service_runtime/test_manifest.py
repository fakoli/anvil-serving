"""Service declarations reject ambiguous ownership before any OS operation."""
import json

import pytest


def manifest_file(tmp_path, **changes):
    data = dict(id="voice", resource="voice", manager="launchd", engine="mlx-lm",
                label="com.example.voice", owner_uid=501, definition="voice.plist",
                definition_sha256="a" * 64)
    data.update(changes)
    path = tmp_path / "services.toml"
    path.write_text('schema = "anvil-services/v1"\n[[service]]\n' +
                    "\n".join(f"{k} = {json.dumps(v)}" for k, v in data.items()) + "\n")
    return path


def test_manifest_symlink_is_refused_even_without_platform_nofollow(tmp_path, monkeypatch):
    from anvil_serving.service_runtime import manifest
    from anvil_serving.service_runtime.contracts import ServiceError

    target = manifest_file(tmp_path)
    link = tmp_path / "linked-services.toml"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("symlinks unavailable")
    monkeypatch.setattr(manifest.os, "O_NOFOLLOW", 0, raising=False)
    with pytest.raises(ServiceError, match="non-symlink"):
        manifest.load_manifest(link)


def test_write_roundtrip_preserves_bindings_and_requires_expected_digest(tmp_path):
    from anvil_serving.service_runtime.manifest import load_manifest, save_manifest, digest
    from anvil_serving.service_runtime.contracts import ServiceError
    path = manifest_file(tmp_path)
    bindings = load_manifest(path)
    before = digest(path)
    save_manifest(path, bindings, expected_digest=before)
    assert load_manifest(path) == bindings
    with pytest.raises(ServiceError, match="changed"):
        save_manifest(path, bindings, expected_digest=before)


def retained_row(**changes):
    row = {
        "id": "retained", "resource": "retained", "manager": "docker", "engine": "none",
        "container": "retained-service", "image_id": "sha256:" + "a" * 64,
        "identity_labels": {"owner": "reviewed"}, "retained_container": True,
        "container_id": "b" * 64, "restart_count": 0, "restart_policy": "unless-stopped",
        "restart_maximum_retry_count": 0, "writable_mounts_sha256": "c" * 64,
        "security_projection_sha256": "d" * 64, "definition": "retained-definition.json",
        "definition_sha256": "e" * 64, "shutdown_grace_seconds": 20,
        "healthcheck_required": True, "healthcheck_sha256": "f" * 64,
    }
    row.update(changes)
    return row


@pytest.mark.parametrize("changes", [
    {"external_compose": True}, {"container_id": "bad"}, {"dependencies": ["other"]},
    {"engine": "generic"}, {"restart_count": -1}, {"restart_policy": "unknown"},
    {"writable_mounts_sha256": "bad"}, {"serve": "anything"},
    {"endpoint": "http://127.0.0.1:9000"}, {"api_key_env": "SERVICE_TOKEN"},
    {"shutdown_grace_seconds": 0}, {"shutdown_grace_seconds": 301},
    {"healthcheck_required": False}, {"healthcheck_sha256": "bad"},
])
def test_retained_container_rejects_ambiguous_or_incomplete_custody(tmp_path, changes):
    from anvil_serving.service_runtime.contracts import ServiceError
    from anvil_serving.service_runtime.manifest import validate

    with pytest.raises(ServiceError):
        validate({"schema": "anvil-services/v1", "service": [retained_row(**changes)]}, tmp_path)


def test_generic_docker_binding_cannot_bypass_recipe_ownership(tmp_path):
    from anvil_serving.service_runtime.contracts import ServiceError
    from anvil_serving.service_runtime.manifest import validate

    row = retained_row(retained_container=False)
    for key in ("container_id", "restart_count", "restart_policy", "restart_maximum_retry_count",
                "writable_mounts_sha256", "security_projection_sha256", "shutdown_grace_seconds",
                "healthcheck_required", "healthcheck_sha256", "definition", "definition_sha256"):
        row.pop(key, None)
    with pytest.raises(ServiceError, match="first-party recipe"):
        validate({"schema": "anvil-services/v1", "service": [row]}, tmp_path)
