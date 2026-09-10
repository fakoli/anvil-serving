"""Closed Workbench routes sharing the existing Observatory identity boundary."""

from __future__ import annotations

import re
import subprocess
import threading
from dataclasses import asdict
from importlib.resources import files
from pathlib import Path

from ..observability.dashboard.contracts import ObservatoryError, digest, fields, identifier
from .config import validate_config
from .playground import Playground
from .projects import Projects
from .store import PrivateStore


DOCUMENTS = {"serving": ("Anvil Serving", "serving.md"), "anvil": ("Anvil State", "anvil.md"),
             "workbench": ("Workbench guide", "workbench.md"), "benchmarks": ("Benchmark methodology", "benchmarks.md"),
             "connect": ("Connect access", "connect.md")}
DOCUMENT_SOURCES = {
    "serving": "https://github.com/fakoli/anvil-serving/blob/main/README.md",
    "anvil": "https://github.com/fakoli/anvil/blob/main/README.md",
    "workbench": "https://github.com/fakoli/anvil-serving/blob/main/docs/WORKBENCH-PORTAL.md",
    "benchmarks": "https://github.com/fakoli/anvil-serving/blob/main/docs/benchmarks/methodology.md",
    "connect": "https://github.com/fakoli/anvil-serving/blob/main/docs/ANVIL-CONNECT-ACCESS.md",
}


class WorkbenchService:
    def __init__(self, config, access, environment, *, adapter=None, store=None, projects=None, playground=None):
        self.config = validate_config(config)
        self.access, self.environment, self.adapter = access, environment, adapter
        self.store = store or PrivateStore(config["state_path"], retention_days=config.get("retention_days", 30))
        self.pi = None
        self.pi_store = None
        self.lock = threading.RLock()
        artifacts = None
        if config.get("pi") and projects is None:
            from .task_artifacts import TaskArtifacts
            from .task_sandbox import ProductionTaskSandbox
            artifacts = TaskArtifacts(ProductionTaskSandbox(config["pi"]), is_active=self._task_runner_active)
        self.projects = projects or Projects(config, self.store, access, artifacts=artifacts)
        from .evidence_jobs import EvidenceJobs
        self.evidence_jobs = EvidenceJobs(self.projects, self.store, self.lock)
        self.playground = playground or Playground(config, self.store, access, environment)
        self.stop = threading.Event()
        self.timer = None
        if config.get("pi"):
            from .pi_sessions import PiConversationService, PiSessionStore
            pi_config = config["pi"]
            self.pi_store = PiSessionStore(Path(pi_config["state_root"]), runtime_root=Path(pi_config["runner_storage_root"]), max_sessions=pi_config.get("max_sessions", 100), max_state_bytes=pi_config.get("max_state_bytes", 64 * 1024**2))
            self.pi = PiConversationService(self.pi_store, self.projects, self._runner,
                allowed_models={key: frozenset(value) for key, value in pi_config["models"].items()},
                allowed_thinking=frozenset(pi_config["thinking_levels"]), max_active=pi_config.get("max_active", 2),
                max_wall_seconds=pi_config.get("max_wall_seconds", 4 * 3600), max_per_principal=pi_config.get("max_per_principal", 2), max_per_task=pi_config.get("max_per_task", 1), reconcile=self._reconcile, attach_factory=self._attach, stop_runner=self._stop_runner)
            self.timer = threading.Thread(target=self._tick, daemon=True, name="workbench-pi-events")
            self.timer.start()

    def _runner(self, session, session_dir):
        from .pi_rpc import PiRpcClient
        from .pi_storage import validate_pool
        from .pi_egress import PiEgress
        validate_pool(self.config)
        config = self.config["pi"]
        row = self.projects.binding_for_pi(session.binding)
        agent_dir = Path(config["runner_storage_root"]) / "agents" / session.session_id
        agent_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        # Never broaden mount permissions. Installation must arrange private
        # ownership for the configured unprivileged identity.
        for path in (agent_dir, session_dir, Path(row["runner_checkout"])):
            if path.is_symlink() or path.stat().st_uid != config["uid"]:
                raise ObservatoryError("pi_mount_ownership", "Pi private mounts must belong to the configured runner UID.", 409)
        native = ["--mode", "rpc", "--no-extensions", "--session-dir", "/sessions"]
        if session.parent_session_id and not session.official_session_id:
            native.extend(["--fork", "/sessions/branch-source.jsonl", "--session-id", session.session_id])
        else:
            # Exact ID resumes the live native file or creates an empty session
            # after a launch interruption before the first assistant message.
            native.extend(["--session-id", session.official_session_id or session.session_id])
        if not (session.pending_target and session.official_session_file and Path(session.official_session_file).is_file()):
            native.extend(["--provider", session.binding.provider_id, "--model", session.model_id, "--thinking", session.thinking_level])
        session = self.pi_store._replace(session.session_id, runtime_command=tuple(native))
        PiEgress(config).setup(session.binding.provider_id, confirm=True, session_id=session.session_id, runner_name=session.container_name)
        policy = self._policy_for(session)
        if policy.network != "none":
            policy.egress_ready()
        custom = config.get("provider_endpoints", {}).get(session.binding.provider_id)
        if custom:
            import json
            models = [{"id": name, "reasoning": custom.get("reasoning", False), "contextWindow": custom.get("context_window", 32768), "maxTokens": custom.get("max_tokens", 4096)} for name in config["models"][session.binding.provider_id]]
            model_config = {"providers": {session.binding.provider_id: {"baseUrl": policy.proxy_url + __import__("urllib.parse", fromlist=["urlsplit"]).urlsplit(custom["base_url"]).path, "api": custom["api"], "apiKey": "anvil-session-gateway-nonsecret", "models": models}}}
            (agent_dir / "models.json").write_text(json.dumps(model_config))
            (agent_dir / "models.json").chmod(0o600)
        launch = policy.launch(session_dir, credential_environment={}, detached=True)
        environment = {key: self.environment[key] for key in ("PATH", "HOME") if key in self.environment}
        environment.update(launch.environment)
        started = subprocess.run(launch.argv, cwd=str(row["runner_checkout"]), env=environment, capture_output=True, text=True, timeout=20)
        if started.returncode != 0:
            raise ObservatoryError("pi_runner_start_failed", "The isolated Pi runner could not start.", 409)
        if self._reconcile(session, session_dir) != "running":
            raise ObservatoryError("pi_runner_policy_unverified", "The started Pi runner does not match its isolation policy.", 409)
        return PiRpcClient((config["engine_binary"], "attach", "--sig-proxy=false", session.container_name), cwd=Path(row["runner_checkout"]), environment={key: environment[key] for key in ("PATH", "HOME") if key in environment})

    def _policy_for(self, session):
        from .pi_runner import PiRunnerPolicy
        from .pi_egress import PiEgress
        import json
        config = self.config["pi"]
        row = self.projects.binding_for_pi(session.binding)
        egress = PiEgress(config)
        proof = json.loads(egress.record_path(session.binding.provider_id, session.session_id).read_text()) if config.get("provider_egress", {}).get(session.binding.provider_id) else None
        return PiRunnerPolicy(engine_argv=(config["engine_binary"],), image=config["image"], worktree=Path(row["runner_checkout"]), agent_dir=Path(config["runner_storage_root"]) / "agents" / session.session_id, uid=config["uid"], gid=config["gid"], cpu_limit=config.get("cpus", 2), memory_limit_bytes=config.get("memory_bytes", 2 * 1024**3), pids_limit=config.get("pids", 256), network=proof["network"] if proof else "none", proxy_url=("http://" + proof["proxy"] + ":3128") if proof else None, container_name=session.container_name or "", session_id=session.session_id, native_args=session.runtime_command, credential_names=(), network_id=proof["network_id"] if proof else None, egress_ready=(lambda: egress.verify(session.binding.provider_id, session_id=session.session_id)) if proof else None)

    def _pi_absence(self, session):
        result = subprocess.run((self.config["pi"]["engine_binary"], "inspect", "--type", "container", session.container_name), capture_output=True, text=True, timeout=5)
        absent = {f"Error: No such container: {session.container_name}", f"Error response from daemon: No such container: {session.container_name}"}
        return result.returncode != 0 and result.stderr.strip() in absent

    def _reconcile(self, session, session_dir):
        from .pi_runner import PiContainerInspector
        if self._pi_absence(session):
            from .pi_egress import PiEgress
            if PiEgress(self.config["pi"]).record_path(session.binding.provider_id, session.session_id).exists():
                return "pending"
            return "absent"
        return PiContainerInspector(self.config["pi"]["engine_binary"]).state(self._policy_for(session), session_dir)

    def _attach(self, session, _session_dir):
        from .pi_rpc import PiRpcClient
        row = self.projects.binding_for_pi(session.binding)
        env = {key: self.environment[key] for key in ("PATH", "HOME") if key in self.environment}
        return PiRpcClient((self.config["pi"]["engine_binary"], "attach", "--sig-proxy=false", session.container_name), cwd=Path(row["runner_checkout"]), environment=env)

    def _stop_runner(self, session):
        # Re-read launch metadata because the caller may hold its pre-launch
        # reservation. No name-only stop is authorized.
        session = self.pi_store.get(session.session_id)
        from .pi_runner import PiContainerInspector
        mode = "absent" if self._pi_absence(session) else PiContainerInspector(self.config["pi"]["engine_binary"]).state(self._policy_for(session), self.pi_store.session_dir(session.session_id), require_egress_ready=False)
        if mode in {"absent", "stopped"}:
            self._remove_pi_gateway(session)
            return
        if mode != "running":
            raise ObservatoryError("pi_stop_unverified", "Cannot prove ownership of this retained Pi runner.", 409)
        result = subprocess.run((self.config["pi"]["engine_binary"], "stop", "--time", "10", session.container_name), capture_output=True, text=True, timeout=15)
        if result.returncode != 0:
            raise ObservatoryError("pi_stop_failed", "The Pi runner did not confirm shutdown.", 409)
        import time
        for _ in range(20):
            state = PiContainerInspector(self.config["pi"]["engine_binary"]).state(self._policy_for(session), self.pi_store.session_dir(session.session_id), require_egress_ready=False)
            if state == "absent":
                self._remove_pi_gateway(session)
                return
            time.sleep(0.1)
        raise ObservatoryError("pi_stop_pending", "Pi stopped but its automatic container removal is not yet confirmed.", 409)

    def _remove_pi_gateway(self, session):
        from .pi_egress import PiEgress
        egress = PiEgress(self.config["pi"])
        if egress.record_path(session.binding.provider_id, session.session_id).exists():
            egress.remove(session.binding.provider_id, confirm=True, session_id=session.session_id)

    def _tick(self):
        count = 0
        while not self.stop.wait(0.5):
            try:
                with self.lock:
                    for session_id in tuple(self.pi._clients):
                        try:
                            self.pi._collect(session_id)
                        except Exception:
                            # One source failure cannot starve other streams or
                            # the independent lease/retention sweep.
                            continue
                    count += 1
                    if count % 60 == 0:
                        self.pi.sweep()
            except Exception:
                # Source failures cannot kill the event pump or replay a command.
                continue

    def close(self):
        self.stop.set()
        if self.timer:
            self.timer.join(timeout=35)
        with self.lock:
            if self.pi:
                self.pi.close()
        self.evidence_jobs.close()
        self.playground.close()
        self.store.close()

    def _task_runner_active(self, row):
        """Only owner-confirmed absence allows a frozen task checkout transfer."""
        with self.lock:
            if self.pi_store is None:
                return True
            for item in self.pi_store.all():
                if (item.binding.principal_id == row["owner"] and item.binding.lease_id == row.get("lease_id")
                        and item.binding.project_id == row["project_id"] and item.binding.task_id == row["task_id"]):
                    if self._reconcile(item, self.pi_store.session_dir(item.session_id)) not in {"absent", "stopped"}:
                        return True
            return False

    def catalog(self, session):
        result = self.playground.catalog(session)
        result["projects"] = [self.projects.public_project(p) for p in self.config.get("projects", []) if session.principal.can_read(p["resource_id"])]
        config = self.config.get("pi", {})
        result["pi"] = {"configured": self.pi is not None, "models": config.get("models", {}), "thinking": config.get("thinking_levels", []),
                        "runner_id": config.get("id"), "network": config.get("network", "none")}
        return result

    def read(self, route, query, session):
        if route.startswith("workloads/") and route.endswith("/logs") and len(route.split("/")) == 3:
            fields(query)
            resource = identifier(route.split("/")[1])
            self.access.permit(session, resource)
            if not self.adapter or not hasattr(self.adapter, "workload_logs"):
                raise ObservatoryError("logs_unavailable", "No workload log owner is configured.", 409)
            return self.adapter.workload_logs(resource)
        if route == "catalog":
            fields(query)
            return self.catalog(session)
        if route == "preferences":
            fields(query)
            try:
                return self.store.get("preferences", session.principal.identity, "default")
            except ObservatoryError as error:
                if error.status != 404:
                    raise
                return {}
        if route == "conversations":
            fields(query)
            return self.playground.conversations(session)
        if route.startswith("conversations/"):
            fields(query)
            return self.playground.read(session, route.split("/", 1)[1])
        if route == "services":
            fields(query, optional=("resource",))
            if not self.adapter or not hasattr(self.adapter, "read_services"):
                return {"items": [], "reason": "No native service owner is configured."}
            resource = identifier(query["resource"]) if "resource" in query else None
            if resource:
                self.access.permit(session, resource)
            data = self.adapter.read_services(resource)
            rows = data.get("items", data.get("services", [])) if type(data) is dict else data
            return {"items": [row for row in rows if session.principal.can_read(row.get("resource_id", row.get("id", "")))]}
        if route == "documents":
            fields(query)
            return {"items": [{"id": key, "title": value[0]} for key, value in DOCUMENTS.items()]}
        if route.startswith("documents/"):
            fields(query)
            key = route.split("/", 1)[1]
            if key not in DOCUMENTS:
                raise ObservatoryError("not_found", "This document is unavailable.", 404)
            title, filename = DOCUMENTS[key]
            raw = files("anvil_serving.observability.dashboard.static").joinpath("documents", filename).read_text()
            from .. import __version__
            return {"id": key, "title": title, "markdown": raw, "version": __version__, "digest": digest(raw), "source_url": DOCUMENT_SOURCES[key]}
        pieces = route.split("/")
        if len(pieces) == 2 and pieces[0] == "projects":
            fields(query)
            return self.projects.read(session, pieces[1])
        if len(pieces) == 4 and pieces[0] == "projects" and pieces[2] == "tasks":
            fields(query)
            return self.projects.task(session, pieces[1], pieces[3])
        if len(pieces) == 4 and pieces[0] == "projects" and pieces[2] == "prds":
            fields(query)
            return self.projects.prd(session, pieces[1], pieces[3])
        if len(pieces) == 2 and pieces[0] == "artifacts":
            fields(query)
            return self.projects.evidence(session, pieces[1]) | {"job": self.evidence_jobs.latest(session, pieces[1])}
        if route.startswith("pi/"):
            return self._pi_read(route, query, session)
        raise ObservatoryError("not_found", "This Workbench route is unavailable.", 404)

    def mutate(self, route, body, session):
        if route == "messages":
            return self.playground.send(session, body)
        pieces = route.split("/")
        if len(pieces) == 3 and pieces[0] == "artifacts" and pieces[2] in {"review", "verify", "submit", "release"}:
            fields(body, required=("artifact_digest",) if pieces[2] in {"verify", "submit"} else ())
            artifact_digest = body.get("artifact_digest")
            if artifact_digest is not None and (type(artifact_digest) is not str or not re.fullmatch(r"[0-9a-f]{64}", artifact_digest)):
                raise ObservatoryError("invalid_artifact", "Use the digest of the reviewed task patch.")
            return self.evidence_jobs.start(session, identifier(pieces[1]), pieces[2], artifact_digest)
        if len(pieces) == 3 and pieces[0] == "conversations":
            fields(body)
            key = identifier(pieces[1])
            if pieces[2] == "cancel":
                return self.playground.cancel(session, key)
            if pieces[2] == "delete":
                return self.playground.delete(session, key)
        if route == "preferences":
            fields(body, optional=("landing", "density", "zone", "range", "pi_provider", "pi_model", "pi_thinking"))
            allowed = {"landing": {"bench", "playground", "models", "work", "observability", "compute"}, "density": {"comfortable", "compact"},
                       "zone": {"UTC", "America/Los_Angeles"}, "range": {"15m", "1h", "6h", "24h", "7d"}}
            for key, value in body.items():
                if type(value) is not str or len(value) > 192 or (key in allowed and value not in allowed[key]):
                    raise ObservatoryError("invalid_preferences", "Choose a supported workspace preference.")
            self.store.put("preferences", session.principal.identity, "default", body)
            return body
        if route.startswith("pi/"):
            return self._pi_mutate(route, body, session)
        raise ObservatoryError("method_denied", "This Workbench route does not accept this action.", 405)

    def _require_pi(self):
        if not self.pi:
            raise ObservatoryError("pi_unavailable", "Configure an isolated Pi runner and provider in the private Workbench configuration.", 409)

    def _pi_session(self, session, key, *, execute=False):
        self._require_pi()
        try:
            item = self.pi_store.get(identifier(key))
        except (OSError, ValueError):
            raise ObservatoryError("not_found", "This Pi conversation is unavailable.", 404) from None
        if item.binding.principal_id != session.principal.identity:
            raise ObservatoryError("not_found", "This Pi conversation is unavailable.", 404)
        self.projects.project(session, item.binding.project_id, execute=execute)
        return item

    def _pi_read(self, route, query, session):
        self._require_pi()
        pieces = route.split("/")
        with self.lock:
            if route == "pi/sessions":
                fields(query, required=("project", "task"))
                self.projects.project(session, query["project"])
                rows = []
                for binding in self.store.list("task-binding", session.principal.identity):
                    if binding["project_id"] == query["project"] and binding["task_id"] == query["task"] and binding.get("lease_id"):
                        for item in self.pi_store.list_for(self.projects.pi_binding(binding)):
                            rows.append(self.pi._session_json(item) | {"binding_id": binding["id"]})
                return {"items": rows}
            if len(pieces) in {3, 4} and pieces[:2] == ["pi", "sessions"]:
                item = self._pi_session(session, pieces[2])
                if len(pieces) == 3:
                    fields(query)
                    return self.pi._session_json(item) | {"binding_id": self.projects.binding_for_pi(item.binding)["id"]}
                if pieces[3] == "events":
                    fields(query, optional=("cursor",))
                    if not re.fullmatch(r"[0-9]{1,10}", query.get("cursor", "0")):
                        raise ObservatoryError("invalid_cursor", "Use the returned conversation cursor.")
                    page = self.pi_store.events_after(item.session_id, int(query.get("cursor", "0")), limit=100)
                    return {"events": [asdict(event) for event in page.events], "next_cursor": page.next_cursor, "gap": page.gap, "session": self.pi._session_json(self.pi_store.get(item.session_id))}
        raise ObservatoryError("not_found", "This Pi route is unavailable.", 404)

    def _pi_mutate(self, route, body, session):
        self._require_pi()
        with self.lock:
            if route == "pi/sessions":
                fields(body, required=("project_id", "task_id", "request_id", "provider_id", "model_id", "thinking_level"), optional=("parent_session_id",))
                config = self.config["pi"]
                from .pi_storage import PiStorageError, validate_pool
                try:
                    validate_pool(self.config)
                except PiStorageError:
                    raise ObservatoryError(
                        "pi_storage_unavailable",
                        "Pi storage is not ready. Check the managed pool mount and ownership before starting a conversation.",
                        409,
                    ) from None
                if body["model_id"] not in config["models"].get(body["provider_id"], []) or body["thinking_level"] not in config["thinking_levels"]:
                    raise ObservatoryError("model_unavailable", "Choose a declared Pi model and thinking level.")
                if body.get("parent_session_id"):
                    parent = self._pi_session(session, body["parent_session_id"], execute=True)
                    if parent.binding.project_id != body["project_id"] or parent.binding.task_id != body["task_id"]:
                        raise ObservatoryError("task_conflict", "Branches must stay with the same task.", 409)
                    if parent.binding.provider_id != body["provider_id"] or parent.model_id != body["model_id"] or parent.thinking_level != body["thinking_level"]:
                        raise ObservatoryError("branch_target_mismatch", "A Pi branch must keep its parent provider, model, and thinking level.", 409)
                    row = self.projects.binding_for_pi(parent.binding)
                else:
                    # Avoid creating a task claim when runner capacity is full.
                    if not any(s.start_key == body["request_id"] and s.binding.principal_id == session.principal.identity for s in self.pi_store.all()):
                        self.pi.capacity(session.principal.identity, body["project_id"], body["task_id"])
                    row = self.projects.prepare(session, body)
                if hasattr(self, "evidence_jobs"):
                    self.evidence_jobs.ensure_idle(row["id"])
                try:
                    result = self.pi.new(self.projects.pi_binding(row), identifier(body["request_id"]), model_id=body["model_id"], thinking_level=body["thinking_level"], parent_session_id=body.get("parent_session_id"))
                except Exception as error:
                    from .pi_sessions import PiSessionAccessError, PiSessionError, PiStartUncertain
                    if isinstance(error, PiSessionAccessError):
                        raise ObservatoryError("pi_forbidden", "This Pi operation is not allowed for the current task binding.", 403) from None
                    if isinstance(error, (PiSessionError, PiStartUncertain)):
                        raise ObservatoryError("pi_recovery_required", str(error), 409) from None
                    raise
                return result | {"binding_id": row["id"]}
            pieces = route.split("/")
            if len(pieces) != 4 or pieces[:2] != ["pi", "sessions"]:
                raise ObservatoryError("not_found", "This Pi action is unavailable.", 404)
            item = self._pi_session(session, pieces[2], execute=True)
            if pieces[3] == "stop":
                fields(body)
                return self.pi.stop(item.session_id, item.binding, validate=False)
            if pieces[3] == "delete":
                fields(body)
                return self.pi.delete(item.session_id, item.binding)
            if pieces[3] in {"resume", "command"} and hasattr(self, "evidence_jobs"):
                self.evidence_jobs.ensure_idle(self.projects.binding_for_pi(item.binding)["id"])
            if pieces[3] == "resume":
                fields(body)
                try:
                    return self.pi.resume(item.session_id, item.binding)
                except Exception as error:
                    from .pi_sessions import PiSessionAccessError, PiSessionError, PiStartUncertain
                    if isinstance(error, PiSessionAccessError):
                        raise ObservatoryError("pi_forbidden", "This Pi operation is not allowed for the current task binding.", 403) from None
                    if isinstance(error, (PiSessionError, PiStartUncertain)):
                        raise ObservatoryError("pi_recovery_required", str(error), 409) from None
                    raise
            if pieces[3] == "command":
                fields(body, required=("name", "payload", "request_id"))
                request_id = identifier(body["request_id"])
                fingerprint = digest({"session": item.session_id, "body": body})
                try:
                    old = self.store.get("pi-command", session.principal.identity, request_id)
                except ObservatoryError as error:
                    if error.status != 404:
                        raise
                    old = None
                if old:
                    if old["digest"] != fingerprint:
                        raise ObservatoryError("request_conflict", "This command key belongs to a different request.", 409)
                    return old["result"]
                self.projects.validate_pi_binding(item.binding)
                self.store.put("pi-command", session.principal.identity, request_id, {"digest": fingerprint, "result": {"accepted": False, "status": "outcome_unknown"}})
                try:
                    result = self.pi.command(item.session_id, item.binding, body["name"], body["payload"])
                except Exception as error:
                    from .pi_sessions import PiSessionAccessError, PiSessionError, PiStartUncertain
                    if isinstance(error, PiSessionAccessError):
                        raise ObservatoryError("pi_forbidden", "This Pi command is not allowed for the current task binding.", 403) from None
                    if isinstance(error, (PiSessionError, PiStartUncertain)):
                        raise ObservatoryError("pi_recovery_required", str(error), 409) from None
                    raise
                self.store.put("pi-command", session.principal.identity, request_id, {"digest": fingerprint, "result": result})
                return result
        raise ObservatoryError("method_denied", "This Pi action is unavailable.", 405)
