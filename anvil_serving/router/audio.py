"""Normalized request/response audio gateway for ``/v1/audio/*``.

The router's realtime proxy handles a long-lived speech-to-speech loop.  This
module is its deliberately separate, one-shot sibling: it translates the
heterogeneous Dark-owned STT/TTS serve contracts into JSON-only, base64-safe
responses for clients that have only the router base URL and bearer token.

This is not the chat policy pipeline and never falls back to a provider.  A
configured route is the only possible upstream, selected by the caller's
declared purpose or an opaque route id.  The caller never sends an upstream URL
or model name.

Content safety is a primary contract: audio bytes, base64 payloads, transcript
text, and synthesis input never enter :class:`DecisionLog`, stderr, or error
messages.  The decision trail contains only route id, byte counts, outcome,
and elapsed time.
"""

from __future__ import annotations

import base64
import http.client
import io
import json
import os
import socket
import sys
import threading
import time
import urllib.parse
import uuid
import wave
from dataclasses import dataclass
from typing import Any, Callable, Dict, Mapping, Optional, Sequence, Tuple

from .backends.cloud import CloudBackendError, Transport
from .config import AUDIO_STT, AUDIO_TTS, AudioRoute
from .decision_log import AttemptRecord, DecisionLog, DecisionRecord, decision_line

TRANSCRIPTIONS_PATH = "/v1/audio/transcriptions"
SPEECH_PATH = "/v1/audio/speech"
_AUDIO_PATHS = {
    TRANSCRIPTIONS_PATH: AUDIO_STT,
    SPEECH_PATH: AUDIO_TTS,
}

_STT_FORMATS: Mapping[str, Tuple[str, str]] = {
    "wav": ("audio/wav", "audio.wav"),
    "pcm16": ("audio/L16", "audio.pcm"),
    "webm_opus": ("audio/webm", "audio.webm"),
}
_TTS_RESPONSE_FORMATS = {"pcm16"}
_KOKORO_RESPONSE_FORMATS = {"pcm16": "pcm"}
_TTS_CONTENT_TYPES = {
    "pcm16": {"audio/pcm", "audio/l16", "application/octet-stream"},
}


class _AudioDeadlineExceeded(Exception):
    """The total wall-clock deadline expired during an audio upstream hop."""


class _AudioResponseLimitExceeded(Exception):
    """The configured upstream response cap was exceeded."""

    def __init__(self, observed_bytes: int):
        super().__init__("audio upstream response exceeded its configured size limit")
        self.observed_bytes = observed_bytes


@dataclass(frozen=True)
class _AudioResponse:
    """Bounded upstream bytes plus the one header needed for TTS validation."""

    body: bytes
    content_type: str


def _deadline_transport(
    url: str,
    *,
    data: bytes,
    headers: Mapping[str, str],
    timeout: float,
    max_bytes: Optional[int] = None,
) -> _AudioResponse:
    """POST with a true monotonic deadline, no proxy discovery or redirects.

    ``urllib`` follows redirects and only applies a socket-inactivity timeout,
    either of which could move private raw audio outside the configured Dark
    route.  ``http.client`` keeps the exact configured origin, ignores ambient
    HTTP(S)_PROXY variables, and reapplies the remaining deadline before each
    bounded response read.
    """
    parsed = urllib.parse.urlsplit(url)
    path = parsed.path or "/"
    if parsed.query:
        path += "?" + parsed.query
    connection_cls = (
        http.client.HTTPSConnection if parsed.scheme == "https" else http.client.HTTPConnection
    )
    deadline = time.monotonic() + timeout
    connection: Optional[http.client.HTTPConnection] = None

    def remaining() -> float:
        value = deadline - time.monotonic()
        if value <= 0:
            raise _AudioDeadlineExceeded()
        return value

    try:
        connection = connection_cls(parsed.hostname, parsed.port, timeout=remaining())
        connection.connect()
        if connection.sock is not None:
            connection.sock.settimeout(remaining())
        connection.putrequest("POST", path, skip_accept_encoding=True)
        for name, value in headers.items():
            connection.putheader(name, value)
        connection.putheader("Content-Length", str(len(data)))
        connection.endheaders(data)
        if connection.sock is not None:
            connection.sock.settimeout(remaining())
        response = connection.getresponse()
        if not 200 <= response.status < 300:
            raise CloudBackendError("audio upstream returned an unsuccessful HTTP status")
        content_type = response.getheader("Content-Type", "").split(";", 1)[0].lower()
        chunks: list[bytes] = []
        size = 0
        while True:
            if connection.sock is not None:
                connection.sock.settimeout(remaining())
            read_size = min(64 * 1024, (max_bytes - size + 1) if max_bytes is not None else 64 * 1024)
            # read1 performs one buffered/socket read, allowing us to reapply
            # the remaining wall-clock budget between slow-drip chunks.
            chunk = response.read1(max(read_size, 1))
            if not chunk:
                break
            size += len(chunk)
            if max_bytes is not None and size > max_bytes:
                # Never retain the overflowing content, but preserve the safe
                # bounded count read to discover the breach for decision logs.
                raise _AudioResponseLimitExceeded(size)
            chunks.append(chunk)
        remaining()  # fail if EOF arrived just after the deadline
        return _AudioResponse(b"".join(chunks), content_type)
    except _AudioDeadlineExceeded:
        raise
    except _AudioResponseLimitExceeded:
        raise
    except (OSError, http.client.HTTPException) as exc:
        if time.monotonic() >= deadline or isinstance(exc, socket.timeout):
            raise _AudioDeadlineExceeded() from None
        raise CloudBackendError("audio upstream request failed") from None
    finally:
        if connection is not None:
            connection.close()


class AudioGatewayError(Exception):
    """A sanitized caller-facing audio gateway failure."""

    def __init__(self, status: int, etype: str, message: str, *, response_bytes: int = 0):
        super().__init__(message)
        self.status = status
        self.etype = etype
        # A safe, content-free quantity for the decision log.  It is populated
        # when a bounded upstream body was received but then rejected.
        self.response_bytes = response_bytes
        self.message = message


def audio_purpose_for_path(path: str) -> Optional[str]:
    """Return the expected purpose for a normalized audio endpoint."""
    return _AUDIO_PATHS.get(path)


def _multipart_encode(
    fields: Mapping[str, str],
    *,
    filename: str,
    content_type: str,
    audio: bytes,
) -> Tuple[bytes, str]:
    """Build the small multipart envelope expected by the Dark STT serve."""
    boundary = uuid.uuid4().hex
    parts: list[bytes] = []
    for name, value in fields.items():
        parts.append(
            (
                "--%s\r\n"
                "Content-Disposition: form-data; name=\"%s\"\r\n\r\n"
                "%s\r\n" % (boundary, name, value)
            ).encode("utf-8")
        )
    parts.append(
        (
            "--%s\r\n"
            "Content-Disposition: form-data; name=\"file\"; filename=\"%s\"\r\n"
            "Content-Type: %s\r\n\r\n" % (boundary, filename, content_type)
        ).encode("utf-8")
    )
    parts.extend((audio, ("\r\n--%s--\r\n" % boundary).encode("utf-8")))
    return b"".join(parts), boundary


def _decode_audio(value: object, limit: int) -> bytes:
    """Strictly decode an encoded audio field without retaining it in logs."""
    if not isinstance(value, str) or not value:
        raise AudioGatewayError(
            400, "invalid_request_error", "audio_b64 must be a non-empty base64 string"
        )
    # Base64 is at most four characters per three bytes.  Reject before decode
    # so a huge JSON string cannot trigger an oversized temporary allocation.
    max_encoded = ((limit + 2) // 3) * 4 + 4
    if len(value) > max_encoded:
        raise AudioGatewayError(
            413, "payload_too_large", "decoded audio exceeds the configured size limit"
        )
    try:
        decoded = base64.b64decode(value.encode("ascii"), validate=True)
    except (UnicodeEncodeError, ValueError) as exc:
        raise AudioGatewayError(
            400, "invalid_request_error", "audio_b64 must be valid base64"
        ) from exc
    if not decoded:
        raise AudioGatewayError(
            400, "invalid_request_error", "audio_b64 must decode to non-empty audio"
        )
    if len(decoded) > limit:
        raise AudioGatewayError(
            413, "payload_too_large", "decoded audio exceeds the configured size limit"
        )
    return decoded


def _duration_ms(audio: bytes, audio_format: str, sample_rate: Optional[int]) -> Optional[int]:
    """Best-effort duration metadata without inspecting or retaining content."""
    if audio_format == "pcm16" and sample_rate:
        return int(round((len(audio) / 2) * 1000 / sample_rate))
    if audio_format != "wav":
        return None
    try:
        with wave.open(io.BytesIO(audio), "rb") as wav:
            rate = wav.getframerate()
            if rate <= 0:
                return None
            return int(round(wav.getnframes() * 1000 / rate))
    except (EOFError, wave.Error):
        # The STT serve remains the audio-format authority.  A clip it accepts
        # but stdlib wave cannot inspect simply reports an unknown duration.
        return None


def _pcm16_to_wav(pcm: bytes, sample_rate: int) -> bytes:
    """Wrap mono PCM16 in WAV so Parakeet receives its required rate metadata."""
    if len(pcm) % 2:
        raise AudioGatewayError(
            400, "invalid_request_error", "pcm16 audio must contain whole 16-bit samples"
        )
    output = io.BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(pcm)
    return output.getvalue()


class AudioGateway:
    """Resolve configured audio routes and normalize their wire contracts."""

    def __init__(
        self,
        routes: Sequence[AudioRoute],
        *,
        max_input_bytes: int,
        max_output_bytes: int,
        max_text_chars: int,
        max_concurrency: int,
        default_timeout: float = 20.0,
        env: Optional[Mapping[str, str]] = None,
        transport: Optional[Transport] = None,
        decision_log: Optional[DecisionLog] = None,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._transport: Transport = transport or _deadline_transport
        self._max_input_bytes = max_input_bytes
        self._max_output_bytes = max_output_bytes
        self._max_text_chars = max_text_chars
        self._default_timeout = default_timeout
        self._log = decision_log
        self._monotonic = monotonic
        self._limit = threading.BoundedSemaphore(max_concurrency)
        environ: Mapping[str, str] = os.environ if env is None else env
        self._routes: Dict[str, AudioRoute] = {}
        self._tokens: Dict[str, Optional[str]] = {}
        declared_by_purpose: Dict[str, list[AudioRoute]] = {}
        for route in routes:
            declared_by_purpose.setdefault(route.purpose, []).append(route)
            token: Optional[str] = None
            if route.auth_env:
                token = (environ.get(route.auth_env) or "").strip() or None
                if token is None:
                    print(
                        f"[anvil-serving] audio route {route.id!r} not bound: "
                        f"auth_env {route.auth_env!r} is unset/empty in the environment",
                        file=sys.stderr,
                        flush=True,
                    )
                    continue
            self._routes[route.id] = route
            self._tokens[route.id] = token
        self._defaults: Dict[str, AudioRoute] = {}
        for purpose, declared in declared_by_purpose.items():
            selected = declared[0] if len(declared) == 1 else next(
                route for route in declared if route.default
            )
            # A route with an unresolved upstream credential is unavailable;
            # never silently select a different same-purpose route as fallback.
            if selected.id in self._routes:
                self._defaults[purpose] = selected

    def __len__(self) -> int:
        return len(self._routes)

    def has_purpose(self, purpose: str) -> bool:
        """Whether an authenticated request can select a bound route for purpose."""
        return any(route.purpose == purpose for route in self._routes.values())

    @property
    def paths(self) -> Tuple[str, ...]:
        """Configured and bound HTTP paths, for discovery and method handling."""
        return tuple(
            path for path, purpose in _AUDIO_PATHS.items() if self.has_purpose(purpose)
        )

    @property
    def max_request_body_bytes(self) -> int:
        """Maximum JSON body accepted before base64/text materialization.

        The larger of a canonical base64 STT body and a worst-case UTF-8 TTS
        text body is used. A small structural allowance covers JSON keys,
        route/purpose metadata, and quotes without reopening the global 32 MiB
        front-door cap to audio callers.
        """
        encoded_audio = ((self._max_input_bytes + 2) // 3) * 4
        # ``json.dumps`` defaults to ensure_ascii=True.  A non-BMP code point
        # can therefore become a 12-byte surrogate-pair escape on the wire.
        encoded_text = self._max_text_chars * 12
        return max(encoded_audio, encoded_text) + 4096

    def acquire(self) -> bool:
        """Acquire the small audio-only admission pool without blocking."""
        return self._limit.acquire(blocking=False)

    def release(self) -> None:
        self._limit.release()

    def dispatch_transcription(
        self, body: Mapping[str, Any], *, correlation: Optional[Mapping[str, str]] = None
    ) -> Dict[str, Any]:
        """Normalize a JSON/base64 STT request through the configured route."""
        route = self._resolve(AUDIO_STT, body)
        audio = _decode_audio(body.get("audio_b64"), self._max_input_bytes)
        audio_format = body.get("format")
        if not isinstance(audio_format, str) or audio_format not in _STT_FORMATS:
            raise AudioGatewayError(
                400,
                "invalid_request_error",
                "format must be wav, pcm16, or webm_opus",
            )
        is_final = body.get("is_final")
        if not isinstance(is_final, bool):
            raise AudioGatewayError(
                400, "invalid_request_error", "is_final must be a boolean"
            )
        if not is_final:
            raise AudioGatewayError(
                422,
                "unsupported_audio_mode",
                "one-shot transcription accepts final audio only",
            )
        sample_rate = body.get("sample_rate")
        if audio_format == "pcm16":
            if (
                isinstance(sample_rate, bool)
                or not isinstance(sample_rate, int)
                or not (8_000 <= sample_rate <= 192_000)
            ):
                raise AudioGatewayError(
                    400,
                    "invalid_request_error",
                    "sample_rate must be an integer from 8000 through 192000 when format is pcm16",
                )
        elif sample_rate is not None:
            raise AudioGatewayError(
                400,
                "invalid_request_error",
                "sample_rate is valid only when format is pcm16",
            )

        content_type, filename = _STT_FORMATS[audio_format]
        upstream_audio = audio
        if audio_format == "pcm16":
            # Parakeet's probed multipart contract consumes a file and has no
            # separate sample-rate field. Wrap PCM16 in WAV rather than hoping
            # it infers a rate from a generic content type.
            upstream_audio = _pcm16_to_wav(audio, sample_rate)
            content_type, filename = _STT_FORMATS["wav"]
        data, boundary = _multipart_encode(
            {"model": route.model}, filename=filename, content_type=content_type, audio=upstream_audio
        )
        started = self._monotonic()
        request_id = self._request_id(correlation)
        audit_correlation = dict(correlation or {})
        audit_correlation["request_id"] = request_id
        raw = b""
        try:
            raw = self._request(
                route,
                "/audio/transcriptions",
                data,
                {"Content-Type": "multipart/form-data; boundary=%s" % boundary},
                started,
                expected_content_types=("application/json",),
            )
            try:
                payload = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, ValueError) as exc:
                raise AudioGatewayError(
                    502, "upstream_error", "configured STT serve returned malformed JSON"
                ) from exc
            if not isinstance(payload, Mapping) or not isinstance(payload.get("text"), str):
                raise AudioGatewayError(
                    502, "upstream_error", "configured STT serve returned an invalid transcript"
                )
        except AudioGatewayError as exc:
            self._record(
                AUDIO_STT, route, outcome="error", request_bytes=len(audio),
                response_bytes=max(len(raw), exc.response_bytes),
                started=started, correlation=audit_correlation,
            )
            self._stderr_error(AUDIO_STT, route, exc)
            raise

        elapsed = self._elapsed_ms(started)
        self._record(
            AUDIO_STT, route, outcome="served", request_bytes=len(audio),
            response_bytes=len(raw), started=started, correlation=audit_correlation,
        )
        return {
            "text": payload["text"],
            "is_final": True,
            "duration_ms": _duration_ms(audio, audio_format, sample_rate),
            "model": route.model,
            "request_id": request_id,
            "latency_ms": elapsed,
        }

    def dispatch_speech(
        self, body: Mapping[str, Any], *, correlation: Optional[Mapping[str, str]] = None
    ) -> Dict[str, Any]:
        """Normalize the raw PCM output from the configured TTS route."""
        route = self._resolve(AUDIO_TTS, body)
        text = body.get("input")
        if not isinstance(text, str) or not text:
            raise AudioGatewayError(
                400, "invalid_request_error", "input must be a non-empty string"
            )
        if len(text) > self._max_text_chars:
            raise AudioGatewayError(
                413, "payload_too_large", "input exceeds the configured text limit"
            )
        requested_format = body.get("response_format")
        if not isinstance(requested_format, str) or requested_format not in _TTS_RESPONSE_FORMATS:
            raise AudioGatewayError(
                400,
                "invalid_request_error",
                "response_format must be pcm16",
            )

        data = json.dumps(
            {
                "model": route.model,
                "input": text,
                "response_format": _KOKORO_RESPONSE_FORMATS[requested_format],
            }
        ).encode("utf-8")
        started = self._monotonic()
        request_id = self._request_id(correlation)
        audit_correlation = dict(correlation or {})
        audit_correlation["request_id"] = request_id
        raw = b""
        try:
            raw = self._request(
                route,
                "/audio/speech",
                data,
                {"Content-Type": "application/json"},
                started,
                expected_audio_format=requested_format,
            )
        except AudioGatewayError as exc:
            self._record(
                AUDIO_TTS, route, outcome="error", request_bytes=len(data),
                response_bytes=max(len(raw), exc.response_bytes),
                started=started, correlation=audit_correlation,
            )
            self._stderr_error(AUDIO_TTS, route, exc)
            raise

        if not _valid_tts_audio(raw, requested_format):
            error = AudioGatewayError(
                502,
                "upstream_error",
                "configured TTS serve returned invalid audio for the requested format",
            )
            self._record(
                AUDIO_TTS, route, outcome="error", request_bytes=len(data),
                response_bytes=len(raw), started=started, correlation=audit_correlation,
            )
            self._stderr_error(AUDIO_TTS, route, error)
            raise error
        output = raw
        if len(output) > self._max_output_bytes:
            error = AudioGatewayError(
                413, "payload_too_large", "synthesized audio exceeds the configured size limit"
            )
            self._record(
                AUDIO_TTS, route, outcome="error", request_bytes=len(data),
                response_bytes=len(raw), started=started, correlation=audit_correlation,
            )
            self._stderr_error(AUDIO_TTS, route, error)
            raise error
        self._record(
            AUDIO_TTS, route, outcome="served", request_bytes=len(data),
            response_bytes=len(output), started=started, correlation=audit_correlation,
        )
        encoded_output = base64.b64encode(output).decode("ascii")
        return {
            "audio_b64": encoded_output,
            "format": requested_format,
            "sample_rate": route.source_sample_rate,
            "model": route.model,
            "request_id": request_id,
            "latency_ms": self._elapsed_ms(started),
        }

    def _resolve(self, expected_purpose: str, body: Mapping[str, Any]) -> AudioRoute:
        route_id = body.get("route")
        requested_purpose = body.get("purpose")
        if route_id is not None:
            if not isinstance(route_id, str) or not route_id:
                raise AudioGatewayError(
                    400, "invalid_request_error", "route must be a non-empty string"
                )
            route = self._routes.get(route_id)
            if route is None or route.purpose != expected_purpose:
                raise AudioGatewayError(404, "route_not_found", "audio route is not configured")
            if requested_purpose is not None and requested_purpose != expected_purpose:
                raise AudioGatewayError(
                    400, "invalid_request_error", "purpose does not match this audio endpoint"
                )
            return route
        if requested_purpose != expected_purpose:
            raise AudioGatewayError(
                400,
                "invalid_request_error",
                "purpose is required and must match this audio endpoint",
            )
        route = self._defaults.get(expected_purpose)
        if route is None:
            raise AudioGatewayError(404, "route_not_found", "audio route is not configured")
        return route

    def _request(
        self,
        route: AudioRoute,
        suffix: str,
        data: bytes,
        headers: Mapping[str, str],
        started: float,
        expected_audio_format: Optional[str] = None,
        expected_content_types: Optional[Sequence[str]] = None,
    ) -> bytes:
        timeout = route.timeout if route.timeout is not None else self._default_timeout
        outbound_headers = dict(headers)
        if expected_audio_format is not None:
            outbound_headers["Accept"] = {
                "pcm16": "audio/pcm",
            }[expected_audio_format]
        token = self._tokens.get(route.id)
        if token:
            outbound_headers["Authorization"] = "Bearer " + token
        try:
            result = self._transport(
                route.base_url.rstrip("/") + suffix,
                data=data,
                headers=outbound_headers,
                timeout=timeout,
                max_bytes=self._max_output_bytes,
            )
        except _AudioDeadlineExceeded:
            raise AudioGatewayError(
                504, "upstream_timeout", "configured audio serve exceeded its deadline"
            ) from None
        except _AudioResponseLimitExceeded as exc:
            raise AudioGatewayError(
                413,
                "payload_too_large",
                "audio upstream exceeded the configured size limit",
                response_bytes=exc.observed_bytes,
            ) from None
        except CloudBackendError as exc:
            raise AudioGatewayError(
                502, "upstream_error", "configured audio serve failed; see router logs"
            ) from exc
        has_response_metadata = isinstance(result, _AudioResponse)
        if has_response_metadata:
            raw = result.body
            content_type = result.content_type
        elif isinstance(result, bytes):
            # Hermetic injected transports predate the audio-specific transport
            # metadata. Their byte-only result is allowed in tests; production
            # uses _deadline_transport and validates Content-Type below.
            raw = result
            content_type = ""
        else:
            raise AudioGatewayError(
                502, "upstream_error", "configured audio serve returned an invalid response"
            )
        if self._monotonic() - started > timeout:
            raise AudioGatewayError(
                504,
                "upstream_timeout",
                "configured audio serve exceeded its deadline",
                response_bytes=len(raw),
            )
        if expected_audio_format is not None:
            expected_content_types = _TTS_CONTENT_TYPES[expected_audio_format]
        if (
            expected_content_types is not None
            and has_response_metadata
            and content_type not in expected_content_types
        ):
            raise AudioGatewayError(
                502,
                "upstream_error",
                "configured audio serve returned an unexpected media type",
                response_bytes=len(raw),
            )
        if len(raw) > self._max_output_bytes:
            # Custom/injected transports must obey the same cap as the default
            # urllib transport; never base64-encode an oversized response.
            raise AudioGatewayError(
                413,
                "payload_too_large",
                "synthesized audio exceeds the configured size limit",
                response_bytes=len(raw),
            )
        return raw

    @staticmethod
    def _request_id(correlation: Optional[Mapping[str, str]]) -> str:
        requested = (correlation or {}).get("request_id")
        return requested or "aud_" + uuid.uuid4().hex

    def _elapsed_ms(self, started: float) -> int:
        return max(0, int(round((self._monotonic() - started) * 1000)))

    def _record(
        self,
        purpose: str,
        route: AudioRoute,
        *,
        outcome: str,
        request_bytes: int,
        response_bytes: int,
        started: float,
        correlation: Optional[Mapping[str, str]],
    ) -> None:
        served = outcome == "served"
        meta = correlation or {}
        record = DecisionRecord(
            work_class="audio-" + purpose,
            requested_tiers=(route.id,),
            attempts=(
                AttemptRecord(
                    tier_id=route.id,
                    verifier_passed=served,
                    verify_reason="audio gateway" if served else "audio upstream error",
                    prompt_tokens=0,
                    completion_tokens=0,
                    outcome=outcome,
                ),
            ),
            served_tier=route.id if served else None,
            total_prompt_tokens=0,
            total_completion_tokens=0,
            fell_back=False,
            intent=purpose,
            request_id=meta.get("request_id"),
            workbench_run_id=meta.get("workbench_run_id"),
            task_id=meta.get("task_id"),
            request_bytes=request_bytes,
            response_bytes=response_bytes,
            latency_ms=self._elapsed_ms(started),
        )
        if self._log is not None:
            self._log.record(record)
        print("[anvil] decision " + decision_line(record), file=sys.stderr, flush=True)

    @staticmethod
    def _stderr_error(purpose: str, route: AudioRoute, error: AudioGatewayError) -> None:
        # Content-free operator signal.  In particular, never render text,
        # audio_b64, decoded bytes, or the raw upstream URL.
        print(
            "[anvil] %d audio-%s route %r failed: %s"
            % (error.status, purpose, route.id, error.etype),
            file=sys.stderr,
            flush=True,
        )


def _valid_tts_audio(audio: bytes, requested_format: str) -> bool:
    """Validate the only live-qualified TTS output: raw little-endian PCM16."""
    return requested_format == "pcm16" and bool(audio) and len(audio) % 2 == 0
