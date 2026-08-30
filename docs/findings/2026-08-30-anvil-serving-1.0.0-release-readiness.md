# Anvil Serving 1.0.0 release readiness

**Date:** 2026-08-30

**Candidate version:** `1.0.0`

**Starting source revision:** `a29fcc9c51204395626a3d91f4c19df55a0a26e5`

**Scope:** public product boundary, all six product-family journeys, CLI and
machine-readable discovery, Fleet version reporting, documentation, package
metadata, and source-package publication

**Deployment state:** `not-deployed`; no route, model assignment, promotion,
container, controller, client catalog, media worker, or live fleet state changed

**Release disposition:** candidate for `published_not_deployed`; merge, tag,
GitHub Release, trusted publication, and clean published-package verification
remain required

## Product boundary decision

Anvil Serving remains one umbrella product. Model Serving, the Capability
Gateway, Evaluation & Evidence, Anvil Voice, Anvil Media, and Control Plane &
Fleet share one package, CLI, topology model, safety/evidence contract, and
release line. Voice and Media are first-class branded families inside that
umbrella, not separate products.

Spinning Media into a separate product was rejected for this release because
its named workflows, durable jobs, qualification, worker lifecycle, opaque
artifacts, controller/MCP exposure, topology ownership, and evidence gates
already depend on the same public contracts. A separate repository or release
line would duplicate those contracts without creating an independent authority
boundary.

The Capability Gateway remains only one family. Its direct alias-to-tier
selection is unchanged: no prompt classifier, semantic model selection,
quality-profile routing, fallback, cloud escalation, or hidden substitution was
introduced.

| Product family | Owned user outcome | Primary root commands |
| --- | --- | --- |
| Model Serving | Reproducible artifact, recipe, serve, and reservation lifecycle | `init`, `models`, `serves` |
| Capability Gateway | Authenticated exact-alias protocol boundary | `router` |
| Evaluation & Evidence | Functional qualification and comparison-safe evidence | `eval` |
| Anvil Voice | Explicit STT, TTS, realtime, and voice qualification lifecycle | `voice` |
| Anvil Media | Bounded named image/video workflows, durable jobs, and artifacts | `media` |
| Control Plane & Fleet | Ownership resolution, typed dispatch, host utilities, and fleet state | `fleet`, `controller`, `mcp`, `host`, `topology`, and integration roots |

[ADR-0042](../adr/0042-anvil-serving-product-family-boundary.md) records the
authority decision. [Product families and user journeys](../PRODUCT-FAMILIES.md)
is the user-facing contract.

## Executable story and compatibility

The candidate adds a code-owned product catalog and read-only discovery:

```text
anvil-serving product families
anvil-serving product journey FAMILY
```

Every visible operational root command is assigned to exactly one family and
validated as a complete partition. Command-manifest schema 6 publishes the
umbrella catalog, family metadata, and each command's family id. Consumers that
accept only schema 5 must update; ordinary existing command invocations remain
compatible.

README, package metadata, root help, documentation navigation, getting started,
architecture, terminology, CLI reference, agent context, and the dedicated
Voice, Media, and control-plane references now use the same story. The Media
journey covers capability discovery, exact bundle inventory, workflow
validation, dry-run submission, durable job state, and opaque artifacts.

The candidate also repairs global JSON output for `fleet version`. A skew or
missing-installation gate still exits nonzero, but automation now receives the
complete per-host report and stable `fleet_version_gate_failed` error instead
of `data: null`. Human rendering and redaction remain intact.

The closed public tickets record the defects and their acceptance evidence:

- `.tickets/closed/2026-08-30-product-family-journeys-not-enforced.md`;
- `.tickets/closed/2026-08-30-fleet-json-drops-nonzero-report.md`;
- `.tickets/closed/2026-08-30-media-cancel-examples-missing-backend-url.md`;
- `.tickets/closed/2026-08-30-media-envelope-finding-not-indexed.md`;
- `.tickets/closed/2026-08-30-existing-finding-missing-from-index.md`;
- `.tickets/closed/2026-08-30-product-journey-json-schema-inconsistent.md`;
- `.tickets/closed/2026-08-30-finding-index-test-includes-untracked-drafts.md`;
- `.tickets/closed/2026-08-30-json-errors-duplicated-as-warnings.md`; and
- `.tickets/closed/2026-08-30-cli-audit-inventory-stale-after-review-test.md`.

Candidate review found and corrected one additional documentation defect: the
Media bundle inventory/staging journey and examples initially omitted their
required workflow id. The commands now include `<WORKFLOW>`, and a regression
requires every catalog journey step to begin with a visible command-tree path.
Three independent review rounds then found incomplete Media cancellation
examples, two missing finding-index entries, an inconsistent shared journey
JSON field type, an index regression that included untracked drafts, and a JSON
error duplicated as a warning. Exact-head CI separately rejected a generated
CLI-reference inventory that had been checked before its new regression file
entered the Git index. Each issue is corrected and covered before the final
exact-head review.

## Version and artifact closure

The `1.0.0` candidate is synchronized across package metadata, runtime version,
README badge, changelog, Dockerfile examples, public controller/router/voice/
media Compose defaults, and the byte-synchronized packaged scaffold. These
Compose tags identify locally built source images; this release does not publish
a container image or rebuild a live service.

## Verification record

Tests ran in an isolated worktree with no GPU, model, route, container, client,
or fleet mutation. The full suite used the repository's isolated pytest wrapper
and ordinary bounded host resources; no parallel model workload was started.

| Surface | Command or method | Result |
| --- | --- | --- |
| Candidate version | source-module version probe | `anvil-serving 1.0.0`; a separate stale PATH shim still reported the previously installed `0.36.0` and was excluded from candidate evidence |
| Focused product/Fleet regression | command-tree, CLI, Fleet, output, and Compose tests | 531 passed before the final journey correction; the post-correction focused set passed 348 tests; the first adversarial-review correction set passed 146 and skipped 6; the second correction set passed 355; the third correction set passed 339 |
| Full Python regression | `python scripts/run_tests.py tests/ -x -q` | third review-corrected code: 4,456 passed and 9 skipped in 186.73 seconds |
| CLI documentation audit | full-scope check/update | final correction inventory covers 836 files with zero violations; generated manifest/reference inventory current |
| Semantic secret hygiene | scanner self-test and current/tracked/untracked scopes | self-test passed; third review-corrected candidate scan covered 2,128 tracked or non-ignored untracked text files with zero findings |
| Pinned signature scan | exact staged-tree archive with pinned Gitleaks digest | final pre-review staged snapshot scanned with zero findings; current-head CI must repeat the gate |
| Full Git history | pinned Gitleaks history scan, reported separately | 21 historical signatures remain: 17 generic-key, 3 private-key, and 1 curl-auth-header; none is present in the current candidate; history rewrite or credential rotation was not authorized |
| Python lint | repository-wide Ruff check | passed |
| Documentation render | strict MkDocs build | passed |
| Markdown links | tracked Markdown link checker | 453 tracked Markdown files passed |
| CLI documentation audit | final full-scope check | 836 files scanned with zero violations; manifest, generated reference, inventory, and navigation current |
| Scaffold synchronization | packaged-scaffold sync check | public examples and packaged copies are byte-identical |
| Patch hygiene | staged diff check | passed |
| Distribution build | isolated `python -m build` | built `anvil_serving-1.0.0-py3-none-any.whl` and `anvil_serving-1.0.0.tar.gz` |
| Distribution metadata | Twine check | wheel and source distribution passed |
| Isolated wheel install | clean wheel smoke outside the checkout | installed package data and `anvil-serving router run --help` passed; wheel SHA-256 `591991ea48b8c20d4ec72ff27c8692330078e2ba9d31993294435dd46ecc5eb9` |
| Independent adversarial review | GPT-5.5/xhigh reviews of pushed commits `7fea8c8bccce04ebb8702302e5c2f806e78d79c9`, `481057c04d0d80a5eac2752e10078c4e54e866cb`, and `ae39f0f0e52aa92a7b6e327f3e1ebaf1d4ebf151` | first review found two P2 documentation defects; second found one P2 shared-schema inconsistency and one P3 test-scope defect; third found one P2 JSON error/warning regression; all findings were ticketed and corrected, and exact-head re-review remains the merge gate |
| Current-head CI | exact pushed commit after all review corrections | `ae39f0f0e52aa92a7b6e327f3e1ebaf1d4ebf151` passed every test, lint, docs, wheel, and secret job but failed the CLI audit because its new test was absent from the generated inventory; corrected exact-head CI remains required |

The host PATH observation is not package evidence: a pre-existing console shim
resolved to an older installation even while the source module resolved this
candidate. Artifact acceptance therefore requires the clean temporary wheel
environment, and post-publication verification must resolve from the published
package rather than that shim.

The historical signature findings are not represented as a clean-history
claim. Current files are clean, the established public-artifact audit remains
tracked by GitHub issue 290, and destructive history rewriting or live
credential rotation is outside this publication authorization.

## Adversarial review

The exact current head must be reviewed independently against at least these
failure classes before merge:

1. an umbrella label that hides or weakens a family's authority boundary;
2. a journey command that is nonexistent, incomplete, or mutates more than its
   prose claims;
3. a root command missing from, or duplicated across, the family partition;
4. routing behavior that infers intent, selects by engine, or introduces
   fallback while presenting as a story-only change;
5. JSON error handling that loses partial diagnostics or reveals private data;
6. version/artifact drift, stale generated docs, or release/deployment
   conflation.

Review comments must be resolved on a new commit and the new head re-reviewed.
A review of an earlier revision is not sufficient.

## Release disposition

The authorized closure is `published_not_deployed`:

1. pass final source, documentation, security, scaffold, and artifact gates;
2. push the candidate and obtain independent adversarial review of the exact
   head;
3. require current-head CI and merge only that reviewed revision;
4. verify the merge revision on `main`;
5. create the `v1.0.0` GitHub Release from that exact merge revision;
6. let the trusted-publisher workflow build and publish from the tag;
7. verify PyPI serves `1.0.0` and a clean install resolves that version; and
8. leave the current live router, controller, serves, clients, media workers,
   and routes untouched.

Any failed merge, tag, release, workflow, index, or clean-install check leaves
publication incomplete. Live deployment remains a separate transaction.
