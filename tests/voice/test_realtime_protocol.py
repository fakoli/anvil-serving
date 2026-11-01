"""Client-event parse table + internal-event -> server-event dispatch table
(``anvil_serving.voice.realtime.events``). Pure dataclass/dict logic --
no sockets, no threads.
"""
from __future__ import annotations


from anvil_serving.voice.realtime.events import (
    ResponseCancel,
    ResponseCreate,
    parse_client_event,
)


# --------------------------------------------------------------------------- #
# parse_client_event: type -> typed dataclass
# --------------------------------------------------------------------------- #


def test_parse_response_create_and_cancel():
    create = parse_client_event({"type": "response.create", "response": {"modalities": ["audio"]}})
    cancel = parse_client_event({"type": "response.cancel"})
    assert isinstance(create, ResponseCreate)
    assert create.response == {"modalities": ["audio"]}
    assert isinstance(cancel, ResponseCancel)


# --------------------------------------------------------------------------- #
# dispatch_internal_event: pipeline message -> server event(s)
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# server_event_to_dict + make_error_event
# --------------------------------------------------------------------------- #
