# Controller health identity envelope

Status: source-only repair verified; final integrated acceptance pending.

Consolidated control-plane review reproduced two fail-open identity checks in
`ControllerTransport._verify_node` at the predecessor revision. A health body
with duplicate `node` keys could select the expected final value, and a valid
health document padded to 65,537 bytes crossed the intended 64 KiB limit. Both
cases reached the tools POST even though the health response was not an exact,
bounded identity assertion.

An in-memory replay of the predecessor verifier produced:

```text
duplicate verified= True tools_post= True transport= controller
overflow verified= True tools_post= True transport= controller
```

The shared strict JSON loader now rejects duplicate object keys. Expected-node
verification rejects over-limit input before decoding, requires exactly the
real controller `status`, `service`, `request_id`, and `node` fields, validates
the existing bounded request-ID grammar, and caches identity only after the
complete predicate passes. Malformed or mismatched health data returns one
fixed `controller_node_mismatch` error with `not_started`; pre-dispatch connect
failure retains its existing code with no input-derived details.

Regression coverage includes the original duplicate and overflow probes,
malformed and non-finite JSON, missing and extra fields, wrong identity, exact
96/97-character request-ID boundaries, 65,536/65,537-byte boundaries, zero
tools POST and no cache on refusal, cached valid identity, legacy operation
compatibility without `expected_node`, and a real authenticated loopback
controller producer. Workload-source fixtures now use that producer's actual
four-field health envelope. No controller protocol, authorization, lifecycle,
scanner rule, credential, deployment, or fleet enrollment behavior changed.

The focused post-repair identity selector passed 16 cases; its 65,537-byte
case replaces the JSON loader with a failing spy, proving the refusal occurs
before parsing. The required five-file suite passed 392 tests, the adjacent
CLI/targets/diagnostics/fleet suite passed 492 tests, Ruff passed, and the diff
check was clean. Final integrated full-suite and CI acceptance remain separate.
