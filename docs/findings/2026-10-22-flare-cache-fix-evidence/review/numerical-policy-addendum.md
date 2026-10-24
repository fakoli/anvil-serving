# Numerical-policy source addendum

Source verified on 2026-10-22 at Flare commit
`47581ccac82660953a5b59cb04b2750d64edc97d`; `docs/core-design.md` has no local
changes. This is documentation of the upstream arithmetic contract, not a
measured attribution of the retained GLM mismatch.

- Lines 960–964 explicitly describe possible arrival-ordered atomic MoE reductions:
  equal graph inputs and RNG state need not produce equal logits or sampled IDs.
  Transfers and request metadata remain exact; recomputation uses calibrated
  component bounds.
- Lines 727–757 distinguish same-capacity deterministic replay, measured atomic
  repeatability, and changed reduction association across capacities. The GDN
  paragraph is component guidance; applicability to the actual loaded graph must
  be established rather than inferred from a model name.
- Correctness acceptance at lines 1543–1600 calls for Rust versus Python replay
  of the same prepared graphs and starting state, plus component comparisons on
  identical operands against Transformers or b12x references. Teacher-forced
  diagnostics retain full-vocabulary logits, relative L2 using the reference
  norm, top-ten overlap, and FP32 KL(reference || actual). Whole-model logit
  distance is diagnostic; numerical acceptance uses component-specific bounds.

The existing exact 128-token sampled-output gate remains failed. This addendum
does not retroactively relax it, prove that the observed failure is benign,
or change formal workloads, image, source pins, thresholds or acceptance flags.
It supplies a concrete candidate mechanism and corrects any suggestion that a
single whole-model logit tolerance alone would discharge numerical acceptance.
A future numerical experiment must freeze arithmetic-derived bounds and matched
starting state before examining restored results, locate the first differing
layer, and test its operators. Current byte-transfer evidence remains separate.
