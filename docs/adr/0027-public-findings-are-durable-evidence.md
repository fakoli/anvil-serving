# ADR-0027 — Public findings are durable evidence

- **Status:** Accepted
- **Date:** 2026-07-22
- **Relates to:** issue #178; issue #175; ADR-0008 (evidence-location convention only);
  ADR-0022; `docs/findings/README.md`

## Context

Product claims in this repository depend on dated benchmarks, live validations, failure records,
and lab notebooks. Those records are intentionally point-in-time evidence: current reference docs
and ADRs may change, but a reader still needs to inspect what was observed when a decision was made.

The repository also has a private companion notes repository for design discussion, PRDs, session
artifacts, and pre-pivot history. Moving public findings there would make public ADRs and product
claims depend on evidence external readers cannot inspect. Keeping every raw log or generated output
in Git without an admission policy, however, would create unbounded repository growth and increase
the chance of publishing secrets, prompts, personal data, or noisy artifacts that reviewers cannot
meaningfully inspect.

## Considered options

1. **Keep public findings as durable evidence.** Preserve sanitized narratives and the bounded raw
   evidence needed to audit their claims. This keeps public reasoning independently inspectable but
   requires admission, retention, and correction rules.
2. **Migrate findings after their conclusions enter an ADR or reference page.** This keeps the
   public tree smaller, but breaks old public citations or replaces evidence with a conclusion that
   cannot be independently checked.
3. **Keep only prose summaries.** This avoids raw-artifact growth, but makes numerical and protocol
   claims hard to reproduce and encourages selective transcription.

## Decision

`docs/findings/` is the durable public evidence layer. A finding is not moved to a private
repository merely because it is dated, superseded, or summarized by a current reference page.
Current recommendations live in the named docs and ADRs; findings preserve the observations that
grounded them.

Public findings use this admission contract:

- A dated Markdown narrative is required. It identifies the tested revision/configuration,
  environment or topology, method, result, failures, caveats, and whether the evidence is local,
  synthetic, live, or external prior art. It links current reference docs rather than pretending a
  point-in-time conclusion is current forever.
- Every narrative is added to `docs/findings/README.md`. Raw artifacts are linked from their
  narrative or indexed directly when they are independently useful.
- All public evidence must be sanitized before publication, whether it is checked into Git or stored
  externally. Secrets, credentials, private prompts, personal data, machine-local tokens, and
  unrelated logs are prohibited in both locations. Of the sanitized material, only evidence needed
  to audit the public claim belongs in Git.
- Prefer compact machine-readable JSON/CSV and small explanatory text. For new checked-in evidence,
  each raw artifact must be at most 1 MiB and the aggregate checked-in raw evidence linked by one
  finding must be at most 5 MiB. A finding bundle is all raw evidence from one experiment,
  qualification, promotion, or evidence packet; splitting it across directories or narratives does
  not reset the limit.
  Exceeding either limit requires an explicit PR exception that identifies the files, total bytes,
  why a bounded subset or external store is insufficient, and the approving reviewer.
- Absent an approved exception, larger, binary, or high-volume evidence belongs in a durable public
  store. The artifact must be downloadable without authentication at a non-expiring versioned or
  content-addressed HTTPS URL,
  immutable at that URL, and retained for at least as long as the public citation. The narrative
  records the retention owner/term, byte size, SHA-256 digest, provenance, and bounded result subset
  used by the claim. Expiring CI artifacts, private buckets, and mutable `latest` URLs do not qualify.
- Raw evidence is retained while a public finding, ADR, benchmark table, release note, or reference
  doc depends on it. There is no age-based deletion. A later result supersedes the recommendation,
  not the historical observation.
- Corrections preserve history. Add a dated correction/erratum or a superseding finding, publish a
  corrected artifact at a new path, and link both directions; ordinary metadata mistakes never
  authorize overwriting the merged evidence or rewriting Git history. If sensitive or legally
  restricted material must be removed, replace the public path with a tombstone when safe and
  lawful, recording the removal date/reason category, prior byte size/digest when those are not
  sensitive, and the replacement evidence. Explain the exceptional removal in the correcting PR.

The private notes repository remains the home for planning discussions, PRDs, review transcripts,
session traces, and exploratory material that does not meet the public admission contract. A
private citation may be supplementary, but no load-bearing public claim may depend only on it; the
claim and its auditable evidence must be restated in a public finding, ADR, or reference page.

Only existing artifacts' size and format are grandfathered. Sanitization, legal-removal, correction,
and public-citation rules apply to all findings regardless of age. This decision does not authorize
a bulk deletion or migration. Any later repository-size cleanup must be separately reviewed and
preserve public narratives, citation targets, provenance, stable artifact access, and content hashes.

This decision supersedes the old evidence-location convention stated in ADR-0008, `CLAUDE.md`, and
the former `CONTRIBUTING.md` design-history note; it does not change ADR-0008's serving decision.
Existing private-only grounding is a known legacy gap tracked by issue #175. Legacy sanitization and
machine-local evidence access are tracked by issue #290. This ADR chooses the policy but does not
claim that every historical citation or artifact has already been remediated.

## Consequences

- New findings, and legacy claims remediated under issue #175, can be followed back to inspectable
  evidence without private-repository access. Existing private-only grounding remains explicitly
  incomplete until remediated.
- The public repository grows with useful evidence, so contributors and reviewers must enforce
  sanitization, bounded artifact size, indexing, and current-vs-historical labels.
- ADRs and reference docs remain the source of current policy; findings remain immutable historical
  inputs rather than a second current configuration guide.
- Absent an approved exception, large raw captures require unauthenticated, immutable, non-expiring
  external storage plus hashes instead of being copied wholesale into Git. Existing size/format
  exceptions remain historical and are not precedent for new unbounded bundles.
- Issue #175 can be resolved claim by claim under one rule: publish or restate load-bearing evidence
  publicly, and treat private notes links as supplementary context only.
- Issue #290 audits the legacy corpus for machine-local identifiers and inaccessible evidence; size
  and format grandfathering does not certify that corpus as sanitized or publicly reproducible.
