"""Exercise privacy through the public CLI JSON envelope, not module helpers."""
import json

import pytest

from anvil_serving import cli


@pytest.mark.parametrize("action", ["validate", "render"])
@pytest.mark.parametrize("operand", [
    '{"api_key":"PRIVATE_SECRET_VALUE"}', "PRIVATE_SECRET_VALUE.json",
])
@pytest.mark.parametrize("inline", [False, True])
def test_manifest_operand_is_not_echoed_in_json_envelope(action, operand, inline, capsys):
    flags = ["--manifest=" + operand] if inline else ["--manifest", operand]
    assert cli.main(["--json", "edge", "bundle", action, *flags]) == 2
    captured = capsys.readouterr()
    assert "PRIVATE_SECRET_VALUE" not in captured.out + captured.err
    assert json.loads(captured.out)["command"] == "edge bundle " + action


def test_rejected_diagnostic_origin_is_not_echoed_in_json_envelope(capsys):
    assert cli.main([
        "--json", "router", "diagnose", "--request-id", "request-1",
        "--router-url", "https://user:PRIVATE_SECRET_VALUE@example.invalid",
    ]) == 2
    captured = capsys.readouterr()
    assert "PRIVATE_SECRET_VALUE" not in captured.out + captured.err
    assert json.loads(captured.out)["command"] == "router diagnose"
