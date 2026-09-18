# Independent planning review

Date: 2026-09-18. Author: GPT-6 Astra. Reviewer: GPT-5.6 Sol,
`anvil-adversarial-reviewer`, separate read-only session. No evaluated model;
this review covers planning, not implementation or live acceptance.

## Findings and dispositions

| Finding | Disposition in this packet |
|---|---|
| Host Pi Web project/explorer filters do not confine operator-owned tools | Added explicit owner-host versus scoped-task modes. Host mode discloses operator access; root permissions are only promised when enforced by the execution owner. T03 and validation updated. |
| Old sessions lack a trusted project/root binding | Added an owner-only Unassigned group, explicit association with identity verification, immutable native cwd/permissions and no implicit task adoption. T03/T05 updated. |
| A slow/hung owner can violate run freshness and block healthy sources | Added independent deadlines, bounded pages/bytes/concurrency, cancellation and per-owner backoff. T06 now requires a hung-source acceptance case. |
| Finishing a failed Pi Web spike could unlock dependent tasks | Added machine-readable `pi_integration_passed` gate and required decision output. T03/T05 require a passed transport/owner decision, not simply a completed T02 document. |

The initial reviewer verdict was “hold for focused plan corrections.” All four
material findings were incorporated by the author, along with the suggested
encoded-traversal, CRLF, leading-dash and metacharacter negative cases in T03. At that stage,
no second reviewer pass was requested, to preserve the owner's usage budget. These dispositions
are source-document checks, not proof that the proposed implementation works.

## Planning validation performed

- Checked the eight-packet dependency graph is acyclic and all dependency IDs exist.
- Checked source anchors, packet paths and listed test paths, with the Anvil State
  test resolved in its separate repository.
- Checked local document links, JSON parseability, whitespace and targeted private
  identity/path patterns in the packet.
- Browser and configured-CLI observations are recorded in PLAN.md. No production
  mutation, implementation test suite or live model acceptance was performed.

## Engineering examples follow-up

A subsequent owner request added junior-engineer implementation guidance,
verified source/symbol references and runnable examples. The source map received
a bounded independent inventory pass by GPT-5.6 Terra. GPT-5.6 Sol separately
reviewed the engineering guide and examples, initially holding for two corrections:

- A bounded-command exception now stores raw output only in a transport attribute;
  its base exception args/message are fixed safe text. Self-checks cover `str`,
  `repr` and args. Logging/serializing raw exception attributes remains forbidden.
- Run projection hashes bounded opaque native IDs, including slash-containing IDs,
  instead of applying the narrower Workbench route-ID grammar. Control characters,
  invalid UTF-8 and oversized values are rejected explicitly.

The author incorporated both findings and reran the pure examples. This follow-up
review does not approve a production implementation or deployed behavior. External
reference checks, documentation-link/JSON/symbol audits and example self-checks
are the validation scope for this documentation-only expansion.

## Full handoff adversarial pass and corrections

The owner requested another adversarial review of the entire handoff. A separate
GPT-5.6 Sol review returned **hold**, with four material findings. The author made
the following corrections; the focused independent recheck is recorded below.

| Finding | Correction |
|---|---|
| High: every manifest task stays `planned`; a fresh agent cannot establish gate/dependency readiness | VALIDATION.md now defines private receipt paths, author/reviewer status transitions, canonical Anvil precedence, decision digests, stale evidence and deterministic resume examples. `tasks.json` records these rules. |
| High: T03/T05/T06 are too broad for one agent turn | Explicit ordered slices with file ownership, acceptance checkpoints and continuation receipts; one slice per run. Partial work never completes a packet. |
| Medium: task-mode acceptance demands host-only follow-up/attachment support | T05 separates required host and managed capabilities. Existing dispatcher limits are named; managed extensions remain explicitly deferred, with disabled controls and negative tests. |
| Medium: native RPC is allowed but acceptance requires an iframe | T02 now tests in-Playground behavior through the selected transport and scopes frame-specific checks correctly. |

Additional corrections: project example is explicitly a fragment that preserves
legacy fields; T06 names owner tests and cannot pass by merely explaining a missing
run; pagination requires stable snapshots; T08 cannot be fully verified with live
acceptance pending; hostile-looking filenames must be literal or rejected, never
interpreted as commands.

During this pass the owner clarified the product target:

- Existing Pi conversations open first; preserve the familiar Pi/Pi Web experience.
- Rename the visible Playground destination to **Pi**, retaining old URLs.
- Explicitly selected secondary directories must be writable. This expands T03
  to include canonical multi-root authority, isolated workspaces and per-root
  evidence. It is not a promise that current Anvil supports that contract. T03d
  must prove/design the owner contract, T03e supply missing owner support, and
  T03f integrate and test it before write controls can claim to work.

These clarified requirements are in PLAN.md, task packets, engineering guide and
the machine-readable manifest. No runtime setting or access permission changed.

### Focused independent recheck

The independent reviewer confirmed the four original findings were closed and
found no material authority contradiction in the expanded multi-root contract.
One further sequencing issue was found: the host Pi tab unnecessarily waited for
all multi-root owner work. The dependency graph now permits T05a after T02/T04,
while T05b/c wait for T03. The final independent dependency recheck returned
**Accept for planning handoff**, confirming agreement between the manifest,
README, T05 and the resume rules. All five material findings are closed in the
documents; this is not implementation certification.

Final documentation checks passed: eight packets and 18 bounded execution slices;
acyclic task/slice graph; simulated T05a readiness while T03 is blocked and T05b
readiness only after T03; 144 local links/anchors; JSON parsing and acceptance-count
consistency; whitespace checks; pure example self-checks. No product tests or
live mutation were performed in this review.

Remaining runtime unknowns include T01 State convergence, T02 embedding/authentication,
T03 multi-root owner support and T06 missing-run owners. Review of this plan does
not satisfy any of those implementation gates or authorize runtime changes.

## Visual handoff supplement

The subsequent mockup request added `mockups/index.html`, synthetic screenshots,
a guide and UX notes, informed by read-only inspection of Pi Web, Workbench and
Open WebUI. A bounded independent documentation pass mapped the screens to task
packets. The author checked prototype interactions and responsive behavior as
recorded in `mockups/GUIDE.md`. The earlier independent planning verdict does
not certify this HTML as production code or complete any implementation gate.

### Publication review

GPT-5.6 Sol independently reviewed the staged documents, prototype source and
all five screenshots after the owner requested publication. Verdict: **Accept
for PR/merge**, with no blocking findings. The review confirmed inert synthetic
behavior, host/task authority disclosure, stale-source semantics and consistent
guide/source references. This accepts the planning package, not implementation
or deployment. No model or route is promoted.

Publication checks passed: strict documentation build, 735 tracked Markdown
files checked for relative links, CLI reference inventory regeneration, repository
Ruff check, runnable design examples and semantic secret/identity scan. The guide
uses `GUIDE.md` to coexist with the standalone HTML preview, and source links are
pinned to the inspected revision so they work on the documentation site.
