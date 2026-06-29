# Findings & evals

Durable, dated records of the measurements and research behind anvil-serving's
fakoli-dark bake-off. Each file is a self-contained finding; `eval-data/` holds
raw, reproducible eval artifacts. Newest decisions supersede older ones where they conflict.

## Index

| Date | Finding | What it answers |
|---|---|---|
| 2026-06-27 | [capacity-throughput](2026-06-27-capacity-throughput.md) | Early single/dual-GPU throughput + latency baseline |
| 2026-06-28 | [model-mixture-research](2026-06-28-model-mixture-research.md) | Deep-research pick of open-weight models for the box (R003) |
| 2026-06-28 | [benchmark-capacity](2026-06-28-benchmark-capacity.md) | Live capacity: tok/s, KV headroom, context caps for both serves |
| 2026-06-28 | [gptoss20b-fast](2026-06-28-gptoss20b-fast.md) | gpt-oss-20b as the FAST tier on sm_120 (engine/quant viability) |
| 2026-06-28 | [preflight-results](2026-06-28-preflight-results.md) | Correctness-gate (needle@128k, thinking-aware) results (T006) |
| 2026-06-28 | [run-log](2026-06-28-run-log.md) | Per-workflow/agent token+timing log for the 9-PR adversarial loop |
| 2026-06-28 | [anvil-integration-audit](2026-06-28-anvil-integration-audit.md) | Does anvil support `llm_provider: custom` role-routing? (T013 premise) |
| 2026-06-28 | [planning-capability-eval](2026-06-28-planning-capability-eval.md) | Is the local tier smart enough for anvil's planning? (blind eval) |

## eval-data/ — reproducible eval bundles

### `eval-data/2026-06-28-planning-capability/`
The full artifact set behind [planning-capability-eval](2026-06-28-planning-capability-eval.md):
local HEAVY/FAST vs a frontier Opus baseline on anvil's real PRD→tasks prompt, graded
deterministically (anvil's own parser rules) **and** by a blind 4-judge panel.

```
prompts/                     exact system+user prompts fed to every model
outputs/out_<prd>__<model>.md   all 6 raw task-graph generations
grading/gen_manifest.json    generation stats (tokens, latency, tok/s)
grading/grade_struct.json    deterministic structural metrics (every check)
grading/judge_prd{A,B}_{1,2}.json   raw blind-judge scores + rationales
grading/anon_map.json        hidden letter→model mapping (de-anon key)
grading/metrics_long.csv     ONE ROW per PRD×model — analysis-ready (pandas/R)
grading/judge_dimensions_long.csv   tidy long: prd×model×judge×dimension×score
grading/aggregate.json       per-model overall + inter-judge agreement
eval_gen.py grade_struct.py aggregate.py   the harness
```

**Reproduce the planning eval** (needs both serves up — see below):
```bash
cd docs/findings/eval-data/2026-06-28-planning-capability
python eval_gen.py          # 1. render anvil's prompt, query :30000 + :30001 -> outputs/, gen_manifest.json
# 2. regenerate the frontier baseline: give an Opus agent prompts/prompt_*.txt,
#    have it write outputs/out_<prd>__frontier.md (see the report §1 for the exact ask)
python grade_struct.py      # 3. deterministic structural grade -> grade_struct.json
# 4. blind judges: copy outputs to anonymized cand_<prd>_{X,Y,Z}.md per anon_map.json,
#    run 2 independent judges per PRD -> judge_<prd>_{1,2}.json (see report §1)
python aggregate.py         # 5. join everything -> metrics_long.csv, aggregate.json
```
`metrics_long.csv` is the file to load for your own analysis. The harness is stdlib-only
(`urllib`), hits `127.0.0.1` (never `localhost` — IPv6 `::1` stalls urllib ~21s/call on Windows).

## Running the serves the findings depend on

Both tiers must be live for the capacity/preflight/planning evals:
```bash
docker ps        # expect: sglang (HEAVY :30000), vllm-gptoss (FAST :30001)
curl -s http://127.0.0.1:30000/v1/models   # HEAVY: qwen3-coder-local
curl -s http://127.0.0.1:30001/v1/models   # FAST:  gpt-oss-20b
```
Serve scripts / composes live in `examples/fakoli-dark/` (e.g. `docker-compose.heavy.yml`,
`serve-fast-glm-vllm.sh`). GPU isolation gotcha: Docker Desktop WSL2 ignores `--gpus device=N`;
use `--gpus all -e CUDA_VISIBLE_DEVICES=<GPU-UUID>`.

## Re-running the anvil-serving tools behind these findings

```bash
pip install -e .                      # stdlib-only package
anvil-serving benchmark --help        # replay the measured request distribution (benchmark-capacity)
anvil-serving preflight  --help       # correctness gate; use --no-thinking for reasoning-default models (preflight-results)
```
- **Preflight on reasoning-default models** (e.g. Qwen3.5): pass `--no-thinking` (injects
  `chat_template_kwargs{enable_thinking:false}`) and ≥256 max_tokens, else they false-FAIL.
  gpt-oss ignores the kwarg — just give it tokens.
- The **anvil-integration-audit** was produced by a multi-agent Workflow over the `anvil`
  repo; it needs no serves (static code audit). Re-run by pointing the same workflow at
  `fakoli/anvil`.

## Conventions
- One dated file per finding: `YYYY-MM-DD-<slug>.md`, newest wins on conflict.
- Cite code as `path:line`. Keep raw eval data under `eval-data/<date>-<slug>/` so results are reproducible, not just asserted.
- Third-party reference material (papers, vendor docs) is **not** committed here — link to the source instead.
