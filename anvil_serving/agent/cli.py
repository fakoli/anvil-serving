"""``anvil-serving agent``: run or validate the Anvil Agent service."""
from __future__ import annotations

import argparse
import json
import shutil
import sys

from . import service


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="anvil-serving agent",
        description="Anvil Agent: glasses turns to your own agents, and the memomind_glasses MCP tool (ADR-0049).",
    )
    sub = parser.add_subparsers(dest="action", required=True, metavar="{run,validate}")
    for action, help_text in (
        ("run", "run the loopback Anvil Agent service in the foreground"),
        ("validate", "check the Anvil Agent config and its references offline, without binding"),
    ):
        sp = sub.add_parser(action, help=help_text)
        sp.add_argument("--config", help="Anvil Agent config TOML; defaults to $ANVIL_SERVING_HOME/agent.toml")
    return parser


def _load(args, action):
    try:
        return service.load_config(args.config)
    except service.ConfigError as exc:
        print("agent %s: %s" % (action, exc), file=sys.stderr)
        return None


def cmd_validate(args) -> int:
    config = _load(args, "validate")
    if config is None:
        return 2
    # Presence only: never print keys, logins or session ids.
    summary = {**service.config_summary(config), "ffmpeg": shutil.which("ffmpeg") is not None}
    print("agent validate: " + json.dumps(summary, sort_keys=True))
    return 0


def cmd_run(args) -> int:
    """Foreground service; Compose supplies supervision (ADR-0049)."""
    config = _load(args, "run")
    if config is None:
        return 2
    if shutil.which("ffmpeg") is None:
        print("agent run: ffmpeg is required to decode spoken turns", file=sys.stderr)
        return 2
    try:
        server = service.make_server(config)
    except OSError as exc:
        print("agent run: cannot bind (%s)" % exc, file=sys.stderr)
        return 1
    print("agent run: backends %s; listening on http://%s:%d" % (
        ", ".join(config.backends), *server.server_address[:2]), file=sys.stderr, flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return {"run": cmd_run, "validate": cmd_validate}[args.action](args)


if __name__ == "__main__":
    raise SystemExit(main())
