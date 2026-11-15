"""Tests for the optional anvil-events lifecycle emission seam."""

import json
import sqlite3
import subprocess
import sys
import threading
import time

import pytest

from tests.conftest import proc


def _direct_promotion_configs(tmp_path, tiers):
    rows = "\n".join(
        """[[router.tiers]]
id = \"{tier}\"
base_url = \"http://127.0.0.1:{port}/v1\"
model = \"candidate-local\"
dialect = \"openai\"
context_limit = 4096
privacy = \"local\"
tool_support = true
auth_env = \"ANVIL_PRIMARY_KEY\"
health_path = \"/health\"
model_identity = true""".format(tier=tier, port=30000 + index)
        for index, tier in enumerate(tiers)
    )
    routes = "\n".join(
        'llm.tier_%d = "%s"' % (index, tier)
        for index, tier in enumerate(tiers)
    )
    contents = "[router]\n%s\n[router.model_routes]\n%s\n" % (rows, routes)
    target = tmp_path / "target-router.toml"
    rollback = tmp_path / "rollback-router.toml"
    target.write_text(contents, encoding="utf-8")
    rollback.write_text(contents, encoding="utf-8")
    return str(target), str(rollback)


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


def test_successful_promotion_emits_once_before_lock_release(tmp_path, monkeypatch):
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

    target_config, rollback_config = _direct_promotion_configs(tmp_path, ["primary-local"])
    promotions = [{
        "name": "candidate-promotion",
        "target": "candidate-serve",
        "rollback": "rollback-serve",
        "affected_tiers": ["primary-local"],
        "needle_ctx": 131072,
        "router_config": target_config,
        "rollback_router_config": rollback_config,
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


def test_successful_rollback_emits_restored_model(tmp_path, monkeypatch):
    from anvil_serving import serves

    monkeypatch.setattr(serves, "_switch_role_lock", lambda _role: _NullLock())
    monkeypatch.setattr(serves, "_cmd_promote_unlocked", lambda *_args, **_kwargs: 0)
    recorded = []
    monkeypatch.setattr(
        serves,
        "emit_lifecycle_event",
        lambda kind, payload, **kwargs: recorded.append((kind, payload, kwargs)),
    )
    target_config, rollback_config = _direct_promotion_configs(tmp_path, ["primary-local"])
    promotions = [{
        "name": "candidate-promotion",
        "target": "candidate-serve",
        "rollback": "rollback-serve",
        "affected_tiers": ["primary-local"],
        "needle_ctx": 131072,
        "router_config": target_config,
        "rollback_router_config": rollback_config,
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


def test_multi_tier_promotion_emits_one_record_per_declared_tier(tmp_path, monkeypatch):
    from anvil_serving import serves

    monkeypatch.setattr(serves, "_switch_role_lock", lambda _role: _NullLock())
    monkeypatch.setattr(serves, "_cmd_promote_unlocked", lambda *_args, **_kwargs: 0)
    recorded = []
    monkeypatch.setattr(
        serves,
        "emit_lifecycle_event",
        lambda kind, payload, **_kwargs: recorded.append((kind, payload)),
    )
    target_config, rollback_config = _direct_promotion_configs(
        tmp_path, ["primary-local", "coding-local"],
    )
    promotions = [{
        "name": "candidate-promotion",
        "target": "candidate-serve",
        "rollback": "rollback-serve",
        "affected_tiers": ["primary-local", "coding-local"],
        "needle_ctx": 131072,
        "router_config": target_config,
        "rollback_router_config": rollback_config,
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


def test_successful_promotion_reports_journal_failure_truthfully(tmp_path, monkeypatch, capsys):
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
    target_config, rollback_config = _direct_promotion_configs(tmp_path, ["primary-local"])
    promotions = [{
        "name": "candidate-promotion",
        "target": "candidate-serve",
        "rollback": "rollback-serve",
        "affected_tiers": ["primary-local"],
        "router_config": target_config,
        "rollback_router_config": rollback_config,
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


class _NullLock:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False
