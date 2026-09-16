"""Launch the packaged TypeScript MCP bridge for split-host controller mode."""

from __future__ import annotations

import importlib.resources
import os
import shutil
import subprocess
from collections.abc import Callable

from .controller_client import resolve_controller_token, resolve_controller_token_file
from .errors import ToolError
from .security import safe_controller_url


_FILE_AUTH_ENV = "ANVIL_MCP_CONTROLLER_TOKEN"


def run_node_bridge(
    controller_url: str,
    auth_env: str,
    server_version: str,
    *,
    auth_file: str = "",
    replace_process: bool = True,
    which: Callable[[str], str | None] = shutil.which,
    call: Callable[..., int] = subprocess.call,
) -> int:
    """Run the official-SDK bridge without putting its bearer token in argv."""

    controller_url = safe_controller_url(controller_url)
    if bool(auth_env) == bool(auth_file):
        raise ToolError(
            "bad_auth_source",
            "exactly one of auth-env or auth-file is required",
        )
    child_environment: dict[str, str] | None = None
    bridge_auth_env = auth_env
    if auth_file:
        token = resolve_controller_token_file(auth_file)
        child_environment = os.environ.copy()
        child_environment[_FILE_AUTH_ENV] = token
        bridge_auth_env = _FILE_AUTH_ENV
    else:
        resolve_controller_token(auth_env)
    node = which("node")
    if not node:
        raise ToolError(
            "node_runtime_missing",
            "Node.js 20 or newer is required for remote MCP controller mode",
        )
    asset = importlib.resources.files("anvil_serving._node").joinpath(
        "mcp_proxy.mjs"
    )
    with importlib.resources.as_file(asset) as script:
        argv = [
            node,
            str(script),
            "--controller-url",
            controller_url,
            "--auth-env",
            bridge_auth_env,
            "--server-version",
            server_version,
        ]
        if replace_process and os.name == "posix" and child_environment is None:
            os.execv(node, argv)
        if replace_process and os.name == "posix":
            os.execve(node, argv, child_environment)
        if child_environment is None:
            return int(call(argv))
        return int(call(argv, env=child_environment))
