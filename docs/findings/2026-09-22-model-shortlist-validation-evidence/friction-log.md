# Friction log

| Item | Evidence | Disposition |
|---|---|---|
| Deterministic debug-loop fixture can return a later scripted result after an extra tool call. | [agentic artifacts](qwen-agentic-scout-native.json) and [ticket](https://github.com/fakoli/anvil-serving/blob/codex/mimo-v26-qualification/.tickets/2026-09-22-agentic-debug-fixture-drift.md) | Open product-fixture defect; add an extra-call regression before using this scenario as a strict model gate. |
| Qwen agentic outcomes are internally above the suite's 0.75 floor but below the campaign's 1.0 gate. | [run plan](run-plan.md), [greedy scout](qwen-agentic-scout-native.json), [sampling scout](qwen-agentic-native-sampling-native.json) | Preserve both thresholds; do not relabel the native artifacts as campaign passes. |
| Ordered planning terms can fail when an incidental later-term substring appears in an earlier section. | [paired GLM scout](glm-agentic-scout-native.json) and [ticket](https://github.com/fakoli/anvil-serving/blob/codex/mimo-v26-qualification/.tickets/2026-09-22-agentic-debug-fixture-drift.md) | Open scorer defect; add positive and negative heading-order regressions before comparative agentic ranking. |
