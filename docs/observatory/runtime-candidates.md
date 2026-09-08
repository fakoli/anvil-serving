# Bounded runtime candidates

Observatory distinguishes request-only evaluations from an explicit router runtime
candidate. A runtime candidate changes one or both declared tier settings
(`max_concurrency`, `max_output_tokens`), recreates the router through its managed
configuration transaction, compares the same fixed functional request, and
restores the exact preceding configuration bytes, installed revision, and
admission states. It never replaces a model, invents a profile, or promotes a
candidate.

The private controller resource uses `kind: experiment`,
`experiment_class: runtime_candidate`, and `candidate_values` containing the
allowed fields and proposed defaults. Its `config`, `tier`, `alias`, `compose`,
`service`, `env_file`, and optional topology/runtime fields bind the operation to
the resource owner. The alias must resolve to the declared non-replica tier.
Browser parameters can change only the declared candidate fields; paths,
endpoints, commands, credentials, and arbitrary prompts are never accepted.

The typed owner tool is `runtime_experiment`, with `status`, `preview`, `apply`,
and `restore` actions. Preview reads the installed/source revision and returns
both digests and the planned phases without generating requests or creating
state. Apply requires the exact baseline digest, confirmed human approval, and
one stable run ID derived from the accepted intent.

The owner holds the shared serving lock through these phases:

1. Send one fixed baseline probe through the authenticated router alias.
2. Install and independently verify the candidate revision through
   `router_configuration` and its managed router recreation.
3. Send the same fixed probe against the candidate.
4. Drain live router requests through the managed transaction, then restore the
   exact baseline bytes and managed router runtime.
5. Verify the restored digest and every captured admission state, including
   intentionally paused tiers.

Both probes request `READY`, use temperature zero, and retain the declared token
and timeout bounds. Correctness requires the expected visible answer and a
normal `stop` finish reason. A truncated or ambiguous response remains incomplete.
Evidence retains each result, the exact request parameters and candidate
settings, baseline/candidate digests, and restoration outcome. Response text,
credentials, endpoint URLs, and private configuration bytes stay out of the
public result. Single-request elapsed times describe these functional samples;
they are not TTFT percentiles or statistically qualified throughput comparisons.

Before any request or runtime change, the owner saves a private, fsynced record
under `operations/runtime-experiments` and a pending marker at
`operations/runtime-experiment-pending.json`. Records contain the exact private
baseline and the deployment declaration digest. While a marker exists,
unrelated managed serving mutations fail closed. A controller interruption can
leave a probe ambiguous, so the same run cannot execute again: recovery may only
restore. A separate confirmed recovery intent retains the original run ID.

Restoration refuses an unrelated source/runtime revision, changed configuration
bind, changed Compose declaration, or changed explicit runtime environment.
An owner-observed stopped or absent router can be recreated from the unchanged captured
deployment and baseline without a drain, but restoration succeeds only after its installed
digest and admissions are verified. Failed restoration retains the marker and
requires attention; it never silently unlocks conflicting work. A successful
restore does not convert a failed or incomplete experiment into a passing result.

Focused synthetic coverage is in
`tests/observability/test_runtime_experiment.py`. Installed catalog/grants,
browser recovery, and live configuration/probe/restoration acceptance require
separate retained deployment evidence.
