# Huihui Qwen3.8 qualification addendum — 2026-09-19

**Status:** qualification evidence only. Operator authorization, applied state, and client distribution are private.

The earlier 8K scout remains historical rejected/no-promotion evidence for its original runtime. The compatible-runtime follow-up remains the evidence basis: functional gates passed, matched 8K MTP3/no-spec capacity cells completed 12/12, and the bounded 32K MTP3 cell completed 6/6. Failed 128-word warmups, boundary normalization, the unpaired 32K comparison, and higher GPU use than the cross-profile GGUF control remain retained limitations.

## Fresh baked-image gate

The baked MTP3 32K image passed a fresh quality gate at the nominal 31K target. Tool calling, unified-diff editing, timeout triage, and session recall passed 3/3. Fresh vision passed 12/12 and baked capacity passed 18/18. The [gate summary](2026-09-19-qwen38-huihui-promotion-evidence/quality-baked-summary.json) is sanitized.

## Historical benchmark publication staging

Six eligible capacity artifacts are retained: no-spec 8K n=12, MTP3 8K n=12, GGUF-control 8K n=12, MTP3 32K n=6, and final baked short n=12 and long n=6. Failed 128-word warmups remain excluded from performance import but retained as failures. Public records do not identify operational dashboards, assignment, readiness, or distribution.

## Limits

Browser acceptance remains unverified. Pinned build inputs are retained, but a fresh-host identical digest is not claimed. Operator records and recovery selection are private.

## 64K C1 reference

The 64K qualification reference passed direct preflight 7/7, routed preflight 6/6, vision 12/12, and native Hermes 9/9. Descriptive short capacity passed 12/12 at 182.1 mean decode tokens/s; long capacity passed 6/6 at 167.1 with 60,769-60,776 actual prompt tokens. See [the extension finding](2026-09-19-qwen38-huihui-64k.md).
