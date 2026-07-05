"""Realtime-transport adapters for the voice pipeline (anvil task T010+).

Mirrors the reference design's ``connections/`` package (see
``docs/findings/2026-07-04-hf-speech-to-speech-review.md`` s2: "four
transports, one core"): each adapter's only job is filling
``VoicePipeline.audio_in`` and draining ``VoicePipeline.audio_out`` -- the
pipeline itself never imports a transport. Today this package ships exactly
one adapter (:mod:`.local_audio`, a ``sounddevice`` mic/speaker duplex); the
Realtime WebSocket transport lives separately at
``anvil_serving.voice.realtime.ws`` (it predates this package).
"""
