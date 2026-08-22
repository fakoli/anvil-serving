# Anvil Serving 0.35.0 release readiness

**Date:** 2026-08-22

**Candidate version:** `0.35.0`

**Scope:** source package and documentation release

**Deployment state:** `not-deployed`; no route, model assignment, promotion, or
fleet state changed

This release candidate turns the Qwen3.8 RTX 5090 qualification workflow into
reusable product surfaces. It adds deterministic recipe feasibility screening,
stronger context and long-agent gates, and a routed evaluation command that
checks router identity and capacity before testing real OpenClaw and Hermes
clients. It also makes the product's routing contract explicitly independent of
llama.cpp, vLLM, SGLang, TensorRT-LLM, Unsloth-derived serving stacks, or any
other OpenAI-compatible engine.

## Model-evaluation boundary

The complete model measurements and immutable artifact identities are in the
[Qwen3.8 27B RTX 5090 finding](2026-08-21-qwen38-27b-gguf-250k-rtx5090.md).
The observed result is a `FAST-TIER` challenger, not a promotion:

- Q4_0 plus the matching MTP3 head accepted 253,822 actual prompt tokens while
  preserving an 8,192-token output reserve and passed exact cross-link retrieval.
- It passed tools 20/20, one tool call after 110,875 actual prompt tokens,
  deterministic images 18/18, and three neutral 101-request endurance sessions.
- MTP improved short decode from 69.1 to 104.1 tok/s and reduced short end-to-end
  latency from 0.91 to 0.74 seconds, with slower TTFT and prefill.
- Q6_K with the same MTP head was mathematically disqualified because its
  optimistic VRAM margin remained negative at the required context and reserve.
- Real `llm.secondary` OpenClaw and Hermes identity and tool-continuation smokes
  passed without accepted fallback. The routed 250K gate failed because the
  router advertised a stale 131,072-token SGLang/NVFP4 profile instead of the
  active 262,144-token llama.cpp Q4_0/MTP3 candidate.

The release does not treat those short routed passes as authorization to update
the router fingerprint, client defaults, or a promoted assignment. A future
operator transaction must deploy truthful metadata, reconcile client catalogs,
and rerun `eval routed` at the 250K minimum before promotion can be considered.

## Session validation record

The following checks were run from the release worktree. Counts describe the
candidate tree at the recorded point; the final release gates are rerun after
the version and narrative edits.

| Surface | Command or method | Result |
|---|---|---|
| Upstream reconciliation | fast-forward from `origin/main`, then editable reinstall | source advanced to `0.34.3`; a stale `0.34.2` console entry point caused two version assertions until `pip install -e ".[dev]"` refreshed the environment |
| Upstream client-catalog regression | `pytest` on client catalog, CLI, and MCP tests | 301 passed after the editable refresh |
| Routed evaluation unit evolution | focused `tests/test_routed_eval.py` runs | 8 passed, then 10 passed, then 11 passed after real catalog-reconciler integration |
| Focused benchmark/docs integration | benchmark, routed-eval, command-tree, audit, and docs tests | 126 passed and 6 skipped at one checkpoint; 113 passed and 6 skipped after command/reference regeneration |
| Python lint | focused and repository-wide Ruff checks | passed |
| Full Python regression | `python -m pytest tests/ -q` | first shell invocation reached its 122-second wrapper timeout with no test failure; bounded rerun completed with 4,126 passed and 9 skipped in 163.08 seconds; the final staged candidate repeated 4,126 passed and 9 skipped in 163.96 seconds |
| Python 3.11 compatibility | GitHub matrix plus isolated CPython 3.11.16 rerun | the first PR head failed five feasibility tests on Ubuntu and Windows 3.11 while both 3.13 lanes passed: integer interval bounds called `int.is_integer()`, which is unavailable on 3.11; normalization now calls `float(value).is_integer()`, a direct regression passes 10/10, and the complete local 3.11 suite passes 4,127 with 9 skipped in 166.01 seconds |
| Documentation render | strict MkDocs build | passed |
| CLI documentation audit | `audit_cli_references.py --check` | early docs-scope pass covered 118 files; final full-scope pass covered 725 files with zero violations and current inventory/generated/nav state; the preceding combined invocation was nonzero only because the audit command was initially called without required `--check` |
| Markdown links | `check_markdown_links.py --root .` | 382 tracked Markdown files passed after the new finding was staged; the expected pre-stage run could not resolve untracked targets |
| Secret hygiene | semantic scanner plus pinned Gitleaks | self-test passed; staged tracked snapshot had zero semantic and zero signature findings; full Git history had zero signature findings; seven ignored working-directory hits were confined to generated `site/` and pytest-cache copies |
| Patch hygiene | `git diff --cached --check` | passed after replacing Markdown hard-break whitespace; Git emitted line-ending conversion warnings only |
| Distribution build | `python -m build` | built `anvil_serving-0.35.0-py3-none-any.whl` and `anvil_serving-0.35.0.tar.gz` from an isolated build environment |
| Distribution metadata | `python -m twine check dist/*` | wheel and sdist passed |
| Isolated wheel install | `python scripts/wheel_smoke.py --json` | passed outside the checkout; package data loaded and `anvil-serving router run --help` succeeded |

The timeout and stale editable install are retained because they explain
non-passing command exits without misclassifying them as product regressions.
Neither was hidden by changing the benchmark or test expectations.

## Independent correctness design

The new gates do not ask the candidate model to grade itself. Context probes
use deterministic exact identifiers and relationships. Long-tool acceptance
requires independently parsed usage of at least 100,000 prompt tokens and a
schema-valid tool selection. Routed acceptance verifies router metadata,
client-observed provider/model identity, fallback state, and tool protocol
rather than grading only the visible answer. The feasibility skill reports
uncertain memory, speed, and quality terms as intervals or unfilled variables.

## Retained blockers and follow-up

- The managed llama.cpp recipe health check targets the image-default internal
  port rather than the recipe port; the durable product gap is recorded in the
  repository ticket dated 2026-08-21.
- The required controller transport to the independent SWE-bench worker was
  unavailable, so difficult coding and time-to-success comparisons against the
  heavy tiers remain incomplete.
- Non-consecutive token-position warnings observed during llama.cpp endurance
  need explanation or elimination.
- The candidate supports images through its F16 projector but does not support
  native video.
- Package release does not rebuild a serving image or prove live fleet version
  parity. Those are deployment-readiness gates for a separately authorized
  deployment.

## Release disposition

The intended closure state is `published-not-deployed`. PR checks, the merged
commit, the `v0.35.0` GitHub Release, trusted-publisher workflow, and an isolated
install from PyPI are authoritative publication records. If any current-head
CI, package, security, or publication gate fails, the release remains blocked
and no tag is cut.
