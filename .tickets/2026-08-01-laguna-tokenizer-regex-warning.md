# Classify and suppress false-positive Mistral regex warnings

**Observed:** 2026-08-01

## Problem

The pinned vLLM 0.25.1 Laguna S 2.1 serve logs a Transformers warning claiming
that the tokenizer has the incorrect Mistral regex and should be loaded with
`fix_mistral_regex=True`. vLLM exposes tokenizer selection modes but no CLI
surface for forwarding arbitrary `AutoTokenizer.from_pretrained` keyword
arguments.

The Laguna checkpoint is not a Mistral checkpoint. A bounded local differential
probe loaded the exact pinned tokenizer both with and without the requested flag
and compared contractions, digits, code-style casing, Unicode, newlines, paths,
and emoji; all tested token-ID sequences were identical. Transformers has also
tracked false-positive warnings for non-Mistral tokenizers.

## Impact

- Operators may reject an otherwise correct qualification or mutate a pinned
  checkpoint based only on a heuristic warning.
- If a future tokenizer is genuinely affected, the recipe surface cannot express
  the required constructor flag.
- Repeated warnings obscure actionable startup failures.

## Proposed resolution

Add a managed tokenizer differential preflight that compares the runtime
tokenizer with the explicitly fixed tokenizer on a deterministic regression
corpus when this warning appears. Record whether the flag changes any token IDs.
Only add a recipe-level tokenizer-kwargs contract if a supported engine interface
can forward it without modifying cached checkpoint files.

## Acceptance

- The preflight records exact model and tokenizer revisions plus a hashed corpus.
- Identical tokenization classifies the warning as non-blocking evidence, not a
  silent success.
- Any token-ID difference fails qualification before benchmark publication.
- No cached model files are edited in place.
