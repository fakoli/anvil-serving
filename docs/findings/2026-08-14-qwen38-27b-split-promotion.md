# Qwen3.8 27B split promotion

**Observed:** 2026-08-14

**Decision:** human-approved promotion

**Hardware:** two equal 96 GB RTX PRO 6000 Blackwell Max-Q cards, one TP=1
serve per card

**Evidence:** [`summary.json`](2026-08-14-qwen38-27b-split-promotion-evidence/summary.json)

## Outcome

The selected everyday configuration is the split deployment the earlier
matrix pointed toward: official FP8 plus MTP=3 is the text Primary, and the
official BF16 checkpoint plus MTP=3 is the multimodal/OCR service. Both are
served at 393,216 tokens, FP8 KV, one admitted sequence, and 4,096 batched
tokens. The multimodal service admits up to 32 images in one request.

This is a capability split, not automatic content classification. Clients
select the text capability for ordinary agent work and the explicit OCR or
general-vision capability for image work. The gateway does not silently move
an image sent to the text model.

## Why this balance won

At the matched 4K/c1/MTP=3 shape, official FP8 delivered 93.6 decode tok/s with
0.834-second median TTFT and 1.295-second median end-to-end latency. BF16
delivered 62.0 tok/s, 0.884-second TTFT, and 1.584-second end-to-end latency,
while retaining native image and video support. That makes FP8 the stronger
interactive text lane without giving up a full-size multimodal model.

TP=2 remains useful for deliberate long-prefill work, but it would consume
both cards for one request stream. The selected split keeps two independent
services available and still retains the separately qualified 393K context on
each card.

## Routed acceptance

The final FP8 route passed short coding, structured JSON, 20/20 parallel tool
calls, streaming tools, tool-result recovery, and the supported stateless
Responses subset. The final BF16 route passed the retained 30-attempt corpus:
12/12 image, 14/14 video, and 4/4 mixed-media attempts. Median routed latency
was 0.967 seconds for image, 2.218 seconds for video, and 2.621 seconds for
mixed media.

The explicit 32-image request also passed. It used 7,173 prompt tokens,
completed in 5.717 seconds, and correctly recovered the requested OCR text
from the final image. This proves the per-request ceiling at concurrency one;
it is not evidence for 32 concurrent requests.

## Failure that changed the serving layer

The first routed media run passed only 22/30 at a 512-token output cap. Raising
the cap to 2,048 improved it to 28/30, but two video attempts still spent the
entire budget without visible output. The same checkpoint and corpus had
already passed directly, so the failure was traced below the model choice:
the OpenAI-to-OpenAI relay preserved media but dropped the caller's
`chat_template_kwargs.enable_thinking=false` extension.

The operational profile now supplies a thinking-disabled soft default, and
the unchanged 512-token corpus passes 30/30. The product relay also preserves
mapping-shaped `chat_template_kwargs` on same-dialect requests, with tests for
caller forwarding, cross-dialect exclusion, and hard-override precedence.
The stateless Responses subset remains intentionally separate because that
endpoint does not accept chat-only template fields.

## Client and compaction acceptance

Hermes uses the FP8 capability for text and the general-vision capability as
its auxiliary image model. Its inline router credential was migrated to an
environment reference. Local text and image smokes passed without fallback.
Its automatic compression remains enabled at 50% usage with a 20% target and
the four enabled scheduled jobs were repinned to the promoted provider/model.

OpenClaw exposes the FP8 Primary, general vision, OCR, and the independent
voice model. Its Primary and vision client turns both reported the 393,216
token window and completed with no fallback. Safeguard compaction remains at a
50,000-token reserve, 30,000 recent tokens, and a 50% maximum history share.

## Boundaries

- The 32-image result is a single-request admission result, not a concurrency
  claim.
- General vision can produce much longer answers than OCR; clients should keep
  a larger completion allowance for descriptive media work.
- Active host overlays, device identities, endpoint addresses, credentials,
  and unsanitized client transcripts remain private operator state.
- The previously qualified TP=2 600K and 1.01M profiles remain batch-like
  experiments, not the everyday routed deployment.
