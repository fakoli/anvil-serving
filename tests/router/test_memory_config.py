"""Memory-route configuration and closed device-key grants."""
from __future__ import annotations

from pathlib import Path

import pytest

from anvil_serving.router.config import ConfigError, load
from anvil_serving.router.keys import KeyStore


_BASE = '''
[router]
[[router.tiers]]
id = "primary"
base_url = "http://127.0.0.1:30000/v1"
model = "primary-model"
dialect = "openai"
context_limit = 4096
privacy = "local"
tool_support = true
auth_env = "ANVIL_PRIMARY_KEY"
[router.model_routes]
llm.primary = "primary"
'''


def _config(tmp_path: Path, memory: str = "", server: bool = True) -> Path:
    path = tmp_path / "router.toml"
    server_text = (
        f'\n[server]\nauth_env = "ROUTER_TOKEN"\napi_keys_path = "{tmp_path / "keys.sqlite3"}"\n'
        if server else ""
    )
    path.write_text(_BASE + memory + server_text, encoding="utf-8")
    return path


_ROUTE = '''
[[router.memory_routes]]
alias = "Memory.Primary"
principal = "phone_1"
backend = "hindsight"
bank = "phone_bank"
base_url = "http://127.0.0.1:4318"
auth_env = "HINDSIGHT_TOKEN"
'''


def test_memory_route_is_device_bound_and_backward_compatible(tmp_path):
    assert load(_config(tmp_path)).memory_routes == ()
    route = load(_config(tmp_path, _ROUTE)).memory_routes[0]
    assert (route.alias, route.principal, route.backend, route.bank, route.timeout) == (
        "memory.primary", "phone_1", "hindsight", "phone_bank", 120.0,
    )


def test_memory_route_rejects_unknown_duplicate_unsafe_and_missing_server_auth(tmp_path):
    with pytest.raises(ConfigError, match="unknown field"):
        load(_config(tmp_path, _ROUTE + 'unknown = "no"\n'))
    with pytest.raises(ConfigError, match="duplicate memory route"):
        load(_config(tmp_path, _ROUTE + _ROUTE.replace('alias = "Memory.Primary"', 'alias = "memory.primary"')))
    with pytest.raises(ConfigError, match="never localhost"):
        load(_config(tmp_path, _ROUTE.replace("127.0.0.1", "localhost")))
    with pytest.raises(ConfigError, match=r"require \[server\]"):
        load(_config(tmp_path, _ROUTE, server=False))


@pytest.mark.parametrize("replacement", [
    ('principal = "phone_1"', 'principal = "_legacy"'),
    ('bank = "phone_bank"', 'bank = "1bank"'),
    ('backend = "hindsight"', 'backend = ["hindsight"]'),
    ('127.0.0.1:4318', '127.0.0.1:4318/v1'),
    ('timeout = 120.0', 'timeout = true'),
    ('timeout = 120.0', 'timeout = 301'),
])
def test_memory_route_rejects_invalid_identity_or_timeout(tmp_path, replacement):
    memory = _ROUTE + "timeout = 120.0\n"
    old, new = replacement
    with pytest.raises(ConfigError):
        load(_config(tmp_path, memory.replace(old, new)))


def test_memory_paths_remain_explicit_key_grants(tmp_path):
    store = KeyStore.initialize(tmp_path / "keys.sqlite3")
    _, secret = store.create("phone", ["memory.primary"], ["/v1/memory", "/v1/memory/mcp"])
    principal = store.authenticate(secret)
    assert principal is not None and principal.allows_model("MEMORY.PRIMARY")
    assert principal.allows_path("POST", "/v1/memory")
    assert principal.allows_path("POST", "/v1/memory/mcp?transport=sse")
    assert not principal.allows_path("GET", "/v1/memory")
    assert not principal.allows_path("POST", "/v1/audio/speech")
