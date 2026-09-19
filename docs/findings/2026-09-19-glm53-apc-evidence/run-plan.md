# APC comparison plan

The pinned checkpoint and v84 image run on two RTX PRO 6000 Blackwell Max-Q
GPUs with TP2/EP2/DCP2, EXL3 4-bpw, FP8 MLA KV, 327680 configured context,
C4, batch 2048, native Max reasoning and no speculation. Only APC and the
explicit candidate containment wrapper differ. Candidate RAM is 52 GiB with
1 MiB swap; the final restored baseline enforces zero swap.

The frozen finalist protocol uses 32 requests per arm and cache population,
nominal 32768 context, strict 16-word visible output, canaries, max 4096 completion
tokens, temperature 0 and top-p0.95. Unique seed 1912 and shared seed 1913 are fixed;
shared prefix target is 28000. Candidate shared cache is warm; actual counter
deltas are captured. Baseline controls follow candidate cells on restored r9.

Gates: at least 15% shared mean visible TTFT improvement; at most 5% fresh-prefix
completed-request throughput loss; deterministic correctness, image and long
context checks; no OOM; exact restoration and independent review. The native
quality protocol requires three repetitions and recorded reasoning provenance.
Original two-repetition diagnostics were rerun accordingly.

Research, feasibility, scouts, finalists, quality and restoration are complete.
Human promotion and client-facing convergence remain separate.
