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


def test_unknown_supervisor_rejected(tmp_path):
    from anvil_serving.service_runtime.manifest import load_manifest
    from anvil_serving.service_runtime.contracts import ServiceError
    with pytest.raises(ServiceError, match="manager"):
        load_manifest(manifest_file(tmp_path, manager="systemd"))


def test_definition_resolves_against_manifest_not_caller(tmp_path):
    from anvil_serving.service_runtime.manifest import load_manifest
    result = load_manifest(manifest_file(tmp_path))
    assert result["voice"]["definition"] == str(tmp_path / "voice.plist")
    assert result["voice"]["dependencies"] == []


@pytest.mark.parametrize("changes", [
    {"id": "--all"}, {"owner_uid": True}, {"definition_sha256": "invalid"},
    {"token": "secret"}, {"endpoint": "http://name:password@127.0.0.1:8000"},
    {"endpoint": "http://localhost:8000"}, {"dependencies": ["voice"]},
    {"dependencies": ["missing"]}, {"model": ""},
])
def test_invalid_binding_is_refused(tmp_path, changes):
    from anvil_serving.service_runtime.manifest import load_manifest
    from anvil_serving.service_runtime.contracts import ServiceError
    with pytest.raises(ServiceError):
        load_manifest(manifest_file(tmp_path, **changes))


def test_duplicate_supervision_identity_is_refused(tmp_path):
    from anvil_serving.service_runtime.manifest import load_manifest
    from anvil_serving.service_runtime.contracts import ServiceError
    path = manifest_file(tmp_path)
    original = path.read_text().split("[[service]]", 1)[1]
    path.write_text(path.read_text() + "[[service]]" + original.replace('id = "voice"', 'id = "other"'))
    with pytest.raises(ServiceError, match="identity"):
        load_manifest(path)


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


def test_legacy_exception_is_only_for_approved_speech_engines():
    from anvil_serving.service_runtime.contracts import validate_platform, ServiceError
    with pytest.raises(ServiceError):
        validate_platform({"manager": "launchd", "engine": "vllm", "support": "legacy"}, "macos")


def test_staged_install_source_resolves_against_manifest(tmp_path):
    from anvil_serving.service_runtime.manifest import load_manifest
    result = load_manifest(manifest_file(tmp_path, source_definition="staged.plist"))
    assert result["voice"]["source_definition"] == str(tmp_path / "staged.plist")
