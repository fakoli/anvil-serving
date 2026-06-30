# `serves` + `eval` — managing the model serves and running the evals

Two CLI verbs that close long-standing gaps: the router only ever *connected* to
the model containers (never controlled them), and the evals were three different
invocation styles with no single entry point.

## `anvil-serving serves` — model-serve lifecycle

The router (`anvil-serving serve`) talks to the GPU model serves as backends but
never starts or stops them. `serves` does, driven by a declarative manifest
(default [`examples/fakoli-dark/serves.toml`](../examples/fakoli-dark/serves.toml))
that is the single source of truth for *which container runs on which port as
which model*.

```bash
anvil-serving serves status           # docker state + health + GPU memory per serve
anvil-serving serves down             # docker stop every serve (free the GPUs)
anvil-serving serves down fast        # stop one (by manifest name or container name)
anvil-serving serves up               # start them (see below)
anvil-serving serves up --dry-run     # print what would run, start nothing
anvil-serving serves --manifest X.toml status   # use a different topology
```

`up` is mechanism-aware: a container that already exists but is **stopped** is
restarted with `docker start` (fast, no reload); a **missing** container is
created fresh from the manifest's `up` command (a compose file for `heavy`, a
`docker run` script for `fast`). That handles the real fakoli-dark mix — `sglang`
via docker-compose and `vllm-gptoss` via raw `docker run` — under one command.

**Manifest entry:**
```toml
[[serve]]
name = "fast"                 # logical name (also accepted by down/up)
container = "vllm-gptoss"     # docker container name
port = 30001
model = "gpt-oss-20b"         # served-model-name (used by `eval`)
health = "/health"
up = "bash {dir}/serve-fast-gptoss-vllm.sh"   # {dir} = the manifest's directory
```

## `anvil-serving eval` — one entry point for the evals

```bash
anvil-serving eval preflight --tier fast     # correctness gate vs the fast serve
anvil-serving eval benchmark --tier heavy    # throughput / request-replay
anvil-serving eval planning                  # planning bake-off (offline re-grade)
anvil-serving eval planning --live           # also re-generate against live serves
anvil-serving eval bootstrap                 # replay eval fixtures -> quality profile
```

- **`preflight` / `benchmark`** resolve `--base-url` and `--model` from the serves
  manifest, so `--tier fast` is enough. If that serve is down, you get an
  actionable hint (`start it: anvil-serving serves up fast`) instead of a
  connection error. Pass extra script flags after the options, or use
  `--base-url`/`--model` to target any endpoint.
- **`planning`** drives the planning-capability bake-off. The default `--offline`
  re-runs the deterministic structural grade + aggregate over the committed
  eval-data (no serves needed, byte-reproducible). `--live` first runs
  `eval_gen.py` against the heavy+fast serves (the frontier baseline and blind
  judge panel remain human-agent steps — see the eval README).
- **`bootstrap`** replays the committed eval fixtures into a quality-profile table
  (`anvil_serving.router.profile_bootstrap --replay`) — the eval-grounded seed for
  the router's routing policy (planning → cloud `allow`; locals `deny`).

### Typical flow

```bash
anvil-serving serves up                       # bring the models up
anvil-serving eval preflight --tier fast      # is it correct?
anvil-serving eval benchmark --tier fast      # is it fast enough?
anvil-serving serves down                     # free the GPUs when done
anvil-serving eval planning                   # re-grade the bake-off offline anytime
```
