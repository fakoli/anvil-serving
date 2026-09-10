"""The diagnostic review must survive the real controller's privacy boundary."""

import pytest

from anvil_serving import mcp
from anvil_serving.control_plane.mcp.tools import services
from anvil_serving.observability.dashboard.controller_adapter import ControllerAdapter
from anvil_serving.transports import Operation
from anvil_serving.workbench_app.container_exec import ContainerExec
from tests.observability.test_controller_adapter import config
from tests.test_controller import running_controller


def test_diagnostic_http_preview_keeps_argv_private_and_apply_pinned(monkeypatch, tmp_path):
    path = tmp_path / "container-exec.toml"
    policy = (
        '[[bindings]]\nid = "serve.chat"\nhost_id = "host-a"\n'
        'execution_runtime = "native"\nservice = "chat"\ncontainer_id = "' + "a" * 64 + '"\n'
        '[bindings.commands]\nhealth = ["/usr/local/bin/health"]\n'
    )
    path.write_text(policy, encoding="utf-8")
    monkeypatch.setattr(services, "config_path", lambda _: str(path))
    executions = []
    monkeypatch.setattr(ContainerExec, "execute", lambda self, preview, **kwargs:
        executions.append(preview) or {"status": "completed", "exit_code": 0,
                                       "output": "ready\n", "truncated": False})

    def dispatch(name, arguments=None):
        if name == "container_exec":
            return mcp.call_tool(name, arguments)
        assert name == "serves_status"
        return {"ok": True, "data": {"serves": [{"name": "chat", "state": "running"}]}}

    environment = {"ANVIL_CONTROLLER_TOKEN_TOKEN": "fixture-controller-auth"}
    with running_controller(auth_token_env="ANVIL_CONTROLLER_TOKEN_TOKEN", env=environment,
                            allow_unauthenticated_loopback=False, node_id="host-a",
                            allowed_operations=("serves_status", "container_exec"),
                            call_tool_func=dispatch) as (host, port):
        cfg = config()
        cfg["controller"]["url"] = f"http://{host}:{port}"
        cfg["resources"][0].update(container_id="a" * 64, execution_runtime="native", exec_commands=["health"])
        adapter = ControllerAdapter(cfg, environment)
        assert adapter.snapshot()["serves"][0]["exec"]["status"] == "available"
        preview = adapter.preview("serve.chat", "container.exec", parameters={"command_id": "health"})
        assert preview["diagnostic"] == {"command_id": "health", "container_id": "a" * 64,
                                        "timeout_seconds": 10, "max_output_bytes": 65536}
        assert "/usr/local/bin/health" not in repr(preview)
        assert "argv" not in preview["binding"]["arguments"]
        wire = adapter._transport.execute(Operation("container_exec", {
            **preview["binding"]["arguments"], "dry_run": True, "confirm": False,
        }))
        assert wire.data["data"]["preview"]["argv"] == "<redacted>"
        with pytest.raises(ValueError):
            Operation("container_exec", {"argv": ["anything"]})
        result = adapter.execute(preview, "intent.http-diagnostic")
        assert result["execution_outcome"] == "succeeded"
        assert result["evidence"]["output"] == "ready\n"
        assert len(executions) == 1
        assert executions[0]["argv"] == ["/usr/local/bin/health"]
        path.write_text(policy.replace("/usr/local/bin/health", "/usr/local/bin/changed"), encoding="utf-8")
        stale = adapter.execute(preview, "intent.http-stale")
        assert stale["execution_outcome"] != "succeeded"
        assert len(executions) == 1


@pytest.mark.parametrize("field,value", [
    ("policy_digest", "p" * 64), ("candidate_digest", ""),
    ("timeout_seconds", True), ("timeout_seconds", 31),
    ("max_output_bytes", 0), ("max_output_bytes", 65537),
])
def test_diagnostic_review_rejects_invalid_owner_pins_and_bounds(field, value):
    resource = {"id": "serve.chat", "host_id": "host-a", "execution_runtime": "native", "container_id": "a" * 64}
    preview = {"resource_id": resource["id"], "host_id": resource["host_id"],
               "execution_runtime": resource["execution_runtime"], "container_id": resource["container_id"],
               "command_id": "health", "argv": "<redacted>", "policy_digest": "b" * 64,
               "candidate_digest": "c" * 64, "timeout_seconds": 10, "max_output_bytes": 65536}
    preview[field] = value
    assert ControllerAdapter._container_preview(resource, "health", {"preview": preview}) is None
