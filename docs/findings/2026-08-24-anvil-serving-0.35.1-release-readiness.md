# Anvil Serving 0.35.1 release readiness

**Date:** 2026-08-24

**Candidate version:** `0.35.1`

**Starting source revision:** `4d6c628573cc31215a96b4191cb6d279d19d9e1f`

**Scope:** source package, capability-meta-router documentation, and public
local-image defaults

**Deployment state:** `not-deployed`; no route, model assignment, promotion,
container, controller, client catalog, or fleet state changed

This release publishes inference-owned model metadata behind stable capability
aliases and names the resulting product contract: Anvil Serving is a
**capability meta-router** implemented as a thin direct gateway. The caller
selects a stable capability alias, operator configuration maps it to exactly
one tier, and that tier's already-selected single-model inference service may
own bounded mutable facts about what it serves.

The term does not restore the removed intent router. There is no prompt
classifier, candidate ranking, quality-profile selection, semantic fallback,
cloud escalation, response-verification retry, or hidden substitute model.

## Product and architecture decision

The documentation and ADR pass establishes one authority model:

| Authority | Owns |
| --- | --- |
| Caller | Requested capability alias and payload |
| Operator configuration | Alias-to-tier mapping, endpoint, dialect, auth reference, readiness contract, and router safety policy |
| Selected inference service | Allowlisted mutable served-model facts only when `metadata_source = "upstream"` |
| Router | Authentication, closed alias resolution, metadata validation, admission, translation, streaming relay, and metadata-only decisions |
| Evaluation and promotion | Qualification evidence and human-gated exposure changes |

Route selection precedes metadata resolution. Missing, ambiguous, malformed,
or conflicting required upstream metadata makes the selected tier unavailable;
it cannot introduce a second candidate or change the endpoint.

[ADR-0039](../adr/0039-capability-meta-router.md) records the category and
invariants. It complements rather than supersedes ADR-0028's thin direct
gateway. [ADR-0038](../adr/0038-inference-owned-model-metadata.md) remains the
concrete metadata-authority mechanism.

## Version and artifact closure

The candidate version is synchronized across:

- `pyproject.toml` and `anvil_serving.__version__`;
- the README source badge and changelog;
- the Dockerfile build example and OCI version argument;
- the public router and voice Compose defaults; and
- the byte-synchronized packaged scaffold copies used by `anvil-serving init`.

The Compose tag names a locally built source image. No container-registry
artifact is published by this release, and no live image is rebuilt or
deployed as part of package publication.

## Verification record

The following checks ran in the isolated release worktree on the staged
candidate:

| Surface | Command or method | Result |
| --- | --- | --- |
| Editable version refresh | editable dev install from the candidate tree | installed `anvil-serving 0.35.1`; corrected the expected stale `0.35.0` console metadata before version assertions |
| Focused CLI regression | `pytest tests/test_cli.py -q` | 281 passed |
| Scaffold and lifecycle regression | focused init, router-management, and CLI tests | 350 passed |
| Full Python regression | `python -m pytest tests/ -q` | final staged candidate: 4,145 passed and 9 skipped in 162.10 seconds |
| Python lint | repository-wide Ruff check | passed |
| Documentation render | strict MkDocs build | passed |
| Markdown links | tracked Markdown link checker | 387 tracked Markdown files passed after the release finding was staged |
| CLI documentation audit | docs, skills, and full scopes | 121 docs files, 19 skill files, and 731 full-scope files; zero violations; inventory, generated references, and navigation current |
| Scaffold synchronization | packaged-scaffold sync check | public examples and packaged copies are byte-identical |
| Patch hygiene | staged diff check | passed |
| Semantic secret hygiene | scanner self-test plus tracked snapshot | self-test passed; 1,865 tracked files scanned with zero findings |
| Signature secret hygiene | pinned Gitleaks against the staged patch | 51.08 KB scanned with zero findings; the full-history scan exited cleanly with only existing allowlisted historical fixture/evidence matches |
| Distribution build | isolated `python -m build` | built `anvil_serving-0.35.1-py3-none-any.whl` and `anvil_serving-0.35.1.tar.gz` |
| Distribution metadata | Twine check | wheel and sdist passed |
| Isolated wheel install | clean wheel smoke | installed wheel reported `0.35.1`, loaded package data, and ran `anvil-serving router run --help` |

The first Markdown-link run reported the two new pages as missing because the
repository checker intentionally considers only tracked files. Staging the
pages and rerunning produced the passing tracked-snapshot result above. This
was not a broken link in the candidate.

## Adversarial review

The documentation diff was reviewed against three failure classes:

1. **Selection ambiguity:** every meta-router definition says one alias maps to
   one tier and that metadata resolution cannot revisit route selection.
2. **Policy-authority leakage:** inference services may report only allowlisted
   served-configuration facts; tool, media, output, readiness, and concurrency
   policy remains router-owned.
3. **Release/deployment conflation:** the changelog, local-image guidance, and
   this finding distinguish source/package publication from container build and
   live fleet deployment.

No runtime selection algorithm, dependency, route mutation, or promotion was
introduced by the documentation reframe.

## Release disposition

The authorized closure is `published_not_deployed`:

1. merge only the reviewed PR head after current-head CI passes;
2. verify the merge revision on `main`;
3. create the `v0.35.1` GitHub Release from that exact revision;
4. let the trusted-publisher workflow build and publish from the tag;
5. verify PyPI serves `0.35.1` and a clean install resolves that version; and
6. leave the current live router, controller, serves, clients, and routes
   untouched.

If the merge, tag, GitHub Release, trusted-publisher workflow, PyPI index, or
clean published-package smoke fails, publication remains incomplete. Live
deployment requires a separate authorization and the complete controller,
router, parity, route, client, and safety gates.
