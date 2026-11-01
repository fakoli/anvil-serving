"""ADR-0033 router durable state: quiesce intent and the decision JSONL sink."""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from anvil_serving.router.config import ConfigError
from anvil_serving.router.serve import build_server


_CONFIG_TEMPLATE = """\
[server]
{server_keys}

[router]

[[router.tiers]]
id = "primary"
base_url = "http://127.0.0.1:31002/v1"
dialect = "openai"
context_limit = 4096
privacy = "local"
tool_support = true
auth_env = "ANVIL_PRIMARY_KEY"
model = "primary-model"

[router.model_routes]
llm.primary = "primary"
"""


class SlowBackend:
    def __init__(self, tokens=("served",), delay=0.02):
        self.tokens = list(tokens)
        self.delay = delay

    def generate(self, request):
        time.sleep(self.delay)
        yield from self.tokens


def _write_config(tmp_path: Path, **server_keys: str) -> str:
    lines = [f'{key} = "{value}"' for key, value in server_keys.items()]
    path = tmp_path / "router.toml"
    path.write_text(
        _CONFIG_TEMPLATE.format(server_keys="\n".join(lines)), encoding="utf-8"
    )
    return str(path)


def _toml_path(path: Path) -> str:
    return str(path).replace("\\", "/")


def _build(config_path, backend=None):
    return build_server(
        config_path,
        host="127.0.0.1",
        port=0,
        backends={"primary": backend or SlowBackend()},
    )


# --- [server] parsing -------------------------------------------------------


# --- admission intent -------------------------------------------------------


def test_promotion_reason_intent_is_not_restored(tmp_path):
    intent_path = tmp_path / "intent.json"
    config_path = _write_config(
        tmp_path, admission_state_path=_toml_path(intent_path)
    )
    intent_path.write_text(
        json.dumps(
            {"version": 1, "tiers": {"primary": {"state": "quiesced", "reason": "promotion"}}}
        ),
        encoding="utf-8",
    )
    server = _build(config_path)
    try:
        assert server.anvil_admission.snapshot("primary").quiesced is False
    finally:
        server.server_close()
    # The boot write also cleans the stale promotion entry out of the file.
    persisted = json.loads(intent_path.read_text(encoding="utf-8"))
    assert persisted["tiers"] == {}


def test_corrupt_intent_file_refuses_to_serve(tmp_path):
    intent_path = tmp_path / "intent.json"
    config_path = _write_config(
        tmp_path, admission_state_path=_toml_path(intent_path)
    )
    intent_path.write_text("{not json", encoding="utf-8")
    with pytest.raises(ConfigError, match="fix or delete"):
        _build(config_path)


# --- decision JSONL sink ----------------------------------------------------
