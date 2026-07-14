"""Reviewed navigation contract for model-serving and recipe command families."""

from __future__ import annotations

import shlex

import pytest

from anvil_serving import cli, models, multiplexer, serves
from anvil_serving.command_tree import COMMAND_TREE, CommandNode


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
    reviewed_detail = text[text.index("Configuration:") :]
    assert all(len(line) <= 72 for line in reviewed_detail.splitlines())


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
