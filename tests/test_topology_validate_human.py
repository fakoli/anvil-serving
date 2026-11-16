"""Human-mode `topology validate` must show why validation failed."""

from anvil_serving import cli


def test_topology_validate_human_output_lists_each_error(tmp_path, capsys):
    bad = tmp_path / "topology.toml"
    bad.write_text("not = [valid\n", encoding="utf-8")
    rc = cli.main(["topology", "validate", "--topology", str(bad)])
    captured = capsys.readouterr()
    assert rc != 0
    assert "topology validation failed" in captured.err
    assert "Invalid value" in captured.err
