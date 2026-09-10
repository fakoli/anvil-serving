"""Closed browser-session lifetime declaration and rendering contract."""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from anvil_serving.connect.config import ManifestError, validate_manifest
from anvil_serving.connect.render import render


ROOT = Path(__file__).parents[2]


def declaration() -> dict:
    return json.loads((ROOT / "connect/examples/deployment.json").read_text(encoding="utf-8"))


def test_browser_session_lifetime_is_optional_and_renders_exactly_when_declared() -> None:
    value = declaration()
    del value["gateway"]["browser_session_lifetime_seconds"]
    normalized = validate_manifest(value)
    assert "browser_session_lifetime_seconds" not in normalized["gateway"]
    assert "browser_session_lifetime_seconds" not in json.loads(render(value)["files"]["gateway.json"])

    for seconds in (60, 86400):
        value = declaration()
        value["gateway"]["browser_session_lifetime_seconds"] = seconds
        normalized = validate_manifest(value)
        assert normalized["gateway"]["browser_session_lifetime_seconds"] == seconds
        assert json.loads(render(value)["files"]["gateway.json"])["browser_session_lifetime_seconds"] == seconds


@pytest.mark.parametrize("bad", [0, -1, 59, 86401, True, "60", None])
def test_browser_session_lifetime_rejects_non_integer_or_out_of_range_values(bad: object) -> None:
    value = copy.deepcopy(declaration())
    value["gateway"]["browser_session_lifetime_seconds"] = bad
    with pytest.raises(ManifestError):
        validate_manifest(value)


def test_browser_session_lifetime_gateway_shape_remains_closed() -> None:
    value = declaration()
    value["gateway"]["browser_session_lifetime_seconds"] = 3600
    value["gateway"]["unexpected"] = 1
    with pytest.raises(ManifestError, match="unknown keys"):
        validate_manifest(value)
