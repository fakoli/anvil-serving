"""``anvil-serving scribe``: run or validate the Anvil Scribe service."""
from __future__ import annotations

import argparse
import json
import shutil
import sys

from . import service


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="anvil-serving scribe",
        description="Anvil Scribe: record-first dictation with saved rewrite profiles (ADR-0048).",
    )
    sub = parser.add_subparsers(dest="action", required=True, metavar="{run,validate}")
    for action, help_text in (
        ("run", "run the loopback Scribe service in the foreground"),
        ("validate", "check the Scribe config and its references offline, without binding"),
    ):
        sp = sub.add_parser(action, help=help_text)
        sp.add_argument("--config", metavar="PATH", help="Scribe config TOML; defaults to $ANVIL_SERVING_HOME/scribe.toml")
    return parser


def _load(args, action):
    try:
        return service.load_config(args.config)
    except service.ConfigError as exc:
        print("scribe %s: %s" % (action, exc), file=sys.stderr)
        return None


def cmd_validate(args) -> int:
    config = _load(args, "validate")
    if config is None:
        return 2
    # Presence only: never print the key, the logins, or the target aliases.
    print("scribe validate: " + json.dumps({
        "ok": True, "listen": "%s:%d" % (config.listen_host, config.listen_port),
        "router_url": config.router_url, "stt_route": config.route, "data_dir": str(config.data_dir),
        "logins": len(config.allowed_logins), "targets": sorted(config.targets),
        "builtin_profiles": len(service.BUILTIN_PROFILES), "ffmpeg": shutil.which("ffmpeg") is not None,
    }, sort_keys=True))
    return 0


def cmd_run(args) -> int:
    """Foreground service; Compose supplies supervision (ADR-0048)."""
    config = _load(args, "run")
    if config is None:
        return 2
    if shutil.which("ffmpeg") is None:
        print("scribe run: ffmpeg is required to decode Ogg Opus recordings", file=sys.stderr)
        return 2
    try:
        server = service.make_server(config)
    except OSError as exc:
        print("scribe run: cannot bind or prepare the store (%s)" % exc, file=sys.stderr)
        return 1
    print("scribe run: listening on http://%s:%d" % server.server_address[:2], file=sys.stderr, flush=True)
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
