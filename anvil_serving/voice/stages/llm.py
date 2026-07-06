"""LLM stage: OpenAI Chat Completions (never Responses) via the anvil router
(anvil task T004).

Streams a user turn's reply from ``{base_url}/chat/completions`` using
``urllib`` + the SAME incremental SSE parsing approach as
``anvil_serving/router/backends/sse.py`` (:class:`OpenAIStreamAssembler`),
sentence-batches the streamed text deltas into speakable
:class:`~anvil_serving.voice.messages.LLMChunk` items, strips characters that
read badly to a TTS engine, and sends a thinking-disable directive so a
thinking-by-default local model doesn't burn its token budget reasoning
before ever emitting speech (CLAUDE.md gotcha #6/#9).

Wire contract (non-negotiable):

* NEVER the Responses API -- always POST ``{base_url}/chat/completions``.
  :func:`stream_chat_completion` hardcodes the path suffix; there is no
  parameter that can select ``/responses``.
* ``base_url`` defaults to the anvil router
  (``http://127.0.0.1:8000/v1`` -- 127.0.0.1, never localhost; CLAUDE.md
  gotcha #1).
* The bearer token, if configured, comes from an ENV VAR NAME
  (``api_key_env``), never a literal -- resolved at call time via
  ``os.environ``, mirroring ``voice/config.py``'s secret-hygiene contract.
* ``model`` defaults to the ``"chat-fast"`` anvil intent preset (see
  ``anvil_serving/router/intent.py`` + ``classify.py``): a voice turn is
  latency-sensitive, so it is routed fast-tier-first under the quality gate,
  the same as every other anvil-fronted harness request -- no special-case
  glue needed on the router side.

Stdlib-only: ``json``, ``os``, ``re``, ``urllib.request``/``urllib.error``.
"""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterator, List, Mapping, Optional

from ...router.backends.sse import OpenAIStreamAssembler, iter_sse_events
from ..cancel_scope import CancelScope
from ..messages import EndOfResponse, GenerateRequest, LLMChunk
from .base import BaseStage

DEFAULT_BASE_URL = "http://127.0.0.1:8000/v1"
DEFAULT_MODEL = "chat-fast"  # anvil intent preset: low-latency, fast-tier-first

# Markdown/formatting characters a TTS engine would mispronounce if spoken
# verbatim (emphasis/heading markers, code fences/backticks, table pipes,
# strikethrough) plus a leading bullet marker on its own line.
_TTS_HOSTILE_RE = re.compile(r"[`*_#>|~]|^\s*[-+]\s+", re.MULTILINE)

# Sentence-end punctuation the sentence-batcher splits speakable chunks on.
_SENTENCE_END_RE = re.compile(r"(?<=[.!?])\s+")


def strip_tts_hostile(text: str) -> str:
    """Remove markdown/formatting characters a TTS engine would mispronounce."""
    return _TTS_HOSTILE_RE.sub("", text)


@dataclass
class LLMStageConfig:
    """Endpoint + request-shaping config for :class:`LLMStage`."""

    base_url: str = DEFAULT_BASE_URL
    model: str = DEFAULT_MODEL
    api_key_env: Optional[str] = None
    timeout: float = 20.0
    max_tokens: Optional[int] = None
    temperature: Optional[float] = None
    system_prompt: Optional[str] = None
    # Sent verbatim so a thinking-by-default local model (Qwen3.5, gpt-oss,
    # ...) doesn't burn its max_tokens budget reasoning and return empty
    # content (CLAUDE.md gotcha #6/#9). Both knobs are harmless no-ops on a
    # model/engine that doesn't recognize them.
    disable_thinking_body: Mapping[str, Any] = field(
        default_factory=lambda: {
            "chat_template_kwargs": {"enable_thinking": False},
            "reasoning_effort": "low",
        }
    )
    # Rides as a harmless top-level extension field; both router dialects
    # pass an unknown top-level key straight through into
    # ``InternalRequest.raw`` verbatim, so classify.py's Tier-0 heuristic can
    # read it as a structural low-latency signal -- defense in depth
    # alongside naming the "chat-fast" preset in ``model`` (see
    # anvil_serving/router/classify.py's ``is_voice`` check).
    modality: Optional[str] = "voice"


class LLMClientError(Exception):
    """Raised when the upstream Chat Completions call fails (transport error)."""


def build_request_body(text: str, config: LLMStageConfig) -> Dict[str, Any]:
    """Build the ``/v1/chat/completions`` request body for one user turn."""
    body: Dict[str, Any] = {
        "model": config.model,
        "messages": [],
        "stream": True,
    }
    if config.system_prompt:
        body["messages"].append({"role": "system", "content": config.system_prompt})
    body["messages"].append({"role": "user", "content": text})
    if config.max_tokens is not None:
        body["max_tokens"] = config.max_tokens
    if config.temperature is not None:
        body["temperature"] = config.temperature
    if config.modality:
        body["modality"] = config.modality
    for key, value in config.disable_thinking_body.items():
        body[key] = value
    return body


#: Signature every transport (the real one and test fakes) implements:
#: ``(url, *, data: bytes, headers: dict, timeout: float) -> a file-like
#: response`` (supports ``for line in resp`` and ``.close()``, matching a live
#: ``urllib`` response -- see ``anvil_serving/router/backends/sse.py``'s
#: ``iter_sse_events``). Mirrors ``CloudBackend``'s ``stream_transport`` DI
#: seam (``anvil_serving/router/backends/cloud.py``) so tests can inject a
#: canned in-memory response instead of opening a real socket.
Transport = Callable[..., Any]


def _default_transport(url: str, *, data: bytes, headers: Mapping[str, str], timeout: float):
    """The real transport: POST via ``urllib.request.urlopen``."""
    req = urllib.request.Request(url, data=data, headers=dict(headers), method="POST")
    try:
        return urllib.request.urlopen(req, timeout=timeout)  # noqa: S310 - local/router URL only
    except urllib.error.URLError as exc:
        raise LLMClientError("LLM stage: request to %s failed: %s" % (url, exc)) from exc


def _post_stream(
    url: str, body: Mapping[str, Any], *, api_key_env: Optional[str], timeout: float,
    transport: Transport,
):
    """POST ``body`` to ``url`` via ``transport``; return its response for SSE parsing.

    NEVER touches ``/v1/responses`` -- callers always build ``url`` by
    appending ``/chat/completions`` (see :func:`stream_chat_completion`).
    """
    headers = {"Content-Type": "application/json", "Accept": "text/event-stream"}
    if api_key_env:
        token = (os.environ.get(api_key_env) or "").strip()
        if token:
            headers["Authorization"] = "Bearer %s" % token
    data = json.dumps(body).encode("utf-8")
    return transport(url, data=data, headers=headers, timeout=timeout)


def stream_chat_completion(
    text: str, config: LLMStageConfig, *, transport: Optional[Transport] = None,
) -> Iterator[str]:
    """Yield text deltas from a streaming ``/v1/chat/completions`` call.

    NEVER ``/v1/responses`` -- the URL is built by string-appending
    ``/chat/completions`` to ``config.base_url``, with no code path that can
    select the Responses API. ``transport`` defaults to the real
    ``urllib``-backed client (:func:`_default_transport`); pass a fake for
    hermetic tests (see ``tests/voice/test_llm_stage.py``).
    """
    url = config.base_url.rstrip("/") + "/chat/completions"
    resp = _post_stream(
        url, build_request_body(text, config),
        api_key_env=config.api_key_env, timeout=config.timeout,
        transport=transport or _default_transport,
    )
    assembler = OpenAIStreamAssembler()
    try:
        for event, data in iter_sse_events(resp):
            delta = assembler.feed(event, data)
            if delta:
                yield delta
            if assembler.done:
                break
    finally:
        try:
            resp.close()
        except Exception:  # noqa: BLE001 - best-effort cleanup
            pass


class SentenceBatcher:
    """Accumulates streamed text deltas and yields complete, TTS-cleaned
    sentence chunks as soon as sentence-ending punctuation arrives."""

    def __init__(self) -> None:
        self._buf = ""

    def feed(self, delta: str) -> List[str]:
        """Feed one text delta; return zero or more completed sentences."""
        self._buf += delta
        parts = _SENTENCE_END_RE.split(self._buf)
        if len(parts) <= 1:
            return []
        *complete, remainder = parts
        self._buf = remainder
        return [strip_tts_hostile(p).strip() for p in complete if p.strip()]

    def flush(self) -> Optional[str]:
        """Return any trailing partial sentence once the stream has ended."""
        text = strip_tts_hostile(self._buf).strip()
        self._buf = ""
        return text or None


StreamFn = Callable[[str, LLMStageConfig], Iterator[str]]


class LLMStage(BaseStage):
    """Streams an LLM reply for each :class:`GenerateRequest`, emitting
    sentence-batched :class:`LLMChunk` items followed by an
    :class:`EndOfResponse`.

    ``process`` is a GENERATOR: each :class:`LLMChunk` is yielded the instant
    its sentence-batch is complete, while :func:`stream_chat_completion` is
    still blocked reading the REST of the reply off the wire.
    :class:`~anvil_serving.voice.stages.base.BaseStage` pulls one yielded item
    at a time and puts it on the downstream queue immediately (see
    ``base.py``'s ``_run``) -- so first-audio latency downstream tracks
    first-SENTENCE latency, not full-reply latency. Do NOT collect chunks
    into a list and return it at the end; that would silently defeat the
    incremental contract this stage exists to provide.

    Checks ``cancel_scope.is_stale`` on the incoming request AND on every
    streamed delta: a barge-in landing mid-stream stops emitting further
    chunks for that now-superseded generation (the in-flight HTTP request
    itself is not aborted -- only its OUTPUT stops being forwarded downstream;
    aborting the socket is a possible future refinement, not required for
    THIS stage's own correctness).

    HONESTY NOTE: whatever this stage already emitted for a since-superseded
    generation is still forwarded -- it is a LATER-unit OBLIGATION for
    whatever real stage(s) sit downstream (real TTS / the realtime unit) to
    also check ``cancel_scope.is_stale`` on what they receive and drop it
    there. Today's default downstream wiring (``pipeline.py``'s
    ``EchoTTSStage``/bridge stubs) does NOT do this -- it holds no
    ``cancel_scope`` at all and forwards everything -- so nothing downstream
    of this stage currently drops a stale item on its own. (The real
    ``stages/tts.py::TTSStage`` DOES already check ``is_stale``, but it is not
    yet the stage ``VoicePipeline`` wires by default.)

    Q1 fix (Opus gate): a mid-turn EXCEPTION (e.g. ``self._stream_fn`` raising
    because the upstream connection dropped) still guarantees a terminal
    :class:`EndOfResponse` is emitted before the exception propagates -- a
    client can no longer hang on an unterminated turn just because the
    upstream call failed partway through. This is distinct from the
    barge-in/stale ``return`` path above, which stays deliberately silent (no
    terminal from this stage) because a superseded turn's own terminal is the
    realtime service's job (``RealtimeService._on_response_cancel`` emits a
    ``cancelled`` terminal on the wire for that case) -- only a genuine
    exception reaches the new error-path terminal.
    """

    name = "llm"

    def __init__(
        self,
        in_queue,
        out_queues=None,
        *,
        cancel_scope: Optional[CancelScope] = None,
        config: Optional[LLMStageConfig] = None,
        stream_fn: Optional[StreamFn] = None,
    ) -> None:
        super().__init__(in_queue, out_queues)
        self.cancel_scope = cancel_scope or CancelScope()
        self.config = config or LLMStageConfig()
        # Injectable for tests: defaults to the real streaming HTTP client.
        self._stream_fn: StreamFn = stream_fn or stream_chat_completion

    def process(self, item: Any):
        if not isinstance(item, GenerateRequest):
            return  # empty generator: emits nothing
        if self.cancel_scope.is_stale(item.generation):
            return  # superseded by a barge-in before we even started

        batcher = SentenceBatcher()
        try:
            for delta in self._stream_fn(item.text, self.config):
                if self.cancel_scope.is_stale(item.generation):
                    # A barge-in landed mid-stream: stop emitting further
                    # chunks for this now-stale generation. Whatever was
                    # already YIELDED (and, per BaseStage._run, therefore
                    # already put() on the downstream queue) stays forwarded
                    # -- there's no reason to try to withhold it here.
                    # Dropping it instead of forwarding it is a LATER-unit
                    # obligation: the real downstream stage(s) (real TTS /
                    # the realtime unit) must check cancel_scope.is_stale
                    # themselves and drop it there (see the class docstring's
                    # HONESTY NOTE -- today's default stub wiring does not).
                    #
                    # Deliberately NOT a terminal EndOfResponse here: a
                    # superseded (barge-in'd) turn must stay silent on the
                    # wire -- the realtime service's own cancel path
                    # (`RealtimeService._on_response_cancel`) emits the
                    # cancelled turn's terminal instead (see Q1's docstring
                    # note below). This `return` is UNCHANGED by the Q1 fix.
                    return
                for sentence in batcher.feed(delta):
                    yield LLMChunk(
                        turn_id=item.turn_id,
                        turn_revision=item.turn_revision,
                        generation=item.generation,
                        text=sentence,
                    )
        except Exception:
            # Q1 fix: a mid-turn failure (the streaming HTTP call raising --
            # e.g. the upstream connection dropping mid-reply) used to leave
            # this turn dangling with no terminal EndOfResponse ever reaching
            # the client, since the code path that yields it below is never
            # reached when the loop above raises instead of completing. Emit
            # the terminal here on the way out so a client can never hang on
            # an unterminated turn, then re-raise so BaseStage._run's own
            # per-item exception isolation still logs the failure (this does
            # NOT apply to the barge-in `return` above -- only a genuine
            # exception reaches this branch).
            yield EndOfResponse(
                turn_id=item.turn_id,
                turn_revision=item.turn_revision,
                generation=item.generation,
            )
            raise

        if self.cancel_scope.is_stale(item.generation):
            return

        trailing = batcher.flush()
        if trailing:
            yield LLMChunk(
                turn_id=item.turn_id,
                turn_revision=item.turn_revision,
                generation=item.generation,
                text=trailing,
                is_final=True,
            )
        yield EndOfResponse(
            turn_id=item.turn_id,
            turn_revision=item.turn_revision,
            generation=item.generation,
        )
