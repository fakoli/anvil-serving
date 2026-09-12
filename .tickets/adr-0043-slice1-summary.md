# ADR-0043 Slice 1 completion summary

Date: 2026-09-12. Scope: schema and reader/render contracts only.

## Implemented

- Added `GatewayConfig.LocalTunnel *LocalTunnelListener` and
  `ConnectorConfig.LocalTunnel *LocalTunnelEndpoint`, both with
  `json:"local_tunnel,omitempty"`, using exactly the PRD's member names.
- Kept `config.Decode` closed: null, empty, partial, unknown, incorrectly cased,
  duplicate and incorrectly typed local declarations are rejected. Valid values
  retain canonical loopback address and DNS-host validation. ServerName and HTTP
  Host may differ; each remains independent from the literal loopback address.
- Added declaration checks for service-identity and listener collisions, clean
  absolute file references, distinct leaf/key/root references, and known public
  or runtime-private trust reuse. The Python deployment reader additionally
  checks every declared origin/reverse/client/IdP/edge address, including the
  wildcard edge port, and prohibits references inside rendered output.
- Added `ConnectorConfig.ValidateGateway` for native checks when both role
  declarations are available. A selected endpoint requires the gateway listener
  with identical address, ServerName and HTTP Host. Python enforces this while
  reading the complete deployment. A gateway-only declaration remains valid.
- Preserved public host/trust/proxy fields. The local objects accept no proxy,
  enabled flag, URL, path, fallback or preference fields.
- Extended Python's allowed keys and normalized output only when fields are
  present. Existing `render.py` already directly projects the complete runtime
  objects; no renderer code change was needed. Tests prove exact field
  consumption into the appropriate role JSON, with all other generated files
  unchanged except the generation/ownership marker.
- Captured pre-change normalized-generation and per-file SHA-256 snapshots of
  the supported isolated synthetic deployment. Omission retains every rendered
  byte, the ownership hashes and generation identity. Removing both local
  declarations restores the original render.
- Added Go acceptance/rejection tests and a 202-case Python-generated corpus
  consumed by the actual Go readers and gateway-binding validator. New semantic
  error phrases agree across languages. Existing structural diagnostic styles
  remain intact: Go uses `ErrConfiguration`, Python supplies JSON-key context.
- Added managed-render tests for unchanged staging, exact native preflight
  inputs, and rejection before any native invocation or service operation.

## Validation

- `go test ./internal/runtime ./internal/config` was attempted from `connect/`.
  The default Go cache was read-only in this sandbox. With a writable temporary
  `GOCACHE`, `internal/config` passed; the full runtime package remained blocked
  by 15 existing tests attempting forbidden socket operations
  (`listen tcp4 127.0.0.1:0: socket: operation not permitted`).
- Focused Go `TestLocalTunnel*` tests passed. The cross-language test is skipped
  without its Python-supplied corpus; it ran and passed within the Python check.
- The requested `python scripts/run_tests.py tests/connect/ -x -q` was attempted
  using an available development-environment Python interpreter (`python` was
  absent from the shell PATH). The wrapper refused the sandbox's untrusted
  working-directory ancestry: group-writable checkout and remapped ownership
  of ancestor directories. No permissions or safety checks were weakened.
- Direct focused pytest: **91 passed, 2 skipped**, including both requested
  Python test files, byte-stability snapshots and the actual Go-reader corpus.
  The two skips require root for real service-UID drops.
- Direct full `tests/connect/` pytest, collected despite environmental failures:
  **504 passed, 4 skipped, 10 failed, 1 setup error**. Nine failures and the
  setup error were forbidden TCP/Unix socket operations in existing isolation,
  qualification and migration tests. The remaining installed-wheel test failed
  because uv could not create its lock in the read-only configured cache.
- `git diff --check` passed.
- Independent Sol review found a missing native admin service-identity collision.
  Both readers and collision tests were corrected; focused checks were rerun.
  The final independent review reported no remaining actionable Slice 1 findings.

The schema checks pass; the requested complete suites still need a run in an
execution environment that permits their existing socket, cache and ancestry
requirements. No production activation, transport qualification or fresh
installation is claimed.

## PRD clarifications and deferred work

1. Separate native role readers cannot validate declarations they never receive.
   `ValidateGateway` provides the explicit native pairing seam; it does not
   discover files, infer a gateway from loopback, or change the current CLI.
   Python owns deployment-wide checks involving the edge, IdP listener, client
   listeners, other connectors, and rendered root. Wiring the joint check into
   activation belongs to the later coordinated lifecycle slice.
2. Reference validation cannot prove two differently named files hold the same
   CA, nor detect copied public/inner/backend CA material. Distinct local trust
   paths are intentionally allowed. Consuming-UID ownership/readability,
   symlink/inode-safe opening, certificate/key match, EKU, expiry, exact SAN,
   chain verification and CA separation remain native preflight/startup work.
   Local files are kept outside runtime state as well as rendered output to
   avoid reusing generated private authorities or backend material.
3. The PRD's test-plan items 1–2 include later TLS, transport and lifecycle
   behavior. Only their schema, reader, renderer and omission contracts were
   implemented here, as requested. No gateway listener, connector dialer,
   transport changes, readiness/recovery changes, proxy routing, enrollment
   changes, live qualification or deployment were added. This schema-only
   slice must not be treated as an operational local fast path.
4. Compatibility evidence uses a public synthetic isolated fixture. No private
   production manifest, credential file or live service was inspected or changed.
