# Failures and limits

- Baseline nonstreamed Messages truncation returned HTTP500: unterminated argument string. The initial fixture stopped on that expected defect; the remaining unique stream case was then captured. Candidate returns200 with a partial input object and max_tokens, without executing the synthetic tool.
- Baseline prompt_logprobs128 returned400 at its20 cap; candidate128 passes and129 refuses.
- CI Python3.11 initially failed an unrelated direct-I/O fixture on its filesystem. PR755 fixes the deterministic shell fixture; all13 required checks passed.
- Source host tests do not prove numerical GPU logprob fidelity or guaranteed live tool-guard rejection.
- Legacy client writers are retired and the replacement workflow config is absent. Keep harness convergence pending; no bypass.
