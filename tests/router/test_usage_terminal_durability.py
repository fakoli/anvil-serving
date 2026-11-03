"""Real front-door accounting failures retain scoped route-drain protection."""
from pathlib import Path

from anvil_serving.router.config import load
from anvil_serving.router.keys import KeyStore
from anvil_serving.router.serve import RoutingBackend
from anvil_serving.router.usage_store import UsageError, UsageStore
from tests.router.helpers import StaticBackend
from tests.router.test_usage_dispatch import post, server
from tests.router.test_usage_lifecycle import rows, store as store


def _routing():
    config = load(Path(__file__).resolve().parents[2] / "configs" / "example.toml")
    return RoutingBackend(config, {
        "primary-local": StaticBackend(["primary result"]),
        "omni-local": StaticBackend(["voice result"]),
    })


def test_delivered_stream_with_terminal_write_failure_retains_targeted_drain(store, monkeypatch):
    """Delivery releases transport capacity, but not a route lease without a durable terminal."""
    usage, *_ = store
    routing = _routing()
    durable_finalize = usage.finalize
    failed_terminals = []

    def fail_first_terminal_write(terminal):
        failed_terminals.append(terminal)
        if len(failed_terminals) == 1:
            raise UsageError("accounting_unavailable")
        return durable_finalize(terminal)

    monkeypatch.setattr(usage, "finalize", fail_first_terminal_write)
    with server(routing, store) as connection:
        status, wire = post(connection, body={
            "model": "llm.primary",
            "messages": [{"role": "user", "content": "hello"}],
            "stream": True,
        })
        assert status == 200
        assert b"primary result" in wire and b"[DONE]" in wire

        hold = routing.begin_route_update_hold(
            update_id="a" * 32, expected_revision=0, tier_ids=("primary-local",),
        )
        assert routing.wait_route_update_drain(hold, 0) == {"drained": False, "pending": 1}

        # The completed primary response returned its transport/admission
        # capacity.  A different tier remains available while only the failed
        # terminal's selected tier is protected from a drain/unload.
        status, wire = post(connection, body={
            "model": "llm.voice",
            "messages": [{"role": "user", "content": "hello"}],
            "stream": False,
        })
        assert status == 200 and b"voice result" in wire

    assert len(failed_terminals) == 2
    assert len(rows(usage, "usage_details")) == 1
    assert usage.health("domain_fixture")["unresolved_requests"] == 1
    assert routing._route_terminals == {}
    assert routing.wait_route_update_drain(hold, 0) == {"drained": False, "pending": 1}
    assert durable_finalize(failed_terminals[0]) == "committed"
    assert routing.wait_route_update_drain(hold, 0) == {"drained": True, "pending": 0}


def test_durable_start_with_lost_reply_still_blocks_selected_tier_drain(store, monkeypatch):
    usage, _owner, _run, scope = store
    routing = _routing()
    durable_start = usage.start
    starts = []

    def commit_then_lose_reply(start, **kwargs):
        result = durable_start(start, **kwargs)
        starts.append(start)
        if len(starts) == 1:
            raise UsageError("accounting_unavailable")
        return result

    monkeypatch.setattr(usage, "start", commit_then_lose_reply)
    with server(routing, store) as connection:
        status, _wire = post(connection, body={
            "model": "llm.primary",
            "messages": [{"role": "user", "content": "hello"}],
        })
        assert status == 503
        hold = routing.begin_route_update_hold(
            update_id="b" * 32, expected_revision=0, tier_ids=("primary-local",),
        )
        assert routing.wait_route_update_drain(hold, 0) == {"drained": False, "pending": 1}

    assert len(starts) == len(rows(usage, "usage_starts")) == 1
    assert routing._route_terminals == {}

    # A fresh router/accounting pair has no transient terminal counter, but
    # must still retain the selected tier from the durable unresolved start.
    recovered_usage = UsageStore(KeyStore(usage.key_store.path))
    recovered_routing = _routing()
    recovered_routing.set_route_drain_usage(recovered_usage, lambda: scope)
    recovered_hold = recovered_routing.begin_route_update_hold(
        update_id="d" * 32, expected_revision=0, tier_ids=("primary-local",),
    )
    assert recovered_routing.wait_route_update_drain(recovered_hold, 0) == {"drained": False, "pending": 1}


def test_route_drain_read_failure_is_fail_closed(store):
    _usage, _owner, _run, scope = store
    routing = _routing()

    class UnavailableLedger:
        def unresolved_route_starts(self, tier_ids, *, authority_scope):
            raise UsageError("accounting_unavailable")

    routing.set_route_drain_usage(UnavailableLedger(), lambda: scope)
    hold = routing.begin_route_update_hold(
        update_id="c" * 32, expected_revision=0, tier_ids=("primary-local",),
    )
    assert routing.wait_route_update_drain(hold, 0) == {"drained": False, "pending": 1}
