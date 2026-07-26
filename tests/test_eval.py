import types
import urllib.error

import pytest

from anvil_serving import eval as ev


def test_resolve_endpoint_target_supports_direct_inputs():
    base_url, model, selected = ev.resolve_endpoint_target(
        base_url="http://127.0.0.1:30000/v1", model="llm.primary"
    )
    assert (base_url, model, selected) == ("http://127.0.0.1:30000/v1", "llm.primary", None)


@pytest.mark.parametrize("url", ["http://localhost:30000/v1", "ftp://127.0.0.1:30000/v1"])
def test_resolve_endpoint_target_rejects_unsafe_urls(url):
    with pytest.raises(ValueError):
        ev.resolve_endpoint_target(base_url=url, model="llm.primary")


def test_reachable_accepts_http_error():
    def request(*_args, **_kwargs):
        raise urllib.error.HTTPError("http://127.0.0.1", 503, "busy", {}, None)
    assert ev._reachable(30000, "/health", _open=request)


def test_endpoint_eval_passes_target_to_preflight():
    args = types.SimpleNamespace(tier=None, manifest=None, base_url="http://127.0.0.1:30000/v1", model="llm.primary")
    captured = {}
    rc = ev._run_endpoint_eval("preflight.py", args, ["--dry-run"], _call=lambda argv: captured.setdefault("argv", argv) and 0)
    assert rc == 0
    assert captured["argv"][-5:] == ["--base-url", "http://127.0.0.1:30000/v1", "--model", "llm.primary", "--dry-run"]


def test_eval_rejects_removed_commands(capsys):
    assert ev.main(["bootstrap"]) == 2
    assert ev.main(["planning"]) == 2
    assert "invalid choice" in capsys.readouterr().err
