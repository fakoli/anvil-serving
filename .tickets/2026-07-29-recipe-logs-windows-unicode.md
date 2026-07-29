# Recipe logs must survive Unicode output on Windows

## Problem

`models recipes logs` decoded managed container output as UTF-8, then wrote it
through the host's default `cp1252` console stream. vLLM's Unicode banner
contains block characters such as `█`, so the managed log command raised
`UnicodeEncodeError` before it could return bounded startup evidence.

## Resolution

- Route managed recipe stdout and stderr through a console-safe writer.
- Preserve output unchanged when the active stream supports it.
- Use deterministic backslash escapes only when the stream codec cannot encode
  a character, retaining the code point without crashing.
- Add a regression test using a real `cp1252` text wrapper and the vLLM banner
  character.

## Verification

Pending focused and full repository gates after the live qualification.
