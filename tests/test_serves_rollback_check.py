"""`serves rollback-check` — prove every declared rollback is actually usable.

Two rollback paths were found broken live on 2026-08-08, each only by
accident: a promotion plan's `rollback_router_config` referenced a file that
did not exist, and a restore-group serve's compose image was a nightly tag
evicted from Docker Hub. See docs/STRATEGY-MAKE-DIVERGENCE-LOUD.md
(feature 4).
"""
import json
import textwrap

from anvil_serving import serves


def _write(tmp_path, filename, body):
    path = tmp_path / filename
    path.write_text(textwrap.dedent(body), encoding="utf-8")
    return str(path)


def _entry(name, container, model="m", port=30001, up="", groups=None):
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


def _promotion_fixture(tmp_path, rollback_model="old-heavy"):
    """A promotable target + rollback pair, both docker-compose owned."""
    _router_config(tmp_path, "router-promoted.toml", "new-heavy")
    _router_config(tmp_path, "router-rollback.toml", rollback_model)
    body = (
        _entry("heavy", "heavy-c", model="new-heavy", port=30002,
               up='up = "docker compose -f {dir}/compose.yml up -d heavy"')
        + _entry("old-heavy", "old-heavy-c", model="old-heavy", port=30002,
                 up='up = "docker compose -f {dir}/compose.yml --profile rollback up -d old-heavy"')
        + _promotion_block()
    )
    return _write(tmp_path, "serves.toml", body)


class _Result:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _ok_run(image="repo/rollback-image:tag"):
    """A fake `_run` where compose config succeeds and the image is present."""
    def _run(argv, **_k):
        if argv[:2] == ["docker", "compose"]:
            return _Result(0, stdout=image + "\n")
        if argv[:2] == ["docker", "image"]:
            return _Result(0)
        raise AssertionError("unexpected argv: %r" % (argv,))
    return _run


def _missing_image_run(image="vllm/vllm-openai:nightly-f25953cc"):
    """A fake `_run` where compose config succeeds but the image inspect fails."""
    def _run(argv, **_k):
        if argv[:2] == ["docker", "compose"]:
            return _Result(0, stdout=image + "\n")
        if argv[:2] == ["docker", "image"]:
            return _Result(1, stderr="Error: No such image: %s" % image)
        raise AssertionError("unexpected argv: %r" % (argv,))
    return _run


def _never_run(*_a, **_k):
    raise AssertionError("docker must not be consulted for this case")


def _oserror_run(*_a, **_k):
    raise OSError("docker: command not found")


def test_clean_manifest_and_valid_promotion_has_no_findings(tmp_path):
    path = _promotion_fixture(tmp_path)
    serves_set = serves.load_manifest_set(path)
    promotions = serves.load_promotions(path)

    report = serves.rollback_check_manifest_set(
        serves_set, promotions, _run=_ok_run())

    assert report["findings"] == []
    assert report["errors"] == 0
    assert report["promotions_checked"] == 1
    assert serves.cmd_rollback_check(serves_set, promotions, _run=_ok_run()) == 0


def test_promotion_with_mismatched_rollback_tier_model_is_an_error(tmp_path):
    # The rollback router config's tier declares a DIFFERENT model than the
    # rollback serve itself -- _validate_promotion_topology must catch this,
    # and rollback-check must report it as a finding, never raise.
    path = _promotion_fixture(tmp_path, rollback_model="some-other-model")
    serves_set = serves.load_manifest_set(path)
    promotions = serves.load_promotions(path)

    report = serves.rollback_check_manifest_set(
        serves_set, promotions, _run=_ok_run())

    topology = [f for f in report["findings"] if f["check"] == "promotion-topology"]
    assert len(topology) == 1
    assert topology[0]["severity"] == "error"
    assert topology[0]["serve"] == "heavy-v2"
    assert report["errors"] >= 1
    assert serves.cmd_rollback_check(serves_set, promotions, _run=_ok_run()) == 1


def test_restore_group_serve_with_missing_image_is_an_error(tmp_path):
    path = _write(tmp_path, "serves.toml", _entry(
        "primary", "primary-c", groups=["restore"],
        up='up = "docker compose -f {dir}/compose.yml up -d primary"',
    ))
    serves_set = serves.load_manifest_set(path)

    run = _missing_image_run()
    report = serves.rollback_check_manifest_set(
        serves_set, [], restore_group="restore", _run=run)

    findings = [f for f in report["findings"] if f["check"] == "rollback-image-missing"]
    assert len(findings) == 1
    assert findings[0]["severity"] == "error"
    assert "primary" in findings[0]["serve"]
    assert "nightly-f25953cc" in findings[0]["detail"]
    assert report["errors"] == 1
    assert serves.cmd_rollback_check(
        serves_set, [], restore_group="restore", _run=run) == 1


def test_restore_group_serve_with_present_image_has_no_finding(tmp_path):
    path = _write(tmp_path, "serves.toml", _entry(
        "primary", "primary-c", groups=["restore"],
        up='up = "docker compose -f {dir}/compose.yml up -d primary"',
    ))
    serves_set = serves.load_manifest_set(path)

    report = serves.rollback_check_manifest_set(
        serves_set, [], restore_group="restore", _run=_ok_run())

    assert report["findings"] == []
    assert report["errors"] == 0


def test_up_command_without_compose_file_is_unverifiable_info(tmp_path):
    path = _write(tmp_path, "serves.toml", _entry(
        "loader", "loader-c", groups=["restore"],
        up='up = "python -m anvil_serving.models recipes load --confirm"',
    ))
    serves_set = serves.load_manifest_set(path)

    def _never(*_a, **_k):
        raise AssertionError("docker must not be consulted when up has no -f")

    report = serves.rollback_check_manifest_set(
        serves_set, [], restore_group="restore", _run=_never)

    assert [f["check"] for f in report["findings"]] == ["image-unverifiable"]
    assert report["findings"][0]["severity"] == "info"
    assert report["errors"] == 0
    assert serves.cmd_rollback_check(
        serves_set, [], restore_group="restore", _run=_never) == 0


def test_docker_unavailable_is_a_warning_not_a_crash(tmp_path):
    path = _write(tmp_path, "serves.toml", _entry(
        "primary", "primary-c", groups=["restore"],
        up='up = "docker compose -f {dir}/compose.yml up -d primary"',
    ))
    serves_set = serves.load_manifest_set(path)

    report = serves.rollback_check_manifest_set(
        serves_set, [], restore_group="restore", _run=_oserror_run)

    assert [f["check"] for f in report["findings"]] == ["docker-unavailable"]
    assert report["findings"][0]["severity"] == "warning"
    assert report["errors"] == 0
    assert serves.cmd_rollback_check(
        serves_set, [], restore_group="restore", _run=_oserror_run) == 0


def test_json_report_is_machine_readable(tmp_path, capsys):
    path = _promotion_fixture(tmp_path, rollback_model="some-other-model")
    serves_set = serves.load_manifest_set(path)
    promotions = serves.load_promotions(path)

    rc = serves.cmd_rollback_check(
        serves_set, promotions, as_json=True, _run=_ok_run())
    assert rc == 1
    report = json.loads(capsys.readouterr().out)
    assert report["errors"] == 1
    checks = [f["check"] for f in report["findings"]]
    assert "promotion-topology" in checks
    assert report["serves_checked"] == 2
    assert report["promotions_checked"] == 1


def test_compose_config_failure_is_an_error_finding(tmp_path):
    # A compose file that `docker compose config` itself rejects (deleted,
    # malformed, bad interpolation) means the rollback cannot even be planned.
    # This is a distinct emission path from a missing image — found untested by
    # a mutation check during review.
    path = _write(tmp_path, "serves.toml", _entry(
        "primary", "primary-c", groups=["restore"],
        up='up = "docker compose -f {dir}/compose.yml up -d primary"',
    ))

    def _config_fails(argv, **_k):
        if argv[:2] == ["docker", "compose"]:
            return _Result(1, stderr="no configuration file provided")
        raise AssertionError("image inspect must not run when config failed")

    report = serves.rollback_check_manifest_set(
        serves.load_manifest_set(path), [], restore_group="restore",
        _run=_config_fails)

    findings = [f for f in report["findings"] if f["check"] == "rollback-image-missing"]
    assert len(findings) == 1
    assert findings[0]["severity"] == "error"
    assert "config --images" in findings[0]["detail"]
    assert "failed" in findings[0]["detail"]
    assert "no configuration file provided" in findings[0]["detail"]


def test_unknown_restore_group_is_an_error_not_silent_success(tmp_path):
    # A typo'd --restore-group matching zero serves must not report a clean
    # check — silently verifying nothing is the false safety net this command
    # exists to kill. Found by an adversarial probe against the live manifest.
    path = _write(tmp_path, "serves.toml", _entry(
        "primary", "primary-c", groups=["restore"],
        up='up = "docker compose -f {dir}/compose.yml up -d primary"',
    ))

    report = serves.rollback_check_manifest_set(
        serves.load_manifest_set(path), [], restore_group="restoer",
        _run=_never_run)

    findings = [f for f in report["findings"] if f["check"] == "unknown-restore-group"]
    assert len(findings) == 1
    assert findings[0]["severity"] == "error"
    assert "restoer" in findings[0]["detail"]
    assert report["errors"] == 1


def test_profile_gated_rollback_is_actually_checked(tmp_path):
    # THE blocker from adversarial review: every real rollback serve is gated
    # behind `--profile rollback`, and compose EXCLUDES profile-gated services
    # from `config` unless the profile is passed. Dropping the profile silently
    # skipped exactly the services this command exists to verify.
    path = _write(tmp_path, "serves.toml", _entry(
        "old-primary", "old-c", groups=["restore"],
        up='up = "docker compose -f {dir}/compose.yml --profile rollback up -d old-primary"',
    ))
    seen = {}

    def _run(argv, **_k):
        if argv[:2] == ["docker", "compose"]:
            seen["argv"] = argv
            return _Result(0, stdout="img:1\n")
        if argv[:2] == ["docker", "image"]:
            return _Result(0)
        raise AssertionError("unexpected argv: %r" % (argv,))

    report = serves.rollback_check_manifest_set(
        serves.load_manifest_set(path), [], restore_group="restore", _run=_run)

    assert report["findings"] == []
    argv = seen["argv"]
    assert "--profile" in argv and argv[argv.index("--profile") + 1] == "rollback"
    # The service name scopes the image query to the serve being checked, so a
    # missing image on an UNRELATED service in the same file cannot masquerade
    # as a broken rollback.
    assert argv[-1] == "old-primary"


def test_long_file_flag_and_equals_forms_are_verified_not_unverifiable(tmp_path):
    # `--file`, `--file=`, and `-f=` are the same declaration as `-f`; they
    # must reach docker, not downgrade to an "unverifiable" info blessing.
    for spelling in ("--file {dir}/compose.yml", "--file={dir}/compose.yml",
                     "-f={dir}/compose.yml"):
        path = _write(tmp_path, "serves.toml", _entry(
            "p", "pc", groups=["restore"],
            up=f'up = "docker compose {spelling} up -d p"',
        ))
        report = serves.rollback_check_manifest_set(
            serves.load_manifest_set(path), [], restore_group="restore",
            _run=_missing_image_run())
        checks = [f["check"] for f in report["findings"]]
        assert checks == ["rollback-image-missing"], (spelling, checks)


def test_overlay_chain_forwards_every_compose_file(tmp_path):
    path = _write(tmp_path, "serves.toml", _entry(
        "p", "pc", groups=["restore"],
        up='up = "docker compose -f {dir}/base.yml -f {dir}/override.yml up -d p"',
    ))
    seen = {}

    def _run(argv, **_k):
        if argv[:2] == ["docker", "compose"]:
            seen["argv"] = argv
            return _Result(0, stdout="img:1\n")
        return _Result(0)

    serves.rollback_check_manifest_set(
        serves.load_manifest_set(path), [], restore_group="restore", _run=_run)
    argv = seen["argv"]
    assert argv.count("-f") == 2
    assert any(a.endswith("base.yml") for a in argv)
    assert any(a.endswith("override.yml") for a in argv)
