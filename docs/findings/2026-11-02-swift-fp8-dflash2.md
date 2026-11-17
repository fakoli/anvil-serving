# Swift FP8 + DFlash2 — faster scout, failed promotion gate

The bounded trial found faster generation with DFlash2, but **neither FP8 arm
passed the frozen strict-format gate**. Both passed 0/6 exact 128-word/prefix
requests. The candidate was not promoted; the pretrial service was restored
and passed bounded functional restoration checks. This is a closed trial
with incomplete comparison coverage, not a fully qualified replacement.

## Configuration and scope

One RTX PRO 6000 Blackwell Max-Q ran TP1. The other GPU was protected and was
never reserved by the trial. The target was
`realderpz/Swift-1.5-Qwen3.8-27B-FP8` at
`64e5388d1d4ebc1941cc17d41e96573a0f9a5dab`; the draft was
`incoai/Qwen3.8-27B-DFlash2` at
`015e795645c74b1a0eeef3b570031fb62e769bc5`. SGLang 0.5.22 used pinned upstream
platform digest `5fc09edb6a0bb1d5b6992b543723c66d08f86d7a507e951e87e0d05db4ac766e`
through an unchanged-layer local image.

Both arms used FP8 block weights, BF16 compute, FP8 E4M3 KV, FP32 recurrent
state, FlashInfer, 2,048-token chunked prefill, static memory fraction 0.90,
maximum eight running requests and configured context 262,144. DFlash used
block eight and draft window 2,048. The control removed speculative flags.
Requests selected medium reasoning, temperature 1 and top-p 0.95. No host KV
offload was used. [Frozen plan](2026-11-02-swift-fp8-dflash2-evidence/raw/plans/run-plan.json) and
[recipe](2026-11-02-swift-fp8-dflash2-evidence/raw/registry-r1.toml) retain the exact controls.

## Matched short-response scout

Each cell used unique prompts and ordinary short summaries, with 3/6/12
requests at C1/C2/C4. Actual prompt lengths were approximately 7.4K and 30.4K.
Reported values are arithmetic medians of request observations; the native
nearest-rank p50 differs for even populations. Generated output lengths differ,
and concurrent prefill interferes with decode timing. These are descriptive
short-output results, not controlled long-decode throughput or robust tail estimates.

| Nominal context | Concurrency | DFlash tok/s per request | No-spec tok/s per request | DFlash / no-spec E2E seconds |
|---|---:|---:|---:|---:|
| 8,192 | 1 | 153.72 | excluded | excluded comparison |
| 8,192 | 2 | 77.45 | 33.14 | 2.89 / 4.77 |
| 8,192 | 4 | 34.57 | 23.02 | 5.35 / 6.77 |
| 32,768 | 1 | 159.04 | 46.41 | 6.22 / 8.09 |
| 32,768 | 2 | 21.67 | 16.25 | 11.92 / 13.85 |
| 32,768 | 4 | 9.51 | 8.19 | 23.20 / 24.80 |

[Comparison data and native artifact hashes](2026-11-02-swift-fp8-dflash2-evidence/raw/capacity-comparison.json).
DFlash completed 42/42 requests across six telemetry-qualified cells. No-spec
also completed 42/42, but only 39 requests across five cells met telemetry
coverage: its first 8K/C1 cell lacked an initial streaming counter series and
remains excluded. The reviewed resume ran only previously unrun cells.

DFlash improved generation across all five eligible pairs; first-output latency
was similar. C2 offers a better interactive balance than C4. At 32K/C2, median
first output was 5.52 versus 5.44 seconds. This finding does not authorize that
candidate as a production default.

## Quality and context

The FP8 + DFlash arm passed full preflight 10/10, deterministic agentic checks
18/18, context checks 36/36, image checks 14/14, and corrected repeated/unique
stream-overlap checks 3/3 each. The five-task coding pilot resolved 4/5, matching
the historical BF16 pilot's same four successes and Django failure. This small
pilot does not prove general coding noninferiority. See the
[evidence index](2026-11-02-swift-fp8-dflash2-evidence/README.md) for native suites and failure records.

Near-limit context tests used 245,596–245,626 actual input tokens with a 16,384
output allowance. A separate concurrent pair used 229,218 and 229,246 input
tokens and observed two active engine requests. DFlash allocated 822,267 target
KV slots and no-spec allocated 974,592; allocation is not measured simultaneous
full-context capacity. The compact draft still allocated 7.84 GB of physical KV.

Both strict finalist cells failed all six requests. Captures were complete,
own markers were present once with no foreign marker, but markers were not at
the start and word counts differed from 128. Disabling speculation therefore
did not remove the observed failure. Failed-cell speed values are excluded.
Earlier BF16 strict misses are historical context, not a pass for this campaign.

## Restoration and limits

[Public restoration attestation](2026-11-02-swift-fp8-dflash2-evidence/restoration.json) records verified restoration with
private native receipts. The restoration admission check did not fit the original 52 GiB
container ceiling plus 24 GiB reserve. Independently reviewed recovery tightened
the RAM and RAM-plus-swap ceiling to 47 GiB, above the previously measured
44.23 GiB peak, while preserving the reserve and all inference settings. Actual
containment and zero OOM events were verified. The original startup recipe was
retained; reboot and fresh provisioning were not tested.

All native originals remain private. Public derivatives redact operator identity
and retain [original-to-public hash lineage](2026-11-02-swift-fp8-dflash2-evidence/redaction-ledger.json).
The full high-frequency sampler streams remain private; public results and
telemetry summaries support the bounded claims. No new deployment, client
contract, Grafana promotion, or Workbench promotion was applied.

## Routed observational follow-up

A later no-change routed follow-up passed the chat-family checks except for a
Responses API authorization block, returned one 108,178-actual-prompt-token
needle in 30.337 seconds, and retained failures rather than retrying them. Its
six 64K/C1 summary requests were all ineligible under the opening-canary
contract; the 224K nominal needle was rejected before upstream and remaining
capacity cells were skipped. It does not alter this finding's strict 0/6 result,
or establish capacity, speculative-decoding speed, or promotion. See the
[separate evidence bundle](2026-11-02-swift-fp8-dflash2-live-followup.md).
