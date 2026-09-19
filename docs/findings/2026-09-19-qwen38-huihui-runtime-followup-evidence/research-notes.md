# Runtime follow-up research notes

**Status:** investigation in progress. These are upstream implementation leads and retained local diagnostics; they are not qualification, performance, or promotion evidence.

## Pinned compatibility boundary

The tested Huihui artifact pins NInfer `a99407c63fc5bbd25d9fb597cbb8ab352bdb01ef` in its model card (published 2026-08-19). The current upstream head observed 2026-09-18 is version-3-only and cannot load the original version-2 artifact without conversion. It is therefore excluded from the compatible-runtime test lane.

Independent source review identifies `70434721b1ae29d0616f3de9b376c8a4d91590b5` (2026-08-28) as retaining version-2 compatibility while fixing tool-contract handling for numeric-shaped strings. The preceding `0e4cdf84f04d74abf6e28b6021b2bd83239d5a1b` (2026-08-27) introduced schema-directed tool-argument typing. Compatible no-spec and MTP3 local recovery evidence is retained; managed restoration is verified.

## Retained local diagnostics

The original pinned runtime's repeated tool artifact records raw `{"zip":98101}` arguments and three failed attempts; the compatible runtime records string `"98101"`. It passes smoke 2/2, off/low core quality 9/9 each, C1 protocol 5/5, and vision 12/12. Its MTP3 8K lane passes core quality 9/9, preflight 7/7, and vision 12/12. Boundary probes retain CRLF-to-LF normalization: off 15/21, low 12/21, generic tool instruction 18/21. The 20-request burst is 17/20 with three HTTP 429 responses. The matched strict 32-word 8K/C1 NInfer cells are a limited local speculation comparison. MTP3 32K is bounded functional evidence only because no no-spec pair exists. These results do not establish high-concurrency admission or separate parser, template, and model causality beyond the recovered argument contract.

## Evidence classification

| Item | Class | Currency | Decision impact |
|---|---|---|---|
| Model card and pinned runtime | official upstream artifact metadata | aging (2026-08-19) | fixes the original artifact/runtime boundary only |
| `0e4cdf84` schema typing | official upstream source change | current (2026-08-27) | compatible repair lead |
| `70434721` contract fix | official upstream source change, independently reviewed for v2 retention | current (2026-08-28) | selected compatible repair lead |
| Current head | official upstream source change | current (2026-09-18) | incompatible with original v2 artifact; excluded |
| Original quality and boundary artifacts | local result | current, incomplete investigation | preserve failure mechanism; no corrected-runtime conclusion |

No external source is promotion-quality evidence. The original dated finding remains historical and is not replaced by this investigation.
