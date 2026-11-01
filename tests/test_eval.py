

from anvil_serving import eval as ev


def test_eval_rejects_removed_commands(capsys):
    assert ev.main(["bootstrap"]) == 2
    assert ev.main(["planning"]) == 2
    assert "invalid choice" in capsys.readouterr().err
