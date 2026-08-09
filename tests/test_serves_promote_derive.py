"""`serves promote --derive` -- generate a [[promotion]] plan from its serves
(issue #381, feature 16).

Mirrors the fixture idiom from tests/test_serves_preflight_gate.py (`_write`,
`_router_config`): serve entries need `runtime = "docker"`, target and
rollback share `port`, `model` doubles as `served_name`, and the router
config template declares tier `primary-local` with `model_identity = true`
under `[router.model_routes] llm.primary`.

`derive_promotion_plan` (anvil_serving/serves.py) is pure: it resolves the
two --*-config paths, computes `affected_tiers` from the PROMOTED config,
fills in the six numeric defaults `load_promotions` would otherwise
`setdefault`, and validates the result with the existing
`_validate_promotion_topology` before returning -- a derivation the
validator rejects is refused exactly like `cmd_promote`'s own
"promotion refused: %s" precedent, printed to stdout. A zero-tier match is
treated as a stricter defect (silent no-op success) and is refused to
stderr instead (`_NoAffectedTiersError`).
"""
import textwrap
import tomllib

import pytest

from anvil_serving import cli, serves


@pytest.fixture(autouse=True)
def _isolated_host_policy(monkeypatch, tmp_path):
    """Never let a developer's enabled machine policy affect unit timing."""
    monkeypatch.setenv("ANVIL_SERVING_HOME", str(tmp_path / ".anvil-serving"))


def _write(tmp_path, filename, body):
    path = tmp_path / filename
    path.write_text(textwrap.dedent(body), encoding="utf-8")
    return str(path)


def _router_config(tmp_path, filename, model, tier_id="primary-local"):
    return _write(tmp_path, filename, """
        [router]
        [[router.tiers]]
        id = "%s"
        base_url = "http://127.0.0.1:30002/v1"
        model = "%s"
        dialect = "openai"
        context_limit = 4096
        privacy = "local"
        tool_support = true
        auth_env = "ANVIL_PRIMARY_LOCAL_KEY"
        health_path = "/health"
        model_identity = true
        [router.model_routes]
        llm.primary = "%s"
    """ % (tier_id, model, tier_id))


def _derive_manifest(tmp_path, *, target_model="new-heavy", rollback_model="old-heavy"):
    """Two serves sharing a port: `heavy` (target) and `old-heavy` (rollback),
    with no `[[promotion]]` block of their own -- `--derive` builds one."""
    body = """
        [[serve]]
        name = "heavy"
        container = "gate-test-heavy"
        runtime = "docker"
        port = 39002
        model = "%s"
        engine = "vllm"

        [[serve]]
        name = "old-heavy"
        container = "gate-test-old-heavy"
        runtime = "docker"
        port = 39002
        model = "%s"
        engine = "vllm"
    """ % (target_model, rollback_model)
    return _write(tmp_path, "serves.toml", body)


def _derive(tmp_path, manifest_path, promoted_cfg, rollback_cfg, *, extra=(), target="heavy",
            rollback="old-heavy"):
    return serves.main([
        "promote", target, rollback, "--derive",
        "--router-config", promoted_cfg,
        "--rollback-router-config", rollback_cfg,
        "--manifest", manifest_path,
        *extra,
    ])


# ---- 1. round-trip (the headline test) --------------------------------------

def test_derive_round_trips_through_load_promotions(tmp_path, capsys):
    manifest_path = _derive_manifest(tmp_path)
    promoted_cfg = _router_config(tmp_path, "router-promoted.toml", "new-heavy")
    rollback_cfg = _router_config(tmp_path, "router-rollback.toml", "old-heavy")

    rc = _derive(tmp_path, manifest_path, promoted_cfg, rollback_cfg)

    assert rc == 0
    block = capsys.readouterr().out
    assert block.count("[[promotion]]") == 1

    # Write the derived block verbatim into a NEW manifest that also carries
    # the two [[serve]] entries, and confirm the loader/validator accept it.
    combined_path = tmp_path / "serves-with-plan.toml"
    combined_path.write_text(
        (tmp_path / "serves.toml").read_text(encoding="utf-8") + "\n" + block,
        encoding="utf-8",
    )
    combined_path = str(combined_path)

    promotions = serves.load_promotions(combined_path)
    assert len(promotions) == 1
    plan = promotions[0]
    manifest_serves = serves.load_manifest(combined_path)
    assert serves._validate_promotion_topology(manifest_serves, plan) is True

    assert plan["name"] == "heavy-promotion"
    assert plan["affected_tiers"] == ["primary-local"]
    assert plan["drain_timeout"] == 120
    assert plan["needle_ctx"] == 32768
    assert plan["tool_batch"] == 20
    assert plan["startup_timeout"] == 600
    assert plan["rollback_startup_timeout"] == 600
    assert plan["poll_interval"] == 5


# ---- 2. mismatched rollback model -------------------------------------------

def test_derive_refuses_mismatched_rollback_model(tmp_path, capsys):
    manifest_path = _derive_manifest(tmp_path)
    promoted_cfg = _router_config(tmp_path, "router-promoted.toml", "new-heavy")
    rollback_cfg = _router_config(tmp_path, "router-rollback.toml", "wrong-model")

    rc = _derive(tmp_path, manifest_path, promoted_cfg, rollback_cfg)

    assert rc == 1
    out = capsys.readouterr().out
    assert (
        "rollback router config tier 'primary-local' model does not match "
        "rollback 'old-heavy'"
    ) in out


# ---- 3. --out refuses to overwrite an existing file -------------------------

def test_derive_out_refuses_existing_file(tmp_path, capsys):
    manifest_path = _derive_manifest(tmp_path)
    promoted_cfg = _router_config(tmp_path, "router-promoted.toml", "new-heavy")
    rollback_cfg = _router_config(tmp_path, "router-rollback.toml", "old-heavy")
    out_path = tmp_path / "plan.toml"
    out_path.write_text("SENTINEL", encoding="utf-8")

    rc = _derive(
        tmp_path, manifest_path, promoted_cfg, rollback_cfg,
        extra=["--out", str(out_path)],
    )

    assert rc != 0
    assert out_path.read_text(encoding="utf-8") == "SENTINEL"
    assert "refus" in capsys.readouterr().err.lower()


# ---- 4. --out writes a new file ---------------------------------------------

def test_derive_out_writes_new_file(tmp_path):
    manifest_path = _derive_manifest(tmp_path)
    promoted_cfg = _router_config(tmp_path, "router-promoted.toml", "new-heavy")
    rollback_cfg = _router_config(tmp_path, "router-rollback.toml", "old-heavy")
    out_path = tmp_path / "plan.toml"

    rc = _derive(
        tmp_path, manifest_path, promoted_cfg, rollback_cfg,
        extra=["--out", str(out_path)],
    )

    assert rc == 0
    with open(out_path, "rb") as handle:
        data = tomllib.load(handle)
    assert len(data["promotion"]) == 1
    assert data["promotion"][0]["name"] == "heavy-promotion"


# ---- 5. no tier matches the target's served_name ----------------------------

def test_derive_refuses_when_no_tier_matches_target(tmp_path, capsys):
    manifest_path = _derive_manifest(tmp_path)
    promoted_cfg = _router_config(tmp_path, "router-promoted.toml", "mystery-model")
    rollback_cfg = _router_config(tmp_path, "router-rollback.toml", "old-heavy")

    rc = _derive(tmp_path, manifest_path, promoted_cfg, rollback_cfg)

    assert rc == 1
    err = capsys.readouterr().err
    assert "new-heavy" in err


# ---- 6. argument validation --------------------------------------------------

def test_derive_requires_exactly_two_positionals(tmp_path, capsys):
    rc = serves.main([
        "promote", "heavy", "--derive",
        "--router-config", "a.toml", "--rollback-router-config", "b.toml",
        "--manifest", str(tmp_path / "serves.toml"),
    ])
    assert rc == 2
    assert capsys.readouterr().err


def test_derive_rejects_rollback_flag(tmp_path, capsys):
    rc = serves.main([
        "promote", "heavy", "old-heavy", "--derive", "--rollback",
        "--router-config", "a.toml", "--rollback-router-config", "b.toml",
        "--manifest", str(tmp_path / "serves.toml"),
    ])
    assert rc == 2
    assert "--rollback" in capsys.readouterr().err


def test_router_config_without_derive_is_rejected(tmp_path, capsys):
    rc = serves.main([
        "promote", "some-plan", "--router-config", "a.toml",
        "--manifest", str(tmp_path / "serves.toml"),
    ])
    assert rc == 2
    assert "--derive" in capsys.readouterr().err


def test_derive_missing_router_config_is_rejected(tmp_path, capsys):
    rc = serves.main([
        "promote", "heavy", "old-heavy", "--derive",
        "--manifest", str(tmp_path / "serves.toml"),
    ])
    assert rc == 2
    assert "--router-config" in capsys.readouterr().err


# ---- 7 & 8. dispatcher-level confirmation carve-out -------------------------

def test_cli_derive_read_path_does_not_require_confirmation(tmp_path, capsys):
    manifest_path = _derive_manifest(tmp_path)
    promoted_cfg = _router_config(tmp_path, "router-promoted.toml", "new-heavy")
    rollback_cfg = _router_config(tmp_path, "router-rollback.toml", "old-heavy")

    rc = cli.main([
        "serves", "promote", "--derive", "heavy", "old-heavy",
        "--router-config", promoted_cfg,
        "--rollback-router-config", rollback_cfg,
        "--manifest", manifest_path,
    ])

    captured = capsys.readouterr()
    assert rc == 0
    assert "confirmation_required" not in captured.out
    assert "confirmation required" not in captured.err
    assert "[[promotion]]" in captured.out


def test_cli_mutation_path_still_requires_confirmation(tmp_path, capsys):
    rc = cli.main([
        "serves", "promote", "some-plan", "--manifest", str(tmp_path / "serves.toml"),
    ])

    assert rc == 3
    assert "confirmation required" in capsys.readouterr().err


def test_cli_hybrid_plan_plus_derive_never_mutates(tmp_path, capsys):
    """`promote PLAN --derive` slips past the mutation gate (carve-out sees
    `--derive`) but must die at the leaf's argument matrix with rc 2 --
    pinning that the bypass can never reach a mutating code path."""
    rc = cli.main([
        "serves", "promote", "some-plan", "--derive",
        "--router-config", "a.toml", "--rollback-router-config", "b.toml",
        "--manifest", str(tmp_path / "serves.toml"),
    ])

    captured = capsys.readouterr()
    assert rc == 2
    assert "confirmation required" not in captured.err
    assert "TARGET ROLLBACK" in captured.err


def test_cli_derive_after_double_dash_is_still_gated(tmp_path, capsys):
    """A `--derive` hidden behind the literal `--` separator is leaf payload,
    not a dispatcher policy arg: the mutation gate must still fire."""
    rc = cli.main([
        "serves", "promote", "some-plan",
        "--manifest", str(tmp_path / "serves.toml"), "--", "--derive",
    ])

    assert rc == 3
    assert "confirmation required" in capsys.readouterr().err


def test_cli_derive_with_value_form_is_still_gated(tmp_path, capsys):
    """`--derive=x` is not the exact `--derive` token; the carve-out must not
    treat it as the read-only shape."""
    rc = cli.main([
        "serves", "promote", "--derive=x", "some-plan",
        "--manifest", str(tmp_path / "serves.toml"),
    ])

    assert rc == 3
    assert "confirmation required" in capsys.readouterr().err


# ---- 9. hardening from adversarial review -----------------------------------

def test_derive_out_missing_parent_dir_fails_cleanly(tmp_path, capsys):
    manifest_path = _derive_manifest(tmp_path)
    promoted_cfg = _router_config(tmp_path, "router-promoted.toml", "new-heavy")
    rollback_cfg = _router_config(tmp_path, "router-rollback.toml", "old-heavy")

    rc = _derive(
        tmp_path, manifest_path, promoted_cfg, rollback_cfg,
        extra=["--out", str(tmp_path / "missing-dir" / "plan.toml")],
    )

    assert rc == 1
    assert "cannot write" in capsys.readouterr().err


def test_render_escapes_control_characters(tmp_path):
    """A control character in a derived string field must round-trip through
    tomllib to the identical value, never emit an unparseable block."""
    plan = {
        "name": "bad\nname",
        "target": "heavy\tserve",
        "rollback": "old-heavy",
        "affected_tiers": ["tier\x7fid"],
        "router_config": "C:\\configs\\router.toml",
        "rollback_router_config": "/tmp/rollback.toml",
        "drain_timeout": 120, "needle_ctx": 32768, "tool_batch": 20,
        "startup_timeout": 600, "rollback_startup_timeout": 600,
        "poll_interval": 5,
    }
    block = serves._render_promotion_toml(plan)

    parsed = tomllib.loads(block)["promotion"][0]
    assert parsed["name"] == "bad\nname"
    assert parsed["target"] == "heavy\tserve"
    assert parsed["affected_tiers"] == ["tier\x7fid"]
    assert parsed["router_config"] == "C:\\configs\\router.toml"
