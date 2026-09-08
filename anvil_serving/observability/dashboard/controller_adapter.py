"""Typed Observatory adapter for the Anvil Serving controller.

The private adapter configuration is the authority for resource bindings.  Browser
callers only ever select the stable resource and action identifiers exposed here;
they cannot supply controller URLs, paths, tool names, or arbitrary arguments.
"""

from __future__ import annotations

from collections.abc import Mapping
import copy
import json
import re
import time
from typing import Any

from ...transports import ControllerTransport, Operation, TransportError
from .contracts import ObservatoryError, digest, identifier, validate_values


_KINDS = frozenset({"serve", "profile", "configuration", "recipe", "experiment"})
_SAFE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,191}\Z")
_TOOLS = frozenset(
    {
        "serves_status", "serves_manage", "serves_probe", "serves_profile", "serves_logs",
        "router_transition", "router_configuration", "recipe_settings",
        "recipe_containers", "benchmark_job_preflight", "benchmark_job_submit",
        "benchmark_job_status",
    }
)


def _mapping(value: object, message: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(message)
    return dict(value)


def _safe(value: object, field: str) -> str:
    if type(value) is not str or not _SAFE.fullmatch(value):
        raise ValueError(f"{field} must be a stable identifier")
    return value


def _public_error(message: str = "The resource owner is unavailable.") -> ObservatoryError:
    return ObservatoryError("owner_unavailable", message, 503)


class ControllerAdapter:
    """Bind closed Observatory controls to one authenticated owner controller."""

    def __init__(
        self,
        config: Mapping[str, Any],
        environment: Mapping[str, str],
        *,
        transport_factory=ControllerTransport,
        clock=time.time,
    ) -> None:
        root = _mapping(config, "controller adapter config must be an object")
        controller = _mapping(root.get("controller", root), "controller must be an object")
        endpoint = controller.get("url", controller.get("endpoint"))
        token_env = controller.get("token_env")
        expected_node = controller.get("expected_node")
        expected_catalog_digest = controller.get("expected_catalog_digest")
        if expected_catalog_digest is not None and (
            type(expected_catalog_digest) is not str
            or re.fullmatch(r"[a-f0-9]{64}", expected_catalog_digest) is None
        ):
            raise ValueError("expected_catalog_digest must be a SHA-256 digest")
        self._expected_catalog_digest = expected_catalog_digest
        self._observed_catalog_digest: str | None = None
        self._context = {
            key: _safe(controller.get(key), key)
            for key in ("topology", "execution_host", "execution_runtime")
        }
        if type(endpoint) is not str or type(token_env) is not str:
            raise ValueError("controller url and token_env are required")
        if expected_node is not None:
            expected_node = _safe(expected_node, "expected_node")
        raw_resources = root.get("resources", controller.get("resources", []))
        if isinstance(raw_resources, Mapping):
            raw_resources = [dict(item, id=key) for key, item in raw_resources.items()]
        if not isinstance(raw_resources, list) or len(raw_resources) > 128:
            raise ValueError("resources must be a bounded list or object")
        self._resources: dict[str, dict[str, Any]] = {}
        for raw in raw_resources:
            item = _mapping(raw, "resource entries must be objects")
            resource_id = _safe(item.get("id"), "resource id")
            kind = item.get("kind")
            if kind not in _KINDS or resource_id in self._resources:
                raise ValueError("resource kind or identity is invalid")
            item["id"] = resource_id
            item["host_id"] = _safe(item.get("host_id", expected_node), "host_id")
            item["label"] = str(item.get("label", resource_id))[:128]
            self._validate_resource(item)
            self._resources[resource_id] = item
        self._transport = transport_factory(
            endpoint,
            auth_env=token_env,
            allowed_operations=sorted(_TOOLS),
            environment=environment,
            expected_node=expected_node,
        )
        self._clock = clock
        self._catalog: frozenset[str] | None = None
        self._catalog_error = False
        self._catalog_checked_at = float("-inf")

    @staticmethod
    def _validate_resource(item: dict[str, Any]) -> None:
        kind = item["kind"]
        if kind in {"serve", "profile"}:
            if type(item.get("manifest")) is not str:
                raise ValueError(f"{kind} resource requires manifest")
        if kind == "serve":
            _safe(item.get("serve"), "serve")
            if item.get("tier") is not None:
                _safe(item["tier"], "tier")
            for key, default, maximum in (
                ("probe_timeout_seconds", 60, 300), ("probe_max_tokens", 256, 4096)
            ):
                value = item.get(key, default)
                if type(value) is not int or not 1 <= value <= maximum:
                    raise ValueError(f"{key} is outside its supported range")
        elif kind == "profile":
            _safe(item.get("profile"), "profile")
        elif kind == "configuration":
            if type(item.get("config")) is not str:
                raise ValueError("configuration resource requires config")
            _safe(item.get("tier"), "tier")
            aliases = item.get("aliases")
            if not isinstance(aliases, list) or not aliases or any(
                type(alias) is not str or not _SAFE.fullmatch(alias) for alias in aliases
            ):
                raise ValueError("configuration resource requires every affected alias")
        elif kind == "recipe":
            if type(item.get("registry")) is not str:
                raise ValueError("recipe resource requires registry")
            if type(item.get("model")) is not str or not item["model"] or len(item["model"]) > 512:
                raise ValueError("recipe model selector is invalid")
        elif kind == "experiment":
            if item.get("suite") not in {"context", "agentic", "swe"}:
                raise ValueError("experiment suite is invalid")
            if not isinstance(item.get("spec"), Mapping):
                raise ValueError("experiment resource requires a spec object")

    def _tools(self) -> frozenset[str]:
        now = self._clock()
        if self._catalog is not None and now - self._catalog_checked_at < 30:
            return self._catalog
        try:
            if self._catalog is not None and hasattr(self._transport, "reset_identity_verification"):
                self._transport.reset_identity_verification()
            declarations = self._transport.tool_catalog()
            relevant = sorted(
                (item for item in declarations if item.get("name") in _TOOLS),
                key=lambda item: item["name"],
            )
            self._observed_catalog_digest = digest(relevant)
            if (self._expected_catalog_digest is not None
                    and self._observed_catalog_digest != self._expected_catalog_digest):
                raise ValueError("controller catalog digest mismatch")
            self._catalog = frozenset(item["name"] for item in relevant)
            self._catalog_error = False
        except (TransportError, ValueError):
            self._catalog = frozenset()
            self._catalog_error = True
        self._catalog_checked_at = now
        return self._catalog

    def snapshot(self) -> dict[str, Any]:
        tools = self._tools()
        resources = [self._summary(item, tools) for item in self._resources.values()]
        serves = []
        host_modes: dict[str, str] = {}
        gpu_owners: dict[tuple[str, str], list[str]] = {}
        for resource in self._resources.values():
            if resource["kind"] != "serve" or "serves_status" not in tools:
                continue
            try:
                payload = self._call("serves_status", {"manifest": resource["manifest"], "names": [resource["serve"]]})
                row = next((item for item in payload.get("serves", []) if item.get("name") == resource["serve"]), {})
                running = row.get("running")
                health = row.get("health_status")
                healthy = type(health) is int and 200 <= health < 400
                operating_mode = payload.get("operating_mode")
                mode = operating_mode.get("mode") if isinstance(operating_mode, Mapping) else None
                if type(mode) is str:
                    host_modes[resource["host_id"]] = mode
                reservation_payload = payload.get("reservations")
                reservations = (reservation_payload.get("gpu_roles", [])
                                if isinstance(reservation_payload, Mapping) else [])
                committed = any(
                    reservation.get("serve") == resource["serve"] and reservation.get("committed") is True
                    for role in reservations if isinstance(role, Mapping)
                    for reservation in role.get("reservations", []) if isinstance(reservation, Mapping)
                )
                if committed:
                    for gpu_id in resource.get("gpu_ids", []):
                        gpu_owners.setdefault((resource["host_id"], gpu_id), []).append(resource["id"])
                admission = "unknown"
                tier_row = None
                if resource.get("tier") and "router_transition" in tools:
                    try:
                        transition = self._call("router_transition", {"action": "status", "tier": resource["tier"]})
                        tier_row = next((item for item in transition.get("tiers", [])
                                         if isinstance(item, Mapping) and item.get("tier_id") == resource["tier"]), None)
                        if isinstance(tier_row, Mapping) and type(tier_row.get("state")) is str:
                            admission = tier_row["state"]
                    except ObservatoryError:
                        pass
                observed_model = (
                    tier_row.get("observed_model")
                    if isinstance(tier_row, Mapping)
                    and type(tier_row.get("observed_model")) is str
                    and len(tier_row["observed_model"]) <= 512
                    else None
                )
                ownership = ("conflict" if row.get("compose_ownership_mismatch") or row.get("port_conflicts")
                             else "owned" if row else "unknown")
                serves.append({
                    "id": resource["id"], "host_id": resource["host_id"],
                    "display_name": resource["label"], "model": row.get("model"),
                    "observed_model": observed_model, "engine": row.get("engine"),
                    "aliases": list(resource.get("aliases", [])),
                    "runtime_state": "running" if running is True else ("stopped" if running is False else "unknown"),
                    "readiness": "ready" if healthy else ("not_ready" if row.get("running") is True else "unknown"),
                    "admission": admission, "gpu_ids": list(resource.get("gpu_ids", [])),
                    "observed_at": self._clock(), "metrics": {}, "ownership_status": ownership,
                })
            except ObservatoryError:
                serves.append({
                    "id": resource["id"], "host_id": resource["host_id"], "display_name": resource["label"],
                    "model": None, "observed_model": None, "engine": None,
                    "aliases": list(resource.get("aliases", [])), "runtime_state": "unknown",
                    "readiness": "unknown", "admission": "unknown", "gpu_ids": list(resource.get("gpu_ids", [])),
                    "observed_at": self._clock(), "metrics": {}, "ownership_status": "unavailable",
                })
        hosts = []
        for host_id in dict.fromkeys(item["host_id"] for item in self._resources.values()):
            gpu_ids = dict.fromkeys(
                gpu_id for resource in self._resources.values() if resource["host_id"] == host_id
                for gpu_id in resource.get("gpu_ids", [])
            )
            hosts.append({
                "id": host_id, "display_name": host_id, "platform": "unknown", "maintenance": None,
                "controller": {"status": "unavailable" if self._catalog_error else "available",
                               "version": self._observed_catalog_digest, "observed_at": self._clock(),
                               "expected_version": self._expected_catalog_digest,
                               "reason": "catalog unavailable" if self._catalog_error else None},
                "telemetry": {"status": "unavailable", "observed_at": self._clock()},
                "gpus": [{"id": gpu_id, "owners": gpu_owners.get((host_id, gpu_id), []),
                           "ownership_status": "observed" if (host_id, gpu_id) in gpu_owners else "unknown"}
                          for gpu_id in gpu_ids],
                "resources": {}, "profiles": [], "mode": host_modes.get(host_id, "unknown"),
                "ownership_status": "observed" if host_id in host_modes else "unavailable",
            })
        return {
            "status": "unavailable" if self._catalog_error else "available",
            "resources": resources, "hosts": hosts, "serves": serves,
            "observed_at_epoch_seconds": self._clock(),
        }

    def _summary(self, resource: dict[str, Any], tools: frozenset[str]) -> dict[str, Any]:
        actions = self._action_specs(resource)
        return {
            "id": resource["id"], "label": resource["label"],
            "host_id": resource["host_id"], "kind": resource["kind"],
            "status": "available" if any(self._supported(a, tools) for a in actions.values()) else "unavailable",
        }

    @staticmethod
    def _supported(spec: Mapping[str, Any], tools: frozenset[str]) -> bool:
        preview_tool = spec.get("preview_tool", spec["tool"])
        required = spec.get("required_tools", ())
        return (
            spec.get("postcondition_supported", True) is True
            and spec["tool"] in tools
            and (preview_tool is None or preview_tool in tools)
            and all(tool in tools for tool in required)
        )

    def controls(self, resource_id: str | None = None) -> dict[str, Any]:
        tools = self._tools()
        if resource_id is None:
            return {"resources": [self._summary(r, tools) for r in self._resources.values()]}
        resource = self._resource(resource_id)
        settings = self._settings(resource)
        specs = self._action_specs(resource)
        actions = []
        for action_id, spec in specs.items():
            supported = self._supported(spec, tools)
            actions.append({
                "id": action_id,
                "label": spec["label"],
                "supported": supported,
                "permitted": supported,
                "reason": (
                    None
                    if supported
                    else "Declare the profile mode, members, GPU owners, and admission postconditions."
                    if spec.get("postcondition_supported") is False
                    else "The owner does not declare this operation."
                ),
                "effect": spec["effect"],
                "stop_semantics": spec.get("stop_semantics"),
            })
        baseline = {"resource": resource["id"], "settings": settings, "tools": sorted(tools & {s["tool"] for s in specs.values()})}
        result = {"resource_id": resource["id"], "actions": actions, "settings": settings,
                  "baseline_digest": digest(baseline)}
        if resource["kind"] == "experiment":
            result["experiment_settings"] = copy.deepcopy(resource.get("experiment_settings", []))
            result["experiment_limit"] = int(resource.get("experiment_limit", 1))
        return result

    def preview(
        self,
        resource_id: str,
        action_id: str,
        values: Mapping[str, Any] | None = None,
        parameters: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        resource = self._resource(resource_id)
        specs = self._action_specs(resource)
        if action_id not in specs:
            raise ObservatoryError("unsupported_action", "Select a declared action.")
        spec = specs[action_id]
        if not self._supported(spec, self._tools()):
            raise _public_error("The resource owner does not support this action.")
        values = {} if values is None else _mapping(values, "values must be an object")
        parameters = {} if parameters is None else _mapping(parameters, "parameters must be an object")
        allowed_parameters = spec.get("parameters", frozenset())
        if set(parameters) - set(allowed_parameters):
            raise ObservatoryError("invalid_parameters", "The preview contains unsupported parameters.")
        settings = self._settings(resource)
        candidate_values = validate_values(values, settings) if settings else {}
        arguments = self._arguments(resource, action_id, candidate_values, parameters)
        preview_args = dict(arguments)
        if resource["kind"] in {"configuration", "recipe"}:
            preview_args["action"] = "preview"
        if spec.get("gated", True):
            preview_args.update({"dry_run": True, "confirm": False})
        preview_tool = spec.get("preview_tool", spec["tool"])
        if preview_tool is None:
            owner_preview = {"plan": [{"kind": "bounded_probe", "target": resource["id"]}]}
        else:
            if preview_tool == "benchmark_job_preflight":
                preview_args.pop("confirm", None)
                preview_args.pop("detach", None)
            owner_preview = self._call(preview_tool, preview_args)
        if resource["kind"] in {"configuration", "recipe"} and owner_preview.get("baseline_sha256"):
            arguments["expected_baseline_sha256"] = owner_preview["baseline_sha256"]
        baseline_state = self._baseline(resource)
        baseline_digest = digest(baseline_state)
        candidate_digest = digest({"baseline": baseline_digest, "action": action_id,
                                   "values": candidate_values, "parameters": parameters,
                                   "arguments": arguments})
        before = {s["setting_id"]: s.get("configured") for s in settings}
        result = {
            "host_id": resource["host_id"], "resource_id": resource["id"],
            "action_id": action_id, "label": spec["label"],
            "baseline_digest": baseline_digest, "candidate_digest": candidate_digest,
            "effect": spec["effect"],
            "diff": [{"field": key, "before": before.get(key), "after": value}
                     for key, value in candidate_values.items() if before.get(key) != value],
            "affected_aliases": list(resource.get("aliases", [])),
            "workload_impact": spec.get("workload_impact", "none"),
            "gpu_ids": list(resource.get("gpu_ids", [])),
            "stop_semantics": spec.get("stop_semantics"),
            "recovery": spec.get("recovery", "Reconcile with the owner operation record."),
            "planned_steps": self._planned_steps(owner_preview),
            "binding": {"tool": spec["tool"], "arguments": arguments,
                        "verify_tool": spec.get("verify_tool"),
                        "verify_arguments": self._verify_arguments(resource, action_id, arguments),
                        "owner_candidate_sha256": owner_preview.get("candidate_sha256")},
        }
        return result

    def execute(self, preview: Mapping[str, Any], intent_key: str) -> dict[str, Any]:
        binding = self._binding(preview)
        arguments = dict(binding["arguments"])
        action_id = preview.get("action_id")
        spec = self._action_specs(self._resource(preview.get("resource_id")))[action_id]
        if spec.get("gated", True):
            arguments.update({"dry_run": False, "confirm": True})
        if spec.get("human_gate"):
            arguments["human_approved"] = True
        try:
            result = self._transport.execute(
                Operation(binding["tool"], arguments, tool_name=binding["tool"]),
                idempotency_key=intent_key,
                idempotency_context=self._context,
            ).data
        except TransportError as exc:
            response = {"ok": False, "owner_operation_id": intent_key,
                    "native_state": exc.execution_state or "unknown",
                    "execution_outcome": "unknown" if exc.execution_state == "partial_result" else "failed"}
            error = exc.details.get("controller_error", {})
            response.update(self._failure_evidence(error, preview))
            return response
        payload = self._payload(result)
        if not payload:
            return {"ok": False, "owner_operation_id": intent_key,
                    "native_state": "unknown", "execution_outcome": "unknown",
                    "verification": {"status": "unavailable", "message": "The owner returned no typed result."}}
        if action_id == "serve.probe" and payload.get("passed") is not True:
            return {"ok": False, "owner_operation_id": intent_key,
                    "native_state": "completed", "execution_outcome": "failed",
                    "verification": copy.deepcopy(payload.get("verification")),
                    "evidence": self._evidence(payload)}
        state = self._native_state(payload)
        outcome = ("pending" if state in {"queued", "running", "cancelling"}
                   else "failed" if state in {"failed", "cancelled"} else "succeeded")
        response = {"ok": outcome != "failed", "owner_operation_id": intent_key,
                    "native_state": state, "execution_outcome": outcome,
                    "evidence": self._evidence(payload)}
        if isinstance(payload.get("recovery"), Mapping):
            response["recovery"] = copy.deepcopy(dict(payload["recovery"]))
        return response

    def reconcile(self, preview: Mapping[str, Any], intent_key: str) -> dict[str, Any]:
        self._binding(preview)
        try:
            record = self._transport.operation_status(intent_key).data
        except TransportError:
            return {"ok": False, "owner_operation_id": intent_key, "native_state": "unknown",
                    "execution_outcome": "unknown"}
        status = record.get("status", "unknown")
        if status != "succeeded":
            response = {"ok": False, "owner_operation_id": intent_key, "native_state": status,
                    "execution_outcome": {"running": "pending", "failed": "failed",
                                          "expired": "unknown"}.get(status, "unknown")}
            stored = record.get("response", {})
            if status == "failed" and isinstance(stored, Mapping):
                response.update(self._failure_evidence(stored.get("error", {}), preview))
            return response
        if preview.get("action_id") == "experiment.start":
            binding = self._binding(preview)
            try:
                spec = json.loads(binding["arguments"]["spec_json"])
                job_payload = self._call("benchmark_job_status", {
                    "suite": binding["arguments"]["suite"], "run_id": spec["run_id"]
                })
            except (KeyError, TypeError, ValueError, ObservatoryError):
                return {"ok": False, "owner_operation_id": intent_key, "native_state": "unknown",
                        "execution_outcome": "unknown"}
            state = self._native_state(job_payload)
            outcome = ({"queued": "pending", "running": "pending", "cancelling": "pending",
                        "completed": "succeeded", "failed": "failed", "cancelled": "failed"}
                       .get(state, "unknown"))
            return {"ok": outcome not in {"failed", "unknown"}, "owner_operation_id": intent_key,
                    "native_state": state, "execution_outcome": outcome,
                    "evidence": self._evidence(job_payload)}
        response = record.get("response")
        payload = self._payload(response) if isinstance(response, Mapping) else {}
        return {"ok": True, "owner_operation_id": intent_key, "native_state": "succeeded",
                "execution_outcome": "succeeded", "evidence": self._evidence(payload)}

    def verify(self, preview: Mapping[str, Any], result: Mapping[str, Any]) -> dict[str, str]:
        binding = self._binding(preview)
        if result.get("execution_outcome") == "failed":
            return {"status": "failed", "message": "The owner reported a failed operation."}
        if preview.get("action_id") == "serve.probe":
            evidence = result.get("evidence")
            verification = evidence.get("verification") if isinstance(evidence, Mapping) else None
            if isinstance(verification, Mapping) and verification.get("status") in {"passed", "failed"}:
                return {"status": verification["status"],
                        "message": str(verification.get("message", "The bounded probe completed."))[:256]}
            return {"status": "unavailable", "message": "The probe returned no structured correctness result."}
        tool = binding.get("verify_tool")
        if not tool or tool not in self._tools():
            return {"status": "unavailable", "message": "No owner verification is declared."}
        try:
            verified = self._call(tool, binding.get("verify_arguments", {}))
        except ObservatoryError:
            return {"status": "failed", "message": "The owner postcondition check failed."}
        expected = binding.get("owner_candidate_sha256")
        if expected and verified.get("baseline_sha256") != expected:
            return {"status": "failed", "message": "The owner state does not match the reviewed candidate."}
        action = preview.get("action_id")
        resource = self._resource(preview.get("resource_id"))
        if action in {"serve.start", "serve.restart", "serve.stop"}:
            row = next((item for item in verified.get("serves", [])
                        if isinstance(item, Mapping) and item.get("name") == resource.get("serve")), None)
            if not isinstance(row, Mapping):
                return {"status": "unavailable", "message": "The owner did not return the serve postcondition."}
            if action == "serve.stop":
                passed = row.get("running") is False
            else:
                health = row.get("health_status")
                passed = row.get("running") is True and type(health) is int and 200 <= health < 400
            return {"status": "passed" if passed else "failed",
                    "message": "The declared serve postcondition passed." if passed else "The declared serve postcondition failed."}
        if action in {"tier.quiesce", "tier.drain", "tier.readmit"}:
            row = next((item for item in verified.get("tiers", [])
                        if isinstance(item, Mapping) and item.get("tier_id") == resource.get("tier")), None)
            if not isinstance(row, Mapping) or type(row.get("state")) is not str:
                return {"status": "unavailable", "message": "The owner did not return the admission postcondition."}
            expected_state = "admitting" if action == "tier.readmit" else "quiesced"
            passed = row.get("state") == expected_state
            if action == "tier.drain" and row.get("active_requests") is not None:
                passed = passed and row.get("active_requests") == 0
            return {"status": "passed" if passed else "failed",
                    "message": "The declared admission postcondition passed." if passed else "The declared admission postcondition failed."}
        if action == "profile.apply":
            expectation = self._profile_expectation(resource)
            if expectation is None:
                return {
                    "status": "unavailable",
                    "message": "The profile has no declared structured postcondition.",
                }
            if verified.get("profile") != resource["profile"]:
                return {
                    "status": "failed",
                    "message": "The owner did not resolve the declared profile.",
                }
            members = [*expectation["active_serves"], *expectation["inactive_serves"]]
            try:
                status = self._call(
                    "serves_status", {"manifest": resource["manifest"], "names": members}
                )
            except ObservatoryError:
                return {
                    "status": "failed",
                    "message": "The owner could not verify the profile members.",
                }
            rows = {
                row.get("name"): row
                for row in status.get("serves", [])
                if isinstance(row, Mapping) and type(row.get("name")) is str
            }
            active_ok = all(
                name in rows
                and rows[name].get("running") is True
                and type(rows[name].get("health_status")) is int
                and 200 <= rows[name]["health_status"] < 400
                for name in expectation["active_serves"]
            )
            inactive_ok = all(
                name in rows and rows[name].get("running") is False
                for name in expectation["inactive_serves"]
            )
            mode = status.get("operating_mode")
            mode_ok = (
                isinstance(mode, Mapping) and mode.get("mode") == expectation["mode"]
            )
            ownership = (
                mode.get("gpu_ownership", []) if isinstance(mode, Mapping) else []
            )
            actual_owners = {
                row.get("gpu_role"): sorted(row.get("owners", []))
                for row in ownership
                if isinstance(row, Mapping)
                and type(row.get("gpu_role")) is str
                and isinstance(row.get("owners"), list)
                and all(type(owner) is str for owner in row["owners"])
            }
            owners_ok = actual_owners == expectation["gpu_owners"]
            admissions_ok = True
            try:
                for tier_id, expected_state in expectation["admissions"].items():
                    transition = self._call(
                        "router_transition", {"action": "status", "tier": tier_id}
                    )
                    tier = next((
                        row
                        for row in transition.get("tiers", [])
                        if isinstance(row, Mapping) and row.get("tier_id") == tier_id
                    ), None)
                    admissions_ok = admissions_ok and (
                        isinstance(tier, Mapping)
                        and tier.get("state") == expected_state
                        and (expected_state != "admitting" or tier.get("ready") is True)
                    )
            except ObservatoryError:
                admissions_ok = False
            passed = active_ok and inactive_ok and mode_ok and owners_ok and admissions_ok
            return {
                "status": "passed" if passed else "failed",
                "message": (
                    "The declared profile members, mode, GPU owners, and admission passed."
                    if passed
                    else "The owner state does not match the declared profile postcondition."
                ),
            }
        if action == "experiment.start":
            return {"status": "unavailable", "message": "Evaluation completion alone does not establish a passing quality result."}
        return {"status": "passed", "message": "The owner postcondition check passed."}

    def _resource(self, resource_id: object) -> dict[str, Any]:
        try:
            return self._resources[identifier(resource_id)]
        except KeyError:
            raise ObservatoryError("unknown_resource", "Select a declared resource.") from None

    @staticmethod
    def _action_specs(resource: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
        kind = resource["kind"]
        if kind == "serve":
            result = {
                "serve.start": {"label": "Start", "tool": "serves_manage", "effect": "Starts the declared serve.", "verify_tool": "serves_status"},
                "serve.stop": {
                    "label": "Stop and remove", "tool": "serves_manage",
                    "effect": "Stops and removes the declared container; its container logs are not retained.",
                    "stop_semantics": "stop_remove", "verify_tool": "serves_status",
                    "recovery": "Start the declared serve again; removed container logs cannot be recovered.",
                },
                "serve.restart": {"label": "Restart", "tool": "serves_manage", "effect": "Recreates the declared serve.", "verify_tool": "serves_status"},
                "serve.probe": {"label": "Probe", "tool": "serves_probe", "effect": "Runs the declared bounded readiness probe.", "gated": False, "preview_tool": None},
            }
            if resource.get("tier"):
                for action in ("quiesce", "drain", "readmit"):
                    result["tier." + action] = {"label": action.title(), "tool": "router_transition",
                                      "effect": f"Requests owner-managed tier {action}.", "verify_tool": "router_transition",
                                      "stop_semantics": "drain" if action == "drain" else None}
            return result
        if kind == "profile":
            return {"profile.apply": {"label": "Apply profile", "tool": "serves_profile",
                              "effect": "Applies the declared managed serve profile.", "human_gate": True,
                              "verify_tool": "serves_profile",
                              "required_tools": ("serves_status", "router_transition"),
                              "postcondition_supported": ControllerAdapter._profile_expectation(resource) is not None,
                              "workload_impact": "declared profile transition"}}
        if kind == "configuration":
            return {"configuration.apply": {"label": "Apply configuration", "tool": "router_configuration",
                              "effect": "Installs bounded tier settings through the canonical router transaction.",
                              "human_gate": True, "verify_tool": "router_configuration", "workload_impact": "tier drain and readmission"}}
        if kind == "recipe":
            return {"configuration.apply": {"label": "Apply recipe settings", "tool": "recipe_settings",
                              "effect": "Updates bounded runtime settings in the managed recipe registry.",
                              "human_gate": True, "verify_tool": "recipe_settings"}}
        return {"experiment.start": {"label": "Run experiment", "tool": "benchmark_job_submit",
                          "effect": "Submits the declared bounded durable evaluation job.",
                          "gated": False, "preview_tool": "benchmark_job_preflight",
                          "parameters": frozenset({"run_id"})}}

    @staticmethod
    def _profile_expectation(resource: Mapping[str, Any]) -> dict[str, Any] | None:
        mode = resource.get("mode")
        active = resource.get("active_serves")
        inactive = resource.get("inactive_serves", [])
        admissions = resource.get("admissions")
        gpu_owners = resource.get("gpu_owners")
        if (
            mode not in {"split", "dual-gpu-exclusive"}
            or not isinstance(active, list)
            or not active
            or not isinstance(inactive, list)
            or not isinstance(admissions, Mapping)
            or not admissions
            or not isinstance(gpu_owners, Mapping)
            or not gpu_owners
        ):
            return None
        if (
            any(type(value) is not str or _SAFE.fullmatch(value) is None for value in active + inactive)
            or len(set(active + inactive)) != len(active + inactive)
        ):
            return None
        normalized_admissions = {}
        for tier_id, state in admissions.items():
            if (
                type(tier_id) is not str
                or _SAFE.fullmatch(tier_id) is None
                or state not in {"admitting", "quiesced"}
            ):
                return None
            normalized_admissions[tier_id] = state
        normalized_owners = {}
        for gpu_role, owners in gpu_owners.items():
            if (
                type(gpu_role) is not str
                or _SAFE.fullmatch(gpu_role) is None
                or not isinstance(owners, list)
                or any(
                    type(owner) is not str or _SAFE.fullmatch(owner) is None
                    for owner in owners
                )
                or len(set(owners)) != len(owners)
            ):
                return None
            normalized_owners[gpu_role] = sorted(owners)
        return {
            "mode": mode,
            "active_serves": list(active),
            "inactive_serves": list(inactive),
            "admissions": normalized_admissions,
            "gpu_owners": normalized_owners,
        }

    @staticmethod
    def _arguments(resource: Mapping[str, Any], action: str, values: Mapping[str, Any], parameters: Mapping[str, Any]) -> dict[str, Any]:
        kind = resource["kind"]
        if kind == "serve":
            if action in {"serve.start", "serve.stop", "serve.restart"}:
                result = {"action": "down" if action == "serve.stop" else "up", "manifest": resource["manifest"], "names": [resource["serve"]]}
                if action == "serve.restart":
                    result["recreate"] = True
                return result
            if action == "serve.probe":
                return {
                    "manifest": resource["manifest"], "name": resource["serve"],
                    "timeout_seconds": int(resource.get("probe_timeout_seconds", 60)),
                    "max_tokens": int(resource.get("probe_max_tokens", 256)),
                }
            transition = action.removeprefix("tier.")
            return {"action": transition, "tier": resource["tier"], **({"timeout": int(resource["drain_timeout"])} if transition == "drain" and resource.get("drain_timeout") else {})}
        if kind == "profile":
            result = {"action": "apply", "manifest": resource["manifest"], "profile": resource["profile"]}
            for key in ("profiles", "config"):
                if resource.get(key):
                    result[key] = resource[key]
            return result
        if kind == "configuration":
            result = {"action": "apply", "config": resource["config"], "tier": resource["tier"], "values": dict(values)}
            for key in ("topology", "topology_overlay", "router_url", "container",
                        "installed_config", "compose", "service", "env_file", "drain_timeout"):
                if resource.get(key) is not None:
                    result[key] = resource[key]
            return result
        if kind == "recipe":
            return {"action": "apply", "registry": resource["registry"], "model": resource["model"], "values": dict(values)}
        spec = copy.deepcopy(dict(resource["spec"]))
        if "run_id" in parameters:
            spec["run_id"] = _safe(parameters["run_id"], "run_id")
        return {"suite": resource["suite"], "spec_json": json.dumps(spec, sort_keys=True, separators=(",", ":")), "detach": True, "confirm": True}

    def _baseline(self, resource: Mapping[str, Any]) -> Any:
        if resource["kind"] in {"serve", "profile"}:
            payload = self._call(
                "serves_status",
                {"manifest": resource["manifest"],
                 "names": [resource.get("serve")] if resource.get("serve") else []},
            )
            baseline = self._stable_serve_baseline(payload)
            if resource.get("tier") and "router_transition" in self._tools():
                transition = self._call(
                    "router_transition", {"action": "status", "tier": resource["tier"]}
                )
                tier_row = next((
                    item for item in transition.get("tiers", [])
                    if isinstance(item, Mapping) and item.get("tier_id") == resource["tier"]
                ), None)
                baseline["admission"] = self._stable_admission_baseline(tier_row)
            return baseline
        if resource["kind"] == "recipe":
            arguments = self._arguments(resource, "configuration.apply", {}, {})
            arguments["action"] = "status"
            arguments.pop("values", None)
            return self._call("recipe_settings", arguments)
        if resource["kind"] == "configuration":
            arguments = self._arguments(resource, "configuration.apply", {}, {})
            arguments["action"] = "status"
            arguments.pop("values", None)
            return self._call("router_configuration", arguments)
        return {"resource": resource["id"], "settings": resource.get("settings", [])}

    @staticmethod
    def _stable_serve_baseline(payload: Mapping[str, Any]) -> dict[str, Any]:
        """Project owner identity/state without volatile health or GPU metrics."""
        row_keys = (
            "name", "container", "service", "runtime", "port", "docker_state", "state",
            "running", "model", "engine", "stack", "expected_compose_project",
            "observed_compose_project", "compose_ownership_mismatch", "port_conflicts",
        )
        rows = [
            {key: copy.deepcopy(row[key]) for key in row_keys if key in row}
            for row in payload.get("serves", []) if isinstance(row, Mapping)
        ]
        return {
            "serves": rows,
            "selected": copy.deepcopy(payload.get("selected", [])),
            # This ledger is derived from declared reservations and container
            # ownership. It does not contain measured free/used device memory.
            "reservations": copy.deepcopy(payload.get("reservations")),
            "operating_mode": copy.deepcopy(payload.get("operating_mode")),
            "recipe_ownership": copy.deepcopy(payload.get("recipe_ownership")),
        }

    @staticmethod
    def _stable_admission_baseline(row: Any) -> Any:
        if not isinstance(row, Mapping):
            return None
        keys = (
            "tier_id", "member_id", "state", "reason", "draining", "max_concurrency",
            "ready", "readiness_state", "readiness_reason", "expected_model",
            "observed_model", "observed_context_limit", "metadata_source",
        )
        result = {key: copy.deepcopy(row[key]) for key in keys if key in row}
        members = row.get("members")
        if isinstance(members, list):
            result["members"] = [
                ControllerAdapter._stable_admission_baseline(member)
                for member in members if isinstance(member, Mapping)
            ]
        return result

    def _settings(self, resource: Mapping[str, Any]) -> list[dict[str, Any]]:
        if resource["kind"] == "recipe":
            if "recipe_settings" not in self._tools():
                return []
            arguments = self._arguments(resource, "configuration.apply", {}, {})
            arguments["action"] = "status"
            arguments.pop("values", None)
            try:
                configured = self._call("recipe_settings", arguments).get("configured", {})
            except ObservatoryError:
                return []
            definitions = (
                ("maximum_context", "Maximum context", 256, 1048576, "tokens"),
                ("concurrent_sequences", "Concurrent sequences", 1, 4096, "count"),
                ("startup_timeout_seconds", "Startup timeout", 1, 14400, "seconds"),
            )
            return [{
                "setting_id": key, "label": label, "value_type": "integer", "unit": unit,
                "configured": configured.get(key), "observed": None,
                "observed_status": "unsupported",
                "constraints": {"minimum": low, "maximum": high, "step": 1},
                "support": "supported" if configured.get(key) is not None else "unsupported",
                "effect": "Applies to the next managed recipe load.",
                "help": "Bounded field mapped to one existing managed recipe value.",
            } for key, label, low, high, unit in definitions]
        if resource["kind"] != "configuration":
            return copy.deepcopy(resource.get("settings", []))
        if "router_configuration" not in self._tools():
            return []
        arguments = self._arguments(resource, "configuration.apply", {}, {})
        arguments["action"] = "status"
        arguments.pop("values", None)
        try:
            configured = self._call("router_configuration", arguments).get("configured", {})
        except ObservatoryError:
            return []
        definitions = (
            ("max_concurrency", "Maximum concurrency", 1, 4096),
            ("max_output_tokens", "Maximum output tokens", 1, 1048576),
        )
        return [{
            "setting_id": key, "label": label, "value_type": "integer", "unit": "count",
            "configured": configured.get(key), "observed": None,
            "observed_status": "unsupported", "constraints": {"minimum": low, "maximum": high, "step": 1},
            "support": "supported", "effect": "Requires a guarded router configuration transaction.",
            "help": "Bounded setting owned by the installed router schema.",
        } for key, label, low, high in definitions]

    @staticmethod
    def _verify_arguments(resource: Mapping[str, Any], action: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
        if resource["kind"] == "serve":
            if action in {"serve.start", "serve.stop", "serve.restart"}:
                return {"manifest": resource["manifest"], "names": [resource["serve"]]}
            if action in {"tier.quiesce", "tier.drain", "tier.readmit"}:
                return {"action": "status", "tier": resource["tier"]}
        if resource["kind"] == "profile":
            return {"action": "status", "manifest": resource["manifest"], "profile": resource["profile"]}
        result = dict(arguments)
        result["action"] = "status"
        result.pop("values", None)
        result.pop("expected_baseline_sha256", None)
        return result

    def _call(self, tool: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
        try:
            result = self._transport.execute(Operation(tool, arguments, tool_name=tool)).data
        except TransportError as exc:
            raise _public_error() from exc
        return self._payload(result)

    @staticmethod
    def _payload(result: Mapping[str, Any]) -> dict[str, Any]:
        data = result.get("data", result)
        return dict(data) if isinstance(data, Mapping) else {}

    @staticmethod
    def _planned_steps(payload: Mapping[str, Any]) -> list[Any]:
        plan = payload.get("plan", [])
        return copy.deepcopy(plan if isinstance(plan, list) else [plan])[:64]

    @staticmethod
    def _native_state(payload: Mapping[str, Any]) -> str:
        job = payload.get("job")
        if not isinstance(job, Mapping) and isinstance(payload.get("spec"), Mapping):
            job = payload
        if isinstance(job, Mapping) and type(job.get("state")) is str:
            return job["state"]
        return "succeeded"

    @staticmethod
    def _evidence(payload: Mapping[str, Any]) -> Any:
        if isinstance(payload.get("probe"), Mapping) and type(payload.get("passed")) is bool:
            return {
                "kind": "serve_probe", "passed": payload["passed"],
                "bounded": payload.get("bounded") is True,
                "parameters": copy.deepcopy(payload.get("parameters", {})),
                "result": copy.deepcopy(dict(payload["probe"])),
                "verification": copy.deepcopy(payload.get("verification", {})),
            }
        job = payload.get("job")
        if not isinstance(job, Mapping) and isinstance(payload.get("spec"), Mapping):
            job = payload
        if isinstance(job, Mapping) and type(job.get("spec")) is dict:
            result = {"run_id": job["spec"].get("run_id"), "suite": job["spec"].get("suite"),
                      "spec_sha256": job.get("spec_sha256"), "state": job.get("state"),
                      "revision": job.get("revision")}
            if isinstance(job.get("failure"), Mapping):
                result["failure"] = copy.deepcopy(dict(job["failure"]))
            artifact = job.get("artifact")
            if isinstance(artifact, Mapping):
                result["artifact"] = {
                    key: copy.deepcopy(artifact[key]) for key in
                    ("schema", "completeness", "results", "failure") if key in artifact
                }
            return result
        return None

    @staticmethod
    def _failure_evidence(error: object, preview: Mapping[str, Any]) -> dict[str, Any]:
        # Project only known outcome codes. Upstream messages/details can contain
        # paths, credentials or model text and must never enter web evidence.
        if not isinstance(error, Mapping):
            return {}
        details = error.get("details")
        if error.get("code") != "configuration_apply_failed" or not isinstance(details, Mapping):
            return {}
        native = details.get("recovery")
        status = {"restored": "succeeded", "failed": "failed", "not_needed": "not_attempted"}.get(native)
        if status is None:
            return {}
        recovery = {"status": status, "message": {
            "succeeded": "Change failed; previous router configuration restored.",
            "failed": "Change and recovery failed; independent managed recovery is required.",
            "not_attempted": "The change failed before a configuration replacement.",
        }[status]}
        return {"recovery": recovery, "evidence": {"kind": "configuration_failure",
            "candidate_digest": preview.get("candidate_digest"), "failure": "configuration_apply_failed", "recovery": recovery}}

    @staticmethod
    def _binding(preview: Mapping[str, Any]) -> dict[str, Any]:
        binding = preview.get("binding") if isinstance(preview, Mapping) else None
        if not isinstance(binding, Mapping) or binding.get("tool") not in _TOOLS or not isinstance(binding.get("arguments"), Mapping):
            raise ObservatoryError("invalid_preview", "The owner preview binding is invalid.")
        return dict(binding)
