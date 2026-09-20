# Independent source and diagnostics review

Date: 2026-09-19

The implementation and campaign lead used GPT-6 Astra. This review used
GPT-5.6 Sol with high reasoning. The evaluated model was
`lyf/Qwen3.8-27B-Huihui-Abliterated-NInfer-NVFP4`, so the reviewer was
independent from both the author and evaluated model. The review used only
repository and retained-artifact inspection. It did not call a model endpoint,
change serve lifecycle state, or authorize promotion.

## Findings

1. **The bounded raw-argument diagnostics are accepted.**
   `tool_argument_observations` retains at most four calls, 2,048 argument
   characters per call, and 128 characters of function name. It inspects at
   most four messages and sixteen call entries per message, including malformed
   entries. Non-string argument values retain only their wire type and are not
   serialized. Both the built-in and external-suite runner paths add this
   evidence after the existing validators, so the diagnostics do not turn a
   failed tool call into a pass. The regression tests cover invalid JSON,
   built-in/external parity, CRLF and command/path metacharacter retention,
   output truncation, non-string non-serialization, and generator-backed scan
   limits. The campaign lead reported 173 focused tests passing; this reviewer
   inspected the changed tests but did not rerun them under the read-only review
   scope.

2. **NInfer revision `70434721b1ae29d0616f3de9b376c8a4d91590b5`
   does not normalize embedded CRLF in declared string arguments.** In
   `src/targets/qwen3_6/impl/frontend/tool_call_parser.cpp`, lines 143-156 remove
   only one leading and trailing XML framing newline. Lines 171-185 assign the
   remaining declared-string bytes directly to the JSON value. The decoder and
   tokenizer paths in `frontend.cpp` and `tokenizer.cpp` pass decoded vocabulary
   bytes through without line-ending conversion. The chat template uses LF for
   the XML framing, not for rewriting generated parameter content.

3. **The observed CRLF failure is an exact-output behavior, not evidence of a
   parser defect.** With the same schema-fixed runtime, the thinking-off run
   emitted literal `\\r\\n`, while the low-reasoning and generic-instruction
   runs emitted LF. The instruction variant passed 18 of 21 attempts, with only
   the three CRLF attempts failing. This variation strongly supports model
   generation or instruction adherence as the cause. Because the artifacts do
   not retain pre-parser XML or generated token IDs, that final causal step
   remains plausible rather than forensically confirmed.

4. **Global newline normalization is not an appropriate runtime fix.** It would
   violate the parser's declared-string byte-preservation contract. A tool field
   intended as semantic text may explicitly define LF-normalized comparison in
   its own application contract. A field requiring exact CRLF bytes should use a
   lossless representation such as base64, code points, or lines with an
   explicit separator, and the exact-byte capability should remain failed until
   that contract is proven.

## Breakage probes

| Axis | Concrete input or state | Result |
|---|---|---|
| Fail closed | Invalid JSON and non-string tool arguments on built-in and external suites | Refuted as a defect: validators still fail and diagnostics do not affect grading. |
| Malformed and boundary input | CRLF plus command/path metacharacters in raw argument strings | Refuted as a diagnostics defect: excerpts preserve the exact Python string and do not interpret it. |
| Resource exhaustion | More than four messages and more than sixteen malformed calls per message | Refuted after correction: `islice` bounds both scan dimensions; the generator regression raises if either bound is crossed. |
| State drift | Built-in tool suite versus external suite-file path | Refuted within the benchmark runner: both paths use the same helper after validation. No live MCP, hook, or config-reload parity claim is made. |

## Disposition

The diagnostics change is accepted for its stated bounded evidence purpose.
Raw excerpts may contain generated user data, so public publication still
requires the repository's normal sanitization and secret scan.

The final retained numeric claims reconcile to the native artifacts:

- The matched 8K/C1 cells use seed 43, unique prompts, strict 32-word output,
  request canaries, twelve eligible requests, and identical 1,756-1,762 actual
  prompt-token ranges. No-spec records 71.3877 mean decode tok/s and
  908.478 ms mean E2E; MTP3 records 176.1983 tok/s and 523.992 ms; the GGUF
  cross-profile control records 107.6522 tok/s and 2,571.788 ms.
- The NInfer no-spec/MTP3 pair isolates the speculation setting within the
  same pinned model artifact, runtime revision, 8K context, C1, INT8 KV, and
  workload. The GGUF comparison changes checkpoint, runtime, quantization, and
  KV format; it is correctly limited to a complete-profile comparison. The
  native capacity artifacts leave `source_recipe` and configuration fingerprint
  empty, so their exact launch binding depends on the configuration snapshots
  and managed private logs. That residual identity boundary is acceptable for
  this no-promotion result but is insufficient for deployment promotion.
- Post-workload whole-GPU readings are 21,852 MiB for Huihui MTP3 and
  19,022 MiB for GGUF, a 2,830 MiB or 14.8775% increase. These readings include
  desktop and driver allocation and are neither peaks nor process RSS. The
  requested footprint improvement is therefore not met.
- At 32K, MTP3 passes preflight 7/7, core quality 9/9, session recall 3/3, one
  context-summary probe with 23,252 actual prompt tokens, and strict 32-word
  capacity 6/6 with 22,418-22,427 actual prompt tokens. With no no-spec 32K
  pair, this establishes bounded long-input operation but does not establish
  whether the isolated 8K speculation gain extends.
- Every retained strict 128-word warmup is ineligible: no-spec off 0/2,
  no-spec low 0/2, MTP3 0/2, and GGUF 0/2. No performance claim uses those
  failed requests. Under the generic tool instruction, no-spec NInfer, MTP3
  NInfer, and GGUF each pass 18/21 boundary attempts; exact CRLF remains 0/3
  in each profile.

Restoration is independently supported by private before/after receipts. Media
worker and media MCP are running with HTTP 200, `qwen-local` is absent, managed
recipe inventory contains only the same stopped/exited incumbent, split mode is
restored, and whole-GPU use returned from 686 MiB to 686 MiB. The three current
operator configuration hashes equal the published before/after hashes. No
public evidence file inspected in this review contains an operator home path,
real GPU UUID, email address, tailnet name, or non-loopback local-service
endpoint URL.

The bounded campaign is accepted as failure-aware evidence for a working
candidate. It does not support the requested promotion because the footprint
criterion failed, the runtime build is not immutably reproduced, and broader
application, endurance, telemetry, routed-client, and deployment acceptance
remain unrun. Recommendation: `do_not_promote`; human promotion gate required;
`promoted=false`.
