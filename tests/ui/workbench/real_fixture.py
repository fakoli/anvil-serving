"""Rich, loopback-only Workbench browser fixture with real Console authorities.

All owner/task/model behavior is deterministic and isolated. No production
controller, Anvil checkout, account, credential, or inference endpoint is used.
"""

from __future__ import annotations

from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import runpy
import ssl
import subprocess
import urllib.request
import sys
import tempfile
import threading
import time

from anvil_serving.observability.api import TelemetryRegistry, run_server_in_thread
from anvil_serving.observability.dashboard.app import create_dashboard_server
from anvil_serving.observability.dashboard.console import Console, attach_console
from anvil_serving.observability.dashboard.contracts import ObservatoryError, digest
from anvil_serving.workbench_app.projects import Projects
from anvil_serving.workbench_app.playground import NoRedirect
from anvil_serving.workbench_app.pi_sessions import (
    PiEventPage,
    PiSession,
    PiSessionEvent,
    PiTaskBinding,
)

BASE = runpy.run_path(
    str(Path(__file__).parents[2] / "observability" / "test_observatory_operations.py")
)


class Owner(BASE["FakeOwner"]):
    def snapshot(self):
        result = super().snapshot()
        result["services"] = self.read_services()
        return result

    def workload_logs(self, resource_id):
        identities = {
            "serve-fixture-a": "fixture-atlas",
            "service-fixture-native": "fixture-collector.service",
            "service-fixture-container": "fixture-gateway",
        }
        if resource_id not in identities:
            raise ObservatoryError("not_found", "Unknown fixture workload.", 404)
        return {
            "resource_id": resource_id,
            "identity": identities[resource_id],
            "text": f"[isolated fixture] {identities[resource_id]} ready\nBounded owner log read completed.\n",
            "observed_at": time.time(),
            "tail": 200,
            "truncated": False,
        }

    def controls(self, resource):
        if resource.startswith("service-fixture-"):
            actions = [("service.start", "Start"), ("service.stop", "Stop")]
            if resource == "service-fixture-container":
                actions.append(("container.exec", "Run container diagnostic"))
            return {"resource_id": resource, "baseline_digest": digest(resource), "settings": [],
                    "actions": [{"id": key, "label": label, "supported": True} for key, label in actions]}
        if resource == "experiment-fixture":
            return {
                "resource_id": resource,
                "baseline_digest": digest("fixture-baseline"),
                "experiment_class": "request_only",
                "experiment_limit": 1,
                "actions": [
                    {
                        "id": "experiment.start",
                        "label": "Run deterministic check",
                        "supported": True,
                    }
                ],
                "settings": [],
                "experiment_settings": [
                    {
                        "setting_id": "max_tokens",
                        "label": "Output limit",
                        "unit": "tokens",
                        "value_type": "integer",
                        "support": "supported",
                        "configured": 64,
                        "constraints": {"minimum": 1, "maximum": 128},
                    }
                ],
            }
        return super().controls(resource)

    def preview(self, resource_id, action_id, values=None, parameters=None):
        if resource_id.startswith("service-fixture-"):
            if action_id == "container.exec" and parameters != {"command_id": "health"}:
                raise ObservatoryError("fixture_parameters", "Choose the declared health diagnostic.")
            return {"host_id": "host-fixture-a", "resource_id": resource_id, "action_id": action_id,
                    "label": "Container health diagnostic" if action_id == "container.exec" else action_id,
                    "baseline_digest": digest(resource_id), "candidate_digest": digest(parameters or {}),
                    "effect": "Runs the declared bounded health diagnostic in fixture-gateway." if action_id == "container.exec" else "Changes the isolated fixture service state.",
                    "planned_steps": ["fixture_owner_operation"], "diff": []}
        if action_id != "experiment.start":
            return super().preview(resource_id, action_id, values=values, parameters=parameters)
        parameters = parameters or {}
        if (
            set(parameters) != {"max_tokens"}
            or type(parameters["max_tokens"]) is not int
            or not 1 <= parameters["max_tokens"] <= 128
        ):
            raise ObservatoryError(
                "fixture_parameters", "Choose a fixture output limit between 1 and 128."
            )
        return {
            "host_id": "host-fixture-a",
            "resource_id": resource_id,
            "action_id": action_id,
            "label": "Deterministic response check",
            "baseline_digest": digest("fixture-baseline"),
            "candidate_digest": digest(parameters),
            "effect": "request_only",
            "diff": [{"field": "max_tokens", "before": 64, "after": parameters["max_tokens"]}],
            "recovery": "The fixture changes no model placement or runtime configuration.",
        }

    def execute(self, preview, intent_key):
        if preview["action_id"] == "container.exec":
            self.mutations += 1
            result = {"ok": True, "owner_operation_id": intent_key, "native_state": "completed", "execution_outcome": "succeeded",
                      "evidence": {"kind": "container_exec", "status": "completed", "exit_code": 0, "output": "fixture-gateway ready\n", "truncated": False}}
            self.completed[intent_key] = result
            return result
        if preview["action_id"] != "experiment.start":
            return super().execute(preview, intent_key)
        self.mutations += 1
        time.sleep(0.2)
        result = {
            "ok": True,
            "owner_operation_id": intent_key,
            "native_state": "succeeded",
            "execution_outcome": "succeeded",
            "evidence": {
                "kind": "request_experiment",
                "label": "Deterministic response fixture",
                "correctness": "passed",
                "fixture": True,
                "comparison_dimensions": {
                    "host": "host-fixture-a",
                    "model_revision": "fixture-v1",
                    "concurrency": 1,
                    "output_constraints": preview["private_parameters"],
                },
                "limitations": [
                    "Deterministic browser fixture; no inference or production benchmark."
                ],
            },
        }
        self.completed[intent_key] = result
        return result

    def verify(self, preview, result):
        return {"status": "passed", "message": "Independent deterministic fixture check passed."}

    def read_services(self, resource_id=None):
        rows = [
            {
                "id": "service-fixture-native",
                "resource_id": "service-fixture-native",
                "host_id": "host-fixture-a",
                "label": "Telemetry collector",
                "display_name": "Telemetry collector",
                "kind": "native",
                "manager": "systemd",
                "identity": "fixture-collector.service",
                "state": "running",
                "runtime_state": "running",
                "logs": {"status": "available"},
                "exec": {"status": "unsupported"},
            },
            {
                "id": "service-fixture-container",
                "resource_id": "service-fixture-container",
                "host_id": "host-fixture-a",
                "label": "Fixture gateway",
                "display_name": "Fixture gateway",
                "kind": "container",
                "manager": "docker",
                "identity": "fixture-gateway",
                "state": "running",
                "runtime_state": "running",
                "logs": {"status": "available"},
                "exec": {"status": "available", "commands": ["health"]},
            },
        ]
        return [row for row in rows if not resource_id or row["id"] == resource_id]


class Metrics(BASE["FakeMetrics"]):
    def chart(self, chart_id, **kwargs):
        now = time.time()
        return {"id": chart_id, "title": chart_id.replace("_", " ").title(), "status": "fresh", "unit": "fixture units",
                "source": "Deterministic fixture", "window": kwargs.get("range_id", "15m"),
                "series": [{"id": "fixture-series", "label": "Synthetic test samples", "points": [[now - 180, 12], [now - 120, 18], [now - 60, None], [now, 16]]}]}

    def snapshot(self):
        now = time.time()
        return {
            "hosts": [
                {
                    "id": "host-fixture-a",
                    "display_name": "Compute host",
                    "controller": {"status": "complete"},
                    "telemetry": {"status": "fresh", "observed_at": now},
                    "gpus": [],
                },
                {
                    "id": "host-fixture-offline",
                    "display_name": "Harness host",
                    "controller": {"status": "unavailable"},
                    "telemetry": {"status": "unavailable"},
                    "gpus": [],
                },
            ],
            "serves": [
                {
                    "id": "serve-fixture-a",
                    "display_name": "Atlas fixture",
                    "host_id": "host-fixture-a",
                    "model": "atlas-fixture",
                    "readiness": "ready",
                    "runtime_state": "running",
                    "observed_at": now,
                    "container": "fixture-atlas",
                }
            ],
            "coverage": {"status": "partial"},
            "observed_at": now,
        }


class FixtureProjects(Projects):
    def cli(self, project, *args):
        tasks = [
            {
                "id": "workspace:T001",
                "title": "Retain a reproducible request",
                "status": "ready",
                "priority": "high",
                "feature_id": "workspace:F001",
                "dependencies": [],
                "acceptance_criteria": ["The saved evidence records the exact declared target."],
            },
            {
                "id": "workspace:T002",
                "title": "Review independent evidence",
                "status": "blocked",
                "priority": "medium",
                "feature_id": "workspace:F001",
                "dependencies": ["workspace:T001"],
                "acceptance_criteria": ["An independent reviewer checks the retained result."],
            },
        ]
        prd = {
            "id": "workspace",
            "title": "Reproducible local research",
            "status": "approved",
            "summary": "Keep task intent and independently checked evidence together.",
        }
        if args[0] == "status":
            return {
                "initialized": True,
                "project_name": project["label"],
                "prd_status": "approved",
                "task_counts": {"ready": 1, "blocked": 1},
                "active_claims": [],
            }
        if args[:2] == ("prd", "list"):
            return {"prds": [prd]}
        if args[:2] == ("prd", "show"):
            return {"schema_id": "anvil.state.prd-content.v1", "content": "# Reproducible local research\n\nKeep task intent and independently checked evidence together.\n\n## Acceptance\n\n- Retain the exact target and request.\n- Review the result independently.", "source_digest": "a" * 64, "prd_revision": 1}
        if args[0] == "list":
            return {"tasks": tasks}
        task = next((task for task in tasks if task["id"] == args[1]), None)
        if not task:
            raise ObservatoryError("not_found", "This fixture task does not exist.", 404)
        if args[0] == "show":
            return {"task": task, "active_claims": []}
        if args[0] == "packet":
            return {
                "task": task,
                "dependencies_open": task["dependencies"],
                "instructions": "Isolated UI fixture. Execution is intentionally unavailable.",
            }
        raise ObservatoryError("fixture_read_only", "This fixture never executes Anvil tasks.", 409)

    def pi_binding(self, row):
        return PiTaskBinding(
            principal_id=row["owner"],
            project_id=row["project_id"],
            task_id=row["task_id"],
            lease_id=row["lease_id"],
            runner_id="fixture-pi-runner",
            provider_id=row["provider_id"],
        )

    def binding_for_pi(self, binding):
        for row in self.store.list("task-binding", binding.principal_id):
            if self.pi_binding(row).fingerprint == binding.fingerprint:
                return row
        raise ObservatoryError("not_found", "This fixture Pi binding is unavailable.", 404)

    def validate_pi_binding(self, binding):
        # This is a fixture-only ownership seam. Production validates the live
        # Anvil lease before every mutation.
        self.binding_for_pi(binding)


class FixturePiStore:
    """Deterministic retained Pi history for browser interaction checks only."""

    def __init__(self, binding):
        now = time.time()
        self.lock = threading.RLock()
        self.reads = 0
        self.session = PiSession(
            session_id="fixture-pi-session",
            binding=binding,
            created_at=now - 120,
            updated_at=now,
            start_key="fixture-retained-start",
            model_id="atlas-fixture",
            thinking_level="low",
            official_session_id="fixture-native-pi-session",
            status="running",
            first_cursor=1,
            next_cursor=9,
        )
        self.events = [
            self._event(1, "command_accepted", {
                "name": "prompt",
                "command_id": "fixture-command-1",
                "message": "Retained fixture request",
            }),
            self._event(2, "event", {
                "type": "message_start",
                "message": {"role": "user", "content": [{"type": "text", "text": "Retained fixture request"}]},
            }),
            self._event(3, "event", {
                "type": "message_end",
                "message": {"role": "user", "content": [{"type": "text", "text": "Retained fixture request"}]},
            }),
            self._event(4, "event", {
                "type": "message_start",
                "message": {"role": "assistant", "content": []},
            }),
            self._event(5, "text", {
                "type": "message_update",
                "message": {"role": "assistant", "content": [{"type": "text", "text": "Fixture assistant "}]},
                "assistantMessageEvent": {"type": "text_delta", "contentIndex": 0, "delta": "Fixture assistant "},
            }),
            self._event(6, "text", {
                "type": "message_update",
                "message": {"role": "assistant", "content": [{"type": "text", "text": "Fixture assistant reply."}]},
                "assistantMessageEvent": {"type": "text_delta", "contentIndex": 0, "delta": "reply."},
            }),
            self._event(7, "event", {
                "type": "message_end",
                "message": {"role": "assistant", "content": [{"type": "text", "text": "Fixture assistant reply."}]},
            }),
            self._event(8, "extension", {
                "type": "extension_ui_request",
                "id": "fixture-extension-input",
                "method": "input",
                "title": "Fixture extension input",
                "placeholder": "Type while fixture events advance",
            }),
        ]

    @staticmethod
    def _event(cursor, kind, data):
        return PiSessionEvent(cursor, time.time(), kind, data)

    def get(self, session_id):
        if session_id != self.session.session_id:
            raise ValueError("unknown fixture Pi session")
        return self.session

    def list_for(self, binding):
        return (self.session,) if binding.fingerprint == self.session.binding.fingerprint else ()

    def all(self):
        return (self.session,)

    def events_after(self, session_id, cursor, *, limit=100):
        with self.lock:
            self.get(session_id)
            self.reads += 1
            # Every poll after initial history advances the cursor with a
            # native-shaped lifecycle record. The transcript remains stable,
            # which makes focus/cursor preservation observable in a browser.
            if self.reads > 1 and len(self.events) < 256:
                next_cursor = self.events[-1].cursor + 1
                self.events.append(self._event(next_cursor, "lifecycle", {
                    "type": "agent_start",
                    "fixture_poll": self.reads,
                }))
                self.session = replace(
                    self.session,
                    updated_at=time.time(),
                    next_cursor=next_cursor + 1,
                )
            selected = tuple(event for event in self.events if event.cursor > cursor)[:limit]
            return PiEventPage(
                selected,
                selected[-1].cursor if selected else cursor,
                False,
            )

    def append(self, kind, data):
        with self.lock:
            cursor = self.events[-1].cursor + 1
            event = self._event(cursor, kind, data)
            self.events.append(event)
            self.session = replace(
                self.session,
                updated_at=time.time(),
                next_cursor=cursor + 1,
            )
            return event


class FixturePi:
    """No-process Pi coordinator stub for the isolated browser fixture."""

    def __init__(self, store):
        self.store = store
        self._clients = {}
        self.commands = []

    @staticmethod
    def _session_json(session):
        return {
            "session_id": session.session_id,
            "status": session.status,
            "parent_session_id": session.parent_session_id,
            "provider_id": session.binding.provider_id,
            "model_id": session.model_id,
            "thinking_level": session.thinking_level,
            "official_session_id": session.official_session_id,
            "created_at": session.created_at,
            "updated_at": session.updated_at,
            "first_cursor": session.first_cursor,
            "next_cursor": session.next_cursor,
        }

    def command(self, session_id, binding, name, payload):
        session = self.store.get(session_id)
        if binding.fingerprint != session.binding.fingerprint or session.status != "running":
            raise ObservatoryError("fixture_pi_unavailable", "The fixture Pi session is unavailable.", 409)
        command_id = f"fixture-command-{len(self.commands) + 2}"
        metadata = {"name": name, "command_id": command_id}
        if name in {"prompt", "steer"}:
            metadata["message"] = payload.get("message", "")
        elif name == "extension_response":
            metadata["request_id"] = payload.get("request_id")
        self.commands.append({"name": name, "payload": dict(payload)})
        self.store.append("command_accepted", metadata)
        return {"accepted": True, "command_id": command_id}

    def stop(self, session_id, binding, *, validate=True):
        session = self.store.get(session_id)
        if binding.fingerprint != session.binding.fingerprint:
            raise ObservatoryError("fixture_pi_unavailable", "The fixture Pi session is unavailable.", 409)
        self.store.session = replace(session, status="recoverable", updated_at=time.time())
        return self._session_json(self.store.session)

    def resume(self, session_id, binding):
        session = self.store.get(session_id)
        if binding.fingerprint != session.binding.fingerprint:
            raise ObservatoryError("fixture_pi_unavailable", "The fixture Pi session is unavailable.", 409)
        self.store.session = replace(session, status="running", updated_at=time.time())
        return self._session_json(self.store.session)

    def delete(self, *_args, **_kwargs):
        raise ObservatoryError("fixture_read_only", "The fixture retains its Pi browser history.", 409)

    def sweep(self):
        return ()

    def close(self):
        pass


class Logs:
    def read(self, query, principal, *, sources=False):
        if sources:
            return {"containers": [{"name": "fixture-gateway"}, {"name": "fixture-atlas"}]}
        lines = [
            "Fixture gateway accepted an authorized request.",
            "Fixture stream completed successfully.",
        ]
        rows = [
            {
                "timestamp_ns": str(int(time.time() * 10**9) - index * 10**9),
                "host_id": "host-fixture-a",
                "container": "fixture-gateway",
                "stream": "stdout",
                "line": line,
                "line_truncated": False,
            }
            for index, line in enumerate(lines)
            if query.get("search", "").lower() in line.lower()
        ]
        return {"items": rows, "observed_at": time.time(), "limit": 100, "limit_reached": False}


class Model(BaseHTTPRequestHandler):
    requests = 0

    def log_message(self, *_args):
        pass

    def do_POST(self):
        data = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        assert self.path == "/v1/chat/completions" and data["model"] in {
            "atlas-fixture",
            "finch-fixture",
        }
        type(self).requests += 1
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        try:
            for word in (
                "Fixture response: ",
                "your declared target ",
                "and request were retained.",
            ):
                self.wfile.write(
                    (
                        "data: " + json.dumps({"choices": [{"delta": {"content": word}}]}) + "\n\n"
                    ).encode()
                )
                self.wfile.flush()
                time.sleep(0.1)
            self.wfile.write(
                b'data: {"choices":[],"usage":{"prompt_tokens":12,"completion_tokens":9,"total_tokens":21}}\n\ndata: [DONE]\n\n'
            )
            self.wfile.flush()
        except BrokenPipeError:
            pass


def main():
    with tempfile.TemporaryDirectory(
        prefix="workbench-browser-", dir=sys.argv[1] if len(sys.argv) > 1 else None
    ) as temporary:
        root = Path(temporary)
        certificate, key = root / "fixture-cert.pem", root / "fixture-key.pem"
        subprocess.run(
            [
                "openssl",
                "req",
                "-x509",
                "-newkey",
                "rsa:2048",
                "-nodes",
                "-keyout",
                str(key),
                "-out",
                str(certificate),
                "-days",
                "1",
                "-subj",
                "/CN=isolated-fixture",
                "-addext",
                "subjectAltName=IP:127.0.0.1",
            ],
            check=True,
            capture_output=True,
        )
        key.chmod(0o600)
        tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        tls.load_cert_chain(certificate, key)
        model = ThreadingHTTPServer(("127.0.0.1", 0), Model)
        model.socket = tls.wrap_socket(model.socket, server_side=True)
        model_thread = threading.Thread(target=model.serve_forever, daemon=True)
        model_thread.start()
        server = create_dashboard_server(TelemetryRegistry(), port=0, environment={})
        origin = f"http://127.0.0.1:{server.server_address[1]}"
        resources = [
            {
                "id": "serve-fixture-a",
                "host_id": "host-fixture-a",
                "kind": "configuration",
                "label": "Atlas fixture recipe",
            },
            {
                "id": "experiment-fixture",
                "host_id": "host-fixture-a",
                "kind": "experiment",
                "label": "Deterministic response check",
            },
        ]
        config = {
            "origin": origin,
            "base_path": "/workbench-fixture/",
            "operate": True,
            "users": [
                {
                    "id": "operator-fixture",
                    "username": "operator",
                    "role": "operator",
                    "resources": ["*"],
                    "actions": [
                        "configuration.apply",
                        "tier.quiesce",
                        "experiment.start",
                        "playground.request",
                        "project.execute",
                        "service.start",
                        "service.stop",
                        "container.exec",
                    ],
                }
            ],
            "authentication": {},
            "state_path": str(root / "journal.sqlite"),
            "fixture": True,
            "build": "isolated-real-console-fixture",
            "controller": {"resources": resources},
            "workbench": {
                "state_path": str(root / "private.sqlite"),
                "projects": [
                    {
                        "id": "research-fixture",
                        "label": "Local research fixture",
                        "resource_id": "project-fixture",
                        "checkout": str(root),
                        "anvil_binary": "/fixture/never-executed",
                    }
                ],
                "connectors": [
                    {
                        "id": "fixture-model",
                        "label": "Deterministic local fixture",
                        "resource_id": "serve-fixture-a",
                        "models": ["atlas-fixture", "finch-fixture"],
                        "host_id": "host-fixture-a",
                        "base_url": f"https://127.0.0.1:{model.server_port}/v1",
                    }
                ],
                "presets": [
                    {
                        "id": "precise",
                        "label": "Precise response",
                        "temperature": 0.2,
                        "max_tokens": 128,
                        "system": "You are a deterministic UI fixture.",
                    }
                ],
            },
        }
        owner = Owner()
        console = Console(
            config,
            adapter=owner,
            metrics=Metrics(),
            authenticate=lambda u, p: u == "operator" and p == "fixture-password",
        )
        model_trust = ssl.create_default_context(cafile=str(certificate))
        console.workbench.playground.open_request = urllib.request.build_opener(
            urllib.request.ProxyHandler({}),
            NoRedirect(),
            urllib.request.HTTPSHandler(context=model_trust),
        ).open
        console.logs = Logs()
        console.workbench.projects = FixtureProjects(
            config["workbench"], console.workbench.store, console.access
        )
        fixture_binding_row = {
            "id": "fixture-pi-binding",
            "project_id": "research-fixture",
            "task_id": "workspace:T001",
            "owner": "operator-fixture",
            "lease_id": "fixture-pi-lease",
            "provider_id": "fixture-provider-a",
            "status": "ready",
        }
        console.workbench.store.put(
            "task-binding", "operator-fixture", fixture_binding_row["id"], fixture_binding_row
        )
        fixture_pi_store = FixturePiStore(
            console.workbench.projects.pi_binding(fixture_binding_row)
        )
        fixture_pi = FixturePi(fixture_pi_store)
        console.workbench.pi_store = fixture_pi_store
        console.workbench.pi = fixture_pi
        original_catalog = console.workbench.catalog

        def catalog(session):
            data = original_catalog(session)
            data["pi"] = {
                "configured": True,
                "models": {
                    "fixture-provider-a": ["atlas-fixture", "finch-fixture"],
                    "fixture-provider-b": ["orion-fixture"],
                },
                "thinking": ["off", "low", "high"],
                "network": "none",
            }
            return data

        console.workbench.catalog = catalog
        attach_console(server, console)
        thread = run_server_in_thread(server)
        print(
            json.dumps(
                {
                    "url": origin + "/workbench-fixture/",
                    "fixture": True,
                    "username": "operator",
                    "password": "fixture-password",
                }
            ),
            flush=True,
        )
        try:
            for line in sys.stdin:
                command = json.loads(line).get("command")
                if command == "stop":
                    break
                if command != "status":
                    raise ValueError("Unsupported fixture control")
                print(
                    json.dumps(
                        {
                            "fixture": True,
                            "owner_mutations": owner.mutations,
                            "model_requests": Model.requests,
                            "pi_event_reads": fixture_pi_store.reads,
                            "pi_commands": fixture_pi.commands,
                        }
                    ),
                    flush=True,
                )
        finally:
            server.shutdown()
            server.server_close()
            thread.join()
            console.close()
            model.shutdown()
            model.server_close()
            model_thread.join()


if __name__ == "__main__":
    main()
