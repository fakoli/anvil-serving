# Agents-A1 deterministic multimodal corpus

The deterministic gate contains synthetic media generated locally from color
fields, geometric primitives, and rendered text; those files contain no
personal, licensed, or downloaded media and are released under CC0-1.0. The
separate `cc/` supplementary lane retains downloaded Creative Commons media
with its own attribution and license record.

`generate.py` is a fixture generator, not an operational serving path. It uses
the campaign's pinned vLLM image because that image already contains FFmpeg:

```powershell
python benchmarks/corpora/agents-a1-v1/generate.py
```

The generator removes metadata, requests bit-exact output, fixes the video
frame rate at one frame per second, and writes only beneath this directory.
`corpus.json` pins the SHA-256 digest of every generated file. Regeneration must
produce the same hashes before the corpus is used as qualification evidence.

The fixtures cover scene understanding, OCR, chart/table extraction, UI state,
spatial counting, multi-image comparison, temporal order, state change, event
localization, video OCR, long continuity, and mixed image/video requests.
