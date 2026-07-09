"""anvil-serving command dispatcher."""
import sys
import os
import subprocess
import difflib

HERE = os.path.dirname(__file__)

MIN_PYTHON = (3, 11)

def _module_main(module_name, attr="main"):
    def _run(argv):
        module = __import__("anvil_serving." + module_name, fromlist=[attr])
        return getattr(module, attr)(argv)
    return _run


def _script_main(script_name):
    return lambda argv: _run_script(script_name, argv)


def _init_main(argv):
    from . import init as _init
    return _init.main(argv)


def _serve_main(argv):
    from .router.serve import main as main
    return main(argv)


def _external_bench_main(argv):
    from .external_benchmarks import cli as external_bench
    return external_bench.main(argv)


COMMANDS = [
    {"group": "Data plane", "name": "serve", "description": "Start the Anthropic/OpenAI router front door.", "handler": _serve_main},
    {"group": "Data plane", "name": "router", "description": "Manage the deployed router container, token, logs, reloads, and promotion.", "handler": _module_main("router_manage")},
    {"group": "Local serving tools", "name": "serves", "description": "Start, stop, inspect, adopt, and read logs for local model serves.", "handler": _module_main("serves")},
    {"group": "Local serving tools", "name": "models", "description": "Sync the model catalog, pull HF repos, and inspect serve recipes.", "handler": _module_main("models")},
    {"group": "Local serving tools", "name": "deploy", "description": "Render tuned model-serve compose and router-tier snippets.", "handler": _module_main("deploy")},
    {"group": "Local serving tools", "name": "init", "description": "Generate a local bring-up from detected model/GPU facts.", "handler": _init_main},
    {"group": "Local serving tools", "name": "onboard", "description": "Alias for init.", "handler": _init_main},
    {"group": "Local serving tools", "name": "doctor", "description": "Check Python, Docker, GPU runtime, and configured tier health.", "handler": _module_main("doctor")},
    {"group": "Local serving tools", "name": "host", "description": "Inspect or repair WSL/Docker Desktop host settings.", "handler": _module_main("host")},
    {"group": "Local serving tools", "name": "preflight", "description": "Correctness-check an OpenAI-compatible model endpoint.", "handler": _script_main("preflight.py")},
    {"group": "Local serving tools", "name": "benchmark", "description": "Replay representative traffic and measure endpoint capacity.", "handler": _script_main("benchmark.py")},
    {"group": "Local serving tools", "name": "external-bench", "description": "Import, report, and compare external benchmark priors.", "handler": _external_bench_main},
    {"group": "Local serving tools", "name": "multiplexer", "description": "Run a single-resident model swap server.", "handler": _module_main("multiplexer")},
    {"group": "Local serving tools", "name": "cache-prune", "description": "Plan and gate local Hugging Face cache cleanup.", "handler": _module_main("cache_prune")},
    {"group": "Quality loop", "name": "profile", "description": "Turn Claude Code logs into usage and role-sizing baselines.", "handler": _module_main("profile")},
    {"group": "Quality loop", "name": "eval", "description": "Run shadow eval, manifest-tier preflight, benchmark, and bootstrap flows.", "handler": _module_main("eval")},
    {"group": "Quality loop", "name": "calibrate", "description": "Measure local tiers and write a reviewable quality profile candidate.", "handler": _module_main("calibrate")},
    {"group": "Quality loop", "name": "score", "description": "Rank models for roles from recorded benchmark evidence.", "handler": _module_main("score")},
    {"group": "Control plane & integrations", "name": "mcp", "description": "Expose operational tools as stdio MCP or controller proxy.", "handler": _module_main("mcp")},
    {"group": "Control plane & integrations", "name": "controller", "description": "Serve the token-authenticated HTTP control plane.", "handler": _module_main("controller")},
    {"group": "Control plane & integrations", "name": "harness", "description": "Render/apply harness config such as OpenClaw.", "handler": _module_main("harness")},
    {"group": "Voice", "name": "voice", "description": "Manage voice serves, realtime server, benchmark, profiles, and bridge.", "handler": _module_main("voice.cli")},
    {"group": "Voice", "name": "voice-sidecar", "description": "Validate/render the HF speech-to-speech sidecar.", "handler": _module_main("voice_sidecar")},
]

COMMAND_BY_NAME = {item["name"]: item for item in COMMANDS}
COMMAND_NAMES = list(COMMAND_BY_NAME)

def _check_python_version(version_info=None):
    """Return an error message if running under an unsupported interpreter, else None."""
    vi = version_info if version_info is not None else sys.version_info
    if (vi[0], vi[1]) < MIN_PYTHON:
        return "anvil-serving needs Python >=%d.%d; you have %d.%d" % (
            MIN_PYTHON[0], MIN_PYTHON[1], vi[0], vi[1],
        )
    return None

def _run_script(name, argv, env=None):
    e = dict(os.environ); e.update(env or {})
    return subprocess.call([sys.executable, os.path.join(HERE, name)] + argv, env=e)

def _print_help():
    print("anvil-serving - quality-gated local-model router and serving workbench")
    print()
    print("Usage:")
    print("  anvil-serving <command> [options]")
    print("  anvil-serving <command> --help")
    print()
    current_group = None
    for item in COMMANDS:
        group = item["group"]
        name = item["name"]
        description = item["description"]
        if group != current_group:
            current_group = group
            print("%s:" % group)
        print("  %-15s %s" % (name, description))
    print()
    print("Examples:")
    print("  anvil-serving serve --config configs/example.toml")
    print("  anvil-serving serves status")
    print("  anvil-serving preflight --base-url http://127.0.0.1:30000/v1 --model local")
    print("  anvil-serving mcp --list-tools")
    print()
    print("Docs: docs/CLI.md")

def _unknown_command(cmd):
    print("unknown command: %s" % cmd, file=sys.stderr)
    matches = difflib.get_close_matches(cmd, COMMAND_NAMES, n=1)
    if matches:
        print("Did you mean '%s'?" % matches[0], file=sys.stderr)
    print("Run 'anvil-serving --help' to see available commands.", file=sys.stderr)
    return 2

def main(argv=None):
    _version_error = _check_python_version()
    if _version_error:
        print(_version_error, file=sys.stderr)
        return 1
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help"):
        _print_help()
        return 0
    cmd, rest = argv[0], argv[1:]
    if cmd in COMMAND_BY_NAME:
        return COMMAND_BY_NAME[cmd]["handler"](rest)
    return _unknown_command(cmd)

if __name__ == "__main__":
    raise SystemExit(main())
