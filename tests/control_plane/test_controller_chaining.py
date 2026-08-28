"""Regression coverage for idempotent controller-to-controller mutations."""

from __future__ import annotations

import re
import threading

import pytest

from anvil_serving import mcp
from anvil_serving.control_plane.controller.operation_context import (
    controller_operation_context,
    current_controller_operation_context,
)
from tests.test_controller import CONTEXT, _request, running_controller


_CHILD_KEY_RE = re.compile(r"^anvil-child:[0-9a-f]{64}$")


def _tool_catalog(*names: str):
    return [
        {
            "name": name,
            "description": "Exercise one bounded chained operation.",
            "inputSchema": {
                "type": "object",
                "additionalProperties": False,
                "maxProperties": 4,
                "properties": {
                    "confirm": {"type": "boolean"},
                    "dry_run": {"type": "boolean"},
                    "step": {"type": "integer"},
                    "value": {"type": "integer"},
                },
                "required": [],
            },
        }
        for name in names
    ]


def _remote_request(name: str, arguments: dict, *, request_id: str | None = None) -> dict:
    return {
        "jsonrpc": "2.0",
        "id": request_id or name,
        "method": "tools/call",
        "params": {
            "name": name,
            "arguments": arguments,
            "_meta": {
                "io.modelcontextprotocol/protocolVersion": mcp.PROTOCOL_VERSION,
                "io.modelcontextprotocol/clientCapabilities": {},
                "io.modelcontextprotocol/clientInfo": {
                    "name": "controller-chaining-test",
                    "version": "1.0",
                },
            },
        },
    }


def _outer_request(name: str, arguments: dict) -> dict:
    request = _remote_request(name, arguments, request_id="outer")
    request["params"]["context"] = dict(CONTEXT)
    return request


def _structured(response: dict) -> dict:
    return response["result"]["structuredContent"]


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": "Bearer " + token, "x-api-key": token}


def test_distinct_child_mutations_get_stable_derived_keys_and_replay(tmp_path):
    child_token = "child-controller-secret"
    child_calls = []
    parent_calls = []

    def child_call(name, arguments):
        operation = current_controller_operation_context()
        assert operation is not None
        child_calls.append(
            {
                "name": name,
                "arguments": arguments,
                "key": operation.idempotency_key,
                "execution": dict(operation.execution),
            }
        )
        return {"ok": True, "data": {"call": len(child_calls), "step": arguments["step"]}}

    with running_controller(
        list_tools_func=lambda: _tool_catalog("child_mutate"),
        call_tool_func=child_call,
        env={"ANVIL_CONTROLLER_TOKEN": child_token},
        allow_unauthenticated_loopback=False,
        idempotency_db_path=str(tmp_path / "child.sqlite3"),
    ) as (child_host, child_port):
        child_url = f"http://{child_host}:{child_port}"

        def parent_call(_name, _arguments):
            parent_calls.append(True)
            first = mcp.remote_controller_request(
                child_url,
                _remote_request(
                    "child_mutate",
                    {"step": 1, "confirm": True, "dry_run": False},
                    request_id="child-first",
                ),
                child_token,
            )
            identical = mcp.remote_controller_request(
                child_url,
                _remote_request(
                    "child-mutate",
                    {"dry_run": False, "confirm": True, "step": 1},
                    request_id="child-retry",
                ),
                child_token,
            )
            second = mcp.remote_controller_request(
                child_url,
                _remote_request(
                    "child_mutate",
                    {"step": 2, "confirm": True, "dry_run": False},
                    request_id="child-second",
                ),
                child_token,
            )
            return {
                "ok": True,
                "data": {
                    "identical_replayed": _structured(identical) == _structured(first),
                    "first": _structured(first)["data"],
                    "second": _structured(second)["data"],
                },
            }

        outer = _outer_request("proxy_mutation", {"confirm": True, "dry_run": False, "step": 1})
        headers = {"X-Anvil-Idempotency-Key": "outer-fanout-1"}
        with running_controller(
            list_tools_func=lambda: _tool_catalog("proxy_mutation"),
            call_tool_func=parent_call,
            idempotency_db_path=str(tmp_path / "parent.sqlite3"),
        ) as (parent_host, parent_port):
            status, _, first_outer, _ = _request(
                parent_host, parent_port, "POST", "/mcp", body=outer, headers=headers
            )
            assert status == 200
            status, _, replayed_outer, _ = _request(
                parent_host, parent_port, "POST", "/mcp", body=outer, headers=headers
            )
            assert status == 200

        assert replayed_outer == first_outer
        assert _structured(first_outer)["data"] == {
            "identical_replayed": True,
            "first": {"call": 1, "step": 1},
            "second": {"call": 2, "step": 2},
        }
        assert parent_calls == [True]
        assert len(child_calls) == 2
        assert child_calls[0]["key"] != child_calls[1]["key"]
        assert all(_CHILD_KEY_RE.fullmatch(call["key"]) for call in child_calls)
        assert all(call["execution"] == CONTEXT for call in child_calls)

        conflicting = _remote_request(
            "child_mutate",
            {"step": 999, "confirm": True, "dry_run": False},
            request_id="forced-conflict",
        )
        conflicting["params"]["context"] = dict(CONTEXT)
        status, _, conflict, _ = _request(
            child_host,
            child_port,
            "POST",
            "/mcp",
            body=conflicting,
            headers={**_auth(child_token), "X-Anvil-Idempotency-Key": child_calls[0]["key"]},
        )
        assert status == 200
        assert conflict["error"]["data"]["code"] == "idempotency_key_conflict"
        assert len(child_calls) == 2


def test_keyed_read_outer_call_cannot_authorize_child_mutation(tmp_path):
    child_token = "child-controller-secret"
    child_calls = []

    with running_controller(
        list_tools_func=lambda: _tool_catalog("child_mutate"),
        call_tool_func=lambda *args: child_calls.append(args) or {"ok": True},
        env={"ANVIL_CONTROLLER_TOKEN": child_token},
        allow_unauthenticated_loopback=False,
        idempotency_db_path=str(tmp_path / "child.sqlite3"),
    ) as (child_host, child_port):
        child_url = f"http://{child_host}:{child_port}"

        def read_parent(_name, _arguments):
            child = mcp.remote_controller_request(
                child_url,
                _remote_request("child_mutate", {"confirm": True, "dry_run": False, "step": 1}),
                child_token,
            )
            return {"ok": True, "data": {"child": child}}

        with running_controller(
            list_tools_func=lambda: _tool_catalog("read_proxy"),
            call_tool_func=read_parent,
            idempotency_db_path=str(tmp_path / "parent.sqlite3"),
        ) as (parent_host, parent_port):
            status, _, outer, _ = _request(
                parent_host,
                parent_port,
                "POST",
                "/mcp",
                body=_outer_request("read_proxy", {}),
                headers={"X-Anvil-Idempotency-Key": "keyed-read-1"},
            )

    assert status == 200
    child_response = _structured(outer)["data"]["child"]
    assert child_response["error"]["data"]["code"] == "idempotency_key_required"
    assert child_calls == []


def test_three_hop_chain_derives_each_identity_and_top_level_conflicts(tmp_path):
    middle_token = "middle-controller-secret"
    leaf_token = "leaf-controller-secret"
    middle_calls = []
    leaf_calls = []
    root_calls = []

    def leaf_call(name, arguments):
        operation = current_controller_operation_context()
        assert operation is not None
        leaf_calls.append((name, arguments, operation))
        return {"ok": True, "data": {"leaf_call": len(leaf_calls)}}

    with running_controller(
        list_tools_func=lambda: _tool_catalog("leaf_mutate"),
        call_tool_func=leaf_call,
        env={"ANVIL_CONTROLLER_TOKEN": leaf_token},
        allow_unauthenticated_loopback=False,
        idempotency_db_path=str(tmp_path / "leaf.sqlite3"),
    ) as (leaf_host, leaf_port):
        leaf_url = f"http://{leaf_host}:{leaf_port}"

        def middle_call(name, arguments):
            operation = current_controller_operation_context()
            assert operation is not None
            middle_calls.append((name, arguments, operation))
            leaf = mcp.remote_controller_request(
                leaf_url,
                _remote_request(
                    "leaf_mutate",
                    {"confirm": True, "dry_run": False, "step": arguments["step"]},
                ),
                leaf_token,
            )
            return {"ok": True, "data": {"leaf": _structured(leaf)["data"]}}

        with running_controller(
            list_tools_func=lambda: _tool_catalog("middle_mutate"),
            call_tool_func=middle_call,
            env={"ANVIL_CONTROLLER_TOKEN": middle_token},
            allow_unauthenticated_loopback=False,
            idempotency_db_path=str(tmp_path / "middle.sqlite3"),
        ) as (middle_host, middle_port):
            middle_url = f"http://{middle_host}:{middle_port}"

            def root_call(_name, arguments):
                root_calls.append(arguments)
                middle = mcp.remote_controller_request(
                    middle_url,
                    _remote_request(
                        "middle_mutate",
                        {
                            "confirm": True,
                            "dry_run": False,
                            "step": arguments["step"],
                        },
                    ),
                    middle_token,
                )
                return {"ok": True, "data": {"middle": _structured(middle)["data"]}}

            headers = {"X-Anvil-Idempotency-Key": "root-chain-1"}
            first_request = _outer_request(
                "root_mutate", {"confirm": True, "dry_run": False, "step": 1}
            )
            with running_controller(
                list_tools_func=lambda: _tool_catalog("root_mutate"),
                call_tool_func=root_call,
                idempotency_db_path=str(tmp_path / "root.sqlite3"),
            ) as (root_host, root_port):
                status, _, first, _ = _request(
                    root_host,
                    root_port,
                    "POST",
                    "/mcp",
                    body=first_request,
                    headers=headers,
                )
                assert status == 200
                status, _, replayed, _ = _request(
                    root_host,
                    root_port,
                    "POST",
                    "/mcp",
                    body=first_request,
                    headers=headers,
                )
                assert status == 200
                changed_request = _outer_request(
                    "root_mutate", {"confirm": True, "dry_run": False, "step": 2}
                )
                status, _, conflict, _ = _request(
                    root_host,
                    root_port,
                    "POST",
                    "/mcp",
                    body=changed_request,
                    headers=headers,
                )

    assert status == 200
    assert first == replayed
    assert _structured(first)["data"] == {"middle": {"leaf": {"leaf_call": 1}}}
    assert conflict["error"]["data"]["code"] == "idempotency_key_conflict"
    assert len(root_calls) == len(middle_calls) == len(leaf_calls) == 1
    middle_operation = middle_calls[0][2]
    leaf_operation = leaf_calls[0][2]
    assert _CHILD_KEY_RE.fullmatch(middle_operation.idempotency_key)
    assert _CHILD_KEY_RE.fullmatch(leaf_operation.idempotency_key)
    assert middle_operation.idempotency_key != leaf_operation.idempotency_key
    assert dict(middle_operation.execution) == CONTEXT
    assert dict(leaf_operation.execution) == CONTEXT


def test_operation_context_resets_after_exception_and_is_thread_local():
    assert current_controller_operation_context() is None
    with pytest.raises(RuntimeError, match="boom"):
        with controller_operation_context("outer-exception", CONTEXT):
            assert current_controller_operation_context() is not None
            raise RuntimeError("boom")
    assert current_controller_operation_context() is None

    barrier = threading.Barrier(2)
    seen = {}

    def worker(name: str):
        execution = {**CONTEXT, "execution_host": "host-" + name}
        with controller_operation_context("outer-" + name, execution):
            barrier.wait(timeout=5)
            operation = current_controller_operation_context()
            assert operation is not None
            seen[name] = (operation.idempotency_key, dict(operation.execution))
        assert current_controller_operation_context() is None

    threads = [threading.Thread(target=worker, args=(name,)) for name in ("one", "two")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)
        assert not thread.is_alive()

    assert seen == {
        "one": ("outer-one", {**CONTEXT, "execution_host": "host-one"}),
        "two": ("outer-two", {**CONTEXT, "execution_host": "host-two"}),
    }
    assert current_controller_operation_context() is None
