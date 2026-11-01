from __future__ import annotations

import dataclasses
import json

from anvil_serving.a2a.agent_card import build_agent_card
from anvil_serving.a2a.protocol import A2A_VERSION, JSONRPC_METHODS
from anvil_serving.media.workflows import WorkflowRegistry


def _workflows():
    registry = WorkflowRegistry("configs/media/workflows/registry.json")
    records = []
    for public in registry.list():
        descriptor = registry.get(public["id"], public["version"])
        records.append(dataclasses.replace(descriptor, available=True, unavailable_reasons=()))
    return records


def test_agent_card_is_v1_secret_free_and_declares_matching_operations():
    card = build_agent_card(
        _workflows(), public_origin="https://gateway.example.com", server_version="1.2.3"
    )
    assert card["supportedInterfaces"] == [
        {
            "url": "https://gateway.example.com/a2a",
            "protocolBinding": "JSONRPC",
            "protocolVersion": A2A_VERSION,
        }
    ]
    assert card["capabilities"] == {
        "streaming": True,
        "pushNotifications": False,
        "extendedAgentCard": False,
    }
    assert {"SendMessage", "SendStreamingMessage", "GetTask", "CancelTask", "SubscribeToTask"} == JSONRPC_METHODS
    assert card["securitySchemes"]["bearer"]["httpAuthSecurityScheme"]["scheme"] == "Bearer"
    encoded = json.dumps(card, sort_keys=True)
    for forbidden in (
        "100.64.", "127.0.0.1", "Fakoli", "GPU-", "C:\\\\", "/home/", "token", "secret"
    ):
        assert forbidden.lower() not in encoded.lower()
