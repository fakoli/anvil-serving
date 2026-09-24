# Quality benchmark sampling controls are unavailable and the client fixes temperature to zero

Status: open.

The native `eval benchmark quality` command does not accept `--temperature`,
`--top-p`, or `--top-k`. Parser-only checks in the campaign source rejected all
three as unrecognized arguments before target resolution or inference. The
request builder used by quality nevertheless always sends `"temperature": 0.0`.
There is no `top_p` or `top_k` input, propagation, or request-body field in the
quality path.

This is a sampling-control availability gap. It does **not** establish why a
model exhausted a reasoning budget. In particular, it must not be used to claim
that deterministic decoding caused the observed long reasoning attempt.

## Verified source and operation scope

The operational campaign launcher and this isolated worktree both resolve to
repository revision `2b99e3353eb2e5a2813195af85050b1da0754332`. Their relevant
source hashes match:

| File | SHA-256 |
| --- | --- |
| `anvil_serving/benchmarking/cli.py` | `d120d55f269c316df3b4cd880a52174efb11d87ffdecf224b3670ad505359734` |
| `anvil_serving/benchmarking/requests.py` | `20d43653e9f2938194be7b0ea111bc5206827bd0db37d18d071601e9490ea9a0` |
| `anvil_serving/benchmarking/runner.py` | `f9fbf37f1a048a0bd7b02d36b1631754ee2df96f4654a4684d4f3e0e134549b8` |

`benchmarking/cli.py` constructs the quality parser at lines 97-109 and
118-298. Its quality-specific options include thinking controls and output
budgets at lines 206-227, suites at lines 248-262, and timeout at lines
292-297; none define sampling controls. `benchmarking/requests.py` sets
`temperature: 0.0` in `build_body` at line 168 and in `post_chat` at line 243.
Quality execution calls `post_chat` from `runner.run_bakeoff`; therefore the
fixed value is the actual non-stream quality request body.

The exact parser-only validations were:

```text
python -m anvil_serving.cli eval benchmark quality --temperature 1 --dry-run
python -m anvil_serving.cli eval benchmark quality --top-p .95 --dry-run
python -m anvil_serving.cli eval benchmark quality --top-k 20 --dry-run
```

Each returned `error: unrecognized arguments` before inference. The requested
publisher values (`temperature=1`, `top_p=.95`, `top_k=20`) were consequently
neither accepted nor sent. Describing them as "ignored" would be imprecise: the
public quality interface rejects them, while its fixed internal temperature
remains zero.

## Evidence interpretation

The completed graph MMLU artifact has a real result: 27 of 30 attempts passed
and all three attempts for `mmlu-pro-30-computer-science` failed with
`finish_reason: length`, `reasoning_tokens: 10240`, and
`failure_class: reasoning_budget_exhausted`. That is distinct from the
normalization artifacts in the same quality evidence:

- `intelligence`, `session`, `tool`, and `voice` show `not_run`, and the
  capacity-style `timing.chat` fields are null or zero because this run supplied
  only an external suite. They are schema placeholders, not failed or measured
  built-in suites.
- The retained quality-launch-friction record contains two local validation
  errors: an incompatible control-evidence schema and the legacy `--bakeoff`
  flag. It explicitly records zero inference requests. Those parser errors are
  not part of the 27/30 model result.

## Acceptance criteria

- Add documented quality sampling parameters only if each accepted value is
  passed unchanged to both the non-stream `post_chat` and streaming request
  builder, then retain request-level evidence proving the effective body.
- If an engine/dialect cannot support a control, reject that specific requested
  value before inference with an actionable error; never silently substitute a
  different sampling value.
- Keep `top_k` explicitly rejected until the selected endpoint contract and
  request implementation support it.
- Add focused parser and body-construction tests covering accepted forwarding,
  unsupported rejection, and the existing default. Do not infer a model-quality
  cause from the absence of controls.
