# Retain and enforce paired SWE instance image identities

Status: implemented; independent review and live paired replay pending.

The pinned mini-SWE-agent and official SWE-bench grader derive prebuilt instance
image references with a mutable `latest` tag. The managed SWE result retains
dataset and harness identities but does not retain task/grader image IDs. The
immutable-only host image inspector cannot resolve these upstream tag references.

During a qualification campaign, narrow read-only Docker image inspection was
needed to capture the five selected instance image IDs and repository digests.
The private campaign retains these observations. No image was removed or retagged.
Pinned mini-SWE-agent uses ordinary Docker run (pull only when missing); the
official grader checks the local image and pulls only after ImageNotFound.

Acceptance: retain selected instance image IDs/digests at task and grader stage
boundaries; accept an explicit paired identity map; reject missing or changed
identities before paired execution; preserve cached images. Use existing managed
SWE and Docker seams, with no general inventory framework. Regression tests must
prove an identity mismatch prevents model execution and grading. State the limits
of boundary observations rather than claim protection against arbitrary concurrent
Docker retagging. Public receipts must omit operator network identities and paths.

Related observation: the job-spec timeout is not forwarded into the SWE adapter's
profile stage timeout. A campaign's shorter review checkpoint is not a hard runtime
deadline. Preserve matched native profiles and document actual stage limits until
timeout propagation is separately implemented and tested.

Implementation uses the existing job parameters and SWE plan: an optional exact
`paired_image_ids` map enables three retained observation boundaries, task
`--pull never`, and a digest-bound grader policy. The guarded grader creates by
immutable ID and refuses pulls. Legacy unpaired runs remain explicitly uncovered.
Focused regressions cover pre-agent, pre-grader, and post-grader mismatches,
missing images, policy replacement, immutable create arguments, and guard cleanup.
