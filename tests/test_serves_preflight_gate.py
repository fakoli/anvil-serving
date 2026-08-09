"""`serves promote` / `serves mode enter` — the implicit preflight gate.

Feature 12 of the divergence program (issue #377): both transactions now run
`lint_manifest_set` + `rollback_check_manifest_set` before their first
mutation. `--skip-preflight-checks` overrides, loudly logged to stderr.
`mode enter` also loads promotions itself (a first load absent from the mode
dispatch before this feature) and forwards its own `--restore-group` into the
rollback-check.

The gate is SCOPED: both checks still run over the full manifest set (so
nothing goes undetected), but only an error-severity finding relevant to THIS
transaction's target/rollback/restore-group serves (`involved`, see
`_finding_is_relevant`) aborts it with exit 3. A finding about an unrelated
serve prints as advisory ("ADVISORY (outside this transaction): ...") and
does not block -- refusing every command over one unrelated manifest entry is
exactly what feature 5's revision in STRATEGY-MAKE-DIVERGENCE-LOUD.md
rejects. This closes a live regression: a fresh `anvil-serving init` home
had `missing-registry`/`rollback-image-missing` findings on scaffold serves
the transaction never touched, and the unscoped gate refused every promote.

Every fixture here is deliberately docker-free unless a fixture's docstring
says otherwise: no serve the gate inspects declares a compose `-f`/`--file`,
so `rollback_check_manifest_set`'s image-presence step never shells out to
`docker`. `serves.main` exposes no `_run` injection seam of its own
(docs/FEATURE-EXECUTION-PLAYBOOK.md test conventions forbid real
docker/ssh/network in tests), so the guaranteed error finding used below is
`missing-registry` (a pure `os.path.isfile` check, no docker) rather than a
duplicate-serve-name -- `promote`/`mode enter` both load their manifest set
with the strict loader, which refuses duplicate names at LOAD time, before
`lint_manifest_set` would ever see them.

Downstream transaction functions (`cmd_promote`/`cmd_mode`) are monkeypatched
in tests that only care whether the gate ran, not what the transaction itself
does -- that behavior is already covered by tests/test_serves.py and
tests/test_serves_manage.py.
"""
import textwrap

import pytest

from anvil_serving import serves
from tests.conftest import proc


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
_LINT_ERROR_UP = (
    'up = "python -m anvil_serving.models recipes load --registry '
    '{dir}/does-not-exist-registry.toml --confirm"'
)

# A bystander serve, tied to neither a promotion's target/rollback nor any
# restore group -- the fresh-scaffold regression fixture: an error here must
# print as advisory and never block.
_LINT_ERROR_BLOCK = """
    [[serve]]
    name = "gate-lint-probe"
    container = "gate-test-lint-probe"
    runtime = "docker"
    port = 39099
    model = "lint-probe"
    engine = "vllm"
    %s
""" % _LINT_ERROR_UP


def _promote_manifest(tmp_path, *, lint_error=False):
    """A valid target/rollback promotion pair. When `lint_error`, the plan's
    ROLLBACK serve (`old-heavy`) -- inside this transaction's blast radius --
    carries a missing-registry error via its `up` command, so the scoped
    gate must still abort on it.
    """
    _router_config(tmp_path, "router-promoted.toml", "new-heavy")
    _router_config(tmp_path, "router-rollback.toml", "old-heavy")
    old_up = _LINT_ERROR_UP if lint_error else ""
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
        %s

        [[promotion]]
        name = "heavy-v2"
        target = "heavy"
        rollback = "old-heavy"
        affected_tiers = ["primary-local"]
        router_config = "{dir}/router-promoted.toml"
        rollback_router_config = "{dir}/router-rollback.toml"
    """ % old_up
    return _write(tmp_path, "serves.toml", body)


def _promote_manifest_with_unrelated_defect(tmp_path):
    """Target + rollback pair (both clean) plus a THIRD serve, tied to
    neither, carrying a missing-registry error -- the fresh-scaffold
    regression the scoped gate exists to prevent: an error on a serve nobody
    is touching must not block this transaction."""
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
    body += _LINT_ERROR_BLOCK
    return _write(tmp_path, "serves.toml", body)


def _promote_manifest_with_compose(tmp_path):
    """Target + rollback pair whose `up` commands go through `docker
    compose`, so rollback-check's image-presence step actually calls `_run`.
    Used to exercise the `rollback-image-missing` relevance branch (matched
    by "(name)" substring against the involved set, not a bare name)."""
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
        up = "docker compose -f {dir}/compose.yml up -d heavy"

        [[serve]]
        name = "old-heavy"
        container = "gate-test-old-heavy"
        runtime = "docker"
        port = 39002
        model = "old-heavy"
        engine = "vllm"
        up = "docker compose -f {dir}/compose.yml --profile rollback up -d old-heavy"

        [[promotion]]
        name = "heavy-v2"
        target = "heavy"
        rollback = "old-heavy"
        affected_tiers = ["primary-local"]
        router_config = "{dir}/router-promoted.toml"
        rollback_router_config = "{dir}/router-rollback.toml"
    """
    return _write(tmp_path, "serves.toml", body)


def _missing_image_run(image="rollback-image:tag"):
    """Fake `_run`: compose config succeeds; `docker image inspect` says the
    image is absent -- the `rollback-image-missing` error shape. Mirrors
    tests/test_serves_rollback_check.py's fake."""
    def _run(argv, **_k):
        if argv[:2] == ["docker", "compose"]:
            return proc(0, image + "\n")
        if argv[:2] == ["docker", "image"]:
            return proc(1, "", "Error: No such image: %s" % image)
        raise AssertionError("unexpected argv: %r" % (argv,))
    return _run


_MODE_BASE_TEMPLATE = """
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
    %s

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


def _mode_manifest(tmp_path, *, lint_error=False):
    """DUAL_MODE_MANIFEST's shape with no compose `-f` anywhere, so the
    gate's restore-group image check never touches docker. When
    `lint_error`, `split-a` -- a member of the `split-stack` restore group,
    inside this transaction's blast radius -- carries a missing-registry
    error via its `up` command, so the scoped gate must still abort on it.
    """
    body = _MODE_BASE_TEMPLATE % (_LINT_ERROR_UP if lint_error else "")
    return _write(tmp_path, "serves.toml", body)


def _mode_manifest_with_unrelated_defect(tmp_path):
    """Same topology, clean, plus a bystander serve tied to neither the
    target nor the restore group -- mode-enter direction of the
    fresh-scaffold regression: the error must not block the transition."""
    body = _MODE_BASE_TEMPLATE % ""
    body += _LINT_ERROR_BLOCK
    return _write(tmp_path, "serves.toml", body)


# ---- promote ----------------------------------------------------------------

def test_promote_gate_aborts_before_transition_on_error(tmp_path, monkeypatch, capsys):
    """An error on the plan's ROLLBACK serve is inside the blast radius and
    still blocks."""
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
    assert "old-heavy" in out.err
    assert "ADVISORY" not in out.err
    assert "promote refused before any mutation" in out.err


def test_promote_advisory_finding_on_uninvolved_serve_does_not_abort(
    tmp_path, monkeypatch, capsys,
):
    """A missing-registry error on a serve that is neither the plan's target
    nor its rollback must not block promote -- the fresh-scaffold regression
    (issue #377 finding 1)."""
    path = _promote_manifest_with_unrelated_defect(tmp_path)
    calls = []
    monkeypatch.setattr(
        serves, "_promotion_transition", lambda *a, **k: calls.append(1) or 0,
    )

    rc = serves.main(["promote", "heavy-v2", "--manifest", path])

    assert rc == 0
    assert calls == [1]
    err = capsys.readouterr().err
    assert "ADVISORY (outside this transaction)" in err
    assert "missing-registry" in err
    assert "gate-lint-probe" in err


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


def test_promote_unknown_plan_name_uses_cmd_promote_refusal_not_gate_abort(
    tmp_path, monkeypatch, capsys,
):
    """A typo'd plan name has nothing for the gate to scope to; it must fall
    through to cmd_promote's own "must match exactly one" refusal, not a
    gate abort (issue #377 finding 8b)."""
    path = _promote_manifest(tmp_path, lint_error=True)
    monkeypatch.setattr(
        serves, "_promotion_transition",
        lambda *a, **k: pytest.fail("_promotion_transition must not run"),
    )

    rc = serves.main(["promote", "no-such-plan", "--manifest", path])

    assert rc == 1
    out = capsys.readouterr()
    assert "must match exactly one" in out.out
    assert "preflight" not in out.out
    assert "preflight" not in out.err


def test_promotion_topology_error_on_promoted_plan_blocks(tmp_path, monkeypatch, capsys):
    """A promotion-topology error on the plan BEING promoted is inside the
    transaction and must abort exit 3 -- not print as advisory and fall
    through -- because the promote call site adds the resolved plan's name
    to `involved`."""
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

        [[promotion]]
        name = "heavy-v2"
        target = "heavy"
        rollback = "ghost-serve"
        affected_tiers = ["primary-local"]
        router_config = "{dir}/router-promoted.toml"
        rollback_router_config = "{dir}/router-rollback.toml"
    """
    path = _write(tmp_path, "serves.toml", body)
    monkeypatch.setattr(
        serves, "_promotion_transition",
        lambda *a, **k: pytest.fail("_promotion_transition must not run"),
    )

    rc = serves.main(["promote", "heavy-v2", "--manifest", path])

    assert rc == 3
    err = capsys.readouterr().err
    assert "promotion-topology" in err
    assert "ADVISORY" not in err


def test_rollback_image_missing_on_involved_serve_blocks(tmp_path, capsys):
    """The `rollback-image-missing` relevance branch matches by "(name)"
    substring against the joined `who` string -- pin the blocking direction,
    so inverting ONLY that branch fails a test."""
    path = _promote_manifest_with_compose(tmp_path)
    serves_set = serves.load_manifest_set(path)
    promotions = serves.load_promotions(path)

    rc = serves._preflight_gate(
        serves_set, promotions, involved={"heavy", "old-heavy", "heavy-v2"},
        label="promote", _run=_missing_image_run(),
    )

    assert rc == 3
    err = capsys.readouterr().err
    assert "rollback-image-missing" in err
    assert "ADVISORY" not in err


def test_rollback_image_missing_on_uninvolved_serve_is_advisory(tmp_path, capsys):
    """Same defect, but the transaction touches neither serve -- the
    substring match must NOT fire and the finding prints as advisory."""
    path = _promote_manifest_with_compose(tmp_path)
    serves_set = serves.load_manifest_set(path)
    promotions = serves.load_promotions(path)

    rc = serves._preflight_gate(
        serves_set, promotions, involved={"some-other-serve"},
        label="promote", _run=_missing_image_run(),
    )

    assert rc is None
    err = capsys.readouterr().err
    assert "ADVISORY" in err
    assert "rollback-image-missing" in err


def test_promote_abort_under_json_carries_stderr_report(tmp_path, monkeypatch, capsys):
    """`--json` builds its error message from stderr; the gate must not lose
    its report the way an accidental stdout print would (issue #377
    finding 2)."""
    import json

    from anvil_serving import cli

    path = _promote_manifest(tmp_path, lint_error=True)
    monkeypatch.setattr(
        serves, "cmd_promote",
        lambda *a, **k: pytest.fail("cmd_promote must not run"),
    )

    rc = cli.main([
        "serves", "promote", "heavy-v2", "--manifest", path,
        "--confirm", "--json",
    ])

    assert rc == 3
    captured = capsys.readouterr()
    assert captured.err == ""
    payload = json.loads(captured.out)
    assert payload["error"]["class"] == "safety"
    assert "missing-registry" in payload["error"]["message"]
    assert payload["error"]["message"].strip() != "command failed"


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
    assert "ADVISORY" not in out.err
    assert "mode enter refused before any mutation" in out.err


def test_mode_enter_advisory_on_uninvolved_serve_does_not_abort(tmp_path, monkeypatch, capsys):
    """Mode-enter direction of the fresh-scaffold rule: an error on a serve
    that is neither the target nor in the restore group must not block the
    transition."""
    path = _mode_manifest_with_unrelated_defect(tmp_path)
    calls = []
    monkeypatch.setattr(serves, "cmd_mode", lambda *a, **k: calls.append(1) or 0)

    rc = serves.main([
        "mode", "enter", "tp2", "--restore-group", "split-stack", "--manifest", path,
    ])

    assert rc == 0
    assert calls == [1]
    err = capsys.readouterr().err
    assert "ADVISORY (outside this transaction)" in err
    assert "missing-registry" in err
    assert "gate-lint-probe" in err


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
