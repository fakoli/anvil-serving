# Voice CLI unit tests read live operator state

## Status

Fixed and regression-tested.

## Symptom

`tests/voice/test_voice_cli.py::test_each_subcommand_validates_and_reports_ok`
failed during the DeepSeek exclusive-mode campaign even though the same test
passes when no exclusive serve is active. The dry-run loaded the user's real
default serve-manifest set, observed the live TP2 owner, and correctly denied
the test STT/TTS targets.

## Root cause

The voice CLI unit module creates temporary voice manifests but did not isolate
`ANVIL_SERVING_HOME`. Managed audio lifecycle resolution therefore fell back to
the real operator home. The test outcome depended on external workstation state
and contradicted the module's foundation-only, no-live-I/O contract.

## Fix

An autouse fixture now points `ANVIL_SERVING_HOME` at a fresh per-test temporary
directory. Tests which need a particular home can still override the variable
explicitly. Production exclusive admission remains unchanged and continues to
deny competing GPU inference while a TP2 owner is active.

## Verification

- Run the previously failing parametrized voice CLI test while exclusive TP2
  remains live.
- Run the complete `tests/voice/test_voice_cli.py` module.
- Run the full repository suite without unloading the exclusive owner.
