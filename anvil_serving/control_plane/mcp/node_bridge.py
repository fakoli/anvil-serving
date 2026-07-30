"""Launch the packaged TypeScript MCP bridge for split-host controller mode."""

from __future__ import annotations

import importlib.resources
import os
import shutil
import subprocess
from collections.abc import Callable

from .controller_client import resolve_controller_token
from .errors import ToolError
from .security import safe_controller_url


def run_node_bridge(
    controller_url: str,
    auth_env: str,
    server_version: str,
    *,
    replace_process: bool = True,
    which: Callable[[str], str | None] = shutil.which,
    call: Callable[..., int] = subprocess.call,
) -> int:
    """Run the official-SDK bridge without putting its bearer token in argv."""

    controller_url = safe_controller_url(controller_url)
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
            auth_env,
            "--server-version",
            server_version,
        ]
        if replace_process and os.name == "posix":
            os.execv(node, argv)
        return int(call(argv))
