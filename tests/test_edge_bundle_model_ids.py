"""Reject remote model names that cannot pass the hub's repository syntax."""
import pytest

from anvil_serving.edge_bundle import EdgeBundle, EdgeBundleError
from tests.test_edge_bundle import _manifest


@pytest.mark.parametrize("model", [
    "org/model.", "org/model-", "org/model--x", "org/model..x", "org/model.git",
    "org./model", "org-/model", "org--x/model", "org..x/model", "org/" + "m" * 97,
])
def test_invalid_remote_model_ids(model):
    manifest = _manifest()
    manifest["inference"]["served_model"] = model
    with pytest.raises(EdgeBundleError, match="inference.served_model is invalid"):
        EdgeBundle.from_mapping(manifest)


@pytest.mark.parametrize("model", ["org/model", "org/model_v1.2-a", "org/" + "m" * 96])
def test_supported_remote_model_ids(model):
    manifest = _manifest()
    manifest["inference"]["served_model"] = model
    assert EdgeBundle.from_mapping(manifest).inference.served_model == model


def test_tailnet_hostname_rejects_trailing_hyphen():
    manifest = _manifest()
    manifest["tailnet"]["hostname"] = "remote-"
    with pytest.raises(EdgeBundleError, match="tailnet.hostname is invalid"):
        EdgeBundle.from_mapping(manifest)


@pytest.mark.parametrize("repository", [
    "registry.example/a/../b", "registry.example/a//b", "registry.example/.bad",
    "registry.example/a/./b", "registry.example/a/", "registry.example/a___b",
    "registry_bad.example/model", "registry.example:5000/model",
])
@pytest.mark.parametrize("section", ["tailnet", "inference"])
def test_image_repositories_reject_normalized_or_unsupported_names(repository, section):
    manifest = _manifest()
    manifest[section]["image"] = repository + ":v1@sha256:" + "a" * 64
    with pytest.raises(EdgeBundleError, match=section + ".image is invalid"):
        EdgeBundle.from_mapping(manifest)


def test_image_repository_permits_distribution_separators():
    manifest = _manifest()
    image = "registry.example/a__b/model--x:v1@sha256:" + "a" * 64
    manifest["inference"]["image"] = image
    assert EdgeBundle.from_mapping(manifest).inference.image == image


@pytest.mark.parametrize("prefix", ["AK" + "IA", "AS" + "IA"])
@pytest.mark.parametrize("section,field", [("inference", "api_key_env"), ("tailnet", "auth_key_env")])
def test_credential_shaped_references_match_canonical_router_rejection(prefix, section, field):
    manifest = _manifest()
    value = prefix + "ABCDEFGHIJKLMNOP"
    manifest[section][field] = value
    with pytest.raises(EdgeBundleError) as caught:
        EdgeBundle.from_mapping(manifest)
    assert "environment reference" in str(caught.value)
    assert value not in str(caught.value)
