"""Tests for the optional anvil-events lifecycle emission seam."""

import json
import sqlite3
import subprocess
import sys
import threading
import time
import uuid

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


def test_enabled_emit_records_via_v2_local_cli_and_stdin(tmp_path):
    from anvil_serving import events

    config = tmp_path / "events.toml"
    config.write_text(
        """\
[events]
enabled = true
command = "/opt/anvil/bin/anvil-events"
node = "node-a"
producer = "node-a:serves"
root = %s
""" % json.dumps(str(tmp_path / "events-root")),
        encoding="utf-8",
    )
    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return proc(0, json.dumps({
            "accepted": True,
            "already_recorded": False,
            "event_id": "node-a:serves:000001",
        }))

    result = events.emit_lifecycle_event(
        "serve.up",
        {"serve": "example", "model": "example-local"},
        correlation_id="change-123",
        config_path=config,
        environ={"UNCHANGED": "yes"},
        _run=run,
        _make_uuid=lambda: uuid.UUID(int=1),
    )

    assert result == {
        "enabled": True,
        "emitted": True,
        "detail": "recorded",
        "event_id": "node-a:serves:000001",
        "already_recorded": False,
    }
    argv, kwargs = calls[0]
    assert argv == [
        "/opt/anvil/bin/anvil-events",
        "--root=%s" % (tmp_path / "events-root"),
        "record",
        "serve.up",
        "--node=node-a",
        "--producer=node-a:serves",
        "--operation-key=anvil-serving:serve.up:00000000000000000000000000000001",
        "--correlation=change-123",
    ]
    assert json.loads(kwargs["input"]) == {
        "serve": "example", "model": "example-local",
    }
    assert kwargs["env"] == {"UNCHANGED": "yes"}
    assert kwargs["timeout"] == 35
    assert kwargs["capture_output"] is True
    assert kwargs["text"] is True


def test_enabled_emit_uses_argparse_safe_option_tokens(tmp_path):
    from anvil_serving import events

    config = tmp_path / "events.toml"
    config.write_text(
        """\
[events]
enabled = true
command = "anvil-events"
node = "-node"
producer = "-node:serves"
root = %s
""" % json.dumps(str(tmp_path / "events-root")),
        encoding="utf-8",
    )
    calls = []

    result = events.emit_lifecycle_event(
        "serve.up",
        {"serve": "example"},
        correlation_id="-correlation",
        config_path=config,
        _run=lambda argv, **kwargs: calls.append((argv, kwargs)) or proc(
            0,
            '{"accepted":true,"already_recorded":false,"event_id":"e-1"}',
        ),
    )

    assert result["emitted"] is True
    argv = calls[0][0]
    assert "--node=-node" in argv
    assert "--producer=-node:serves" in argv
    assert "--correlation=-correlation" in argv
    assert "-node" not in argv
    assert "-correlation" not in argv


def test_record_timeout_covers_real_sqlite_writer_contention(tmp_path):
    from anvil_serving import events

    config = tmp_path / "events.toml"
    config.write_text(
        """\
[events]
enabled = true
command = "anvil-events"
node = "node-a"
producer = "node-a:serves"
root = %s
""" % json.dumps(str(tmp_path / "events-root")),
        encoding="utf-8",
    )
    database = tmp_path / "writer-lock.db"
    holder = sqlite3.connect(database, isolation_level=None, check_same_thread=False)
    holder.execute("PRAGMA journal_mode = WAL")
    holder.execute("CREATE TABLE facts(value TEXT)")
    holder.execute("BEGIN IMMEDIATE")

    def release_lock():
        time.sleep(0.2)
        holder.commit()

    release = threading.Thread(target=release_lock, daemon=True)
    release.start()
    helper = """
import json
import sqlite3
import sys

connection = sqlite3.connect(sys.argv[1], timeout=30, isolation_level=None)
connection.execute("PRAGMA busy_timeout = 30000")
connection.execute("BEGIN IMMEDIATE")
connection.commit()
connection.close()
print(json.dumps({"accepted": True, "already_recorded": False, "event_id": "e-1"}))
"""

    def run(_argv, **kwargs):
        assert kwargs["timeout"] == 35
        return subprocess.run(
            [sys.executable, "-c", helper, str(database)],
            **kwargs,
        )

    try:
        result = events.emit_lifecycle_event(
            "serve.up",
            {"serve": "example"},
            config_path=config,
            _run=run,
        )
    finally:
        release.join(timeout=2)
        holder.close()

    assert result["emitted"] is True


def test_enabled_emit_does_not_require_broker_configuration(tmp_path):
    from anvil_serving import events

    config = tmp_path / "events.toml"
    config.write_text(
        """\
[events]
enabled = true
command = "anvil-events"
node = "node-a"
producer = "node-a:serves"
root = %s
""" % json.dumps(str(tmp_path / "events-root")),
        encoding="utf-8",
    )
    calls = []

    result = events.emit_lifecycle_event(
        "serve.up",
        {"serve": "example"},
        config_path=config,
        environ={},
        _run=lambda argv, **kwargs: calls.append((argv, kwargs)) or proc(
            0,
            '{"accepted":true,"already_recorded":false,"event_id":"e-1"}',
        ),
    )

    assert result["detail"] == "recorded"
    assert calls[0][1]["env"] == {}


def test_enabled_emit_fails_loudly_when_outbox_cli_fails(tmp_path):
    from anvil_serving import events

    config = tmp_path / "events.toml"
    config.write_text(
        """\
[events]
enabled = true
command = "anvil-events"
node = "node-a"
producer = "node-a:serves"
root = %s
""" % json.dumps(str(tmp_path / "events-root")),
        encoding="utf-8",
    )

    def run(_argv, **_kwargs):
        return proc(2, "", "outbox fsync failed")

    with pytest.raises(RuntimeError, match="outbox fsync failed"):
        events.emit_lifecycle_event(
            "serve.down",
            {"serve": "example"},
            config_path=config,
            environ={},
            _run=run,
        )


@pytest.mark.parametrize(
    "failure, message",
    [
        (FileNotFoundError("anvil-events"), "could not invoke"),
        (PermissionError("anvil-events"), "could not invoke"),
        (subprocess.TimeoutExpired(["anvil-events", "record"], 35), "timed out"),
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
node = "node-a"
producer = "node-a:serves"
root = %s
""" % json.dumps(str(tmp_path / "events-root")),
        encoding="utf-8",
    )

    def run(*_args, **_kwargs):
        raise failure

    with pytest.raises(events.LifecycleEventError, match=message):
        events.emit_lifecycle_event(
            "serve.up",
            {"serve": "example"},
            config_path=config,
            environ={},
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

        def run(argv, **_kwargs):
            if "config" in argv and "--format" in argv:
                return proc(0, json.dumps({
                    "services": {"example": {"networks": {"default": None}}},
                    "networks": {"default": {"internal": True}},
                }))
            return proc()

        rc = serves.cmd_up(managed, ["example"], _run=run)
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
        if "config" in argv and "--format" in argv:
            return proc(0, json.dumps({
                "services": {"example": {"networks": {"default": None}}},
                "networks": {"default": {"internal": True}},
            }))
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
        "gpu_inference": False,
        "network_egress": "allow",
        "network_egress_role": "capability-gateway",
        "network_egress_reason": "no-op lifecycle event fixture",
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
            "retired v1 fields",
        ),
        (
            """\
[events]
enabled = true
command = "anvil-events"
node = "node-a"
producer = "node-b:serves"
root = "C:/events"
""",
            "must belong",
        ),
    ],
)
def test_events_config_rejects_v1_fields_and_foreign_producer(tmp_path, body, message):
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
node = "node-a"
producer = "node-a:serves"
root = "relative/events"
""",
            "root must be absolute",
        ),
        ("[events\nenabled = true\n", "could not load"),
        (
            """\
[events]
enabled = true
node = "node-a"
producer = "node-a:serves"
root = "C:/events"
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


@pytest.mark.parametrize(
    "stdout",
    [
        "not-json",
        "{}",
        '{"accepted":true,"already_recorded":"no","event_id":"e-1"}',
        '{"accepted":true,"already_recorded":false,"event_id":""}',
    ],
)
def test_enabled_emit_rejects_invalid_acceptance_evidence(tmp_path, stdout):
    from anvil_serving import events

    config = tmp_path / "events.toml"
    config.write_text(
        """\
[events]
enabled = true
command = "anvil-events"
node = "node-a"
producer = "node-a:serves"
root = %s
""" % json.dumps(str(tmp_path / "events-root")),
        encoding="utf-8",
    )
    with pytest.raises(
        events.LifecycleEventError, match="invalid acceptance evidence",
    ):
        events.emit_lifecycle_event(
            "serve.up",
            {"serve": "example"},
            config_path=config,
            environ={},
            _run=lambda *_args, **_kwargs: proc(0, stdout),
        )


def test_enabled_emit_rejects_non_json_payload_without_spawning(tmp_path):
    from anvil_serving import events

    config = tmp_path / "events.toml"
    config.write_text(
        """\
[events]
enabled = true
command = "anvil-events"
node = "node-a"
producer = "node-a:serves"
root = %s
""" % json.dumps(str(tmp_path / "events-root")),
        encoding="utf-8",
    )
    calls = []
    with pytest.raises(
        events.LifecycleEventError, match="only JSON values",
    ):
        events.emit_lifecycle_event(
            "serve.up",
            {"value": float("nan")},
            config_path=config,
            _run=lambda *_args, **_kwargs: calls.append(True),
        )
    assert calls == []


class _NullLock:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False
