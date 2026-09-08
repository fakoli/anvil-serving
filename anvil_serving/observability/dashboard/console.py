"""Opt-in Observatory composition on the existing dashboard HTTP server."""

from __future__ import annotations

import concurrent.futures
import html
import json
import mimetypes
import os
import re
import threading
import time
import urllib.parse
from email.message import Message
from importlib.resources import files
from pathlib import Path

from .access import Access, GrafanaLogin
from .contracts import (ObservatoryError, canonical, digest, fields, identifier,
                        preview_is_current, strict_json, validate_values)
from .intents import IntentStore

PREVIEW_FIELDS = frozenset({"id", "host_id", "resource_id", "action_id", "label", "baseline_digest", "candidate_digest", "policy_digest", "expires_at_epoch_seconds", "effect", "diff", "affected_aliases", "workload_impact", "gpu_ids", "stop_semantics", "recovery", "planned_steps", "actor", "service_identity", "acknowledgement_required"})
_SHELL_ROUTES = frozenset({"overview", "workstations", "serves", "workloads", "configuration", "experiments", "operations", "settings"})


def load_config(path: str) -> dict:
    source = Path(path)
    if not source.is_absolute() or source.stat().st_size > 262144:
        raise ValueError("use an absolute bounded private Observatory config")
    config = strict_json(source.read_bytes())
    fields(config, required=("schema", "origin", "base_path", "users", "authentication", "inventory", "prometheus_url", "state_path"),
           optional=("operate", "grafana_url", "controller", "workload", "build", "fixture", "strip_prefix", "evidence"))
    if config["schema"] != "anvil-observatory/config/v1":
        raise ValueError("unsupported Observatory configuration")
    if type(config.get("operate", False)) is not bool or type(config.get("fixture", False)) is not bool:
        raise ValueError("invalid Observatory mode")
    if not Path(config["state_path"]).is_absolute():
        raise ValueError("journal path must be absolute")
    return config


class Console:
    def __init__(self, config: dict, *, environment=None, adapter=None, metrics=None, authenticate=None, workload_service=None):
        self.config = config
        env = os.environ if environment is None else environment
        self.access = Access(config["users"], authenticate=authenticate or GrafanaLogin(config["authentication"]["grafana_url"]),
                             origin=config["origin"], base_path=config["base_path"], operate=config.get("operate", False))
        if metrics is None:
            from .metrics_client import MetricsClient
            metrics = MetricsClient(prometheus_url=config["prometheus_url"], inventory=config["inventory"], grafana_url=config.get("grafana_url"))
        self.metrics, self.adapter = metrics, adapter
        if adapter is None and config.get("controller"):
            from .controller_adapter import ControllerAdapter
            self.adapter = ControllerAdapter(config["controller"], env)
        self.store = IntentStore(config["state_path"])
        self._workers = concurrent.futures.ThreadPoolExecutor(max_workers=4, thread_name_prefix="observatory-intent")
        self._reconciling: set[str] = set()
        self._lock = threading.Lock()
        self.workload_service = workload_service
        self.workload_token = ""
        if config.get("workload"):
            from ...control_plane.authorization import load_authorization_policy
            from ..workload_http import WorkloadHTTPService
            binding = config["workload"]
            token_name = binding["token_env"]
            self.workload_token = env.get(token_name, "")
            if workload_service is None:
                policy = load_authorization_policy(binding["authorization_policy"], env=env)
                self.workload_service = WorkloadHTTPService(binding["controller_url"], binding["expected_node"], policy)
        self.policy_digest = digest({"users": config["users"], "operate": config.get("operate", False), "controller": config.get("controller", {})})

    def close(self):
        self._workers.shutdown(wait=True)
        self.store.close()

    def session_view(self, session):
        return {"authenticated": session is not None, "identity": session.principal.identity if session else None,
                "role": session.principal.role if session else "viewer", "operate": bool(session and self.access.operate and session.principal.actions),
                "csrf_token": session.csrf if session else None, "expires_at": session.expires_at if session else None,
                "base_path": self.config["base_path"], "build": self.config.get("build", "development"), "fixture": self.config.get("fixture", False)}

    def resources(self, session):
        resources = self.config.get("controller", {}).get("resources", [])
        return [{"id": item["id"], "host_id": item["host_id"], "kind": item["kind"], "label": item.get("label", item["id"]),
                 **({"experiment_class": "runtime_candidate"} if item.get("experiment_class") == "runtime_candidate" else {})}
                for item in resources if session.principal.can_read(item["id"])]

    def fleet(self, session):
        fleet = self.metrics.snapshot()
        fleet = json.loads(canonical(fleet))
        if self.adapter:
            try:
                owner = self.adapter.snapshot()
                for kind in ("hosts", "serves"):
                    updates = {item["id"]: item for item in owner.get(kind, [])}
                    for item in fleet.get(kind, []):
                        if item["id"] in updates:
                            observation = updates[item["id"]]
                            allowed = ({"controller", "profiles", "mode", "ownership_status", "maintenance"}
                                       if kind == "hosts" else {"runtime_state", "readiness", "admission", "observed_model", "observed_at", "ownership_status", "operation_id", "configuration"})
                            item.update({key: value for key, value in observation.items() if key in allowed})
                            if kind == "hosts":
                                for gpu in item.get("gpus", []):
                                    match = next((g for g in observation.get("gpus", []) if g.get("id") == gpu["id"] or (g.get("uuid") and g["uuid"] == gpu.get("uuid"))), None)
                                    if match:
                                        for field in ("owners", "ownership_status"):
                                            if field in match:
                                                gpu[field] = match[field]
            except Exception:
                # Telemetry is independent. A failed owner read cannot erase it.
                pass
        fleet["hosts"] = [h for h in fleet.get("hosts", []) if session.principal.can_read(h["id"]) or any(session.principal.can_read(s["id"]) and s["host_id"] == h["id"] for s in fleet.get("serves", []))]
        fleet["serves"] = [s for s in fleet.get("serves", []) if session.principal.can_read(s["id"])]
        coverage = dict(fleet.get("coverage", {}))
        owner_missing = sum(host.get("controller", {}).get("status") not in {"available", "complete", "fresh"} for host in fleet["hosts"])
        coverage["status"] = "complete" if coverage.get("status") in {"ok", "complete"} and not owner_missing else "partial"
        coverage["controller_unavailable_hosts"] = owner_missing
        coverage["reason"] = "Telemetry and controller coverage are evaluated independently; declared hosts remain visible."
        fleet["coverage"] = coverage
        fleet["control_resources"] = self.resources(session)
        return fleet

    def controls(self, session, resource):
        self.access.permit(session, resource)
        if self.adapter:
            result = self.adapter.controls(resource)
        else:
            result = {"resource_id": resource, "actions": [], "settings": [], "reason": "No controller binding is configured."}
        for action in result.get("actions", []):
            action["permitted"] = bool(self.access.operate and session.principal.can_operate(resource, action["id"]) and action.get("supported"))
            if not action["permitted"] and action.get("supported"):
                action["reason"] = "Your session does not grant this action."
        return result

    def _permitted_action(self, session, resource, action):
        self.access.permit(session, resource, action)
        catalog = self.controls(session, resource)
        control = next((i for i in catalog.get("actions", []) if i["id"] == action), None)
        if not control or not control.get("supported") or not control.get("permitted"):
            raise ObservatoryError("action_unavailable", "This action is not available from the current owner and access policy.", 409)
        return catalog

    def create_draft(self, session, body):
        fields(body, required=("resource_id", "values"), optional=("draft_id",))
        resource = identifier(body["resource_id"])
        if "draft_id" in body:
            identifier(body["draft_id"])
        catalog = self._permitted_action(session, resource, "configuration.apply")
        values = validate_values(body["values"], catalog.get("settings", []))
        return self.store.save_draft(session.principal.identity, {"resource_id": resource, "values": values,
            "baseline_digest": catalog["baseline_digest"], "candidate_digest": digest(values), "errors": []}, previous=body.get("draft_id"))

    def create_preview(self, session, body):
        fields(body, required=("resource_id", "action_id"), optional=("draft_id", "parameters", "operation_id"))
        resource, action = identifier(body["resource_id"]), identifier(body["action_id"])
        self._permitted_action(session, resource, action)
        recovery_of = None
        recovery_parameters = None
        if action == "operation.recover":
            if not body.get("operation_id") or "parameters" in body or "draft_id" in body:
                raise ObservatoryError("invalid_recovery", "Review recovery from the retained operation.")
            original = self.store.get(identifier(body["operation_id"]))
            if (original["resource_id"] != resource or original["action_id"] != "experiment.start"
                    or original["status"] not in {"manual_recovery_required", "outcome_unknown"}):
                raise ObservatoryError("invalid_recovery", "This operation does not require the selected recovery.", 409)
            recovery_of = original["id"]
            recovery_parameters = {"run_id": original["intent_key"]}
        elif "operation_id" in body:
            raise ObservatoryError("invalid_recovery", "Operation references are only accepted for recovery.")
        values = {}
        if body.get("draft_id"):
            draft = self.store.draft(identifier(body["draft_id"]), session.principal.identity)
            if draft["resource_id"] != resource:
                raise ObservatoryError("draft_conflict", "Review the draft on its original resource.", 409)
            catalog = self.controls(session, resource)
            if draft["baseline_digest"] != catalog["baseline_digest"]:
                raise ObservatoryError("stale_baseline", "The installed configuration changed; reload and review a new draft.", 409)
            values = draft["values"]
        if action == "configuration.apply" and not body.get("draft_id"):
            raise ObservatoryError("draft_required", "Validate a configuration draft before reviewing it.")
        parameters = recovery_parameters if recovery_parameters is not None else body.get("parameters", {})
        if type(parameters) is not dict or len(canonical(parameters)) > 16384:
            raise ObservatoryError("invalid_parameters", "Use the bounded experiment fields.")
        preview = self.adapter.preview(resource, action, values=values, parameters=parameters)
        preview.update({"resource_id": resource, "action_id": action, "policy_digest": self.policy_digest,
                        "expires_at_epoch_seconds": time.time() + 120, "actor": session.principal.identity,
                        "private_values": values, "private_parameters": parameters})
        if recovery_of:
            preview["private_recovery_of"] = recovery_of
        # Enforce the digest/identity contract before storing any review.
        if not preview_is_current(preview, preview, now_epoch_seconds=time.time()):
            raise ObservatoryError("invalid_owner_preview", "The owner did not provide a valid current preview.", 503)
        result = self.store.save_preview(session.principal.identity, preview)
        return {key: value for key, value in result.items() if key in PREVIEW_FIELDS}

    def apply(self, session, body):
        fields(body, required=("preview_id", "intent_key"))
        key, preview_id = identifier(body["intent_key"]), identifier(body["preview_id"])
        old = self.store.existing(key, session.principal.identity, preview_id)
        if old:
            self.access.permit(session, old["resource_id"], old["action_id"])
            return self.store.public(old)
        preview = self.store.preview(preview_id, session.principal.identity)
        self._permitted_action(session, preview["resource_id"], preview["action_id"])
        fresh = self.adapter.preview(preview["resource_id"], preview["action_id"], values=preview["private_values"], parameters=preview["private_parameters"])
        fresh["policy_digest"] = self.policy_digest
        if not preview_is_current(preview, fresh, now_epoch_seconds=time.time()):
            raise ObservatoryError("stale_preview", "State or policy changed; review a fresh preview before applying.", 409)
        item, created = self.store.accept(preview, session.principal.identity, key)
        if created:
            self._workers.submit(self._execute, item)
        return self.store.public(item)

    def _execute(self, item):
        try:
            result = self.adapter.execute(item["private_preview"], item["intent_key"])
            self._finish(item, result)
        except Exception:
            self.store.update(item["id"], status="outcome_unknown", execution_outcome="unknown",
                event=("facade", "reconciling", "Outcome unknown; reconciling with the owner. Do not repeat the action."))

    def _finish(self, item, result):
        if not result or result.get("status") in {"outcome_unknown", "running", "submitting", "pending"} or result.get("execution_outcome") in {"running", "pending", "unknown"}:
            status = "running" if result and result.get("status") in {"running", "pending"} else "outcome_unknown"
            self.store.update(item["id"], status=status, native_state=(result or {}).get("native_state"),
                event=("owner" if status == "running" else "facade", status, "Waiting for the existing owner operation to resolve."))
            return
        owner_id = result.get("owner_operation_id", item["intent_key"])
        # Failed runs are evidence too. Save before branching on execution or
        # verification, so a fast incorrect result never vanishes from history.
        evidence = result.get("evidence")
        evidence_id = self.store.save_evidence(item["resource_id"], evidence) if evidence else item.get("evidence_id")
        if result.get("ok") is False or result.get("execution_outcome") == "failed":
            recovery = result.get("recovery", {"status": "not_attempted", "message": "The owner retained the failure; inspect before recovery."})
            self.store.update(item["id"], status="manual_recovery_required" if recovery.get("status") == "failed" else "failed",
                owner_operation_id=owner_id, execution_outcome="failed", native_state=result.get("native_state"), recovery=recovery, evidence_id=evidence_id,
                verification={"status": "failed", "message": "The owner reported that the requested change failed."},
                event=("owner", "failed", "The requested change failed. Review the retained recovery outcome."))
            return
        self.store.update(item["id"], status="verifying", owner_operation_id=owner_id, execution_outcome="succeeded", evidence_id=evidence_id,
                          event=("facade", "verifying", "Checking resulting state independently with the owner."))
        verification = self.adapter.verify(item["private_preview"], result)
        passed = verification.get("status") == "passed"
        self.store.update(item["id"], status="succeeded" if passed else "failed", native_state=result.get("native_state"),
            verification=verification, evidence_id=evidence_id,
            **({"recovery": result["recovery"]} if isinstance(result.get("recovery"), dict) else {}),
            event=("facade", "succeeded" if passed else "verification_failed", "Resulting state verified." if passed else "Execution returned, but resulting state could not be verified."))
        original_id = item["private_preview"].get("private_recovery_of")
        if passed and item["action_id"] == "operation.recover" and original_id:
            self.store.update(original_id, status="failed",
                recovery={"status": "succeeded", "message": "Previous state restored and verified by the linked recovery operation.", "operation_id": item["id"]},
                event=("owner", "recovered", "Recovery verified; the original failed or interrupted test is retained."))
        self.store.prune()

    def operation(self, session, operation_id):
        item = self.store.get(identifier(operation_id))
        self.access.permit(session, item["resource_id"])
        if item["status"] in {"outcome_unknown", "running"} and self.adapter:
            with self._lock:
                if item["id"] not in self._reconciling:
                    self._reconciling.add(item["id"])
                    self._workers.submit(self._reconcile, item)
        return self.store.public(item)

    def _reconcile(self, item):
        try:
            result = self.adapter.reconcile(item["private_preview"], item["intent_key"])
            if result:
                self._finish(item, result)
        except Exception:
            self.store.update(item["id"], status="outcome_unknown",
                event=("facade", "reconciling", "The owner or verification read is temporarily unavailable; reconciliation remains pending."))
        finally:
            with self._lock:
                self._reconciling.discard(item["id"])

    def read(self, route, query, session):
        if route == "fleet":
            fields(query)
            return self.fleet(session)
        if route.startswith(("hosts/", "serves/")):
            fields(query)
            kind, resource = route.split("/", 1)
            identifier(resource)
            fleet = self.fleet(session)
            item = next((r for r in fleet[kind] if r["id"] == resource), None)
            if item is None:
                raise ObservatoryError("not_found", "This resource is unavailable.", 404)
            if kind == "serves":
                item["evidence"] = [{"id": row["id"], "label": row.get("label", row["id"])} for row in self.config.get("evidence", []) if row.get("resource_id") == resource]
                if self.adapter and hasattr(self.adapter, "detail"):
                    item.update(self.adapter.detail(resource))
            return item
        if route == "controls":
            fields(query, optional=("resource",))
            return self.controls(session, identifier(query["resource"])) if "resource" in query else {"resources": self.resources(session)}
        if route == "metrics":
            fields(query, required=("chart",), optional=("host", "serve", "range"))
            from .metrics_client import CHARTS, WINDOWS
            if query["chart"] not in CHARTS or query.get("range", "1h") not in WINDOWS:
                raise ObservatoryError("invalid_metric_filter", "Select a supported chart and time range.")
            for name in ("host", "serve"):
                if name in query:
                    self.access.permit(session, identifier(query[name]))
                    records = self.config.get("inventory", {}).get(name + "s", [])
                    if records and not any(item["id"] == query[name] for item in records):
                        raise ObservatoryError("not_found", "This metric resource is unavailable.", 404)
            if "host" in query and "serve" in query:
                records = self.config.get("inventory", {}).get("serves", [])
                if any(item["id"] == query["serve"] and item["host_id"] != query["host"] for item in records):
                    raise ObservatoryError("invalid_metric_filter", "Select a serve belonging to the selected workstation.")
            if not any(name in query for name in ("host", "serve")) and "*" not in session.principal.resources:
                raise ObservatoryError("permission_denied", "Select a resource within your access scope.", 403)
            if query["chart"] in {"generation", "ttft", "queue", "prompt", "kv_cache", "errors"} and "serve" not in query:
                selected = [item for item in self.config.get("inventory", {}).get("serves", []) if not query.get("host") or item["host_id"] == query["host"]]
                if any(not session.principal.can_read(item["id"]) for item in selected):
                    raise ObservatoryError("permission_denied", "Select an authorized serve for inference metrics.", 403)
            return self.metrics.chart(query["chart"], host_id=query.get("host"), serve_id=query.get("serve"), window=query.get("range", "1h"))
        if route == "operations":
            fields(query)
            rows = [item for item in self.store.list_private() if session.principal.can_read(item["resource_id"])]
            return {"items": [self.store.public(item) for item in rows[:100]], "truncated": len(rows) > 100}
        if route.startswith("operations/"):
            fields(query)
            return self.operation(session, route.split("/", 1)[1])
        if route.startswith("evidence/"):
            fields(query)
            evidence_id = identifier(route.split("/", 1)[1])
            historical = next((row for row in self.config.get("evidence", []) if row["id"] == evidence_id), None)
            if historical is not None:
                self.access.permit(session, historical["resource_id"])
                return historical
            resource, body = self.store.evidence(evidence_id)
            self.access.permit(session, resource)
            return body
        if route == "evidence":
            fields(query)
            rows = [row for row in self.config.get("evidence", []) if session.principal.can_read(row["resource_id"])]
            for item in self.store.list_private():
                if item.get("evidence_id") and session.principal.can_read(item["resource_id"]):
                    _, body = self.store.evidence(item["evidence_id"])
                    rows.append({**body, "id": item["evidence_id"], "resource_id": item["resource_id"], "label": item["label"]})
            return {"items": rows[:100], "truncated": len(rows) > 100}
        if route == "workloads":
            if not self.workload_service or not self.workload_token:
                raise ObservatoryError("workload_scope_unavailable", "Canonical workload reads require a separately granted workload connection.", 403)
            if "*" not in session.principal.resources:
                if "host" not in query:
                    raise ObservatoryError("permission_denied", "Select an authorized workstation for workload reads.", 403)
                self.access.permit(session, identifier(query["host"]))
            headers = Message()
            headers["Authorization"] = f"Bearer {self.workload_token}"
            status, raw = self.workload_service.read(urllib.parse.urlencode(query), headers)
            if status != 200:
                raise ObservatoryError("workload_source_unavailable", "The separately authorized workload source is unavailable.", status)
            return strict_json(raw)["data"]
        if route == "settings":
            fields(query)
            integrations = self.metrics.integration_status()
            for collection in ("hosts", "serves", "host_ids", "serve_ids"):
                if collection in integrations:
                    integrations[collection] = [key for key in integrations[collection] if session.principal.can_read(key)]
            return {"defaults": {"timezone": "America/Los_Angeles", "range": "1h", "landing": "overview", "density": "comfortable"},
                    "integrations": integrations, "resources": self.resources(session),
                    "session": self.session_view(session), "schemas": {"facade": "v1", "journal": 1},
                    "workloads": {"configured": bool(self.workload_service), "reason": "Workload read grants are separate from control grants."}}
        raise ObservatoryError("not_found", "This application route is unavailable.", 404)


def attach_console(server, console: Console):
    """Extend the existing handler while preserving its legacy workload boundary."""
    legacy = server.RequestHandlerClass
    base = console.config["base_path"]
    root = files("anvil_serving.observability.dashboard.static")
    assets = {}
    # Only packaged assets, never a user-supplied path or general file reader.
    def collect(directory, prefix=""):
        for path in directory.iterdir():
            if path.is_dir() and path.name == "views":
                collect(path, "views/")
            elif path.is_file() and path.name.endswith((".js", ".css")) and path.name != "workloads.js":
                assets[prefix + path.name] = (mimetypes.guess_type(path.name)[0] or "text/javascript", path.read_bytes())
    collect(root)

    class Handler(legacy):
        def _relative(self):
            parsed = urllib.parse.urlsplit(self.path)
            if parsed.fragment or len(self.path) > 8192:
                raise ObservatoryError("invalid_path", "Use a valid application route.")
            if parsed.path.startswith(base):
                return parsed.path[len(base):], parsed.query
            if console.config.get("strip_prefix", True) and parsed.path.startswith("/"):
                return parsed.path[1:], parsed.query
            return None, None

        def _respond(self, status, payload, *, cookie=None):
            raw = canonical(payload)
            if len(raw) > 4 * 1024 * 1024:
                status, raw = 413, b'{"ok":false,"error":{"code":"response_limit","message":"The response exceeded its safe bound."}}'
            self._asset(status, "application/json", raw, cookie=cookie)

        def _asset(self, status, content_type, raw, *, cookie=None):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'")
            if cookie:
                self.send_header("Set-Cookie", cookie)
            self.end_headers()
            self.wfile.write(raw)

        def _body(self):
            lengths = self.headers.get_all("Content-Length") or []
            content_types = self.headers.get_all("Content-Type") or []
            if self.headers.get_all("Transfer-Encoding") or len(lengths) != 1 or not re.fullmatch(r"[0-9]{1,6}", lengths[0]) or not 1 <= int(lengths[0]) <= 65536:
                self.close_connection = True
                raise ObservatoryError("invalid_framing", "The request body must have a bounded exact length.")
            if len(content_types) != 1 or content_types[0].lower() != "application/json":
                raise ObservatoryError("content_type_denied", "Use the application's JSON request form.", 415)
            self.connection.settimeout(5)
            raw = self.rfile.read(int(lengths[0]))
            if len(raw) != int(lengths[0]):
                raise ObservatoryError("incomplete_body", "The request body was incomplete.")
            value = strict_json(raw)
            if type(value) is not dict:
                raise ObservatoryError("invalid_fields", "A JSON object is required.")
            return value

        def _dispatch(self, method):
            relative, raw_query = self._relative()
            if relative is None:
                return False
            if not relative.startswith("api/observatory/v1/"):
                if method != "GET":
                    return False
                if relative in assets and not raw_query:
                    console.access.check_host(self.headers)
                    self._asset(200, *assets[relative])
                    return True
                if relative in {"", "index.html"} or relative.split("/", 1)[0] in _SHELL_ROUTES:
                    console.access.check_host(self.headers)
                    document = root.joinpath("observatory.html").read_bytes()
                    document = document.replace(b"__OBSERVATORY_BASE__", html.escape(base, quote=True).encode())
                    self._asset(200, "text/html; charset=utf-8", document)
                    return True
                return False
            console.access.check_host(self.headers)
            route = relative[len("api/observatory/v1/"):]
            query = {}
            for key, value in urllib.parse.parse_qsl(raw_query, keep_blank_values=True, max_num_fields=10):
                if key in query:
                    raise ObservatoryError("invalid_query", "Each filter may appear only once.")
                query[key] = value
            if method == "GET":
                if self.headers.get_all("Transfer-Encoding") or self.headers.get_all("Content-Length") not in (None, ["0"]):
                    raise ObservatoryError("invalid_framing", "Read requests cannot carry a body.")
                session = console.access.session(self.headers, required=route != "session")
                if route == "session":
                    fields(query)
                data = console.session_view(session) if route == "session" else console.read(route, query, session)
                self._respond(200, {"ok": True, "data": data})
                return True
            if query:
                raise ObservatoryError("invalid_query", "Mutations do not accept URL parameters.")
            if route == "session" and method == "POST":
                console.access.require_origin(self.headers)
                body = self._body()
                fields(body, required=("username", "password"))
                session = console.access.login(body["username"], body["password"], client=self.client_address[0], previous=console.access.session(self.headers, required=False))
                self._respond(200, {"ok": True, "data": console.session_view(session)}, cookie=console.access.cookie(session))
                return True
            session = console.access.mutation(self.headers)
            if route == "session" and method == "DELETE":
                console.access.logout(session)
                self._respond(200, {"ok": True, "data": {"authenticated": False}}, cookie=console.access.cookie(None))
                return True
            if method != "POST" or route not in {"drafts", "previews", "operations"}:
                raise ObservatoryError("method_denied", "This route does not accept mutations.", 405)
            body = self._body()
            call = {"drafts": console.create_draft, "previews": console.create_preview, "operations": console.apply}[route]
            data = call(session, body)
            self._respond(202 if route == "operations" else 200, {"ok": True, "data": data})
            return True

        def _handle_console(self, method):
            try:
                return self._dispatch(method)
            except ObservatoryError as error:
                self._respond(error.status, {"ok": False, "error": {"code": error.code, "message": error.message}})
            except Exception:
                self._respond(503, {"ok": False, "error": {"code": "source_unavailable", "message": "The requested source is unavailable. Existing operations are retained."}})
            return True

        def do_GET(self):
            if self.path.startswith("/v1/") or self.path == "/health":
                # Legacy workload reads keep their independently scoped handler.
                # Other legacy routes cannot acquire anonymous publication when
                # a previously loopback-only dashboard gains a web session.
                if self.path != "/health" and urllib.parse.urlsplit(self.path).path != "/v1/workloads" and not getattr(server, "observatory_legacy_authenticated", False):
                    self._respond(401, {"ok": False, "error": {"code": "legacy_auth_required", "message": "Legacy telemetry requires its separately configured read credential."}})
                    return
                return super().do_GET()
            if not self._handle_console("GET"):
                super().do_GET()

        def do_POST(self):
            if self.path.startswith("/v1/"):
                return super().do_POST()
            if not self._handle_console("POST"):
                super().do_POST()

        def do_DELETE(self):
            if not self._handle_console("DELETE"):
                self._respond(405, {"ok": False, "error": {"code": "method_denied", "message": "This route does not accept deletion."}})

    server.RequestHandlerClass = Handler
    server.observatory = console
    return server
