"""ADR-0033 router durable state: quiesce intent and the decision JSONL sink."""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from anvil_serving.router.config import ConfigError, load_server_config
from anvil_serving.router.decision_log import (
    DecisionLog,
    DecisionLogWriter,
    DecisionRecord,
)
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


def test_server_table_rejects_unknown_keys(tmp_path):
    path = tmp_path / "router.toml"
    path.write_text(
        _CONFIG_TEMPLATE.format(server_keys='admision_state_path = "typo.json"'),
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="unknown field"):
        load_server_config(str(path))


def test_server_table_parses_durability_paths(tmp_path):
    config_path = _write_config(
        tmp_path,
        admission_state_path=_toml_path(tmp_path / "intent.json"),
        decision_log_path=_toml_path(tmp_path / "decisions.jsonl"),
    )
    server_config = load_server_config(config_path)
    assert server_config.admission_state_path.endswith("intent.json")
    assert server_config.decision_log_path.endswith("decisions.jsonl")
    assert server_config.auth_env is None


def test_server_table_rejects_empty_paths(tmp_path):
    path = tmp_path / "router.toml"
    path.write_text(
        _CONFIG_TEMPLATE.format(server_keys='decision_log_path = ""'),
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="non-empty"):
        load_server_config(str(path))


# --- admission intent -------------------------------------------------------


def test_quiesce_intent_survives_rebuild(tmp_path):
    intent_path = tmp_path / "intent.json"
    config_path = _write_config(
        tmp_path, admission_state_path=_toml_path(intent_path)
    )

    server = _build(config_path)
    try:
        server.anvil_routing.quiesce_tier("primary", "eviction")
    finally:
        server.server_close()
    persisted = json.loads(intent_path.read_text(encoding="utf-8"))
    assert persisted["tiers"]["primary"] == {"state": "quiesced", "reason": "eviction"}

    rebuilt = _build(config_path)
    try:
        snapshot = rebuilt.anvil_admission.snapshot("primary")
        assert snapshot.quiesced is True
        assert snapshot.reason == "eviction"
    finally:
        rebuilt.server_close()


def test_readmit_clears_persisted_intent(tmp_path):
    intent_path = tmp_path / "intent.json"
    config_path = _write_config(
        tmp_path, admission_state_path=_toml_path(intent_path)
    )
    server = _build(config_path)
    try:
        server.anvil_routing.quiesce_tier("primary", "eviction")
        server.anvil_admission.readmit("primary")
    finally:
        server.server_close()
    persisted = json.loads(intent_path.read_text(encoding="utf-8"))
    assert persisted["tiers"] == {}

    rebuilt = _build(config_path)
    try:
        assert rebuilt.anvil_admission.snapshot("primary").quiesced is False
    finally:
        rebuilt.server_close()


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


def test_unknown_tier_intent_warns_and_boots(tmp_path, capsys):
    intent_path = tmp_path / "intent.json"
    config_path = _write_config(
        tmp_path, admission_state_path=_toml_path(intent_path)
    )
    intent_path.write_text(
        json.dumps(
            {"version": 1, "tiers": {"retired": {"state": "quiesced", "reason": "eviction"}}}
        ),
        encoding="utf-8",
    )
    server = _build(config_path)
    server.server_close()
    assert "unknown tier 'retired'" in capsys.readouterr().err


def test_corrupt_intent_file_refuses_to_serve(tmp_path):
    intent_path = tmp_path / "intent.json"
    config_path = _write_config(
        tmp_path, admission_state_path=_toml_path(intent_path)
    )
    intent_path.write_text("{not json", encoding="utf-8")
    with pytest.raises(ConfigError, match="fix or delete"):
        _build(config_path)


def test_no_admission_state_path_means_no_file(tmp_path):
    config_path = _write_config(tmp_path)
    server = _build(config_path)
    try:
        server.anvil_routing.quiesce_tier("primary", "eviction")
    finally:
        server.server_close()
    assert list(tmp_path.glob("*.json")) == []


# --- decision JSONL sink ----------------------------------------------------


def test_decision_log_stamps_unix_ts():
    log = DecisionLog()
    log.record(
        DecisionRecord(
            kind="chat",
            requested_tier="primary",
            attempts=(),
            served_tier="primary",
            total_prompt_tokens=1,
            total_completion_tokens=1,
        )
    )
    assert log.last.unix_ts > 0


def test_decision_sink_writes_metadata_only_jsonl(tmp_path):
    sink_path = tmp_path / "decisions.jsonl"
    config_path = _write_config(
        tmp_path, decision_log_path=_toml_path(sink_path)
    )
    server = _build(config_path)
    try:
        from anvil_serving.router.internal import InternalRequest, Message

        request = InternalRequest(
            model="llm.primary",
            messages=[Message(role="user", content="secret-prompt-text")],
            raw={},
        )
        list(server.anvil_routing.generate(request))
    finally:
        server.server_close()

    lines = sink_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    payload = json.loads(lines[0])
    assert payload["route"] == "llm.primary"
    assert payload["served_tier"] == "primary"
    assert payload["unix_ts"] > 0
    assert payload["latency_ms"] > 0  # SlowBackend sleeps 20ms
    assert "secret-prompt-text" not in lines[0]


def test_decision_writer_rotates_once(tmp_path):
    path = tmp_path / "decisions.jsonl"
    writer = DecisionLogWriter(str(path), max_bytes=1024)
    record = DecisionRecord(
        kind="chat",
        requested_tier="primary",
        attempts=(),
        served_tier="primary",
        total_prompt_tokens=1,
        total_completion_tokens=1,
        unix_ts=1.0,
    )
    for _ in range(64):
        writer(record)
    assert path.exists()
    assert (tmp_path / "decisions.jsonl.1").exists()


def test_decision_writer_unwritable_directory_is_boot_error(tmp_path):
    config_path = _write_config(
        tmp_path,
        decision_log_path=_toml_path(tmp_path / "absent-dir" / "decisions.jsonl"),
    )
    with pytest.raises(ConfigError, match="not writable"):
        _build(config_path)
