# Observatory facade contract (v1)

The existing `dashboard serve` remains read-only by default. Explicit private
`--observatory-config` enables the Observatory composition and its packaged
modular UI. No dependency is added to the Python runtime. The facade is not a
resource owner, scheduler, inference gateway, or arbitrary controller proxy.

## Browser contract

All API routes are relative to the configured base path, under
`api/observatory/v1/`. Success is `{ok:true,data:...}`; safe failure is
`{ok:false,error:{code,message}}`. JSON objects reject unknown/duplicate fields.
GET never mutates. Browser receives no upstream URLs/tokens/operator paths.

- `session` GET: `{authenticated,identity,role,operate,csrf_token,expires_at,
  base_path,build,fixture}`. Unauthenticated GET is 200 with authenticated false.
  POST `session` authenticates `{username,password}`; DELETE signs out with CSRF.
- `fleet` GET: `{hosts:[Host],serves:[Serve],coverage:{status,sources},observed_at}`.
  Host: `{id,display_name,platform,maintenance,controller:{status,version,
  observed_at,reason},telemetry:{status,observed_at},gpus:[GPU],resources:{...},
  profiles:[...],mode,ownership_status}`. GPU `{id,label,uuid,role,owners:[serve_id],
  memory_used:Metric,memory_total:Metric,utilization:Metric}`. Metric has value,
  last_known_value, status, unit, source_timestamp and optional reason.
  Serve: `{id,host_id,display_name,model,observed_model,engine,aliases:[],
  runtime_state,readiness,admission,gpu_ids:[],observed_at,metrics:{...},
  ownership_status}`. Unavailable data stays explicit, never fabricated zero.
- `hosts/{id}`, `serves/{id}` GET: the corresponding detail plus bounded additional
  diagnostics/configuration/evidence. Logical IDs only.
- `workloads` GET: preserve canonical workload envelope inside data without
  replacing owner/kind/state/phase/outcome/quality enums.
- `metrics?chart=...&host=...&serve=...&range=1h` GET: approved chart only;
  `{id,title,unit,source,window,status,reason,series:[{id,label,points:[[epoch,
  number_or_null]]}],grafana_url}`. Range options 15m,1h,6h,24h,7d. Max8series,
  1000points, gaps/null distinct from zero.
- `controls?resource=...` GET: `{resource_id,actions:[{id,label,supported,
  permitted,reason,effect,stop_semantics}],settings:[Setting],baseline_digest}`.
  Setting `{setting_id,label,value_type,unit,configured,observed,observed_status,
  constraints:{minimum,maximum,step,choices},support,effect,help}`.
- `drafts` POST `{resource_id,values:{setting_id:value},draft_id?}` returns
  `{id,version,resource_id,values,baseline_digest,candidate_digest,errors:[]}`.
  Writes private candidate only. Errors are `{field,message}`.
- `previews` POST `{resource_id,action_id,draft_id?,parameters?}` returns
  `{id,host_id,resource_id,action_id,label,baseline_digest,candidate_digest,
  policy_digest,expires_at_epoch_seconds,effect,diff:[{field,before,after}],
  affected_aliases,workload_impact,gpu_ids,stop_semantics,recovery,planned_steps}`.
- `operations` POST `{preview_id,intent_key}` returns Operation, 202. Durable key
  binds actor+resource+action+candidate. Repeated same content retrieves same
  operation; differing reuse rejected. Preview errors never dispatch.
- `operations` GET returns `{items:[Operation],truncated}`. Operation `{id,
  resource_id,host_id,action_id,label,actor,service_identity,submitted_at,
  updated_at,status,native_state,owner_operation_id,execution_outcome,
  verification:{status,message},recovery:{status,message},events:[{at,source,
  phase,message}],evidence_id}`. GET `operations/{id}` resumes without dispatch.
- `evidence/{id}` GET metadata only; opaque allowlisted IDs, never file paths.
- `settings` GET returns display defaults, integration health, permitted catalog
  and schema/build versions. Display-only preferences may live in localStorage.

## Boundaries

Application authentication is independent of legacy telemetry/workload bearer
credentials. Opaque Secure HttpOnly SameSite=Strict path-scoped session cookie;
CSRF tied to session plus exact configured HTTPS Origin on mutations. No proxy
identity trust by default. Explicit user/resource/action policy, independent
control credentials, current expected owner identity/version and owner conflict
protection are all required. No browser bearer token control path.

Adapter seam: `snapshot()`, `controls(resource_id)`, `preview(resource_id,
 action_id,values,parameters)`, `execute(preview,intent_key)`,
`reconcile(owner_reference)`, `verify(preview,result)`. Exact controller bindings
are closed and verified in the adapter. A journal correlates intents to existing
owner operation records; ambiguous delivery never silently retries.
