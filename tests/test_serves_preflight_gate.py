"""`serves promote` / `serves mode enter` — the implicit preflight gate.

Feature 12 of the divergence program (issue #377): both transactions now run
`lint_manifest_set` + `rollback_check_manifest_set` before their first
mutation. An error-severity finding aborts with exit 3, before any mutation;
`--skip-preflight-checks` overrides, loudly logged to stderr. `mode enter`
also loads promotions itself (a first load absent from the mode dispatch
before this feature) and forwards its own `--restore-group` into the
rollback-check.

Every fixture here is deliberately docker-free: no serve the gate inspects
(the promotion's `rollback` serve, or any `--restore-group` member) declares
a compose `-f`/`--file`, so `rollback_check_manifest_set`'s image-presence
step never shells out to `docker`. `serves.main`/`cli.main` expose no `_run`
injection seam of their own (docs/FEATURE-EXECUTION-PLAYBOOK.md test
conventions forbid real docker/ssh/network in tests), so the guaranteed error
finding used below is `missing-registry` (a pure `os.path.isfile` check, no
docker) rather than a duplicate-serve-name -- `promote`/`mode enter` both load
their manifest set with the strict loader, which refuses duplicate names at
LOAD time, before `lint_manifest_set` would ever see them.

Downstream transaction functions (`cmd_promote`/`cmd_mode`) are monkeypatched
in tests that only care whether the gate ran, not what the transaction itself
does -- that behavior is already covered by tests/test_serves.py and
tests/test_serves_manage.py.
"""
import textwrap

import pytest

from anvil_serving import serves


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


# A `--registry` naming a file that does not exist is a pure, docker-free
# `missing-registry` lint error (os.path.isfile only) -- unlike a duplicate
# serve name, it survives the strict loader `promote`/`mode` both use.
_LINT_ERROR_BLOCK = """
    [[serve]]
    name = "gate-lint-probe"
    container = "gate-test-lint-probe"
    runtime = "docker"
    port = 39099
    model = "lint-probe"
    engine = "vllm"
    up = "python -m anvil_serving.models recipes load --registry {dir}/does-not-exist-registry.toml --confirm"
"""


def _promote_manifest(tmp_path, *, lint_error=True):
    """A valid target/rollback promotion pair. `old-heavy` (the plan's
    `rollback` serve) declares no `up` at all, so rollback-check's image
    check reports it "unverifiable" instead of shelling out to docker.
    """
    _router_config(tmp_path, "router-promoted.toml", "new-heavy")
    _router_config(tmp_path, "router-rollback.toml", "old-heavy")
    body = """
        [[serve]]
        name = "heavy"
        container = "gate-test-heavy"
        runtime = "docker"
        port = 39002
        model = "new-heavy"
        engine = "vllm"

        [[serve]]
        name = "old-heavy"
        container = "gate-test-old-heavy"
        runtime = "docker"
        port = 39002
        model = "old-heavy"
        engine = "vllm"

        [[promotion]]
        name = "heavy-v2"
        target = "heavy"
        rollback = "old-heavy"
        affected_tiers = ["primary-local"]
        router_config = "{dir}/router-promoted.toml"
        rollback_router_config = "{dir}/router-rollback.toml"
    """
    if lint_error:
        body += _LINT_ERROR_BLOCK
    return _write(tmp_path, "serves.toml", body)


_MODE_BASE = """
    [[gpu_roles]]
    id = "gate-test-a"
    vram_mib = 97887
    reserve_mib = 3072

    [[gpu_roles]]
    id = "gate-test-b"
    vram_mib = 97887
    reserve_mib = 3072

    [[serve]]
    name = "split-a"
    container = "gate-test-split-a"
    runtime = "docker"
    port = 39001
    model = "split-a-local"
    engine = "vllm"
    gpu_role = "gate-test-a"
    vram_mib = 80000
    residency = "resident"
    groups = ["split-stack"]

    [[serve]]
    name = "split-b"
    container = "gate-test-split-b"
    runtime = "docker"
    port = 39002
    model = "split-b-local"
    engine = "vllm"
    gpu_role = "gate-test-b"
    vram_mib = 80000
    residency = "resident"
    groups = ["split-stack"]

    [[serve]]
    name = "tp2"
    container = "gate-test-tp2"
    runtime = "docker"
    port = 39003
    model = "candidate-local"
    engine = "vllm"
    gpu_roles = ["gate-test-a", "gate-test-b"]
    vram_mib = 90000
    residency = "on-demand"
    operating_mode = "dual-gpu-exclusive"
    tensor_parallel_size = 2
"""


def _mode_manifest(tmp_path, *, lint_error=True):
    """DUAL_MODE_MANIFEST's shape with no compose `-f` anywhere, so the
    gate's restore-group image check never touches docker.
    """
    body = _MODE_BASE + (_LINT_ERROR_BLOCK if lint_error else "")
    return _write(tmp_path, "serves.toml", body)


# ---- promote ----------------------------------------------------------------

def test_promote_gate_aborts_before_transition_on_error(tmp_path, monkeypatch, capsys):
    path = _promote_manifest(tmp_path, lint_error=True)
    calls = []
    monkeypatch.setattr(
        serves, "_promotion_transition", lambda *a, **k: calls.append(1) or 0,
    )

    rc = serves.main(["promote", "heavy-v2", "--manifest", path])

    assert rc == 3
    assert calls == []
    out = capsys.readouterr()
    assert "missing-registry" in out.err
    assert "promote refused before any mutation" in out.err


def test_promote_gate_exit_code_via_full_cli(tmp_path, monkeypatch):
    from anvil_serving import cli

    path = _promote_manifest(tmp_path, lint_error=True)
    monkeypatch.setattr(
        serves, "_promotion_transition", lambda *a, **k: 0,
    )

    # `--confirm` clears the dispatcher's OWN mutation-confirmation gate (a
    # different check, at a different layer) so this exercises the new
    # preflight gate inside serves.main, reached through the real CLI surface.
    rc = cli.main(["serves", "promote", "heavy-v2", "--manifest", path, "--confirm"])

    assert rc == 3


def test_promote_skip_preflight_checks_bypasses_gate_with_loud_stderr(
    tmp_path, monkeypatch, capsys,
):
    path = _promote_manifest(tmp_path, lint_error=True)
    calls = []
    monkeypatch.setattr(
        serves, "_promotion_transition", lambda *a, **k: calls.append(1) or 0,
    )

    rc = serves.main([
        "promote", "heavy-v2", "--manifest", path, "--skip-preflight-checks",
    ])

    assert rc == 0
    assert calls == [1]
    err = capsys.readouterr().err
    assert (
        "preflight checks SKIPPED (--skip-preflight-checks): lint and "
        "rollback-check were NOT run for this promote" in err
    )


def test_promote_dry_run_still_runs_the_gate(tmp_path, monkeypatch, capsys):
    path = _promote_manifest(tmp_path, lint_error=True)
    calls = []
    monkeypatch.setattr(
        serves, "_promotion_transition", lambda *a, **k: calls.append(1) or 0,
    )

    rc = serves.main([
        "promote", "heavy-v2", "--manifest", path, "--dry-run",
    ])

    assert rc == 3
    assert calls == []
    assert "missing-registry" in capsys.readouterr().err


def test_promote_clean_manifest_reaches_transition(tmp_path, monkeypatch):
    # The mirror image of the abort case: no findings means the gate is
    # silent and the transaction is reached exactly once.
    path = _promote_manifest(tmp_path, lint_error=False)
    calls = []
    monkeypatch.setattr(
        serves, "_promotion_transition", lambda *a, **k: calls.append(1) or 0,
    )

    rc = serves.main(["promote", "heavy-v2", "--manifest", path])

    assert rc == 0
    assert calls == [1]


# ---- mode enter ---------------------------------------------------------------

def test_mode_enter_gate_aborts_before_cmd_mode_on_error(tmp_path, monkeypatch, capsys):
    path = _mode_manifest(tmp_path, lint_error=True)
    calls = []
    monkeypatch.setattr(serves, "cmd_mode", lambda *a, **k: calls.append(1) or 0)

    rc = serves.main([
        "mode", "enter", "tp2", "--restore-group", "split-stack", "--manifest", path,
    ])

    assert rc == 3
    assert calls == []
    out = capsys.readouterr()
    assert "missing-registry" in out.err
    assert "mode enter refused before any mutation" in out.err


def test_mode_leave_and_preview_are_not_gated(tmp_path, monkeypatch):
    # Only `enter` runs the preflight gate; the same bad manifest must not
    # block `preview`/`leave`.
    path = _mode_manifest(tmp_path, lint_error=True)
    seen = []
    monkeypatch.setattr(serves, "cmd_mode", lambda *a, **k: seen.append(a[1]) or 0)

    for mode_action in ("preview", "leave"):
        rc = serves.main([
            "mode", mode_action, "tp2", "--restore-group", "split-stack",
            "--manifest", path,
        ])
        assert rc == 0

    assert seen == ["preview", "leave"]


def test_mode_skip_preflight_checks_rejected_for_non_enter(tmp_path, monkeypatch, capsys):
    path = _mode_manifest(tmp_path, lint_error=False)
    calls = []
    monkeypatch.setattr(serves, "cmd_mode", lambda *a, **k: calls.append(1) or 0)

    rc = serves.main([
        "mode", "leave", "tp2", "--restore-group", "split-stack",
        "--manifest", path, "--skip-preflight-checks",
    ])

    assert rc == 2
    assert calls == []
    assert (
        "--skip-preflight-checks is only valid with mode enter"
        in capsys.readouterr().err
    )


def test_mode_enter_skip_preflight_checks_bypasses_gate_with_loud_stderr(
    tmp_path, monkeypatch, capsys,
):
    path = _mode_manifest(tmp_path, lint_error=True)
    calls = []
    monkeypatch.setattr(serves, "cmd_mode", lambda *a, **k: calls.append(1) or 0)

    rc = serves.main([
        "mode", "enter", "tp2", "--restore-group", "split-stack",
        "--manifest", path, "--skip-preflight-checks",
    ])

    assert rc == 0
    assert calls == [1]
    err = capsys.readouterr().err
    assert (
        "preflight checks SKIPPED (--skip-preflight-checks): lint and "
        "rollback-check were NOT run for this mode enter" in err
    )


def test_mode_enter_first_promotion_load_reports_bad_plan(tmp_path, monkeypatch, capsys):
    # `mode enter` has never loaded promotions before this feature; a bad
    # plan anywhere in the manifest SET must surface with the same message
    # shape the standalone `rollback-check` dispatch uses, not a traceback.
    path = _mode_manifest(tmp_path, lint_error=False)
    _write(tmp_path, "serves.broken.toml", """
        [[promotion]]
        name = "broken"
        target = "split-a"
        rollback = "split-b"
        affected_tiers = ["llm-a"]
        router_config = "{dir}/does-not-exist.toml"
        rollback_router_config = "{dir}/does-not-exist-2.toml"
    """)
    calls = []
    monkeypatch.setattr(serves, "cmd_mode", lambda *a, **k: calls.append(1) or 0)

    rc = serves.main([
        "mode", "enter", "tp2", "--restore-group", "split-stack", "--manifest", path,
    ])

    assert rc == 2
    assert calls == []
    assert "bad promotion plan in" in capsys.readouterr().err


def test_mode_enter_restore_group_reaches_rollback_check(tmp_path, monkeypatch):
    path = _mode_manifest(tmp_path, lint_error=False)
    captured = {}

    def fake_rollback_check(serves_set, promotions, restore_group=None, _run=None):
        captured["restore_group"] = restore_group
        return {
            "findings": [], "errors": 0, "warnings": 0, "infos": 0,
            "serves_checked": len(serves_set), "promotions_checked": len(promotions),
        }

    monkeypatch.setattr(serves, "rollback_check_manifest_set", fake_rollback_check)
    monkeypatch.setattr(serves, "cmd_mode", lambda *a, **k: 0)

    rc = serves.main([
        "mode", "enter", "tp2", "--restore-group", "split-stack", "--manifest", path,
    ])

    assert rc == 0
    assert captured["restore_group"] == "split-stack"


def test_gate_prints_warning_findings_to_stderr_without_blocking(monkeypatch, capsys):
    """A warning-only report is advisory: printed (to stderr, where a --json
    caller's error envelope would find it) but the transaction proceeds."""
    warning_report = {
        "findings": [{
            "check": "worktree-anchored-registry", "severity": "warning",
            "serve": "primary", "detail": "registry resolves inside a linked "
            "git worktree", "files": ["serves.toml"],
        }],
        "errors": 0, "warnings": 1, "serves_checked": 1,
    }
    clean_rollback = {
        "findings": [], "errors": 0, "warnings": 0, "infos": 0,
        "serves_checked": 1, "promotions_checked": 0,
    }
    monkeypatch.setattr(
        serves, "lint_manifest_set", lambda *a, **k: warning_report)
    monkeypatch.setattr(
        serves, "rollback_check_manifest_set", lambda *a, **k: clean_rollback)

    rc = serves._preflight_gate([], [], label="promote")

    assert rc is None
    err = capsys.readouterr().err
    assert "worktree-anchored-registry" in err
    assert "preflight checks for promote: 0 error(s), 1 warning(s), 0 info" in err
    assert "refused" not in err
