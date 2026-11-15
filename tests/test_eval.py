

from anvil_serving import eval as ev
from contextlib import nullcontext
from types import SimpleNamespace

import pytest


def test_eval_rejects_removed_commands(capsys):
    assert ev.main(["bootstrap"]) == 2
    assert ev.main(["planning"]) == 2
    assert "invalid choice" in capsys.readouterr().err


@pytest.mark.parametrize("served_name", [None, "declared-api-model"])
def test_manifest_eval_uses_api_identity_and_preserves_explicit_override(tmp_path, served_name):
    manifest = tmp_path / "serves.toml"
    manifest.write_text(
        '[[serve]]\nname="chat"\ncontainer="test-chat"\nport=19170\n'
        'runtime="docker"\nmodel="Example/Weights"\n'
        + (f'served_name="{served_name}"\n' if served_name else "")
    )
    base_url, model, selected = ev.resolve_endpoint_target(tier="chat", manifest=str(manifest))
    assert (base_url, model) == ("http://127.0.0.1:19170/v1", served_name or "Example/Weights")
    assert selected["model"] == model
    assert ev.resolve_endpoint_target(tier="chat", manifest=str(manifest), model="explicit-model")[1] == "explicit-model"
    for script in ("preflight.py", "benchmark.py"):
        calls = []
        arguments = SimpleNamespace(tier="chat", manifest=str(manifest), base_url=None, model=None)
        assert ev._run_endpoint_eval(script, arguments, ["--checks", "smoke"],
                                     _call=lambda argv: calls.append(argv) or 0,
                                     _open=lambda *args, **kwargs: nullcontext()) == 0
        assert calls[0][2:] == ["--base-url", base_url, "--model", model, "--checks", "smoke"]
