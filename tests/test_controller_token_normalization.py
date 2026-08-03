"""Regression coverage for controller tokens loaded from dotenv files."""

import pytest

from anvil_serving import controller


def test_controller_auth_token_strips_crlf_and_edge_whitespace():
    assert controller.resolve_auth_token("TOKEN", env={"TOKEN": "  value\r"}) == "value"


def test_controller_auth_token_rejects_whitespace_only_when_required():
    with pytest.raises(controller.ControllerError) as exc:
        controller.resolve_auth_token("TOKEN", env={"TOKEN": "\r"}, required=True)

    assert exc.value.code == "auth_token_missing"
