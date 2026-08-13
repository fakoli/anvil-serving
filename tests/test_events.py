"""Tests for the optional anvil-events lifecycle emission seam."""

import json
import subprocess

import pytest

from tests.conftest import proc


def test_emit_disabled_by_default_never_spawns(tmp_path):
    from anvil_serving import events

    calls = []
    result = events.emit_lifecycle_event(
        "serve.up",
        {"serve": "example"},
        config_path=tmp_path / "missing-events.toml",
        _run=lambda *args, **kwargs: calls.append((args, kwargs)),
    )

    assert result == {"enabled": False, "emitted": False, "detail": "disabled"}
    assert calls == []


def test_enabled_emit_records_via_canonical_cli_and_env_transport(tmp_path):
    from anvil_serving import events

    config = tmp_path / "events.toml"
    config.write_text(
        """\
[events]
enabled = true
command = "/opt/anvil/bin/anvil-events"
host = "node-a"
producer = "node-a:serves"
nats_url_env = "FLEET_NATS_URL"
""",
        encoding="utf-8",
    )
    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return proc(0, "emitted node-a:serves:000001 -> anvil.fleet.node-a.serve.up seq=1 sent=True\n")

    result = events.emit_lifecycle_event(
        "serve.up",
        {"serve": "example", "model": "example-local"},
        correlation_id="change-123",
        config_path=config,
        environ={"FLEET_NATS_URL": "nats://127.0.0.1:4222"},
        _run=run,
    )

    assert result == {"enabled": True, "emitted": True, "detail": "recorded"}
    argv, kwargs = calls[0]
    assert argv[:7] == [
        "/opt/anvil/bin/anvil-events",
        "emit",
        "serve.up",
        "--host",
        "node-a",
        "--producer",
        "node-a:serves",
    ]
    assert argv[7:9] == ["--correlation", "change-123"]
    assert json.loads(argv[9]) == {"serve": "example", "model": "example-local"}
    assert kwargs["env"]["ANVIL_EVENTS_NATS_URL"] == "nats://127.0.0.1:4222"
    assert kwargs["timeout"] == 5
    assert kwargs["capture_output"] is True
    assert kwargs["text"] is True


def test_enabled_emit_resolves_transport_from_operator_dotenv(tmp_path, monkeypatch):
    from anvil_serving import events

    operator_home = tmp_path / "operator-home"
    operator_home.mkdir()
    (operator_home / ".env").write_text(
        "FLEET_NATS_URL=nats://127.0.0.1:4222\n",
        encoding="utf-8",
    )
    config = operator_home / "events.toml"
    config.write_text(
        """\
[events]
enabled = true
command = "anvil-events"
host = "node-a"
producer = "node-a:serves"
nats_url_env = "FLEET_NATS_URL"
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("ANVIL_SERVING_HOME", str(operator_home))
    monkeypatch.delenv("FLEET_NATS_URL", raising=False)
    calls = []

    result = events.emit_lifecycle_event(
        "serve.up",
        {"serve": "example"},
        config_path=config,
        _run=lambda argv, **kwargs: calls.append((argv, kwargs)) or proc(0),
    )

    assert result["detail"] == "recorded"
    assert calls[0][1]["env"]["ANVIL_EVENTS_NATS_URL"] == "nats://127.0.0.1:4222"


def test_enabled_emit_fails_loudly_when_outbox_cli_fails(tmp_path):
    from anvil_serving import events

    config = tmp_path / "events.toml"
    config.write_text(
        """\
[events]
enabled = true
command = "anvil-events"
host = "node-a"
producer = "node-a:serves"
nats_url_env = "ANVIL_EVENTS_NATS_URL"
""",
        encoding="utf-8",
    )

    def run(_argv, **_kwargs):
        return proc(2, "", "outbox fsync failed")

    with pytest.raises(RuntimeError, match="outbox fsync failed"):
        events.emit_lifecycle_event(
            "serve.down",
            {"serve": "example"},
            config_path=config,
            environ={"ANVIL_EVENTS_NATS_URL": "nats://127.0.0.1:4222"},
            _run=run,
        )


@pytest.mark.parametrize(
    "failure, message",
    [
        (FileNotFoundError("anvil-events"), "could not invoke"),
        (PermissionError("anvil-events"), "could not invoke"),
        (subprocess.TimeoutExpired(["anvil-events", "emit"], 5), "timed out"),
    ],
)
def test_enabled_emit_normalizes_invocation_failures(tmp_path, failure, message):
    from anvil_serving import events

    config = tmp_path / "events.toml"
    config.write_text(
        """\
[events]
enabled = true
command = "anvil-events"
host = "node-a"
producer = "node-a:serves"
nats_url_env = "ANVIL_EVENTS_NATS_URL"
""",
        encoding="utf-8",
    )

    def run(*_args, **_kwargs):
        raise failure

    with pytest.raises(events.LifecycleEventError, match=message):
        events.emit_lifecycle_event(
            "serve.up",
            {"serve": "example"},
            config_path=config,
            environ={"ANVIL_EVENTS_NATS_URL": "nats://127.0.0.1:4222"},
            _run=run,
        )


def test_successful_promotion_emits_once_before_lock_release(monkeypatch):
    from anvil_serving import serves

    order = []

    class Lock:
        def __enter__(self):
            order.append("lock-enter")

        def __exit__(self, *_args):
            order.append("lock-exit")

    monkeypatch.setattr(serves, "_switch_role_lock", lambda _role: Lock())
    monkeypatch.setattr(
        serves,
        "_cmd_promote_unlocked",
        lambda *_args, **_kwargs: order.append("promoted") or 0,
    )
    recorded = []
    monkeypatch.setattr(
        serves,
        "emit_lifecycle_event",
        lambda kind, payload, **kwargs: recorded.append((kind, payload, kwargs))
        or order.append("recorded")
        or {"enabled": True, "emitted": True, "detail": "recorded"},
        raising=False,
    )

    promotions = [{
        "name": "candidate-promotion",
        "target": "candidate-serve",
        "rollback": "rollback-serve",
        "affected_tiers": ["primary-local"],
        "needle_ctx": 131072,
    }]
    managed = [
        {"name": "candidate-serve", "served_name": "candidate-local"},
        {"name": "rollback-serve", "served_name": "rollback-local"},
    ]
    rc = serves.cmd_promote(managed, promotions, "candidate-promotion", "serves.toml")

    assert rc == 0
    assert order == ["lock-enter", "promoted", "recorded", "lock-exit"]
    assert recorded == [(
        "promote.applied",
        {
            "promotion": "candidate-promotion",
            "tier": "primary-local",
            "model": "candidate-local",
            "context": 131072,
            "rollback": "rollback-local",
        },
        {},
    )]


def test_successful_rollback_emits_restored_model(monkeypatch):
    from anvil_serving import serves

    monkeypatch.setattr(serves, "_switch_role_lock", lambda _role: _NullLock())
    monkeypatch.setattr(serves, "_cmd_promote_unlocked", lambda *_args, **_kwargs: 0)
    recorded = []
    monkeypatch.setattr(
        serves,
        "emit_lifecycle_event",
        lambda kind, payload, **kwargs: recorded.append((kind, payload, kwargs)),
    )
    promotions = [{
        "name": "candidate-promotion",
        "target": "candidate-serve",
        "rollback": "rollback-serve",
        "affected_tiers": ["primary-local"],
        "needle_ctx": 131072,
    }]
    managed = [
        {"name": "candidate-serve", "served_name": "candidate-local"},
        {"name": "rollback-serve", "served_name": "rollback-local"},
    ]

    assert serves.cmd_promote(
        managed, promotions, "candidate-promotion", "serves.toml", rollback=True,
    ) == 0
    assert recorded == [(
        "promote.rolled_back",
        {
            "promotion": "candidate-promotion",
            "tier": "primary-local",
            "restored_model": "rollback-local",
        },
        {},
    )]


def test_multi_tier_promotion_emits_one_record_per_declared_tier(monkeypatch):
    from anvil_serving import serves

    monkeypatch.setattr(serves, "_switch_role_lock", lambda _role: _NullLock())
    monkeypatch.setattr(serves, "_cmd_promote_unlocked", lambda *_args, **_kwargs: 0)
    recorded = []
    monkeypatch.setattr(
        serves,
        "emit_lifecycle_event",
        lambda kind, payload, **_kwargs: recorded.append((kind, payload)),
    )
    promotions = [{
        "name": "candidate-promotion",
        "target": "candidate-serve",
        "rollback": "rollback-serve",
        "affected_tiers": ["primary-local", "coding-local"],
        "needle_ctx": 131072,
    }]
    managed = [
        {"name": "candidate-serve", "model": "candidate-local"},
        {"name": "rollback-serve", "model": "rollback-local"},
    ]

    assert serves.cmd_promote(
        managed, promotions, "candidate-promotion", "serves.toml",
    ) == 0
    assert [payload["tier"] for _kind, payload in recorded] == [
        "primary-local", "coding-local",
    ]
    assert all(kind == "promote.applied" for kind, _payload in recorded)


@pytest.mark.parametrize("operation", ["up", "down", "profile"])
def test_non_promotion_lifecycle_reports_journal_failure_without_traceback(
    monkeypatch, capsys, operation,
):
    from anvil_serving import events, serves

    monkeypatch.setattr(
        serves,
        "emit_lifecycle_event",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            events.LifecycleEventError("outbox fsync failed")
        ),
    )
    managed = [{
        "name": "example",
        "container": "example-container",
        "runtime": "docker",
        "port": 9001,
        "model": "example-local",
        "engine": "vllm",
        "health": "/health",
        "up": ["docker", "compose", "up", "-d", "example"],
    }]
    monkeypatch.setattr(serves.reservations, "deny_exclusive_conflict", lambda *_a, **_k: None)
    monkeypatch.setattr(serves.reservations, "deny_over_budget", lambda *_a, **_k: None)
    if operation == "up":
        monkeypatch.setattr(serves, "docker_states", lambda *_a, **_k: {"example-container": "absent"})
        monkeypatch.setattr(serves, "docker_state", lambda *_a, **_k: "absent")
        rc = serves.cmd_up(managed, ["example"], _run=lambda *_a, **_k: proc())
    elif operation == "down":
        monkeypatch.setattr(serves, "docker_state", lambda *_a, **_k: "running")
        monkeypatch.setattr(
            serves.host_ops,
            "container_uses_native_kv_offload",
            lambda *_a, **_k: False,
        )
        rc = serves.cmd_down(managed, ["example"], _run=lambda *_a, **_k: proc())
    else:
        profiles = [{
            "id": "exclusive-example",
            "mode": "dual-gpu-exclusive",
            "exclusive_target": "example",
            "restore_group": "split-default",
            "startup_timeout": 30,
            "poll_interval": 1,
        }]
        monkeypatch.setattr(
            serves,
            "operating_mode_summary",
            lambda *_a, **_k: {"mode": "split", "exclusive_owner": None},
        )
        monkeypatch.setattr(serves, "profile_transition_action", lambda *_a, **_k: "enter")
        monkeypatch.setattr(serves, "cmd_mode", lambda *_a, **_k: 0)
        rc = serves.cmd_profile(managed, profiles, "apply", "exclusive-example")

    assert rc == 1
    err = capsys.readouterr().err
    assert "lifecycle change applied but event was not recorded" in err
    assert "Traceback" not in err


def test_successful_promotion_reports_journal_failure_truthfully(monkeypatch, capsys):
    from anvil_serving import events, serves

    monkeypatch.setattr(serves, "_switch_role_lock", lambda _role: _NullLock())
    monkeypatch.setattr(serves, "_cmd_promote_unlocked", lambda *_args, **_kwargs: 0)
    monkeypatch.setattr(
        serves,
        "emit_lifecycle_event",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            events.LifecycleEventError("outbox fsync failed")
        ),
    )
    promotions = [{
        "name": "candidate-promotion",
        "target": "candidate-serve",
        "rollback": "rollback-serve",
        "affected_tiers": ["primary-local"],
    }]
    managed = [
        {"name": "candidate-serve", "model": "candidate-local"},
        {"name": "rollback-serve", "model": "rollback-local"},
    ]

    assert serves.cmd_promote(
        managed, promotions, "candidate-promotion", "serves.toml",
    ) == 1
    err = capsys.readouterr().err
    assert "promotion applied but lifecycle event was not recorded" in err
    assert "promotion refused" not in err


def test_successful_serves_up_emits_changed_targets_only(monkeypatch):
    from anvil_serving import serves

    managed = [{
        "name": "example",
        "container": "example-container",
        "runtime": "docker",
        "port": 9001,
        "model": "example-local",
        "served_name": "example-local",
        "engine": "vllm",
        "stack": "serving",
        "health": "/health",
        "up": ["docker", "compose", "up", "-d", "example"],
        "gpu_roles": ["compute-a"],
        "residency": "on-demand",
    }]
    monkeypatch.setattr(serves, "docker_states", lambda *_args, **_kwargs: {"example-container": "absent"})
    monkeypatch.setattr(serves, "docker_state", lambda *_args, **_kwargs: "absent")
    monkeypatch.setattr(serves.reservations, "deny_exclusive_conflict", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(serves.reservations, "deny_over_budget", lambda *_args, **_kwargs: None)
    recorded = []
    monkeypatch.setattr(
        serves,
        "emit_lifecycle_event",
        lambda kind, payload, **kwargs: recorded.append((kind, payload, kwargs))
        or {"enabled": True, "emitted": True, "detail": "recorded"},
    )
    run_calls = []

    def run(argv, **_kwargs):
        run_calls.append(argv)
        return proc()

    assert serves.cmd_up(managed, ["example"], _run=run) == 0
    assert run_calls
    assert recorded == [(
        "serve.up",
        {
            "serve": "example",
            "model": "example-local",
            "port": 9001,
            "gpu_roles": ["compute-a"],
            "residency": "on-demand",
        },
        {},
    )]


def test_successful_serves_down_emits_after_running_target_stops(monkeypatch):
    from anvil_serving import serves

    managed = [{
        "name": "example",
        "container": "example-container",
        "runtime": "docker",
        "port": 9001,
        "model": "example-local",
        "engine": "vllm",
    }]
    monkeypatch.setattr(serves, "docker_state", lambda *_args, **_kwargs: "running")
    monkeypatch.setattr(
        serves.host_ops,
        "container_uses_native_kv_offload",
        lambda *_args, **_kwargs: False,
    )
    recorded = []
    monkeypatch.setattr(
        serves,
        "emit_lifecycle_event",
        lambda kind, payload, **kwargs: recorded.append((kind, payload, kwargs))
        or {"enabled": True, "emitted": True, "detail": "recorded"},
    )
    run_calls = []

    def run(argv, **_kwargs):
        run_calls.append(argv)
        return proc()

    assert serves.cmd_down(managed, ["example"], _run=run) == 0
    assert ["docker", "stop", "example-container"] in run_calls
    assert recorded == [(
        "serve.down",
        {"serve": "example", "graceful": True},
        {},
    )]


@pytest.mark.parametrize(
    "keep_container, force_remove, stop_result, expected_graceful",
    [
        (True, False, proc(), True),
        (False, True, proc(), False),
        (False, False, subprocess.TimeoutExpired(["docker", "stop"], 45), False),
    ],
)
def test_serves_down_release_modes_emit_once(
    monkeypatch, keep_container, force_remove, stop_result, expected_graceful,
):
    from anvil_serving import serves

    managed = [{
        "name": "example",
        "container": "example-container",
        "runtime": "docker",
        "port": 9001,
        "model": "example-local",
        "engine": "vllm",
    }]
    states = iter(["running", "exited"])
    monkeypatch.setattr(serves, "docker_state", lambda *_args, **_kwargs: next(states, "exited"))
    monkeypatch.setattr(
        serves.host_ops,
        "container_uses_native_kv_offload",
        lambda *_args, **_kwargs: False,
    )
    monkeypatch.setattr(serves, "_docker_rm_f", lambda *_args, **_kwargs: (True, "removed"))
    recorded = []
    monkeypatch.setattr(
        serves,
        "emit_lifecycle_event",
        lambda kind, payload, **kwargs: recorded.append((kind, payload, kwargs))
        or {"enabled": True, "emitted": True, "detail": "recorded"},
    )

    def run(argv, **_kwargs):
        if argv[:2] == ["docker", "stop"] and isinstance(stop_result, BaseException):
            raise stop_result
        return stop_result if argv[:2] == ["docker", "stop"] else proc()

    assert serves.cmd_down(
        managed,
        ["example"],
        keep_container=keep_container,
        force_remove=force_remove,
        _run=run,
    ) == 0
    assert recorded == [(
        "serve.down",
        {"serve": "example", "graceful": expected_graceful},
        {},
    )]


def test_successful_profile_apply_emits_transition(monkeypatch):
    from anvil_serving import serves

    profiles = [{
        "id": "exclusive-example",
        "mode": "dual-gpu-exclusive",
        "exclusive_target": "example",
        "restore_group": "split-default",
        "startup_timeout": 30,
        "poll_interval": 1,
    }]
    managed = [{
        "name": "example",
        "container": "example-container",
        "runtime": "docker",
        "engine": "vllm",
        "gpu_role": "compute-a",
    }]
    monkeypatch.setattr(
        serves,
        "docker_states",
        lambda *_args, **_kwargs: {"example-container": "absent"},
    )
    monkeypatch.setattr(
        serves,
        "operating_mode_summary",
        lambda *_args, **_kwargs: {"mode": "split", "exclusive_owner": None},
    )
    monkeypatch.setattr(serves, "profile_transition_action", lambda *_args, **_kwargs: "enter")
    monkeypatch.setattr(serves, "cmd_mode", lambda *_args, **_kwargs: 0)
    recorded = []
    monkeypatch.setattr(
        serves,
        "emit_lifecycle_event",
        lambda kind, payload, **kwargs: recorded.append((kind, payload, kwargs))
        or {"enabled": True, "emitted": True, "detail": "recorded"},
    )

    assert serves.cmd_profile(
        managed,
        profiles,
        "apply",
        "exclusive-example",
        confirm=True,
    ) == 0
    assert recorded == [(
        "profile.enter",
        {
            "profile": "exclusive-example",
            "mode": "dual-gpu-exclusive",
            "exclusive_target": "example",
            "restore_group": "split-default",
        },
        {},
    )]


def test_lifecycle_noops_dry_runs_and_failures_emit_nothing(monkeypatch):
    from anvil_serving import serves

    recorded = []
    monkeypatch.setattr(
        serves,
        "emit_lifecycle_event",
        lambda *args, **kwargs: recorded.append((args, kwargs)),
    )

    # Promotion dry-run and failed transaction.
    monkeypatch.setattr(serves, "_cmd_promote_unlocked", lambda *_args, **_kwargs: 0)
    assert serves.cmd_promote([], [], "candidate", "serves.toml", dry_run=True) == 0
    monkeypatch.setattr(serves, "_switch_role_lock", lambda _role: _NullLock())
    monkeypatch.setattr(serves, "_cmd_promote_unlocked", lambda *_args, **_kwargs: 1)
    assert serves.cmd_promote([], [], "candidate", "serves.toml") == 1

    # Already-running script serve executes no lifecycle step.
    managed = [{
        "name": "example",
        "container": "example-container",
        "runtime": "docker",
        "port": 9001,
        "model": "example-local",
        "engine": "vllm",
        "health": "/health",
    }]
    monkeypatch.setattr(serves, "docker_states", lambda *_args, **_kwargs: {"example-container": "running"})
    monkeypatch.setattr(serves, "docker_state", lambda *_args, **_kwargs: "running")
    monkeypatch.setattr(serves.reservations, "deny_exclusive_conflict", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(serves.reservations, "deny_over_budget", lambda *_args, **_kwargs: None)
    assert serves.cmd_up(managed, ["example"], _run=lambda *_args, **_kwargs: proc()) == 0

    # Absent down target and noop profile produce no change event.
    monkeypatch.setattr(serves, "docker_state", lambda *_args, **_kwargs: "absent")
    assert serves.cmd_down(managed, ["example"], _run=lambda *_args, **_kwargs: proc()) == 0
    profiles = [{
        "id": "already-active",
        "mode": "split",
        "exclusive_target": "example",
        "restore_group": "split-default",
        "startup_timeout": 30,
        "poll_interval": 1,
    }]
    monkeypatch.setattr(serves, "profile_transition_action", lambda *_args, **_kwargs: "noop")
    monkeypatch.setattr(
        serves,
        "operating_mode_summary",
        lambda *_args, **_kwargs: {"mode": "split", "exclusive_owner": None},
    )
    assert serves.cmd_profile(managed, profiles, "apply", "already-active") == 0
    assert recorded == []


@pytest.mark.parametrize(
    "body, message",
    [
        (
            """\
[events]
enabled = true
command = "anvil-events"
host = "node-a"
producer = "node-a:serves"
nats_url = "nats://127.0.0.1:4222"
nats_url_env = "ANVIL_EVENTS_NATS_URL"
""",
            "nats_url is forbidden",
        ),
        (
            """\
[events]
enabled = true
command = "anvil-events"
host = "node-a"
producer = "node-a:serves"
nats_url_env = "not-valid"
""",
            "nats_url_env",
        ),
    ],
)
def test_events_config_rejects_inline_transport_and_bad_env_names(tmp_path, body, message):
    from anvil_serving import events

    config = tmp_path / "events.toml"
    config.write_text(body, encoding="utf-8")
    with pytest.raises(events.LifecycleEventError, match=message):
        events.emit_lifecycle_event(
            "serve.up",
            {"serve": "example"},
            config_path=config,
            environ={"ANVIL_EVENTS_NATS_URL": "nats://127.0.0.1:4222"},
            _run=lambda *_args, **_kwargs: proc(),
        )


def test_emit_rejects_kind_outside_frozen_vocabulary(tmp_path):
    from anvil_serving import events

    with pytest.raises(ValueError, match="unsupported lifecycle event kind"):
        events.emit_lifecycle_event(
            "router.updated",
            {},
            config_path=tmp_path / "missing.toml",
            _run=lambda *_args, **_kwargs: proc(),
        )


@pytest.mark.parametrize(
    "body, message",
    [
        (
            """\
[events]
enabled = true
command = "anvil-events"
host = "node-a"
producer = "node-a:serves"
nats_url_env = "MISSING_NATS_URL"
""",
            "is not set",
        ),
        ("[events\nenabled = true\n", "could not load"),
        (
            """\
[events]
enabled = true
host = "node-a"
producer = "node-a:serves"
nats_url_env = "ANVIL_EVENTS_NATS_URL"
""",
            "command must be a non-empty string",
        ),
    ],
)
def test_enabled_operator_config_failures_are_typed(tmp_path, body, message):
    from anvil_serving import events

    config = tmp_path / "events.toml"
    config.write_text(body, encoding="utf-8")
    with pytest.raises(events.LifecycleEventError, match=message):
        events.emit_lifecycle_event(
            "serve.up",
            {"serve": "example"},
            config_path=config,
            environ={},
            _run=lambda *_args, **_kwargs: proc(),
        )


class _NullLock:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False
