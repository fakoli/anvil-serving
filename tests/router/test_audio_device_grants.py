"""Device keys reach /v1/audio/* only through a route-bound grant (ADR-0048)."""
import json

import pytest

from anvil_serving.router.config import ServerConfig
from anvil_serving.router.keys import KeyStoreError
from tests.router.test_audio_gateway import FakeAudioTransport, gateway
from tests.router.test_usage_dispatch import post, relay, server
from tests.router.test_usage_lifecycle import store as store  # noqa: F401 - fixture
from tests.router.key_fixtures import tmp_path as tmp_path  # noqa: F401 - fixture

STT_PATH = "/v1/audio/transcriptions"
STT_BODY = {"purpose": "stt", "audio_b64": "AQACAA==", "format": "pcm16", "sample_rate": 16000, "is_final": True}


def _call(store, models, paths, path=STT_PATH, body=STT_BODY):
    usage, *_ = store
    keys = usage.key_store
    _, secret = keys.create("scribe", models, paths)
    transport = FakeAudioTransport()
    with server(relay(usage, []), store, audio=gateway(transport),
                server_config=ServerConfig(api_keys_path=str(keys.path))) as conn:
        status, raw = post(conn, path, body, headers={"Authorization": "Bearer " + secret})
    return status, json.loads(raw or b"{}"), transport


def test_route_bound_stt_key_transcribes(store):
    status, payload, transport = _call(store, ["dark-stt"], [STT_PATH])
    assert status == 200 and payload["is_final"] is True
    assert len(transport.calls) == 1


def test_explicit_route_selection_uses_the_same_grant(store):
    status, _, transport = _call(store, ["dark-stt"], [STT_PATH], body={**STT_BODY, "route": "dark-stt"})
    assert status == 200 and len(transport.calls) == 1


def test_other_route_grant_is_denied_before_upstream(store):
    status, payload, transport = _call(store, ["dark-other"], [STT_PATH])
    assert status == 403 and payload["error"]["type"] == "key_access_denied"
    assert transport.calls == []


def test_stt_grant_does_not_grant_speech(store):
    status, _, transport = _call(store, ["dark-tts"], [STT_PATH], path="/v1/audio/speech",
                                 body={"purpose": "tts", "input": "hello", "response_format": "pcm16"})
    assert status == 403 and transport.calls == []


def test_unresolvable_body_is_rejected_without_upstream(store):
    status, _, transport = _call(store, ["dark-stt"], [STT_PATH], body={"audio_b64": "AQACAA=="})
    assert status == 400 and transport.calls == []


def test_audio_paths_are_grantable_but_unknown_paths_are_not(store):
    usage, *_ = store
    with pytest.raises(KeyStoreError):
        usage.key_store.create("bad", ["dark-stt"], ["/v1/audio/translations"])
