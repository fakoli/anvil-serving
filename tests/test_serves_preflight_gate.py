"""`serves promote` / `serves mode enter` run lint + rollback-check first.

Both checks already existed standalone (`serves lint`, `serves rollback-check`)
but were never wired as a precondition: a transaction could complete against
a manifest set that would fail either check. This wires them in at one shared
chokepoint (`_preflight_gate`), called from `serves.main`'s dispatch before
either transaction's first mutation. See docs/STRATEGY-MAKE-DIVERGENCE-LOUD.md
and anvil-serving issue #377.

The gate is SCOPED: both checks still run over the full manifest set (so
nothing goes undetected), but only an error-severity finding relevant to
THIS transaction's target/rollback/restore-group serves aborts it. A finding
about an unrelated serve prints as advisory and does not block -- refusing
every command over one unrelated manifest entry is exactly what feature 5's
revision in STRATEGY-MAKE-DIVERGENCE-LOUD.md rejects. All gate output goes to
stderr (so `--json`/`--quiet` don't lose it), and a passing gate that has any
findings at all (warnings, infos, or advisory errors) still prints them --
silent pass on a swallowed finding (e.g. `docker-unavailable`, meaning the
rollback verification did not actually run) is itself a silent failure.

Docker/git are injected via the module's `_run` seam; no real docker in
these tests. CLI-level dispatch tests go through `cli.main` and monkeypatch
the module functions the dispatcher calls by name (`_preflight_gate`,
`_promotion_transition`, `cmd_mode`, `cmd_promote`) -- a leaf-level call to
`cmd_promote`/`_promotion_transition` directly would bypass the new gate
entirely, since the gate lives in `serves.main`'s dispatch, not in those
functions.
"""
import json
import textwrap

import pytest

from anvil_serving import cli, serves
from tests.conftest import proc


@pytest.fixture(autouse=True)
def _isolated_host_policy(monkeypatch, tmp_path):
    """Never let a developer's enabled machine policy affect unit timing."""
    monkeypatch.setenv("ANVIL_SERVING_HOME", str(tmp_path / ".anvil-serving"))


def _write(tmp_path, filename, body):
    path = tmp_path / filename
    path.write_text(textwrap.dedent(body), encoding="utf-8")
    return str(path)


def _entry(name, container, model, port, up="", groups=None):
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
        {up}
        {groups_line}
    """


def _router_config(tmp_path, filename, model, tier_id="primary-local"):
    _write(tmp_path, filename, f"""
        [router]
        [[router.tiers]]
        id = "{tier_id}"
        base_url = "http://127.0.0.1:30002/v1"
        model = "{model}"
        dialect = "openai"
        context_limit = 4096
        privacy = "local"
        tool_support = true
        auth_env = "ANVIL_PRIMARY_LOCAL_KEY"
        health_path = "/health"
        model_identity = true
        [router.model_routes]
        llm.primary = "{tier_id}"
    """)


def _promotion_block():
    return """
        [[promotion]]
        name = "heavy-v2"
        target = "heavy"
        rollback = "old-heavy"
        affected_tiers = ["primary-local"]
        router_config = "{dir}/router-promoted.toml"
        rollback_router_config = "{dir}/router-rollback.toml"
    """


def _promote_manifest(tmp_path, *, broken_registry=False):
    """A promotable target + rollback pair; no compose `up` (`-f`), so the
    rollback-check's docker image-presence step never touches `_run`. The
    broken registry (when requested) is on `old-heavy`, the plan's rollback
    serve -- i.e. an error INSIDE this transaction's blast radius."""
    _router_config(tmp_path, "router-promoted.toml", "new-heavy")
    _router_config(tmp_path, "router-rollback.toml", "old-heavy")
    old_up = 'up = "true --registry {dir}/missing-registry.toml"' if broken_registry else ""
    body = (
        _entry("heavy", "heavy-c", "new-heavy", 30002)
        + _entry("old-heavy", "old-heavy-c", "old-heavy", 30002, up=old_up)
        + _promotion_block()
    )
    return _write(tmp_path, "serves.toml", body)


def _promote_manifest_with_unrelated_defect(tmp_path):
    """Target + rollback pair (both clean) plus a THIRD serve, tied to
    neither, carrying a missing-registry error -- the fresh-scaffold
    regression case feature 5's revision exists to prevent: an error on a
    serve nobody is touching must not block this transaction."""
    _router_config(tmp_path, "router-promoted.toml", "new-heavy")
    _router_config(tmp_path, "router-rollback.toml", "old-heavy")
    body = (
        _entry("heavy", "heavy-c", "new-heavy", 30002)
        + _entry("old-heavy", "old-heavy-c", "old-heavy", 30002)
        + _entry("unrelated", "unrelated-c", "unrelated-model", 30099,
                 up='up = "true --registry {dir}/missing-registry.toml"')
        + _promotion_block()
    )
    return _write(tmp_path, "serves.toml", body)


def _promote_manifest_with_compose(tmp_path):
    """Target + rollback pair whose `up` commands go through `docker
    compose`, so the rollback-check's image-presence step actually calls
    `_run`. Used to exercise the `docker-unavailable` warning path."""
    _router_config(tmp_path, "router-promoted.toml", "new-heavy")
    _router_config(tmp_path, "router-rollback.toml", "old-heavy")
    body = (
        _entry("heavy", "heavy-c", "new-heavy", 30002,
               up='up = "docker compose -f {dir}/compose.yml up -d heavy"')
        + _entry("old-heavy", "old-heavy-c", "old-heavy", 30002,
                 up='up = "docker compose -f {dir}/compose.yml --profile rollback up -d old-heavy"')
        + _promotion_block()
    )
    return _write(tmp_path, "serves.toml", body)


def _mode_manifest(tmp_path, *, broken_registry=False, restore_group="split-stack"):
    """No [[promotion]] entries -- `load_promotions` returns [] with no disk I/O.
    The broken registry (when requested) is on `split-a`, tagged with the
    restore group -- i.e. an error INSIDE this transaction's blast radius."""
    old_up = 'up = "true --registry {dir}/missing-registry.toml"' if broken_registry else ""
    body = (
        _entry("heavy", "heavy-c", "candidate-local", 30003)
        + _entry("split-a", "split-a-c", "split-a-local", 30001,
                 up=old_up, groups=[restore_group])
    )
    return _write(tmp_path, "serves.toml", body)


def _git_worktree_run():
    """Fake `_run`: `git rev-parse` reports a linked worktree; nothing else called."""
    def run(argv, **_k):
        if argv[:1] == ["git"]:
            if argv[-1] == "--git-common-dir":
                return proc(0, "common-dir\n")
            if argv[-1] == "--git-dir":
                return proc(0, "own-dir\n")
        raise AssertionError("unexpected argv: %r" % (argv,))
    return run


def _docker_missing_run():
    """Fake `_run`: every `docker compose` invocation raises, as it would if
    the docker CLI/daemon were unavailable on this host."""
    def run(argv, **_k):
        if argv[:2] == ["docker", "compose"]:
            raise OSError("docker: command not found")
        raise AssertionError("unexpected argv: %r" % (argv,))
    return run


def _never_run(*_a, **_k):
    raise AssertionError("docker/git must not be consulted for this case")


# --- _preflight_gate itself (leaf-level, fake `_run`) -----------------------


def test_gate_passes_clean_manifest_with_no_findings(tmp_path):
    path = _promote_manifest(tmp_path)
    serves_set = serves.load_manifest_set(path)
    promotions = serves.load_promotions(path)

    assert serves._preflight_gate(
        serves_set, promotions, None, False, {"heavy", "old-heavy"}) is True


def test_gate_aborts_and_prints_findings_on_error(tmp_path, capsys):
    path = _promote_manifest(tmp_path, broken_registry=True)
    serves_set = serves.load_manifest_set(path)
    promotions = serves.load_promotions(path)

    ok = serves._preflight_gate(
        serves_set, promotions, None, False, {"heavy", "old-heavy"})

    assert ok is False
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "preflight gate: aborting before any mutation" in captured.err
    assert "missing-registry" in captured.err
    assert "old-heavy" in captured.err


def test_gate_skip_bypasses_both_checks_and_warns_loudly(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(serves, "lint_manifest_set", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("lint must not run when skipped")))
    monkeypatch.setattr(serves, "rollback_check_manifest_set", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("rollback-check must not run when skipped")))

    ok = serves._preflight_gate([], [], None, True, set())

    assert ok is True
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "WARNING: --skip-preflight-checks" in captured.err


def test_gate_pass_with_warning_finding_prints_report_to_stderr(tmp_path, capsys):
    """A worktree-anchored-registry WARNING (never blocking) must still be
    reported -- a clean-looking pass that dropped a real finding is the
    silent-failure class this gate exists to close (finding 4)."""
    registry = tmp_path / "registry.toml"
    registry.write_text("", encoding="utf-8")
    body = _entry("heavy", "heavy-c", "heavy-model", 30002,
                   up='up = "true --registry {dir}/registry.toml"')
    manifest_path = _write(tmp_path, "serves.toml", body)
    serves_set = serves.load_manifest_set(manifest_path)

    ok = serves._preflight_gate(
        serves_set, [], None, False, {"heavy"}, _run=_git_worktree_run())

    assert ok is True
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "preflight gate: passed with" in captured.err
    assert "worktree-anchored-registry" in captured.err


def test_gate_pass_with_docker_unavailable_warning_is_not_silent(tmp_path, capsys):
    """`docker-unavailable` means the rollback image-presence check did not
    actually run -- a fail-open case that must never pass silently."""
    path = _promote_manifest_with_compose(tmp_path)
    serves_set = serves.load_manifest_set(path)
    promotions = serves.load_promotions(path)

    ok = serves._preflight_gate(
        serves_set, promotions, None, False, {"heavy", "old-heavy"},
        _run=_docker_missing_run())

    assert ok is True
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "docker-unavailable" in captured.err
    assert "preflight gate: passed with" in captured.err


def test_gate_forwards_restore_group_to_rollback_check(monkeypatch):
    captured = {}

    def fake_rollback_check(serves_set, promotions, restore_group=None, _run=None):
        captured["restore_group"] = restore_group
        return {"errors": 0, "findings": []}

    monkeypatch.setattr(serves, "lint_manifest_set", lambda *a, **k: {"errors": 0, "findings": []})
    monkeypatch.setattr(serves, "rollback_check_manifest_set", fake_rollback_check)

    assert serves._preflight_gate([], [], "split-default", False, set()) is True
    assert captured["restore_group"] == "split-default"


# --- dispatcher-level: serves.main's promote/mode enter call sites ---------


def test_promote_aborts_before_transition_on_error_finding(tmp_path, monkeypatch, capsys):
    """An error on the plan's ROLLBACK serve is inside the blast radius and
    still blocks."""
    path = _promote_manifest(tmp_path, broken_registry=True)
    calls = []
    monkeypatch.setattr(
        serves, "_promotion_transition",
        lambda *a, **k: calls.append(1) or 0,
    )

    rc = cli.main([
        "serves", "promote", "heavy-v2", "--manifest", path, "--confirm",
    ])

    assert rc == 3
    assert calls == []
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "missing-registry" in captured.err


def test_promote_advisory_finding_on_uninvolved_serve_does_not_abort(tmp_path, monkeypatch, capsys):
    """A missing-registry error on a serve that is neither the plan's target
    nor its rollback must not block promote -- the fresh-scaffold regression
    (issue #377 finding 1)."""
    path = _promote_manifest_with_unrelated_defect(tmp_path)
    calls = []
    monkeypatch.setattr(serves, "cmd_promote", lambda *a, **k: calls.append(1) or 0)

    rc = cli.main([
        "serves", "promote", "heavy-v2", "--manifest", path, "--confirm",
    ])

    assert rc == 0
    assert calls == [1]
    captured = capsys.readouterr()
    assert "ADVISORY (outside this transaction)" in captured.err
    assert "missing-registry" in captured.err
    assert "unrelated" in captured.err


def test_promote_rollback_and_resume_flags_are_gated(tmp_path, monkeypatch):
    path = _promote_manifest(tmp_path, broken_registry=True)
    monkeypatch.setattr(
        serves, "cmd_promote",
        lambda *a, **k: pytest.fail("cmd_promote must not run"),
    )

    rc_rollback = cli.main([
        "serves", "promote", "heavy-v2", "--manifest", path,
        "--confirm", "--rollback",
    ])
    rc_resume = cli.main([
        "serves", "promote", "heavy-v2", "--manifest", path,
        "--confirm", "--resume",
    ])

    assert rc_rollback == 3
    assert rc_resume == 3


def test_promote_unknown_plan_name_uses_cmd_promote_refusal_not_gate_abort(tmp_path, monkeypatch, capsys):
    """A typo'd plan name has nothing for the gate to scope to; it must fall
    through to cmd_promote's own "must match exactly one" refusal, not a
    gate abort (issue #377 finding 8b)."""
    path = _promote_manifest(tmp_path)
    monkeypatch.setattr(
        serves, "_promotion_transition",
        lambda *a, **k: pytest.fail("_promotion_transition must not run"),
    )

    rc = cli.main([
        "serves", "promote", "no-such-plan", "--manifest", path, "--confirm",
    ])

    assert rc == 1
    captured = capsys.readouterr()
    assert "must match exactly one" in captured.out
    assert "preflight gate" not in captured.out
    assert "preflight gate" not in captured.err


def test_promote_dry_run_still_gates_and_aborts(tmp_path, monkeypatch):
    path = _promote_manifest(tmp_path, broken_registry=True)
    calls = []
    monkeypatch.setattr(
        serves, "_promotion_transition",
        lambda *a, **k: calls.append(1) or 0,
    )

    rc = cli.main([
        "serves", "promote", "heavy-v2", "--manifest", path,
        "--dry-run", "--confirm",
    ])

    assert rc == 3
    assert calls == []


def test_promote_skip_preflight_checks_bypasses_gate_and_warns(tmp_path, monkeypatch, capsys):
    path = _promote_manifest(tmp_path, broken_registry=True)
    monkeypatch.setattr(serves, "cmd_promote", lambda *a, **k: 0)

    rc = cli.main([
        "serves", "promote", "heavy-v2", "--manifest", path,
        "--skip-preflight-checks", "--confirm",
    ])

    assert rc == 0
    assert "WARNING: --skip-preflight-checks" in capsys.readouterr().err


def test_promote_abort_under_json_carries_stderr_report(tmp_path, monkeypatch, capsys):
    """`--json` builds its error message from stderr; the gate must not lose
    its report the way an accidental stdout print would (issue #377
    finding 2)."""
    path = _promote_manifest(tmp_path, broken_registry=True)
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


def _missing_image_run(image="rollback-image:tag"):
    """Compose config succeeds; `docker image inspect` says the image is
    absent -- the `rollback-image-missing` error shape. Mirrors
    tests/test_serves_rollback_check.py's fake."""
    def _run(argv, **_k):
        if argv[:2] == ["docker", "compose"]:
            return proc(0, image + "\n")
        if argv[:2] == ["docker", "image"]:
            return proc(1, "", "Error: No such image: %s" % image)
        raise AssertionError("unexpected argv: %r" % (argv,))
    return _run


def test_rollback_image_missing_on_involved_serve_blocks(tmp_path, capsys):
    """The `rollback-image-missing` relevance branch matches by "(name)"
    substring against the joined `who` string -- pin the blocking
    direction, so inverting ONLY that branch fails a test."""
    path = _promote_manifest_with_compose(tmp_path)
    serves_set = serves.load_manifest_set(path)
    promotions = serves.load_promotions(path)

    ok = serves._preflight_gate(
        serves_set, promotions, None, False,
        {"heavy", "old-heavy", "heavy-v2"}, _run=_missing_image_run())

    assert ok is False
    captured = capsys.readouterr()
    assert "rollback-image-missing" in captured.err
    assert "ADVISORY" not in captured.err


def test_rollback_image_missing_on_uninvolved_serve_is_advisory(tmp_path, capsys):
    """Same defect, but the transaction touches neither serve -- the
    substring match must NOT fire and the finding prints as advisory."""
    path = _promote_manifest_with_compose(tmp_path)
    serves_set = serves.load_manifest_set(path)
    promotions = serves.load_promotions(path)

    ok = serves._preflight_gate(
        serves_set, promotions, None, False,
        {"some-other-serve"}, _run=_missing_image_run())

    assert ok is True
    captured = capsys.readouterr()
    assert "ADVISORY" in captured.err
    assert "rollback-image-missing" in captured.err


def test_promotion_topology_error_on_promoted_plan_blocks(tmp_path, monkeypatch, capsys):
    """A promotion-topology error on the plan BEING promoted is inside the
    transaction and must abort exit 3 -- not print as "outside this
    transaction" and fall through (re-review finding 1)."""
    _router_config(tmp_path, "router-promoted.toml", "new-heavy")
    _router_config(tmp_path, "router-rollback.toml", "old-heavy")
    body = _entry("heavy", "heavy-c", "new-heavy", 30002) + """
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
        serves, "cmd_promote",
        lambda *a, **k: pytest.fail("cmd_promote must not run"),
    )

    rc = cli.main([
        "serves", "promote", "heavy-v2", "--manifest", path, "--confirm",
    ])

    assert rc == 3
    captured = capsys.readouterr()
    assert "promotion-topology" in captured.err
    assert "ADVISORY" not in captured.err


def test_mode_enter_advisory_on_uninvolved_serve_does_not_abort(tmp_path, monkeypatch, capsys):
    """Mode-enter direction of the fresh-scaffold rule: an error on a serve
    that is neither the target nor in the restore group must not block the
    transition (only the promote direction was pinned before)."""
    body = (
        _entry("heavy", "heavy-c", "candidate-local", 30003)
        + _entry("split-a", "split-a-c", "split-a-local", 30001,
                 groups=["split-stack"])
        + _entry("bystander", "bystander-c", "bystander-model", 30099,
                 up='up = "true --registry {dir}/missing-registry.toml"')
    )
    path = _write(tmp_path, "serves.toml", body)
    calls = []
    monkeypatch.setattr(
        serves, "cmd_mode", lambda *a, **k: calls.append(a) or 0)

    rc = cli.main([
        "serves", "mode", "enter", "heavy",
        "--restore-group", "split-stack", "--manifest", path, "--confirm",
    ])

    assert rc == 0
    assert len(calls) == 1
    captured = capsys.readouterr()
    assert "ADVISORY" in captured.err
    assert "missing-registry" in captured.err


def test_mode_enter_aborts_before_cmd_mode_on_error_finding(tmp_path, monkeypatch, capsys):
    path = _mode_manifest(tmp_path, broken_registry=True)
    monkeypatch.setattr(serves, "cmd_mode", lambda *a, **k: pytest.fail("cmd_mode must not run"))

    rc = cli.main([
        "serves", "mode", "enter", "heavy",
        "--restore-group", "split-stack", "--manifest", path, "--confirm",
    ])

    assert rc == 3
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "missing-registry" in captured.err


def test_mode_enter_dry_run_is_gated(tmp_path, monkeypatch):
    path = _mode_manifest(tmp_path, broken_registry=True)
    monkeypatch.setattr(serves, "cmd_mode", lambda *a, **k: pytest.fail("cmd_mode must not run"))

    rc = cli.main([
        "serves", "mode", "enter", "heavy",
        "--restore-group", "split-stack", "--manifest", path,
        "--dry-run", "--confirm",
    ])

    assert rc == 3


def test_mode_enter_restore_group_typo_exits_3(tmp_path, monkeypatch):
    """Behavior change (issue #377 finding 6, intentionally kept): before the
    gate, a typo'd `--restore-group` surfaced from `cmd_mode` as exit 2. The
    gate's `unknown-restore-group` finding is a blocking error, so it now
    aborts as exit 3 -- the gate's own abort code, and still loud."""
    path = _mode_manifest(tmp_path)
    monkeypatch.setattr(serves, "cmd_mode", lambda *a, **k: pytest.fail("cmd_mode must not run"))

    rc = cli.main([
        "serves", "mode", "enter", "heavy",
        "--restore-group", "split-stack-typo", "--manifest", path, "--confirm",
    ])

    assert rc == 3


def test_mode_leave_is_not_gated(tmp_path, monkeypatch):
    path = _mode_manifest(tmp_path)
    gate_calls = []
    monkeypatch.setattr(
        serves, "_preflight_gate",
        lambda *a, **k: gate_calls.append(1) or True,
    )
    monkeypatch.setattr(serves, "cmd_mode", lambda *a, **k: 0)

    rc = cli.main([
        "serves", "mode", "leave", "heavy",
        "--restore-group", "split-stack", "--manifest", path, "--confirm",
    ])

    assert rc == 0
    assert gate_calls == []


def test_mode_enter_forwards_restore_group_to_rollback_check(tmp_path, monkeypatch):
    path = _mode_manifest(tmp_path, restore_group="split-default")
    captured = {}

    def fake_rollback_check(serves_set, promotions, restore_group=None, _run=None):
        captured["restore_group"] = restore_group
        return {"errors": 0, "findings": []}

    monkeypatch.setattr(serves, "rollback_check_manifest_set", fake_rollback_check)
    monkeypatch.setattr(serves, "cmd_mode", lambda *a, **k: 0)

    rc = cli.main([
        "serves", "mode", "enter", "heavy",
        "--restore-group", "split-default", "--manifest", path, "--confirm",
    ])

    assert rc == 0
    assert captured["restore_group"] == "split-default"


def test_skip_preflight_checks_rejected_outside_mode_enter(tmp_path):
    path = _mode_manifest(tmp_path)

    rc = cli.main([
        "serves", "mode", "leave", "heavy",
        "--restore-group", "split-stack", "--manifest", path,
        "--skip-preflight-checks", "--confirm",
    ])

    assert rc == 2


def test_skip_preflight_checks_rejected_on_preview(tmp_path):
    path = _mode_manifest(tmp_path)

    rc = cli.main([
        "serves", "mode", "preview", "heavy",
        "--restore-group", "split-stack", "--manifest", path,
        "--skip-preflight-checks",
    ])

    assert rc == 2


def test_skip_preflight_checks_rejected_on_status(tmp_path):
    path = _mode_manifest(tmp_path)

    rc = cli.main([
        "serves", "mode", "status", "--manifest", path,
        "--skip-preflight-checks",
    ])

    assert rc == 2
