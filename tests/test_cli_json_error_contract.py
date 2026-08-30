from __future__ import annotations

import json

from anvil_serving.cli import main


def test_structured_json_error_is_not_repeated_as_a_warning(capsys):
    rc = main(["router", "up", "--json"])
    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert rc == 3
    assert captured.err == ""
    assert payload["error"]["code"] == "confirmation_required"
    assert payload["warnings"] == []
