"""MCP stdio transport loop and CLI argument coordination."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable, Iterable
from typing import Any

from .errors import ToolError
from .protocol import jsonrpc_error


def serve_stdio(
    stdin: Iterable[str],
    stdout: Any,
    *,
    controller_url: str,
    controller_token: str,
    handle_local_request: Callable[[dict], dict | None],
    handle_remote_request: Callable[[dict, str, str], dict | None],
) -> int:
    for line in stdin:
        if not line.strip():
            continue
        try:
            request = json.loads(line)
        except ValueError as exc:
            response = {
                "jsonrpc": "2.0",
                "id": None,
                "error": {
                    "code": -32700,
                    "message": str(exc),
                },
            }
        else:
            if not isinstance(request, dict):
                response = jsonrpc_error(
                    None,
                    -32600,
                    "request must be a JSON object",
                )
            elif controller_url:
                response = handle_remote_request(
                    request,
                    controller_url,
                    controller_token,
                )
            else:
                response = handle_local_request(request)
        if response is not None:
            stdout.write(
                json.dumps(response, separators=(",", ":")) + "\n"
            )
            stdout.flush()
    return 0


def build_main_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="anvil-serving mcp serve",
        description=(
            "Run the stdio MCP control plane locally, list available tools, "
            "or proxy MCP tool calls to a token-authenticated controller."
        ),
    )
    parser.add_argument(
        "action",
        nargs="?",
        choices=["list-tools"],
        help="compatibility alias for --list-tools",
    )
    parser.add_argument(
        "--list-tools",
        action="store_true",
        help="print the MCP tool catalog as JSON and exit",
    )
    parser.add_argument(
        "--controller-url",
        metavar="URL",
        help="remote controller URL for split-host proxy mode",
    )
    parser.add_argument(
        "--auth-env",
        metavar="ENV",
        help="environment variable containing the controller token",
    )
    return parser


def parse_main_args(argv: list[str]) -> tuple[str, str, bool]:
    parser = build_main_parser()
    args = parser.parse_args(argv)
    list_tools_requested = bool(args.list_tools or args.action == "list-tools")
    if list_tools_requested and (args.controller_url or args.auth_env):
        parser.error("--list-tools cannot be combined with proxy mode")
    if bool(args.controller_url) != bool(args.auth_env):
        parser.error(
            "--controller-url and --auth-env must be provided together"
        )
    return args.controller_url or "", args.auth_env or "", list_tools_requested


def main(
    argv: list[str],
    *,
    list_tools: Callable[[], list[dict]],
    safe_controller_url: Callable[[str], str],
    resolve_controller_token: Callable[[str], str],
    serve: Callable[..., int],
) -> int:
    try:
        controller_url, auth_env, list_tools_requested = parse_main_args(argv)
    except SystemExit as exc:
        if exc.code == 0:
            raise
        return int(exc.code or 2)
    if list_tools_requested:
        print(
            json.dumps({"tools": list_tools()}, indent=2, sort_keys=True),
        )
        return 0
    if controller_url:
        try:
            controller_url = safe_controller_url(controller_url)
            token = resolve_controller_token(auth_env)
        except ToolError as exc:
            print(exc.message, file=sys.stderr)
            return 2
        return serve(
            controller_url=controller_url,
            controller_token=token,
        )
    return serve()
