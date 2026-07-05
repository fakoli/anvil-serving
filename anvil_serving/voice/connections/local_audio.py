"""Local mic/speaker duplex transport via ``sounddevice`` (anvil task T010).

The in-process audio adapter for :class:`~anvil_serving.voice.pipeline.VoicePipeline`
when driven from a real local microphone/speaker pair (as opposed to the
Realtime WebSocket transport in ``anvil_serving.voice.realtime.ws``, which
receives audio over the network instead). Used by
``scripts/voice/local_loop_demo.py`` (T010) and
``scripts/voice/mini_validation.py`` (T016).

IMPORT-GUARDED: ``sounddevice`` (a thin ctypes wrapper around PortAudio) is
imported ONLY inside :meth:`LocalAudioDuplex.__init__`, never at module import
time. That means this module -- and every script that imports it -- stays
importable on a machine with no audio hardware and no ``sounddevice``/PortAudio
installed at all (a CI box, this dev sandbox, ...); only actually
*constructing* a :class:`LocalAudioDuplex` requires the real dependency,
raising the clear :class:`LocalAudioUnavailable` instead of a bare
``ImportError`` bubbling out of module import.

HONESTY NOTE: nothing in this module has been exercised against a real
microphone or speaker -- see CLAUDE.md's "never claim a live capability is
proven" rule and the root README's honesty conventions. The frame-buffering
math (block size <-> ``VADConfig.frame_ms``) is straightforward and believed
correct, but is unverified against real PortAudio callback timing/jitter.

``sounddevice`` is an OPTIONAL dependency, declared under the ``voice`` extra
in ``pyproject.toml`` -- it is never required to import
``anvil_serving.router`` or run the core router/substrate CLI.

Stdlib-only at import time: ``queue``, ``dataclasses``, ``typing``.
"""
from __future__ import annotations

import queue
from dataclasses import dataclass
from typing import Any, Optional


class LocalAudioUnavailable(RuntimeError):
    """Raised when ``sounddevice``/PortAudio can't be used in this environment.

    Covers three distinct causes, all reported the same way since none of
    them are actionable by catching a specific exception type: the
    ``sounddevice`` package isn't installed (``pip install anvil-serving[voice]``
    doesn't pull it by default -- see the module docstring), the PortAudio
    shared library isn't present on this OS, or no audio device is attached
    (a headless box/container).
    """


@dataclass
class LocalAudioConfig:
    """Duplex stream tunables.

    ``frame_ms`` defaults to 20 to match :class:`~anvil_serving.voice.stages.vad.VADConfig`'s
    own default ``frame_ms`` -- the VAD stage's turn-taking state machine is
    tuned assuming its input frames are that duration; a mismatch here would
    silently skew ``silence_ms`` timing.
    """

    sample_rate: int = 16000
    channels: int = 1
    frame_ms: int = 20
    dtype: str = "int16"
    input_device: Optional[Any] = None   # sounddevice device index or name; None = system default
    output_device: Optional[Any] = None  # sounddevice device index or name; None = system default
    input_queue_maxsize: int = 0         # 0 = unbounded (matches queue.Queue's own default)

    @property
    def frame_samples(self) -> int:
        """Samples per channel in one ``frame_ms``-duration block."""
        return max(1, round(self.sample_rate * self.frame_ms / 1000))

    @property
    def frame_bytes(self) -> int:
        """Bytes in one frame (int16 mono/stereo PCM, no container)."""
        bytes_per_sample = 2 if self.dtype == "int16" else 4  # int16 is the only dtype this module uses
        return self.frame_samples * self.channels * bytes_per_sample


class LocalAudioDuplex:
    """Full-duplex mic-in / speaker-out stream, mic frames delivered via a queue.

    Usage (see ``scripts/voice/local_loop_demo.py``)::

        with LocalAudioDuplex(LocalAudioConfig()) as audio:
            frame = audio.read_frame(timeout=0.5)   # -> bytes | None
            audio.play(synthesized_pcm)              # blocks until buffered

    :meth:`read_frame` pulls from an internal queue fed by ``sounddevice``'s
    own callback thread (PortAudio's realtime audio thread, NOT a Python
    ``threading.Thread`` this class spawns) -- callers should call it in a
    loop rather than assuming any particular producer thread identity.
    """

    def __init__(self, config: Optional[LocalAudioConfig] = None) -> None:
        self.config = config or LocalAudioConfig()
        self._frames_in: "queue.Queue[bytes]" = queue.Queue(maxsize=self.config.input_queue_maxsize)
        self._in_stream = None
        self._out_stream = None
        # Import (and therefore any ImportError/OSError) deferred to here --
        # see the module docstring's IMPORT-GUARDED note.
        self._sd = self._import_sounddevice()

    @staticmethod
    def _import_sounddevice() -> Any:
        try:
            import sounddevice as sd  # type: ignore
        except Exception as exc:  # ImportError (not installed) or OSError (no PortAudio lib found)
            raise LocalAudioUnavailable(
                "sounddevice/PortAudio is not usable in this environment (%s). "
                "Install the 'voice' extra (`pip install anvil-serving[voice]`) "
                "and run on a machine with a real audio device -- e.g. fakoli-dark "
                "or a 16GB Mini with a mic/speakers attached." % exc
            ) from exc
        return sd

    # -- sounddevice callback (runs on PortAudio's own audio thread) ---------
    def _on_input(self, indata, frames, time_info, status) -> None:  # noqa: ARG002 - fixed sounddevice signature
        # `status` (an sd.CallbackFlags) reports xruns/overflows; best-effort
        # only -- an occasional dropped/late block should not raise out of a
        # realtime audio callback (PortAudio would abort the stream).
        try:
            self._frames_in.put_nowait(bytes(indata))
        except queue.Full:
            pass  # caller isn't draining fast enough; drop the oldest-pending frame's replacement rather than block

    # -- lifecycle -------------------------------------------------------------
    def start(self) -> None:
        """Open and start both the input and output PortAudio streams."""
        cfg = self.config
        self._in_stream = self._sd.RawInputStream(
            samplerate=cfg.sample_rate,
            channels=cfg.channels,
            dtype=cfg.dtype,
            blocksize=cfg.frame_samples,
            device=cfg.input_device,
            callback=self._on_input,
        )
        self._out_stream = self._sd.RawOutputStream(
            samplerate=cfg.sample_rate,
            channels=cfg.channels,
            dtype=cfg.dtype,
            device=cfg.output_device,
        )
        self._in_stream.start()
        self._out_stream.start()

    def stop(self) -> None:
        """Stop and close both streams (best-effort; safe to call more than once)."""
        for stream in (self._in_stream, self._out_stream):
            if stream is None:
                continue
            try:
                stream.stop()
                stream.close()
            except Exception:  # noqa: BLE001 - teardown must never raise over an already-broken stream
                pass
        self._in_stream = None
        self._out_stream = None

    def __enter__(self) -> "LocalAudioDuplex":
        self.start()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.stop()

    # -- data path --------------------------------------------------------------
    def read_frame(self, timeout: Optional[float] = None) -> Optional[bytes]:
        """Return the next mic frame (raw PCM, ``config.frame_bytes`` long), or
        ``None`` if none arrived within ``timeout`` seconds."""
        try:
            return self._frames_in.get(timeout=timeout)
        except queue.Empty:
            return None

    def play(self, pcm: bytes) -> None:
        """Write synthesized PCM to the output stream (blocks until buffered)."""
        if self._out_stream is None:
            raise LocalAudioUnavailable("play() called before start() (or after stop())")
        self._out_stream.write(pcm)

    def clear_pending_input(self) -> int:
        """Drop every currently-buffered mic frame; returns the count dropped.

        Useful right after a barge-in cancel: discard stale mic audio that
        queued up while the previous turn's response was playing, so the
        next read isn't backlogged with frames from before the interruption.
        """
        dropped = 0
        while True:
            try:
                self._frames_in.get_nowait()
                dropped += 1
            except queue.Empty:
                return dropped
