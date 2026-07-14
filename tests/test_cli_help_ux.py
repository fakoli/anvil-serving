"""Reviewed navigation contracts for the migrated operator CLI families."""

from __future__ import annotations

from pathlib import Path
import shlex

import pytest

from anvil_serving import (
    benchmark_evidence,
    cli,
    collectors,
    controller,
    edge,
    gpu_sharing,
    harness,
    host,
    mcp,
    models,
    multiplexer,
    router_endpoint,
    router_manage,
    serves,
    topology_cli,
    voice_sidecar,
)
from anvil_serving.command_tree import COMMAND_TREE, CommandNode, HandlerRef
from anvil_serving.external_benchmarks import store as external_benchmark_store
from anvil_serving.observability.dashboard import app as dashboard_app
from anvil_serving.router import serve as router_serve
from anvil_serving.voice import cli as voice_cli


SERVES_LEAVES = (
    "render",
    "up",
    "down",
    "rm",
    "adopt",
    "switch",
    "promote",
    "status",
    "groups",
    "logs",
    "multiplex",
)

MODELS_LEAVES = (
    ("sync",),
    ("pull",),
    ("score",),
    ("recipes", "list"),
    ("recipes", "show"),
    ("recipes", "create"),
    ("recipes", "update"),
    ("recipes", "delete"),
    ("recipes", "load"),
    ("cache", "prune"),
)

ROUTER_LEAVES = (
    "run",
    "up",
    "down",
    "restart",
    "reload",
    "promote",
    "endpoint",
    "status",
    "transition-status",
    "quiesce",
    "drain",
    "readmit",
    "logs",
    "token",
)

ROUTER_MANAGE_LEAVES = tuple(
    action for action in ROUTER_LEAVES if action not in {"run", "endpoint"}
)

EVAL_LEAVES = (
    ("usage",),
    ("preflight",),
    ("bootstrap",),
    ("calibrate",),
    ("benchmark", "capacity"),
    ("benchmark", "quality"),
    ("benchmark", "evidence", "list"),
    ("benchmark", "evidence", "show"),
    ("benchmark", "evidence", "compare"),
    ("benchmark", "external", "init"),
    ("benchmark", "external", "sources"),
    ("benchmark", "external", "fetch"),
    ("benchmark", "external", "import"),
    ("benchmark", "external", "list"),
    ("benchmark", "external", "report"),
    ("benchmark", "external", "export"),
    ("benchmark", "external", "compare"),
    ("benchmark", "external", "notebook", "add"),
    ("benchmark", "external", "notebook", "list"),
    ("benchmark", "external", "notebook", "render"),
)

SETUP_HOST_LEAVES = (
    ("init",),
    ("doctor",),
    ("upgrade",),
    ("host", "status"),
    ("host", "gpus"),
    ("host", "gpu-sharing", "inspect"),
    ("host", "gpu-sharing", "probe"),
    ("host", "doctor"),
    ("host", "memory"),
    ("host", "wsl-config"),
    ("host", "restart-docker"),
    ("host", "reset-wsl"),
    ("host", "reclaim"),
    ("dashboard", "serve"),
)

SETUP_HOST_GUARDED_MUTATIONS = (
    ("upgrade",),
    ("host", "gpu-sharing", "probe"),
    ("host", "wsl-config"),
    ("host", "restart-docker"),
    ("host", "reset-wsl"),
    ("host", "reclaim"),
)

CONTROL_PLANE_LEAVES = (
    ("harness", "sync", "openclaw"),
    ("harness", "restart", "openclaw"),
    ("harness", "status", "openclaw"),
    ("mcp", "serve"),
    ("mcp", "tools"),
    ("controller", "serve"),
    ("controller", "status"),
    ("topology", "show"),
    ("topology", "validate"),
    ("topology", "resolve"),
    ("collectors", "configure"),
    ("collectors", "validate"),
    ("collectors", "capabilities"),
    ("collectors", "inspect"),
    ("edge", "render"),
    ("edge", "status"),
    ("edge", "up"),
    ("edge", "down"),
)

CONTROL_PLANE_GUARDED_MUTATIONS = (
    ("harness", "sync", "openclaw"),
    ("harness", "restart", "openclaw"),
    ("edge", "up"),
    ("edge", "down"),
)

VOICE_LEAVES = (
    ("audio", "up"),
    ("audio", "down"),
    ("audio", "status"),
    ("audio", "logs"),
    ("proxy", "run"),
    ("proxy", "up"),
    ("proxy", "down"),
    ("proxy", "restart"),
    ("proxy", "status"),
    ("proxy", "logs"),
    ("proxy", "bridge"),
    ("benchmark",),
    ("profiles", "list"),
    ("profiles", "validate"),
    ("sidecar", "validate"),
    ("sidecar", "command"),
    ("sidecar", "compose"),
)

VOICE_GUARDED_MUTATIONS = (
    ("audio", "up"),
    ("audio", "down"),
    ("proxy", "up"),
    ("proxy", "down"),
    ("proxy", "restart"),
)


def _serves_leaf(action: str) -> CommandNode:
    family = next(node for node in COMMAND_TREE.nodes if node.name == "serves")
    return next(node for node in family.children if node.name == action)


def _leaf(*parts: str) -> CommandNode:
    siblings = COMMAND_TREE.nodes
    node = None
    for part in parts:
        node = next(item for item in siblings if item.name == part)
        siblings = node.children
    assert node is not None
    return node


def _assert_reviewed_help(path: tuple[str, ...], node: CommandNode, text: str) -> None:
    command = " ".join(("anvil-serving", *path))
    assert text.startswith(f"{command}\n{node.summary}\n")
    for heading in (
        "Usage:",
        "Examples:",
        "Configuration:",
        "Behavior:",
        "Global options:",
        "Docs:",
    ):
        assert f"\n{heading}" in text
    assert text.index("Examples:") < text.index("Options:")
    assert text[: text.index("Examples:")].rstrip().endswith("[global options]")
    assert f"Docs: {node.docs_anchor}" in text
    assert "Dispatcher options:" not in text
    assert "usage:" not in text
    assert node.examples
    normalized_text = " ".join(text.split())
    for example in node.examples:
        assert " ".join(example.invocation.split()) in normalized_text
        assert " ".join(example.summary.split()) in normalized_text
    reviewed_detail = text[text.index("Configuration:") :]
    assert all(len(line) <= 100 for line in reviewed_detail.splitlines())


@pytest.mark.parametrize("action", SERVES_LEAVES)
def test_serves_leaf_help_has_reviewed_navigation_contract(action, capsys):
    node = _serves_leaf(action)

    assert cli.main(["serves", action, "--help"]) == 0
    text = capsys.readouterr().out

    _assert_reviewed_help(("serves", action), node, text)


@pytest.mark.parametrize("parts", MODELS_LEAVES)
def test_models_leaf_help_has_reviewed_navigation_contract(parts, capsys):
    node = _leaf("models", *parts)
    path = ("models", *parts)

    assert cli.main([*path, "--help"]) == 0
    text = capsys.readouterr().out

    _assert_reviewed_help(path, node, text)


@pytest.mark.parametrize("action", ROUTER_LEAVES)
def test_router_leaf_help_has_reviewed_navigation_contract(action, capsys):
    node = _leaf("router", action)

    assert cli.main(["router", action, "--help"]) == 0
    text = capsys.readouterr().out

    _assert_reviewed_help(("router", action), node, text)


@pytest.mark.parametrize("parts", EVAL_LEAVES)
def test_eval_leaf_help_has_reviewed_navigation_contract(parts, capsys):
    node = _leaf("eval", *parts)
    path = ("eval", *parts)

    assert cli.main([*path, "--help"]) == 0
    text = capsys.readouterr().out

    _assert_reviewed_help(path, node, text)
    assert len(node.examples) == 2
    assert len(node.configuration_notes) == 2
    assert len(node.behavior_notes) == 2


@pytest.mark.parametrize("parts", SETUP_HOST_LEAVES)
def test_setup_host_leaf_help_has_reviewed_navigation_contract(parts, capsys):
    node = _leaf(*parts)

    assert cli.main([*parts, "--help"]) == 0
    text = capsys.readouterr().out

    _assert_reviewed_help(parts, node, text)
    assert len(node.examples) == 2
    assert len(node.configuration_notes) == 2
    assert len(node.behavior_notes) == 2


@pytest.mark.parametrize("parts", CONTROL_PLANE_LEAVES)
def test_control_plane_leaf_help_has_reviewed_navigation_contract(parts, capsys):
    node = _leaf(*parts)

    assert cli.main([*parts, "--help"]) == 0
    text = capsys.readouterr().out

    _assert_reviewed_help(parts, node, text)
    assert len(node.examples) == 2
    assert len(node.configuration_notes) == 2
    assert len(node.behavior_notes) == 2


@pytest.mark.parametrize("parts", VOICE_LEAVES)
def test_voice_leaf_help_has_reviewed_navigation_contract(parts, capsys):
    node = _leaf("voice", *parts)
    path = ("voice", *parts)

    assert cli.main([*path, "--help"]) == 0
    text = capsys.readouterr().out

    _assert_reviewed_help(path, node, text)
    assert len(node.examples) == 2
    assert len(node.configuration_notes) == 2
    assert len(node.behavior_notes) == 2


def test_topology_help_explains_every_leaf_owned_resolution_selector(capsys):
    assert cli.main(["topology", "resolve", "--help"]) == 0
    text = capsys.readouterr().out

    for flag in (
        "--topology",
        "--topology-overlay",
        "--command-host",
        "--command-runtime",
        "--target",
        "--transport",
        "--experimental-model-workload",
    ):
        assert flag in text
    assert "--allow-ssh-fallback" not in text


def test_mcp_serve_help_hides_compatibility_tool_listing_alias(capsys):
    assert cli.main(["mcp", "serve", "--help"]) == 0
    text = capsys.readouterr().out

    assert "{list-tools}" not in text
    assert "compatibility alias" not in text
    assert not any(
        line.lstrip().startswith("--list-tools") for line in text.splitlines()
    )

    assert cli.main(["mcp", "--help"]) == 0
    group_help = capsys.readouterr().out
    assert "tools" in group_help
    assert "List bounded MCP tools." in group_help


def test_remote_harness_sync_example_never_writes_a_loopback_router_url():
    node = _leaf("harness", "sync", "openclaw")
    remote = next(
        example.invocation
        for example in node.examples
        if "--gateway-host" in example.invocation
    )

    assert "--base-url" in remote
    assert "--base-url http://127.0.0.1" not in remote


@pytest.mark.parametrize("action", SERVES_LEAVES)
def test_serves_reviewed_examples_resolve_to_the_documented_leaf(action):
    node = _serves_leaf(action)
    for example in node.examples:
        tokens = shlex.split(example.invocation, posix=True)
        assert tokens[0] == "anvil-serving"
        path, rest, unknown, _siblings = cli._resolve(tokens[1:])
        assert unknown is None
        assert tuple(item.name for item in path) == ("serves", action)
        assert cli._tombstone(path, rest) is None


@pytest.mark.parametrize("parts", MODELS_LEAVES)
def test_models_reviewed_examples_resolve_to_the_documented_leaf(parts):
    node = _leaf("models", *parts)
    expected_path = ("models", *parts)
    for example in node.examples:
        tokens = shlex.split(example.invocation, posix=True)
        assert tokens[0] == "anvil-serving"
        path, rest, unknown, _siblings = cli._resolve(tokens[1:])
        assert unknown is None
        assert tuple(item.name for item in path) == expected_path
        assert cli._tombstone(path, rest) is None


@pytest.mark.parametrize("action", ROUTER_LEAVES)
def test_router_reviewed_examples_resolve_to_the_documented_leaf(action):
    node = _leaf("router", action)
    for example in node.examples:
        tokens = shlex.split(example.invocation, posix=True)
        assert tokens[0] == "anvil-serving"
        path, rest, unknown, _siblings = cli._resolve(tokens[1:])
        assert unknown is None
        assert tuple(item.name for item in path) == ("router", action)
        assert cli._tombstone(path, rest) is None


@pytest.mark.parametrize("parts", EVAL_LEAVES)
def test_eval_reviewed_examples_resolve_and_use_real_parser_flags(parts, capsys):
    node = _leaf("eval", *parts)
    expected_path = ("eval", *parts)

    assert cli.main([*expected_path, "--help"]) == 0
    help_text = capsys.readouterr().out

    for example in node.examples:
        tokens = shlex.split(example.invocation, posix=True)
        assert tokens[0] == "anvil-serving"
        path, rest, unknown, _siblings = cli._resolve(tokens[1:])
        assert unknown is None
        assert tuple(item.name for item in path) == expected_path
        assert cli._tombstone(path, rest) is None
        for token in rest:
            if token.startswith("--"):
                assert token.split("=", 1)[0] in help_text


@pytest.mark.parametrize("parts", SETUP_HOST_LEAVES)
def test_setup_host_reviewed_examples_resolve_and_use_real_parser_flags(
    parts, capsys
):
    node = _leaf(*parts)

    assert cli.main([*parts, "--help"]) == 0
    help_text = capsys.readouterr().out

    for example in node.examples:
        tokens = shlex.split(example.invocation, posix=True)
        assert tokens[0] == "anvil-serving"
        path, rest, unknown, _siblings = cli._resolve(tokens[1:])
        assert unknown is None
        assert tuple(item.name for item in path) == parts
        assert cli._tombstone(path, rest) is None
        if "--target" in rest:
            assert "--topology" in rest
        for token in rest:
            if token.startswith("--"):
                assert token.split("=", 1)[0] in help_text


@pytest.mark.parametrize("parts", CONTROL_PLANE_LEAVES)
def test_control_plane_reviewed_examples_resolve_and_use_real_parser_flags(
    parts, capsys
):
    node = _leaf(*parts)

    assert cli.main([*parts, "--help"]) == 0
    help_text = capsys.readouterr().out

    for example in node.examples:
        tokens = shlex.split(example.invocation, posix=True)
        assert tokens[0] == "anvil-serving"
        path, rest, unknown, _siblings = cli._resolve(tokens[1:])
        assert unknown is None
        assert tuple(item.name for item in path) == parts
        assert cli._tombstone(path, rest) is None
        if "--target" in rest:
            assert "--topology" in rest
        for token in rest:
            if token.startswith("--"):
                assert token.split("=", 1)[0] in help_text


@pytest.mark.parametrize("parts", CONTROL_PLANE_LEAVES)
def test_control_plane_reviewed_examples_reach_the_real_leaf_parser(parts):
    node = _leaf(*parts)
    parsers = {
        "anvil_serving.harness": harness._build_parser,
        "anvil_serving.mcp": mcp._build_main_parser,
        "anvil_serving.controller": controller._build_parser,
        "anvil_serving.topology_cli": topology_cli._parser,
        "anvil_serving.collectors": collectors.build_parser,
        "anvil_serving.edge": edge.build_parser,
    }
    assert node.handler is not None
    parser = parsers[node.handler.module]()

    for example in node.examples:
        tokens = shlex.split(example.invocation, posix=True)
        path, rest, unknown, _siblings = cli._resolve(tokens[1:])
        assert unknown is None
        if node.handler.module != "anvil_serving.topology_cli":
            _resolution, rest = cli._extract_resolution_options(rest)
        rest = tuple(
            token
            for token in rest
            if token not in {"--confirm", "--json", "--quiet", "--verbose"}
        )
        parser.parse_args([*cli._handler_argv(path), *rest])


@pytest.mark.parametrize("parts", VOICE_LEAVES)
def test_voice_reviewed_examples_resolve_and_use_real_parser_flags(parts, capsys):
    node = _leaf("voice", *parts)
    expected_path = ("voice", *parts)

    assert cli.main([*expected_path, "--help"]) == 0
    help_text = capsys.readouterr().out

    for example in node.examples:
        tokens = shlex.split(example.invocation, posix=True)
        assert tokens[0] == "anvil-serving"
        path, rest, unknown, _siblings = cli._resolve(tokens[1:])
        assert unknown is None
        assert tuple(item.name for item in path) == expected_path
        assert cli._tombstone(path, rest) is None
        if "--target" in rest:
            assert "--topology" in rest
        for token in rest:
            if token.startswith("--"):
                assert token.split("=", 1)[0] in help_text


@pytest.mark.parametrize(
    "parts",
    tuple(parts for parts in VOICE_LEAVES if parts[0] != "profiles"),
)
def test_voice_reviewed_examples_reach_the_real_leaf_parser(parts):
    node = _leaf("voice", *parts)
    parser = (
        voice_sidecar.build_parser()
        if node.handler.module == "anvil_serving.voice_sidecar"
        else voice_cli.build_parser()
    )

    for example in node.examples:
        tokens = shlex.split(example.invocation, posix=True)
        path, rest, unknown, _siblings = cli._resolve(tokens[1:])
        assert unknown is None
        rest = tuple(
            token for token in rest if token not in {"--json", "--quiet", "--verbose"}
        )
        parser.parse_args([*cli._handler_argv(path), *rest])


@pytest.mark.parametrize("parts", (("profiles", "list"), ("profiles", "validate")))
def test_voice_profile_examples_reach_the_real_adapter(parts, monkeypatch):
    node = _leaf("voice", *parts)
    captured = []
    monkeypatch.setattr(
        voice_cli,
        "main",
        lambda argv: captured.append(tuple(argv)) or 0,
    )
    handler = getattr(voice_cli, node.handler.attribute)

    for example in node.examples:
        tokens = shlex.split(example.invocation, posix=True)
        path, rest, unknown, _siblings = cli._resolve(tokens[1:])
        assert unknown is None
        assert handler(list(rest)) == 0
        assert captured[-1][0] == "profiles"


@pytest.mark.parametrize("action", ROUTER_MANAGE_LEAVES)
def test_router_manage_examples_reach_the_real_action_parser(action):
    node = _leaf("router", action)
    parser = router_manage._build_parser()
    for example in node.examples:
        arguments = shlex.split(example.invocation, posix=True)[2:]
        arguments = [
            argument
            for argument in arguments
            if argument not in {"--confirm", "--json"}
        ]
        parsed = parser.parse_args(arguments)
        assert parsed.action == action


def test_router_run_examples_reach_the_real_parser(monkeypatch):
    node = _leaf("router", "run")
    calls = []
    monkeypatch.setattr(
        router_serve,
        "resolve_serve_config",
        lambda **kwargs: ("resolved.toml", kwargs["mode_flag"]),
    )
    monkeypatch.setattr(
        router_serve,
        "serve",
        lambda config, **kwargs: calls.append((config, kwargs)),
    )

    for example in node.examples:
        arguments = shlex.split(example.invocation, posix=True)[3:]
        assert router_serve.main(arguments) == 0

    assert len(calls) == len(node.examples)


def test_router_endpoint_examples_reach_the_real_parser(monkeypatch, capsys):
    node = _leaf("router", "endpoint")
    monkeypatch.setattr(
        router_endpoint,
        "discover_router_endpoint",
        lambda **_kwargs: router_endpoint.RouterEndpoint(
            "127.0.0.1",
            8000,
            "http://127.0.0.1:8000",
            "default",
            "anvil-router",
            True,
            "node.example.ts.net",
            "connected",
        ),
    )

    for example in node.examples:
        arguments = shlex.split(example.invocation, posix=True)[1:]
        assert cli.main(arguments) == 0
        capsys.readouterr()


@pytest.mark.parametrize(
    "parts",
    tuple(parts for parts in MODELS_LEAVES if parts[0] == "recipes"),
)
def test_recipe_reviewed_examples_reach_the_real_action_parser(parts):
    node = _leaf("models", *parts)
    parser = models._build_recipe_parser()
    for example in node.examples:
        arguments = shlex.split(example.invocation, posix=True)[3:]
        parsed = parser.parse_args(arguments)
        assert parsed.recipe_action == parts[-1]


@pytest.mark.parametrize("parts", MODELS_LEAVES)
def test_models_reviewed_help_is_windows_console_safe(parts, monkeypatch, capsys):
    monkeypatch.setenv("COLUMNS", "72")

    assert cli.main(["models", *parts, "--help"]) == 0
    text = capsys.readouterr().out

    text.encode("cp1252")
    assert all(len(line) <= 72 for line in text.splitlines())


@pytest.mark.parametrize("action", ROUTER_LEAVES)
def test_router_reviewed_help_is_windows_console_safe(action, monkeypatch, capsys):
    monkeypatch.setenv("COLUMNS", "72")

    assert cli.main(["router", action, "--help"]) == 0
    text = capsys.readouterr().out

    text.encode("cp1252")
    reviewed_detail = text[text.index("Configuration:") :]
    assert all(len(line) <= 72 for line in reviewed_detail.splitlines())


@pytest.mark.parametrize("parts", EVAL_LEAVES)
def test_eval_reviewed_help_is_windows_console_safe(parts, monkeypatch, capsys):
    monkeypatch.setenv("COLUMNS", "60")

    assert cli.main(["eval", *parts, "--help"]) == 0
    text = capsys.readouterr().out

    text.encode("cp1252")
    assert all(len(line) <= 60 for line in text.splitlines())


@pytest.mark.parametrize("parts", SETUP_HOST_LEAVES)
def test_setup_host_reviewed_help_is_windows_console_safe(
    parts, monkeypatch, capsys
):
    monkeypatch.setenv("COLUMNS", "60")

    assert cli.main([*parts, "--help"]) == 0
    text = capsys.readouterr().out

    text.encode("cp1252")
    assert all(len(line) <= 60 for line in text.splitlines())


@pytest.mark.parametrize("parts", CONTROL_PLANE_LEAVES)
def test_control_plane_reviewed_help_is_windows_console_safe(
    parts, monkeypatch, capsys
):
    monkeypatch.setenv("COLUMNS", "60")

    assert cli.main([*parts, "--help"]) == 0
    text = capsys.readouterr().out

    text.encode("cp1252")
    assert all(len(line) <= 60 for line in text.splitlines())


@pytest.mark.parametrize("parts", VOICE_LEAVES)
def test_voice_reviewed_help_is_windows_console_safe(parts, monkeypatch, capsys):
    monkeypatch.setenv("COLUMNS", "60")

    assert cli.main(["voice", *parts, "--help"]) == 0
    text = capsys.readouterr().out

    text.encode("cp1252")
    assert all(len(line) <= 60 for line in text.splitlines())


def test_recipe_configuration_notes_match_runtime_precedence(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    config_home = tmp_path / "operator-home"
    config_home.mkdir()
    monkeypatch.setenv("ANVIL_SERVING_HOME", str(config_home))

    operator_registry = config_home / "serve-recipes.toml"
    operator_registry.write_text('schema = "operator"\n', encoding="utf-8")
    assert models._default_registry() == str(operator_registry)

    project_registry = tmp_path / "configs" / "serve-recipes.toml"
    project_registry.parent.mkdir()
    project_registry.write_text('schema = "project"\n', encoding="utf-8")
    assert models._default_registry() == str(project_registry)


def test_router_docs_group_every_operator_task():
    text = (Path(__file__).parents[1] / "docs" / "cli" / "router.md").read_text(
        encoding="utf-8"
    )
    for heading in (
        "### Run and discover",
        "### Deployment lifecycle",
        "### Safe tier transitions",
        "### Credentials",
    ):
        assert heading in text
    for action in ROUTER_LEAVES:
        assert f"`router {action}`" in text
    assert "router token --reveal --confirm" in text


def test_eval_docs_group_every_operator_task():
    text = (Path(__file__).parents[1] / "docs" / "cli" / "eval.md").read_text(
        encoding="utf-8"
    )
    for heading in (
        "### Prepare and gate",
        "### Measure and inspect",
        "### Build reviewable profiles",
        "### Manage external priors",
        "### Retain comparison runs",
    ):
        assert heading in text
    for parts in EVAL_LEAVES:
        assert "`eval %s`" % " ".join(parts) in text


def test_setup_host_docs_group_every_operator_task():
    text = (Path(__file__).parents[1] / "docs" / "cli" / "host.md").read_text(
        encoding="utf-8"
    )
    for heading in (
        "### Configure and maintain the installation",
        "### Inspect the host",
        "### Plan and apply host repair",
        "### Inspect GPU-sharing prerequisites",
        "### Observe the host",
    ):
        assert heading in text
    for parts in SETUP_HOST_LEAVES:
        assert "`%s`" % " ".join(parts) in text
    assert "`--confirm`" in text
    assert "one consent spelling" in text


def test_control_plane_docs_group_every_operator_task():
    text = (
        Path(__file__).parents[1] / "docs" / "cli" / "control-plane.md"
    ).read_text(encoding="utf-8")
    for heading in (
        "### Describe deployment ownership",
        "### Connect the operator harness",
        "### Expose the management plane",
        "### Integrate read-only telemetry",
        "### Publish tailnet surfaces",
    ):
        assert heading in text
    for parts in CONTROL_PLANE_LEAVES:
        assert "`%s`" % " ".join(parts) in text
    assert "tailscale serve reset" in text
    assert "never" in text


def test_voice_docs_group_every_operator_task():
    text = (Path(__file__).parents[1] / "docs" / "cli" / "voice.md").read_text(
        encoding="utf-8"
    )
    for heading in (
        "### Operate Dark-owned audio serves",
        "### Operate the Mini realtime layer",
        "### Evaluate and inspect configuration",
        "### Prepare the optional sidecar",
    ):
        assert heading in text
    for parts in VOICE_LEAVES:
        assert "`voice %s`" % " ".join(parts) in text
    assert "127.0.0.1:30110" in text
    assert "not local\nmodel serves" in text


def test_voice_configuration_notes_match_runtime_contract():
    audio_status = voice_cli.build_parser().parse_args(["audio", "status"])
    audio_logs = voice_cli.build_parser().parse_args(["audio", "logs"])
    proxy_logs = voice_cli.build_parser().parse_args(["proxy", "logs"])
    proxy_run = voice_cli.build_parser().parse_args(["proxy", "run"])
    sidecar_compose = voice_sidecar.build_parser().parse_args(["compose"])

    assert audio_status.ready_timeout == 3.0
    assert audio_logs.tail == 200
    assert proxy_logs.tail == 200
    assert proxy_run.transport == "auto"
    assert sidecar_compose.service_name == "speech-to-speech"
    assert voice_cli.ENDPOINT_PROBE_TIMEOUT_S == 3.0
    assert _leaf("voice", "audio", "up").coowned_resource_roles == ("tts-serve",)
    assert _leaf("voice", "proxy", "run").execution_runtime_roles == ("native",)
    assert _leaf("voice", "proxy", "bridge").output_policy == "foreground"


def test_control_plane_configuration_notes_match_runtime_contract():
    sync = harness._build_parser().parse_args(
        ["sync", "openclaw", "--config", "router.toml"]
    )
    restart = harness._build_parser().parse_args(["restart", "openclaw"])
    status = harness._build_parser().parse_args(["status", "openclaw"])
    controller_serve = controller._build_parser().parse_args(["serve"])
    controller_status = controller._build_parser().parse_args(["status"])
    collector_inspect = collectors.build_parser().parse_args(
        ["inspect", "--config", "collector.json"]
    )
    edge_render = edge.build_parser().parse_args(["render"])

    assert sync.base_url == "http://127.0.0.1:8000/v1"
    assert sync.timeout_seconds == harness.DEFAULT_TRANSPORT_TIMEOUT_SECONDS
    assert restart.timeout_seconds == harness.DEFAULT_TRANSPORT_TIMEOUT_SECONDS
    assert status.max_output_bytes == harness.DEFAULT_STATUS_MAX_OUTPUT_BYTES
    assert controller_serve.host == controller.DEFAULT_HOST
    assert controller_serve.port == controller.DEFAULT_PORT
    assert controller_serve.auth_token_env == controller.DEFAULT_AUTH_TOKEN_ENV
    assert controller_status.max_response_bytes == (
        controller.DEFAULT_STATUS_MAX_RESPONSE_BYTES
    )
    assert collector_inspect.timeout == 5.0
    assert edge_render.host == edge.DEFAULT_TARGET_HOST
    assert edge.DEFAULT_TIMEOUT_SECONDS == 15.0

    assert "controller" in _leaf("harness", "status", "openclaw").transports
    assert _leaf("topology", "resolve").transports == ()
    assert _leaf("collectors", "inspect").transports == ()
    assert _leaf("edge", "up").transports == ("local",)


def test_setup_host_configuration_notes_match_runtime_contract():
    inspect = gpu_sharing.build_parser().parse_args([])
    probe = gpu_sharing.build_probe_parser().parse_args(
        ["--gpu-uuid", "GPU-00000000-0000-0000-0000-000000000000"]
    )
    dashboard = dashboard_app.build_parser().parse_args([])

    assert inspect.timeout == gpu_sharing.DEFAULT_TIMEOUT_SECONDS
    assert probe.timeout == gpu_sharing.DEFAULT_PROBE_TIMEOUT_SECONDS
    assert probe.compose_file == str(gpu_sharing.DEFAULT_PROBE_COMPOSE_FILE)
    assert dashboard.host == "127.0.0.1"
    assert dashboard.port == 8766
    assert host.MIN_WINDOWS_RESERVE_GB == 10
    assert host.RECOMMENDED_WINDOWS_RESERVE_GB == 14

    assert "controller" in _leaf("doctor").transports
    assert "controller" in _leaf("host", "status").transports
    assert "controller" in _leaf("host", "gpus").transports
    assert "controller" in _leaf("host", "doctor").transports
    assert _leaf("host", "memory").transports == ("local",)
    assert _leaf("host", "reclaim").transports == ("local",)
    assert _leaf("host", "memory").execution_host_os == ("windows",)
    assert _leaf("host", "restart-docker").execution_host_os == (
        "windows",
        "macos",
    )


@pytest.mark.parametrize(
    ("parts", "removed"),
    (
        (("host", "restart-docker"), "--force"),
        (("host", "reset-wsl"), "--force"),
        (("host", "reclaim"), "--yes"),
    ),
)
def test_host_alternate_consent_flags_are_hidden_and_refused(
    parts, removed, capsys
):
    assert cli.main([*parts, "--help"]) == 0
    help_text = capsys.readouterr().out
    assert not any(
        line.lstrip().startswith(removed) for line in help_text.splitlines()
    )

    assert cli.main([*parts, removed]) == 2
    error = capsys.readouterr().err
    assert "was removed" in error
    assert "use `--confirm` instead" in error


@pytest.mark.parametrize("parts", SETUP_HOST_GUARDED_MUTATIONS)
def test_setup_host_mutations_preview_or_use_shared_confirmation(
    parts, monkeypatch, capsys
):
    calls = []
    monkeypatch.setattr(
        HandlerRef,
        "resolve",
        lambda self: lambda argv: calls.append(tuple(argv)) or 0,
    )
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)

    assert cli.main(list(parts)) == 3
    assert not calls
    assert "confirmation required" in capsys.readouterr().err

    assert cli.main([*parts, "--dry-run"]) == 0
    assert calls[-1][-1] == "--dry-run"

    assert cli.main([*parts, "--confirm"]) == 0
    assert "--confirm" not in calls[-1]


@pytest.mark.parametrize("parts", CONTROL_PLANE_GUARDED_MUTATIONS)
def test_control_plane_mutations_preview_or_use_shared_confirmation(
    parts, monkeypatch, capsys
):
    calls = []
    monkeypatch.setattr(
        HandlerRef,
        "resolve",
        lambda self: lambda argv: calls.append(tuple(argv)) or 0,
    )
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)

    assert cli.main(list(parts)) == 3
    assert not calls
    assert "confirmation required" in capsys.readouterr().err

    assert cli.main([*parts, "--dry-run"]) == 0
    assert calls[-1][-1] == "--dry-run"

    assert cli.main([*parts, "--confirm"]) == 0
    assert "--confirm" not in calls[-1]


def test_collectors_configure_gates_only_the_output_write(monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(
        HandlerRef,
        "resolve",
        lambda self: lambda argv: calls.append(tuple(argv)) or 0,
    )
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    inline = [
        "collectors",
        "configure",
        "--name",
        "local-gap",
        "--endpoint",
        "http://127.0.0.1:9100/capabilities",
        "--capability",
        "gpu-gap",
    ]

    assert cli.main(inline) == 0
    assert calls

    calls.clear()
    assert cli.main([*inline, "--output", "collector.json"]) == 3
    assert not calls
    assert "confirmation required" in capsys.readouterr().err

    assert cli.main([*inline, "--output", "collector.json", "--confirm"]) == 0
    assert "--confirm" not in calls[-1]


@pytest.mark.parametrize("parts", VOICE_GUARDED_MUTATIONS)
def test_voice_mutations_preview_or_use_shared_confirmation(
    parts, monkeypatch, capsys
):
    calls = []
    monkeypatch.setattr(
        HandlerRef,
        "resolve",
        lambda self: lambda argv: calls.append(tuple(argv)) or 0,
    )
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)

    assert cli.main(["voice", *parts]) == 3
    assert not calls
    assert "confirmation required" in capsys.readouterr().err

    assert cli.main(["voice", *parts, "--dry-run"]) == 0
    assert calls[-1][-1] == "--dry-run"

    assert cli.main(["voice", *parts, "--confirm"]) == 0
    assert "--confirm" not in calls[-1]


def test_controller_unauthenticated_loopback_flag_is_hidden_and_refused(capsys):
    assert cli.main(["controller", "serve", "--help"]) == 0
    help_text = capsys.readouterr().out
    assert "--allow-unauthenticated-loopback" not in help_text

    assert cli.main(["controller", "serve", "--allow-unauthenticated-loopback"]) == 2
    error = capsys.readouterr().err
    assert "was removed" in error
    assert "Configure the token named by --auth-token-env" in error


def test_eval_configuration_notes_match_runtime_defaults():
    evidence_parser = benchmark_evidence._parser()
    evidence_list = evidence_parser.parse_args(["list"])
    evidence_show = evidence_parser.parse_args(["show", "artifact.json"])
    evidence_compare = evidence_parser.parse_args(["compare", "artifact.json"])

    assert evidence_list.root == benchmark_evidence.DEFAULT_ROOT
    assert evidence_list.limit == benchmark_evidence.DEFAULT_LIMIT
    assert evidence_show.format == "human"
    assert evidence_compare.allow_mismatch is False

    for parts in EVAL_LEAVES:
        if parts[:2] == ("benchmark", "external"):
            notes = " ".join(_leaf("eval", *parts).configuration_notes)
            assert external_benchmark_store.DEFAULT_DB in notes

    assert "controller" in _leaf("eval", "preflight").transports
    assert _leaf("eval", "benchmark", "capacity").transports == ("local",)
    assert _leaf("eval", "benchmark", "quality").transports == ("local",)


def test_router_transition_configuration_matches_runtime_precedence():
    environment = {
        "ANVIL_ROUTER_URL": "http://100.87.34.66:8000",
    }
    inherited = router_manage.transition_request(
        "quiesce",
        tier_id="heavy-local",
        env=environment,
    )
    explicit = router_manage.transition_request(
        "quiesce",
        tier_id="heavy-local",
        router_url="http://100.87.34.67:9000",
        env=environment,
    )

    assert inherited["router_url"] == "http://100.87.34.66:8000"
    assert explicit["router_url"] == "http://100.87.34.67:9000"


def test_router_manage_configuration_notes_match_parser_defaults():
    parser = router_manage._build_parser()

    for action in ("restart", "reload", "status", "logs", "token"):
        parsed = parser.parse_args([action])
        assert parsed.container == router_manage.DEFAULT_CONTAINER
    for action in ("up", "down"):
        parsed = parser.parse_args([action])
        assert parsed.service == router_manage.DEFAULT_SERVICE

    logs = parser.parse_args(["logs"])
    assert logs.tail == "200"

    promote = parser.parse_args(["promote", "--profile", "candidate.json"])
    assert promote.container == router_manage.DEFAULT_CONTAINER
    assert promote.cfg_volume == router_manage.DEFAULT_CFG_VOLUME
    assert promote.image == router_manage.DEFAULT_IMAGE
    assert promote.profile_dest == router_manage.DEFAULT_PROFILE_DEST
    assert promote.config_dest == router_manage.DEFAULT_CONFIG_DEST


@pytest.mark.parametrize(
    "action",
    tuple(action for action in SERVES_LEAVES if action not in {"render", "multiplex"}),
)
def test_serves_reviewed_examples_reach_the_real_action_parser(action):
    node = _serves_leaf(action)
    parser = serves._build_action_parser(action)
    for example in node.examples:
        arguments = shlex.split(example.invocation, posix=True)[3:]
        arguments = [
            argument
            for argument in arguments
            if argument not in {"--confirm", "--json"}
        ]
        parser.parse_intermixed_args(arguments)


@pytest.mark.parametrize("action", ("rm", "adopt"))
def test_serves_removed_yes_consent_is_hidden_and_refused(action, capsys):
    assert cli.main(["serves", action, "--help"]) == 0
    help_text = capsys.readouterr().out
    assert "\n  --yes" not in help_text

    assert cli.main(["serves", action, "heavy", "--yes"]) == 2
    error = capsys.readouterr().err
    assert f"`serves {action} --yes` was removed" in error
    assert "use `--confirm` instead" in error


def test_serves_configuration_notes_match_runtime_precedence(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    config_home = tmp_path / "operator-home"
    config_home.mkdir()
    monkeypatch.setenv("ANVIL_SERVING_HOME", str(config_home))

    explicit_manifest = tmp_path / "explicit.toml"
    assert serves.resolve_manifest_path(str(explicit_manifest)) == str(explicit_manifest)
    operator_manifest = config_home / "serves.toml"
    operator_manifest.write_text("[[serve]]\n", encoding="utf-8")
    assert serves.resolve_manifest_path() == str(operator_manifest)
    project_manifest = tmp_path / "serves.toml"
    project_manifest.write_text("[[serve]]\n", encoding="utf-8")
    assert serves.resolve_manifest_path() == "./serves.toml"

    explicit_registry = tmp_path / "explicit-recipes.toml"
    assert serves.resolve_recipe_registry_path(str(explicit_registry)) == str(
        explicit_registry
    )
    operator_registry = config_home / "serve-recipes.toml"
    operator_registry.write_text("schema_version = 1\n", encoding="utf-8")
    assert serves.resolve_recipe_registry_path() == str(operator_registry)
    project_registry = tmp_path / "serve-recipes.toml"
    project_registry.write_text("schema_version = 1\n", encoding="utf-8")
    assert serves.resolve_recipe_registry_path() == "./serve-recipes.toml"


def test_serves_multiplex_self_check_example_reaches_the_real_parser(capsys):
    assert multiplexer.main(["--self-check"]) == 0
    assert "self-check OK" in capsys.readouterr().out


def test_reviewed_help_keeps_visible_mutually_exclusive_usage_alternatives():
    rendered = """usage: tool [--json | --yaml] [--name NAME]

options:
  --json       emit JSON
  --yaml       emit YAML
  --name NAME  select a name
"""

    usage, sections = cli._normalized_leaf_sections(
        rendered, hidden_flags=frozenset({"--json"})
    )

    assert usage == ["tool [--yaml] [--name NAME] [global options]"]
    assert "  --yaml       emit YAML" in sections
    assert "  --json       emit JSON" not in sections


def test_reviewed_help_respects_a_narrow_terminal(monkeypatch, capsys):
    monkeypatch.setenv("COLUMNS", "72")

    assert cli.main(["serves", "switch", "--help"]) == 0
    text = capsys.readouterr().out

    reviewed_detail = text[text.index("Configuration:") :]
    assert all(len(line) <= 72 for line in reviewed_detail.splitlines())


def test_reviewed_help_caps_an_oversized_terminal(monkeypatch, capsys):
    monkeypatch.setenv("COLUMNS", "1000000")

    assert cli.main(["models", "pull", "--help"]) == 0
    text = capsys.readouterr().out

    assert all(len(line) <= 100 for line in text.splitlines())
