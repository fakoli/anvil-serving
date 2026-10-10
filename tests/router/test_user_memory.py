"""User grants are authority, rather than caller-selected identity or attribution."""
import json
from dataclasses import replace

import pytest

from anvil_serving import memory_access
from anvil_serving.router.keys import Principal
from anvil_serving.router.memory import MemoryError, MemoryRouter
from anvil_serving.router.memory_mcp import MemoryMCP
from tests.router.key_fixtures import tmp_path as tmp_path
from tests.router.test_memory import ROUTE, CaptureTransport

USER = "human:" + "a" * 64
OTHER = "human:" + "b" * 64
ADMIN = "human:" + "c" * 64


def test_users_shared_grants_defaults_admin_discovery_and_live_policy(tmp_path):
    policy = tmp_path / "access.json"
    value = {"schema": memory_access.SCHEMA, "users": {
        USER: {"default_bank": "personal-a", "admin": False,
               "banks": {"personal-a": ["retain", "recall", "reflect"], "shared": ["recall"]}},
        OTHER: {"default_bank": "personal-b", "admin": False, "banks": {"personal-b": ["recall"]}},
        ADMIN: {"default_bank": "shared", "admin": True, "banks": {"shared": ["recall"]}},
    }}
    def publish():
        policy.write_text(json.dumps(value)); policy.chmod(0o600)
    publish()
    transport = CaptureTransport(b'{"results":[]}')
    memory = MemoryRouter([replace(ROUTE, principal="connect", bank="connect", access_file=str(policy))],
                          env={"HINDSIGHT_TOKEN": "test-token"}, transport=transport)
    def person(subject):
        return Principal("terminal", ("memory.personal",), ("/v1/memory",), owner=(subject, "1", "0" * 64))
    request = {"alias": "memory.personal", "operation": "recall", "arguments": {"query": "x"}}
    memory.dispatch(request, principal=person(USER))
    assert "/personal-a/" in transport.calls[-1]["url"]
    memory.dispatch(request, principal=person(OTHER))
    assert "/personal-b/" in transport.calls[-1]["url"]
    memory.dispatch({**request, "bank": "shared"}, principal=person(USER))
    for body, principal in (({**request, "bank": "personal-b"}, person(USER)),
                            ({**request, "bank": "shared", "operation": "retain", "arguments": {"content": "x"}}, person(USER)),
                            (request, "terminal"), (request, Principal("terminal", (), ()))):
        before = len(transport.calls)
        with pytest.raises(MemoryError):
            memory.dispatch(body, principal=principal)
        assert len(transport.calls) == before
    discovery = memory.dispatch({**request, "operation": "banks", "arguments": {}}, principal=person(USER))["result"]
    assert discovery["default_bank"] == "personal-a"
    assert {bank["bank_id"] for bank in discovery["banks"]} == {"personal-a", "shared"}
    memory.dispatch({**request, "bank": "personal-b"}, principal=person(ADMIN))
    assert "/personal-b/" in transport.calls[-1]["url"]
    transport.reply = b'{"banks":[{"bank_id":"personal-b"},{"bank_id":"shared"}]}'
    result = memory.dispatch({**request, "operation": "banks", "arguments": {}}, principal=person(ADMIN))
    assert len(result["result"]["banks"]) == 2 and transport.calls[-1]["data"] is None
    mcp = MemoryMCP(memory, person(USER), ("memory.personal",))
    assert "memory_banks" in mcp.tools
    assert mcp._call("memory_banks", {"alias": "memory.personal"})["ok"]
    assert not mcp._call("memory_recall", {"alias": "memory.personal", "bank": "personal-b", "query": "x"})["ok"]
    del value["users"][USER]; publish()
    assert memory.aliases(person(USER)) == ()
    assert not mcp._call("memory_banks", {"alias": "memory.personal"})["ok"]


def test_malformed_or_writable_policy_grants_nothing(tmp_path):
    policy = tmp_path / "access.json"
    for text in ('{"schema":"anvil-memory-access/v1","users":{},"users":{}}', '{}'):
        policy.write_text(text); policy.chmod(0o600)
        with pytest.raises(ValueError): memory_access.read(str(policy))
    policy.write_text('{"schema":"anvil-memory-access/v1","users":{}}'); policy.chmod(0o666)
    with pytest.raises(ValueError): memory_access.read(str(policy))
    policy.chmod(0o644)
    with pytest.raises(ValueError): memory_access.read(str(policy))
