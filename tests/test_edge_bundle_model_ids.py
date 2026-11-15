"""Reject remote model names that cannot pass the hub's repository syntax."""
import pytest

from anvil_serving.edge_bundle import EdgeBundle, EdgeBundleError
from tests.test_edge_bundle import _manifest


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
