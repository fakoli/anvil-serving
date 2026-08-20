# Qwen3.8 27B agentic and SWE-bench Verified scout

**Date:** 2026-08-15
**Gate:** complete bounded scout; no promotion change

## Outcome

The current official-FP8 Qwen3.8 27B SGLang service passed the two-attempt
agentic smoke, passed 16 of 18 attempts in the broader agentic scout (88.9%,
above its 75% gate), and resolved all five fixed SWE-bench Verified scout
instances under the official grader. The SWE result is a five-instance fixed
sample, not a claim of 100% on the full benchmark.

Every model request used the private router over Tailscale. The worker was
AI-MBP25 (macOS arm64); official Linux x86-64 SWE evaluation images ran under
Docker's `linux/amd64` emulation. No request went directly to the model serve,
and the campaign did not restart a controller or model, change a route, or
promote a profile.

## Exact configuration

- Model: `Qwen/Qwen3.8-27B-FP8` at `017b9c7af6b5689d5dd426a76e0bc077eb5ca20a`.
- Engine: SGLang at `c4271c3fe1262fc2adbd162c33b25de5255251c5`; image digest `sha256:506525a5907ea22c9d445afb7c03603959b912de034d86915cf17da814f1a124`.
- Topology: one RTX PRO 6000 Max-Q, TP=1, 393,216-token configured context, FP8 E4M3 KV, EAGLE MTP `3/1/4`, admission one; the second equal card was idle.
- Benchmark source: `19783914f24367ff6b96eb91fb35cb0efbeced18`.
- SWE Verified dataset: official revision [`c104f840cc67f8b6eec6f759ebc8b2693d585d4a`](https://huggingface.co/datasets/princeton-nlp/SWE-bench_Verified/commit/c104f840cc67f8b6eec6f759ebc8b2693d585d4a).

## Agentic result

The thinking-disabled smoke passed 2/2. The thinking-enabled scout passed
planning, reasoning, structured-output, sequential, parallel, and dependent
tool cases, tool-error recovery, and context recovery. Both failures were the
two repetitions of `debug-loop`: extra reads desynchronized the fixed tool
sequence, followed by an overlong explanation instead of the expected compact
completion. The request control recorded thinking enabled, but the normalized
evidence did not identify a separate reasoning field.

This is a useful, narrow defect signal: tool use and recovery are broadly
healthy, while repeated debugging needs a more adversarial follow-up with a
less sequence-sensitive fixture and explicit efficiency scoring.

## SWE-bench Verified result

| Instance | Official grader | Model requests |
|---|---:|---:|
| `django__django-11099` | resolved | 19 |
| `pytest-dev__pytest-10051` | resolved | 28 |
| `scikit-learn__scikit-learn-10297` | resolved | 31 |
| `psf__requests-1142` | resolved | 57 |
| `sympy__sympy-11618` | resolved | 21 |

All five attempted instances were graded and resolved. The run took 42 minutes
29 seconds end to end. Retained normalized evidence did not expose comparable
per-instance token or duration fields, so none are inferred. Request counts
ranged from 19 to 57, making the perfect bounded score compatible with
materially different solution costs.

## Comparison with the earlier DeepSeek worker smoke

The earlier DeepSeek V4 Flash 0731 r16/DSpark campaign used the same AI-MBP25
worker boundary but an exclusive two-card TP=2 serve and an earlier harness.
Its `tool-recovery-error` smoke followed the required retry protocol but failed
to produce the required final answer (0/1); Qwen passed both repetitions of the
current scenario. On SWE-bench Verified, both models resolved the only exact
overlap, `django__django-11099`. Qwen also resolved the fixed pytest,
scikit-learn, requests, and SymPy tasks, expanding the retained official-grader
sample from one task to five.

This favors Qwen for the tested repository-agent workflow, with half the
measured GPU count, but it is not a controlled intelligence ranking: the model
recipes, GPU topology, benchmark source, request controls, and sample sizes
differ. The [comparison table](../benchmarks/comparison.md#remote-coding-agent-comparison)
keeps those boundaries visible.

## Harness corrections made before the retained run

The campaign exposed and fixed six benchmark-worker defects rather than
misclassifying them as model failures:

1. Corrected the SWE-bench Verified dataset revision to the official immutable commit.
2. Installed pinned mini-SWE-agent and official grader checkouts with `uv`.
3. Selected `linux/amd64` for official evaluation images on Apple Silicon.
4. Resolved the container runtime to an absolute executable in the immutable plan.
5. Overrode a stale Colima `DOCKER_HOST` with the active Docker Desktop context.
6. Normalized surrounding whitespace in the router credential passed to the child process.

These changes are infrastructure corrections. Failed setup attempts were not
counted as model attempts. The focused SWE tests passed 7/7 after the final
change; the broader branch previously passed 4,055 tests with 10 skips.

## Evidence and decision

The sanitized machine-readable result is [`summary.json`](2026-08-15-qwen38-27b-agentic-swe-scout-evidence/summary.json).
It retains the profile, plan, stage, artifact, preflight, asset, and official
grader hashes. Private operator evidence retains the unsanitized artifacts and
worker topology.

This campaign strengthens confidence in the current model for repository work
and tool use. It does not justify a full-suite SWE score, eliminate the
debug-loop weakness, or authorize a route or promotion change. Retain the
current service and run a larger stratified SWE sample plus an efficiency-aware
debugging set before making broader coding-quality claims.
