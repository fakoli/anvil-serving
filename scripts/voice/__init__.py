"""Standalone runnable voice-pipeline harness scripts (anvil tasks T007/T009/
T010/T014/T016 -- see the root ``docs/findings/2026-07-04-hf-speech-to-speech-review.md``).

Every script under this package targets REAL hardware this dev sandbox does
not have (an sm_120 GPU running the STT/TTS/LLM serves, a real microphone/
speaker, a running anvil router) and is explicitly marked
``NOT YET EXECUTED`` in its own module docstring -- see each script and
``docs/findings/2026-07-voice-*.md`` for the measurement templates they feed.
The modules retain guarded heavy-dependency imports (``torch``,
``sounddevice``, ``openai``), so a missing optional dependency degrades to a
clear, catchable error at call time rather than crashing at `import`.
"""
