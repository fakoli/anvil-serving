"""Shared hermetic fakes for the voice test suite.

None of these ever open a real socket or shell out to docker -- they stand
in for `subprocess.run` (``FakeRun``) or an open ``urllib`` response
(``FakeOpenResponse``/``fake_open_ok``/``fake_open_fails``,
``FakeLineResponse``, ``FakeReadResponse``), letting
``test_stt_serve.py``/``test_tts_serve.py``/``test_proxy_serve.py``/
``test_llm_stage.py``/``test_voice_benchmark.py`` exercise real wire-building
code (multipart bodies, SSE assembly, docker-lifecycle probes) without a GPU,
real audio, or network. ``FakeTransport`` is deliberately NOT here: each of
those files' version records/replays a different wire shape (raw multipart
bytes vs. parsed JSON body, a fixed response vs. one rebuilt per call), so
sharing one implementation would risk silently changing what a test proves.
"""
from __future__ import annotations

import io
from types import SimpleNamespace


class FakeRun:
    """Stands in for `subprocess.run`: matches an argv PREFIX against a table
    of canned `(returncode, stdout, stderr)` responses, in order."""

    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def __call__(self, argv, **kwargs):
        self.calls.append(list(argv))
        for prefix, rc, out, err in self.responses:
            if argv[: len(prefix)] == prefix:
                return SimpleNamespace(returncode=rc, stdout=out, stderr=err)
        return SimpleNamespace(returncode=1, stdout="", stderr="no matcher for %r" % (argv,))


class FakeOpenResponse:
    """Stands in for the context manager `urllib.request.urlopen` returns for
    a plain readiness-probe GET (no body needed -- just a status code)."""

    def __init__(self, status: int):
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def getcode(self):
        return self.status


def fake_open_ok(url, timeout=None):
    return FakeOpenResponse(200)


def fake_open_fails(url, timeout=None):
    raise OSError("connection refused")


class FakeLineResponse:
    """Line-iterable fake of an open urllib response (no socket) -- what
    SSE-consuming code (`for raw in fp`) needs."""

    def __init__(self, payload: bytes):
        self._fp = io.BytesIO(payload)
        self.closed = False

    def __iter__(self):
        return iter(self._fp)

    def close(self) -> None:
        self.closed = True


class FakeReadResponse:
    """Fake of a non-streaming response: just `.read()`/`.close()` over one
    in-memory payload (no socket)."""

    def __init__(self, payload: bytes):
        self._fp = io.BytesIO(payload)
        self.closed = False

    def read(self, *a, **kw):
        return self._fp.read(*a, **kw)

    def close(self) -> None:
        self.closed = True
