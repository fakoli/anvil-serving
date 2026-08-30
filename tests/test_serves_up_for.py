"""`serves up-for ALIAS` — walk alias -> tier -> serve and start it.

The join key already exists in the data: `[router.model_routes]` maps alias
to tier id, and a `[[serve]]` entry carries `router_tier`. Nothing joined
them, so an operator answering "how do I start what `llm.primary` needs" read
four files by hand. See docs/PRODUCT-DISCOVERY-PERSONAS.md §2 and
docs/STRATEGY-MAKE-DIVERGENCE-LOUD.md (feature 11).
"""
import json
import textwrap

from anvil_serving import serves
from anvil_serving.router import config as router_config
from tests.conftest import proc


def _write(tmp_path, filename, body):
    path = tmp_path / filename
    path.write_text(textwrap.dedent(body), encoding="utf-8")
    return str(path)


def _entry(name, container, router_tier=None, port=30001, model="m", groups=None):
    tier_line = 'router_tier = "%s"' % router_tier if router_tier else ""
    groups_line = ""
    if groups:
        joined = ", ".join('"%s"' % g for g in groups)
        groups_line = "groups = [%s]" % joined
    return f"""
        [[serve]]
        name = "{name}"
        container = "{container}"
        runtime = "docker"
        port = {port}
        model = "{model}"
        engine = "vllm"
        up = "docker compose -f compose.yml up -d {name}"
        {tier_line}
        {groups_line}
    """


def _router_config(tmp_path, filename="router.toml", tier_id="primary-local",
                    alias="llm.primary"):
    return _write(tmp_path, filename, f"""
        [router]
        [[router.tiers]]
        id = "{tier_id}"
        base_url = "http://127.0.0.1:30001/v1"
        model = "m"
        dialect = "openai"
        context_limit = 4096
        privacy = "local"
        tool_support = true
        auth_env = "ANVIL_PRIMARY_LOCAL_KEY"
        health_path = "/health"
        model_identity = true
        [router.model_routes]
        {alias} = "{tier_id}"
    """)


def _load(tmp_path, serves_body, router_filename="router.toml", **router_kwargs):
    serves_path = _write(tmp_path, "serves.toml", serves_body)
    router_path = _router_config(tmp_path, filename=router_filename, **router_kwargs)
    config = router_config.load(router_path)
    serves_set = serves.load_manifest_set(serves_path)
    return config, serves_set, router_path


def test_resolve_alias_backers_walks_alias_to_tier_to_serve(tmp_path):
    config, serves_set, _ = _load(
        tmp_path, _entry("primary", "primary-c", router_tier="primary-local"))

    result = serves.resolve_alias_backers(config, serves_set, "llm.primary")

    assert result["tier_id"] == "primary-local"
    assert [c["name"] for c in result["candidates"]] == ["primary"]
    assert result["candidates"][0]["container"] == "primary-c"
    assert result["candidates"][0]["port"] == 30001
    assert result["candidates"][0]["up"][:2] == ["docker", "compose"]


def test_alias_normalization_uppercase_and_whitespace(tmp_path):
    config, serves_set, _ = _load(
        tmp_path, _entry("primary", "primary-c", router_tier="primary-local"))

    result = serves.resolve_alias_backers(config, serves_set, "  LLM.Primary  ")

    assert result["normalized_alias"] == "llm.primary"
    assert result["tier_id"] == "primary-local"
    assert len(result["candidates"]) == 1


def test_cmd_up_for_happy_path_prints_full_chain(tmp_path, capsys):
    config, serves_set, router_path = _load(
        tmp_path, _entry("primary", "primary-c", router_tier="primary-local"))

    rc = serves.cmd_up_for(config, serves_set, "llm.primary", router_path)

    assert rc == 0
    out = capsys.readouterr().out
    assert "llm.primary" in out
    assert "primary-local" in out
    assert "primary" in out
    assert "primary-c" in out
    assert "docker compose" in out


def test_cmd_up_for_json_happy_path_has_full_chain(tmp_path, capsys):
    config, serves_set, router_path = _load(
        tmp_path, _entry("primary", "primary-c", router_tier="primary-local"))

    rc = serves.cmd_up_for(config, serves_set, "llm.primary", router_path, as_json=True)

    assert rc == 0
    report = json.loads(capsys.readouterr().out)
    assert report["tier_id"] == "primary-local"
    assert report["resolved"]["name"] == "primary"
    assert report["resolved"]["container"] == "primary-c"


def test_unknown_alias_exits_2_and_lists_configured_aliases(tmp_path, capsys):
    config, serves_set, router_path = _load(
        tmp_path, _entry("primary", "primary-c", router_tier="primary-local"))

    rc = serves.cmd_up_for(config, serves_set, "llm.nope", router_path)

    assert rc == 2
    err = capsys.readouterr().err
    assert "llm.nope" in err
    assert "llm.primary" in err


def test_tier_with_zero_backers_exits_1(tmp_path, capsys):
    config, serves_set, router_path = _load(
        tmp_path, _entry("other", "other-c"))  # no router_tier at all

    rc = serves.cmd_up_for(config, serves_set, "llm.primary", router_path)

    assert rc == 1
    err = capsys.readouterr().err
    assert "primary-local" in err
    assert "no backing serve" in err


def test_multi_candidate_refuses_and_lists_all(tmp_path, capsys):
    body = (
        _entry("primary", "primary-c", router_tier="primary-local")
        + _entry("old-primary", "old-primary-c", router_tier="primary-local",
                  groups=["restore"])
    )
    config, serves_set, router_path = _load(tmp_path, body)

    rc = serves.cmd_up_for(config, serves_set, "llm.primary", router_path)

    assert rc == 1
    err = capsys.readouterr().err
    assert "2 backing serves" in err
    assert "primary" in err
    assert "old-primary" in err
    assert "restore" in err


def test_single_candidate_dry_run_confirm_reaches_cmd_up_dry_run(tmp_path, capsys):
    config, serves_set, router_path = _load(
        tmp_path, _entry("primary", "primary-c", router_tier="primary-local"))

    def run(argv, **_kwargs):
        if "config" in argv and "--format" in argv:
            return proc(0, json.dumps({
                "services": {"primary": {"networks": {"default": None}}},
                "networks": {"default": {"internal": True}},
            }))
        if argv[:3] == ["docker", "ps", "-a"]:
            return proc(0, "")
        if argv[:2] == ["docker", "inspect"]:
            return proc(1, "", "No such object")
        raise AssertionError("dry-run executed an unexpected command: %r" % (argv,))

    rc = serves.cmd_up_for(
        config, serves_set, "llm.primary", router_path,
        confirm=True, dry_run=True, ledger_serves=serves_set, _run=run)

    assert rc == 0
    out = capsys.readouterr().out
    # cmd_up's dry-run path resolves the effective Compose policy, then prints
    # the up invocation without starting a container.
    assert "primary" in out
    assert "docker compose" in out


def test_confirm_without_dry_run_still_only_previews_when_multi_candidate(tmp_path, capsys):
    # A --confirm on a refused (multi-candidate) resolution must not fall
    # through to cmd_up -- the refusal is the terminal outcome.
    body = (
        _entry("primary", "primary-c", router_tier="primary-local")
        + _entry("old-primary", "old-primary-c", router_tier="primary-local")
    )
    config, serves_set, router_path = _load(tmp_path, body)

    rc = serves.cmd_up_for(
        config, serves_set, "llm.primary", router_path, confirm=True)

    assert rc == 1
    err = capsys.readouterr().err
    assert "2 backing serves" in err


def test_cli_read_path_needs_no_confirmation(tmp_path, capsys):
    # The dispatcher-level regression the direct-call tests could not see:
    # with an unconditional mutation gate, the read-only resolution demanded
    # --confirm (exit 3) — the exact ceremony this feature exists to remove.
    # The node uses a conditional gate (the `switch --recipe` pattern) so only
    # --confirm invocations are guarded. Found live, not by the unit tests.
    from anvil_serving import cli

    manifest = _write(tmp_path, "serves.toml", _entry(
        "solo", "solo-c", router_tier="primary-local"))
    config = _router_config(tmp_path)

    rc = cli.main(["serves", "up-for", "llm.primary",
                   "--config", config, "--manifest", manifest])
    out = capsys.readouterr().out
    assert rc == 0
    assert "solo" in out
    assert "confirmation required" not in out
