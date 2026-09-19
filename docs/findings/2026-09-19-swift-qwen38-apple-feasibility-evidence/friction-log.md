# Friction and fix-forward record

| ID | Observation / earliest evidence | Immediate disposition | Durable follow-up | Independent verification / status |
|---|---|---|---|---|
| F1 | models cache inventory/pull used Docker only at campaign base | No Docker cache substituted for native HF storage | This change adds native read-only inventory and exact-revision removal preview; native apply/download remain unsupported | Real local HF cache plus temporary-layout regression tests; implementation review pending final gates |
| F2 | Initial native inventory treated absent refs as invalid, file sizes as allocated bytes | Retain first private attempt; do not use its byte fields for cleanup | Correct optional refs, logical/allocated distinction, local-link integrity vs upstream completeness in same change | New tests and repeat local inspection required before closure |
| F3 | Native runtime validator rejects llama-cpp; memory_mib is admission only | No load, no launchd change, no weights pull | Public ticket 2026-09-19-apple-native-artifact-and-memory-containment | Independent source/validator review confirms scoped product gap; open |
| F4 | All selected GGUFs exceed disk policy even before temporary allocation | No deletion or download; unchanged 10GiB free reserve | Same native exact-artifact/ownership ticket; retained disk-feasibility-v1.json | Exact sizes and arithmetic independently reviewable; disk blocker open |
| F5 | Installed CLI differs from campaign source; host memory verb rejects macOS | Use verified worktree python module and host status unified-memory snapshot | Commands/version/source retained; no install/service migration | Launcher 1.2.1 resolved to selected checkout; host status succeeded |
| F6 | Shared research-synthesis skill not available in active skill catalog | Do not claim full plugin method | Three bounded independent roles supplied artifact research, code audit and feasibility critique; optional shared-helper integration deferred | Missing helper explicitly recorded; does not weaken gates |

Configuration search disposition: no candidate configuration has been run. Storage cleanup alone does not supply missing loader containment. No plausible safe managed trial is established by replacing the runtime name, lowering the reserve, advisory MLX memory limits, or polling after startup. This is an unresolved managed-runtime/safety barrier, not evidence that the model or macOS is intrinsically incapable.

Final cache inspection recognizes exact-revision caches without refs and distinguishes logical bytes, filesystem allocation, local link integrity, and unverified upstream completeness. The initial failed/partial inventories are retained privately; final inventory does not prove cache inactivity or guaranteed APFS reclamation.
