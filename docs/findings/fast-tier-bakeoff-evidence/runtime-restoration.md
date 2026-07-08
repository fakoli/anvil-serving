# Fast-Tier Bakeoff Runtime Restoration Evidence

Date: 2026-07-08

This note records the Heavy health observations and final runtime state after
the disruptive Fast-tier candidate matrix.

Heavy continuity evidence:

```text
docker inspect vllm-gptoss120:
name=/vllm-gptoss120 started=2026-07-07T08:08:16.490006854Z running=true status=running restart_count=0

Selected /health 200 log observations during the matrix window:
2026-07-08T16:59:42.255817263Z GET /health HTTP/1.1 -> 200 OK
2026-07-08T17:03:33.924299374Z GET /health HTTP/1.1 -> 200 OK
2026-07-08T17:10:43.597347407Z GET /health HTTP/1.1 -> 200 OK
2026-07-08T18:40:48.536277436Z GET /health HTTP/1.1 -> 200 OK
2026-07-08T18:43:56.317369855Z GET /health HTTP/1.1 -> 200 OK
```

Final serve status command:

```bash
anvil-serving serves --manifest examples/fakoli-dark/serves.toml status
```

Final observed status:

```text
SERVE            CONTAINER        PORT   DOCKER    HEALTH
heavy            vllm-gptoss120   30002  running   200
fast             vllm-qwen36      30003  running   200
fast-qwen36-35b-a3b vllm-fast-qwen36-35b-a3b 39010  exited    -
fast-gemma4-31b  vllm-fast-gemma4-31b 39011  exited    -
fast-glm47-flash-sglang sglang-fast-glm47-flash 39012  exited    -
fast-glm47-flash-llamacpp llamacpp-fast-glm47-flash 39013  exited    -
fast-devstral-small2 vllm-fast-devstral-small2 39014  exited    -

GPU memory (index, used MiB, total MiB):
  0, 27056, 32607
  1, 89386, 97887
```

Direct health probes:

```text
http://127.0.0.1:30003/health -> 200
http://127.0.0.1:30002/health -> 200
```

Conclusion: Heavy stayed on the same running container with restart count `0`
and health `200` observations during the matrix window and final handoff.
Production Fast was restored to `vllm-qwen36` on port `30003`, and all
experimental Fast candidate serves were stopped or absent at final handoff.
