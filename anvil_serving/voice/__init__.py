"""anvil-serving voice — the voice-pipeline extension (stdlib-flavored).

An anvil-style stdlib orchestrator + Realtime server that talks to three wires:
an STT serve, a TTS serve, and the anvil router for the brain (OpenAI Chat
Completions, never the Responses API). See
``docs/findings/2026-07-04-hf-speech-to-speech-review.md`` for the design
rationale and ``examples/voice/voice.example.toml`` for a manifest.

This subpackage is deliberately import-light: importing ``anvil_serving.voice``
or ``anvil_serving.voice.config`` pulls in stdlib only (``tomllib``, ``re``,
``urllib.parse``). Heavy audio/ML dependencies for the actual STT/TTS serves
and any in-process audio glue belong behind the ``anvil-serving[voice]``
optional extra (see ``pyproject.toml``) and are never imported here or from
``anvil_serving.router``.
"""
