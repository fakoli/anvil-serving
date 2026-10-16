# Independent matched-baseline review

The candidate R2 and restored-original SWE plans use the same five case IDs,
temperature 0, top-p 0.95, default thinking with max effort, 16384 completion
tokens, 60 steps, pinned agent/dataset/grader revisions, task limits, and exact
image identities. The plan differences are the required base URL, served-model
identity, run ID, and timestamp.

The baseline is an admitted-incumbent control: its protocol, topology, and test
contract match R2, but R2 was isolated while the incumbent was quiesced and the
restored control runs while admitted. The control can compare completed
per-instance outcomes, resolved counts, and whether each profile meets the
absolute 4/5 coding gate. It cannot support latency, throughput, strict
exclusive-load, causal runtime, or model-family claims.

The candidate's 3/5 absolute failure stands regardless of the baseline result.
The review is independent of the evaluated models and source author. Protected
source hashes: `09facf7bd0800dc18fd5c129ca5a8a7abbfb0c0f6ed2dc032bde6033e9fb87bb`
(review) and `a7e85eb5b9a7a3551103b92dc7903d888fa78db4da29a0097be51b21d4cbe4c0`
(admission note).
