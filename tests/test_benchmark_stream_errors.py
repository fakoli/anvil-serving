"""A server-side SSE error must not look like an empty model response."""

import urllib.request

import pytest

from anvil_serving.benchmarking.requests import stream_chat


def test_capacity_stream_surfaces_server_error_without_observer(monkeypatch):
    payload = (
        b'data: {"error":{"code":503,"message":"request queue timeout"}}\n\n'
        b'data: [DONE]\n\n'
    )

    class Stream:
        def __enter__(self):
            return iter(payload.splitlines(keepends=True))

        def __exit__(self, *_args):
            return False

    monkeypatch.setattr(urllib.request, "urlopen", lambda *_args, **_kwargs: Stream())
    with pytest.raises(ValueError, match="upstream sent an SSE error"):
        stream_chat("http://127.0.0.1:30002/v1", "model", "prompt", None, 16)
