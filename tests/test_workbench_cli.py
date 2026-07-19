from __future__ import annotations

import json

from anvil_serving import workbench


def test_workbench_up_dry_run_uses_packaged_compose_template(capsys):
    assert workbench.main(["up", "--dry-run"]) == 0
    rendered = json.loads(capsys.readouterr().out)
    assert rendered["dry_run"] is True
    assert rendered["command"][:3] == ["docker", "compose", "--project-name"]
    assert rendered["command"][-2:] == ["up", "--detach"]


def test_workbench_missing_compose_file_is_a_clean_usage_error(tmp_path, capsys):
    assert workbench.main(["status", "--compose", str(tmp_path / "missing.yml")]) == 2
    assert "does not exist" in json.loads(capsys.readouterr().err)["error"]
