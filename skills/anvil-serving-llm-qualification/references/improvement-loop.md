# Evidence-driven skill improvement

Read this at campaign closure, after a material process failure, or when asked
to improve the benchmarking skill. Improve the agent's workflow and reusable
instructions; model settings still use `configuration-search.md`. A thought
about what went wrong is a hypothesis, not evidence that a lesson works.

## Capture one useful lesson

Use the existing friction log and evidence index, not a new memory database.
Look for repeated waste, a verified root cause, a missed capability, or a
material correction from the operator. Record successful methods only when
the evidence explains why they worked. One impactful, reproducible failure can
justify a narrow correction; an unexplained isolated failure cannot justify a
universal rule. If no actionable evidence exists, record no change and finish.

Separate the layers before editing: harness/transport, served-model recipe,
workload/validator, source interpretation, agent decision, and skill instruction.
Correct the owning layer. A provider timeout is not proof that a skill needs
more reflection. A bad validator needs its own reviewed repair and invalidates
comparisons made with it; do not quietly weaken it while optimizing the skill.

For each proposed lesson retain: observation and evidence path; causal
hypothesis; applicable model/engine/hardware/workload and revisions; proposed
instruction delta; disconfirming evidence; evaluation result; expiry trigger;
and accepted, rejected, or unresolved disposition. These are Markdown notes,
not new native evidence-schema fields. Keep private traces out of public skills.
Do not write account-wide memories, alter unrelated instructions, or broaden
service/promotion authority through this loop.

## Propose, evaluate, then adopt

1. **Freeze the baseline.** Record the skill commit and hashes of loaded
   references, agent model/effort, harness/tools, relevant workload and rubric,
   limits, and evidence-capture policy. Keep skill changes out of a running
   qualification cell. A changed instruction starts a new version; existing
   results keep their original identity.
2. **Pick one falsifiable improvement.** State the observed bad decision and the
   desired next action, then make the smallest source-of-truth edit. Prefer
   removing contradictory, obsolete, or duplicated instructions over adding
   another unconditional rule. Apply edits within existing authorization;
   otherwise leave a concrete proposal without claiming it is installed.
3. **Predeclare the comparison.** Keep known development cases separate from
   independently prepared transfer/holdout cases. Include the triggering case,
   a previously successful case, and a case where the new rule must not apply.
   Hold task, tools, model/effort, context/history, and budget comparable between
   baseline and candidate. If effort or delegation also changes, attribute the
   result to the combined configuration, not just the prose edit.
4. **Observe behavior.** Replay with fresh, isolated agent contexts and no live
   side effects unless already authorized. Score actual decisions, tool use,
   evidence fidelity, stopping behavior, and completion. Prefer executable
   checks where outcomes are deterministic; otherwise use an independent
   reviewer with explicit pass/fail criteria and evidence. The optimizer must
   not grade its own answers. Hide variant identity during comparison when
   feasible, counterbalance order, and allow ties and insufficient evidence.
   Static skill validation alone proves neither behavior nor improvement.
5. **Check transfer.** Freeze the candidate before the final independent cases;
   do not give their answers to the optimizer beforehand. After a holdout is
   used to guide edits, label it development evidence and obtain fresh cases
   before claiming unseen generalization. Repeat noisy or consequential cases
   to separate a real change from ordinary run-to-run variation.
6. **Decide and retain.** Preserve correctness, reliability, evidence integrity,
   and authority gates before comparing speed or tokens. Accept a scoped fix
   with independent behavioral support; claim a performance gain only when a
   matched baseline comparison supports it. A tie can justify clearer or
   shorter instructions, but is not a measured capability gain. Keep the old
   version on unexplained regression; retain unresolved proposals for later.
   Reverting a skill experiment does not authorize changing a served model.

## Bound the improvement cost

Default to one focused proposal and one paired comparison at an appropriate
campaign boundary. Retry a new hypothesis only when the evidence warrants it
and the shared budget permits; do not enter a perpetual critique loop. Keep
research panels for material uncertainty under `configuration-search.md`, not
every lesson. Stop meta-work before it consumes the reserve needed to complete
qualification, restore/promote within authority, and report the actual result.

Compare total effort to accepted work: elapsed time, calls/retries, input,
reasoning/output, cached-input usage when available, and discarded candidates.
Include research and evaluation overhead when claiming overall savings. Keep
Codex account usage separate from API dollar estimates. If exact counts are
unavailable, say so; shorter visible output is not proof of fewer total tokens.
Use the installed `session-retro` skill for requested or warranted detailed
session-cost analysis, rather than creating another log parser.

## Astra-oriented instruction review

When Astra leads, use these concrete review questions:

- Did ambiguous stop/approval wording prevent already-authorized work?
- Was parallel work useful and explicitly scoped, or merely duplicated?
- Did verification expand beyond the change without a failure or requirement?
- Did verbosity or repeated history crowd out decisive evidence?
- Did a new instruction conflict with the user request or a sibling skill?

Use the selected repository's agent roles: Astra owns difficult diagnosis and
synthesis; economical workers gather bounded evidence; an independent model
or human reviews Astra's changes. Preserve configured effort by default and
measure any proposed change. Inspect the actual host's supported controls;
API examples are not authority to change Codex settings. These are adaptations
to documented Astra behavior, not a claim of locally measured model gains.

## Keep learning small and current

Retrieve only lessons matching the next task's scope and revisions. Keep a
compact rule and evidence pointer in active context; retain full permitted
traces in protected artifacts. At runtime/model upgrades or contradictory new
evidence, revalidate the applicable lesson, narrow it, or mark it superseded.
Replace duplicated instructions and archive stale lessons instead of growing
an unlimited playbook. Audit entrypoints and required references for conflicts.

Use `skill-creator` for validated edits and compatible installed learning
helpers for optional capture; their absence does not block closure. Separate a
companion skill only when another actual workflow needs this same lifecycle.
Research rationale and limitations are in
`docs/benchmarks/skill-improvement-research.md`; it need not be loaded for every
benchmark request.
