import json
from pathlib import Path

import pytest

from anvil_serving.connect.config import ManifestError, validate_manifest
from anvil_serving.connect.render import render_for_inspection


def test_reset_link_lifespan_is_validated_and_changes_only_authelia():
    value = json.loads((Path(__file__).parents[2] / "connect/examples/deployment.json").read_text())
    before = render_for_inspection(value)["files"]
    assert "jwt_lifespan:" not in before["authelia/configuration.yml"]
    value["authelia"]["reset_link_lifespan_seconds"] = 3600
    normalized = validate_manifest(value)
    assert validate_manifest(normalized) == normalized
    after = render_for_inspection(value)["files"]
    assert "    jwt_lifespan: '3600 seconds'\n" in after["authelia/configuration.yml"]
    assert {name for name in before if before[name] != after[name]} == {"authelia/configuration.yml", "managed.json"}
    for invalid in (True, 0, -1, 86401, "3600", None, 1.5):
        value["authelia"]["reset_link_lifespan_seconds"] = invalid
        with pytest.raises(ManifestError):
            validate_manifest(value)
