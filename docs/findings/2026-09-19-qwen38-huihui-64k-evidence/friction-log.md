# Friction and dispositions

- Default preflight requested128K and a20-request burst. The server rejected out-of-window input and excess queued requests. Retained in `preflight-default-128k-failure.json`; corrected60000 nominal needle/C1 passed.
- Initial resource estimate mislabeled the32K runtime allocation as pure KV; a later draft double-counted it. Both planning drafts are rejected. Retained observed whole-GPU use is24224MiB; no scaling or transient-peak claim.
- Router model identity changed before its quoted context fields. Exact parsed TOML assertions corrected context/KV to65536..
- A saved alternate Hermes custom provider used a stale credential and fell back to cloud. Those attempts failed identity acceptance. The configured native `anvil` provider passed all9 checks with `llm.secondary`; no cloud run is credited.
- Compatibility testing found stale observer and renderer metadata. A cross-module regression check covers consistent served identity and the unsupported-native-metrics sentinel.
- Interactive browser sign-in remains unverified.
