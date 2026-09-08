"""Closed private runtime-candidate bindings and public evidence projections."""

import copy

from .contracts import ObservatoryError, identifier


FIELDS = ("config", "tier", "alias", "compose", "service", "env_file", "container",
          "installed_config", "topology", "topology_overlay", "router_url", "drain_timeout",
          "probe_max_tokens", "probe_timeout_seconds")
LIMITS = {"max_concurrency": ("Tier admission", "requests", 4096),
          "max_output_tokens": ("Maximum output", "tokens", 1048576)}


def is_runtime(resource):
    return resource.get("kind") == "experiment" and resource.get("experiment_class") == "runtime_candidate"


def validate(resource):
    for key in ("config", "tier", "alias", "compose", "service", "env_file"):
        if type(resource.get(key)) is not str or not resource[key]:
            raise ValueError("runtime experiment requires an explicit " + key)
    identifier(resource["alias"])
    candidate(resource, {})
    for key, default, maximum in (("probe_max_tokens", 256, 4096), ("probe_timeout_seconds", 60, 300), ("drain_timeout", 120, 3600)):
        value = resource.get(key, default)
        if type(value) is not int or not 1 <= value <= maximum:
            raise ValueError("runtime experiment bound is invalid")


def candidate(resource, parameters):
    declared = resource.get("candidate_values")
    if type(declared) is not dict or not declared or set(declared) - set(LIMITS):
        raise ValueError("runtime experiment requires closed candidate_values")
    if set(parameters) - set(declared):
        raise ObservatoryError("invalid_parameters", "Only declared candidate fields may change.")
    result = {**declared, **parameters}
    for key, value in result.items():
        if type(value) is not int or not 1 <= value <= LIMITS[key][2]:
            raise ObservatoryError("invalid_parameters", "A candidate setting is outside its declared bound.")
    return result


def settings(resource):
    return [{"setting_id": key, "label": LIMITS[key][0], "unit": LIMITS[key][1], "value_type": "integer",
        "configured": value, "support": "supported", "constraints": {"minimum": 1, "maximum": LIMITS[key][2], "step": 1},
        "help": "Proposed runtime candidate; the owner restores the exact prior revision after comparison."}
        for key, value in resource["candidate_values"].items()]


def arguments(resource, action, parameters):
    result = {key: resource[key] for key in FIELDS if key in resource}
    recovering = action == "operation.recover"
    result.update(action="restore" if recovering else "apply",
                  values=candidate(resource, {} if recovering else parameters))
    if recovering:
        if set(parameters) != {"run_id"}:
            raise ObservatoryError("invalid_parameters", "Restore one exact retained experiment run.")
        result["run_id"] = identifier(parameters["run_id"])
    return result


def actions(resource):
    return {
        "experiment.start": {"label": "Evaluate runtime candidate", "tool": "runtime_experiment",
            "effect": "Compare one explicit router revision through its fixed alias, then restore the exact baseline.",
            "human_gate": True, "verify_tool": "runtime_experiment", "parameters": frozenset(resource["candidate_values"]),
            "workload_impact": "Two bounded probes and router recreation; model containers are unchanged.",
            "recovery": "Restore the retained exact revision through the owner; interrupted probes never replay."},
        "operation.recover": {"label": "Restore runtime experiment", "tool": "runtime_experiment",
            "effect": "Restore the retained baseline and admission states without repeating either probe.",
            "human_gate": True, "verify_tool": "runtime_experiment", "parameters": frozenset({"run_id"}),
            "workload_impact": "Owner restoration only; no probe or model promotion."},
    }


def evidence(payload):
    return {key: copy.deepcopy(payload.get(key)) for key in ("kind", "run_id", "state", "phase", "baseline_sha256",
        "candidate_sha256", "alias", "parameters", "baseline_result", "candidate_result", "recovery",
        "correctness", "failure", "updated_at")}


def result(payload, intent_key, *, recovering=False):
    restored = payload.get("recovery", {}).get("status") == "succeeded"
    state = payload.get("state", "unknown")
    outcome = ("pending" if state == "running" else "succeeded" if
        (restored and (recovering or payload.get("correctness") == "passed")) else "failed")
    return {"ok": outcome != "failed", "owner_operation_id": intent_key, "native_state": state,
        "execution_outcome": outcome, "recovery": copy.deepcopy(payload.get("recovery", {})),
        "evidence": evidence(payload)}
