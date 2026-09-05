"""Hermetic tests for typed local and controller transport adapters."""

from __future__ import annotations

import io
import base64
import hashlib
import json
import os
import socket
import subprocess
import urllib.error

import pytest

from anvil_serving import transports


TOKEN = "controller-secret-token"


def _known_host(tmp_path, host="100.64.0.10", port=22):
    key = b"synthetic ssh host key"
    encoded = base64.b64encode(key).decode("ascii")
    fingerprint = "SHA256:" + base64.b64encode(hashlib.sha256(key).digest()).decode("ascii").rstrip(
        "="
    )
    target = host if port == 22 else f"[{host}]:{port}"
    path = tmp_path / "known_hosts"
    path.write_text(f"{target} ssh-ed25519 {encoded}\n", encoding="utf-8")
    return str(path), fingerprint


def _identity(tmp_path):
    path = tmp_path / "id_recovery"
    path.write_bytes(b"synthetic private key")
    return str(path)


class Response:
    def __init__(self, body: bytes):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def read(self, amount=None):
        return self.body if amount is None else self.body[:amount]


def test_local_transport_only_calls_declared_typed_handler():
    seen = []
    transport = transports.LocalTransport(
        {"router-status": lambda args: seen.append(args) or {"status": "ok"}}
    )

    result = transport.execute(transports.Operation("router-status", {"verbose": True}))

    assert result.transport == "local"
    assert result.data == {"status": "ok"}
    assert seen == [{"verbose": True}]
    with pytest.raises(transports.TransportError) as exc:
        transport.execute(transports.Operation("router-restart", {}))
    assert exc.value.execution_state == "not_started"


@pytest.mark.parametrize(
    "key",
    [
        "command",
        "argv",
        "shell",
        "token",
        "api_key",
        "private_key",
        "accessToken",
        "privateKey",
        "clientSecret",
        "authorizationToken",
        "ACCESS-TOKEN",
        "private.key",
        "client_secret",
        "authorization-token",
    ],
)
def test_operation_rejects_raw_command_and_credential_payloads(key):
    with pytest.raises(ValueError, match="command or credential"):
        transports.Operation("router-status", {key: "not-allowed"})


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_operation_rejects_non_finite_numbers(value):
    with pytest.raises(ValueError, match="finite"):
        transports.Operation("router-status", {"value": value})


def test_controller_transport_uses_env_token_and_redacts_response():
    seen = {}

    def opener(request, timeout):
        seen["headers"] = dict(request.header_items())
        seen["body"] = json.loads(request.data.decode("utf-8"))
        seen["timeout"] = timeout
        return Response(
            json.dumps(
                {
                    "ok": True,
                    "data": {
                        "diagnostic": TOKEN,
                        "nested": {
                            "accessToken": "access-value",
                            "privateKey": "private-value",
                            "clientSecret": "client-value",
                            "authorizationToken": "authorization-value",
                        },
                    },
                }
            ).encode("utf-8")
        )

    transport = transports.ControllerTransport(
        "http://100.64.0.10:8765",
        auth_env="ANVIL_CONTROLLER_TOKEN",
        allowed_operations=["router-status"],
        environment={"ANVIL_CONTROLLER_TOKEN": TOKEN},
        timeout_seconds=3,
        opener=opener,
    )
    result = transport.execute(transports.Operation("router-status", {"detail": "short"}))

    assert result.transport == "controller"
    assert result.data["data"]["diagnostic"] == "<redacted>"
    assert set(result.data["data"]["nested"].values()) == {"<redacted>"}
    assert seen["body"] == {"name": "router_status", "arguments": {"detail": "short"}}
    assert seen["headers"]["Authorization"] == "Bearer " + TOKEN
    assert seen["timeout"] == 3
    assert TOKEN not in json.dumps(result.as_dict())


def test_controller_transport_uses_typed_tool_alias_without_changing_allowlist_name():
    seen = {}

    def opener(request, timeout):
        seen["payload"] = json.loads(request.data)
        return Response(b'{"ok":true,"data":{}}')

    transport = transports.ControllerTransport(
        "http://100.64.0.10:8765",
        auth_env="ANVIL_CONTROLLER_TOKEN",
        allowed_operations=["router-up"],
        environment={"ANVIL_CONTROLLER_TOKEN": TOKEN},
        opener=opener,
    )

    result = transport.execute(
        transports.Operation(
            "router-up",
            {"action": "up", "dry_run": True},
            tool_name="router_manage",
        )
    )

    assert result.operation == "router-up"
    assert seen["payload"] == {
        "name": "router_manage",
        "arguments": {"action": "up", "dry_run": True},
    }


@pytest.mark.parametrize(
    "endpoint",
    [
        "https://8.8.8.8:8765",
        "https://172.32.0.1:8765",
        "https://100.128.0.1:8765",
        "https://[2001:4860:4860::8888]:8765",
        "https://controller.example:8765",
    ],
)
def test_controller_transport_rejects_unsafe_endpoint_before_token_or_open(monkeypatch, endpoint):
    opened = False

    def unexpected_dns(*args, **kwargs):
        raise AssertionError("controller endpoint validation must not resolve hostnames")

    monkeypatch.setattr(transports.socket, "getaddrinfo", unexpected_dns)

    class NoTokenEnvironment(dict):
        def get(self, key, default=None):
            raise AssertionError("controller token must not be read for an unsafe endpoint")

    def opener(request, timeout):
        nonlocal opened
        opened = True
        raise AssertionError("unsafe endpoint must not be opened")

    transport = transports.ControllerTransport(
        endpoint,
        auth_env="ANVIL_CONTROLLER_TOKEN",
        allowed_operations=["router-status"],
        environment=NoTokenEnvironment(),
        opener=opener,
    )

    with pytest.raises(transports.TransportError) as exc:
        transport.execute(transports.Operation("router-status", {}))

    assert exc.value.code == "unsafe_controller_endpoint"
    assert exc.value.execution_state == "not_started"
    assert opened is False


def test_local_transport_rejects_hyphen_underscore_handler_collision():
    with pytest.raises(ValueError, match="normalization collisions"):
        transports.LocalTransport(
            {
                "router-status": lambda arguments: {"status": "hyphen"},
                "router_status": lambda arguments: {"status": "underscore"},
            }
        )


def test_controller_transport_rejects_hyphen_underscore_operation_collision():
    with pytest.raises(ValueError, match="normalization collisions"):
        transports.ControllerTransport(
            "http://127.0.0.1:8765",
            auth_env="ANVIL_CONTROLLER_TOKEN",
            allowed_operations=["router-status", "router_status"],
            environment={"ANVIL_CONTROLLER_TOKEN": TOKEN},
        )


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://127.0.0.1:8765",
        "http://10.0.0.1:8765",
        "http://172.16.0.1:8765",
        "http://192.168.0.1:8765",
        "http://100.64.0.1:8765",
        "http://[::1]:8765",
        "http://[fd00::1]:8765",
    ],
)
def test_controller_transport_allows_safe_literal_endpoint_families(endpoint):
    transport = transports.ControllerTransport(
        endpoint,
        auth_env="ANVIL_CONTROLLER_TOKEN",
        allowed_operations=["router-status"],
        environment={"ANVIL_CONTROLLER_TOKEN": TOKEN},
        opener=lambda request, timeout: Response(b'{"ok":true}'),
    )

    result = transport.execute(transports.Operation("router-status", {}))

    assert result.transport == "controller"


def test_controller_transport_bounds_response_and_classifies_partial_result():
    transport = transports.ControllerTransport(
        "http://100.64.0.10:8765",
        auth_env="ANVIL_CONTROLLER_TOKEN",
        allowed_operations=["router-status"],
        environment={"ANVIL_CONTROLLER_TOKEN": TOKEN},
        max_response_bytes=8,
        opener=lambda request, timeout: Response(b"x" * 16),
    )

    with pytest.raises(transports.TransportError) as exc:
        transport.execute(transports.Operation("router-status", {}))

    assert exc.value.code == "response_too_large"
    assert exc.value.execution_state == "partial_result"
    assert exc.value.may_have_executed is True


def test_controller_transport_accepts_configured_response_above_default_bound():
    body = json.dumps({"ok": True, "data": {"content": "x" * 71_000}}).encode("utf-8")
    assert transports.DEFAULT_MAX_RESPONSE_BYTES < len(body) <= transports.MAX_RESPONSE_BYTES
    transport = transports.ControllerTransport(
        "http://100.64.0.10:8765",
        auth_env="ANVIL_CONTROLLER_TOKEN",
        allowed_operations=["host-config-export"],
        environment={"ANVIL_CONTROLLER_TOKEN": TOKEN},
        max_response_bytes=transports.MAX_RESPONSE_BYTES,
        opener=lambda request, timeout: Response(body),
    )

    result = transport.execute(transports.Operation("host-config-export", {}))

    assert result.data["data"]["content"] == "x" * 71_000
    assert result.response_bytes == len(body)


@pytest.mark.parametrize("method", ["constructor", "execute", "operation_status"])
@pytest.mark.parametrize("limit", [1_048_577, 8_388_608])
@pytest.mark.parametrize("overflow", [False, True])
def test_controller_explicit_large_response_limit(method, limit, overflow):
    status = method == "operation_status"
    envelope = {"key": "probe-1", "status": "succeeded", "result": {}} if status else {
        "ok": True, "data": {}
    }
    # Whitespace is valid JSON and lets the fixture set an exact wire-byte bound.
    raw = json.dumps(envelope).encode("ascii")
    raw += b" " * (limit + int(overflow) - len(raw))
    reads = []

    class MeasuredResponse(Response):
        def read(self, amount=None):
            reads.append(amount)
            return super().read(amount)

    transport = transports.ControllerTransport(
        "http://127.0.0.1:8765", auth_env="TOKEN",
        allowed_operations=["router-status"], environment={"TOKEN": TOKEN},
        opener=lambda request, timeout: MeasuredResponse(raw),
        **({"max_response_bytes": limit} if method == "constructor" else {}),
    )

    def run():
        if status:
            return transport.operation_status("probe-1", max_response_bytes=limit)
        return transport.execute(
            transports.Operation("router-status", {}),
            **({"max_response_bytes": limit} if method == "execute" else {}),
        )

    if overflow:
        with pytest.raises(transports.TransportError) as exc:
            run()
        assert exc.value.code == "response_too_large"
        assert exc.value.execution_state == "partial_result"
        assert exc.value.details["max_response_bytes"] == limit
    else:
        result = run()
        assert result.data == envelope
        assert result.response_bytes == limit
    assert reads == [limit + 1]


@pytest.mark.parametrize("method", ["constructor", "execute", "operation_status"])
@pytest.mark.parametrize("limit", [8_388_609, True, False, 1.5, 0, -1])
def test_controller_rejects_invalid_explicit_bounds_without_http(method, limit):
    def run():
        transport = transports.ControllerTransport(
            "http://127.0.0.1:8765", auth_env="TOKEN",
            allowed_operations=["router-status"], environment={"TOKEN": TOKEN},
            opener=lambda *args, **kwargs: pytest.fail("invalid bound opened HTTP"),
            **({"max_response_bytes": limit} if method == "constructor" else {}),
        )
        if method == "execute":
            transport.execute(transports.Operation("router-status", {}), max_response_bytes=limit)
        elif method == "operation_status":
            transport.operation_status("probe-1", max_response_bytes=limit)

    with pytest.raises(ValueError, match="max_response_bytes"):
        run()


def test_transport_defaults_and_non_controller_caps_remain_unchanged(tmp_path):
    from anvil_serving.observability.workloads import MAX_JSON_BYTES

    assert transports.DEFAULT_MAX_RESPONSE_BYTES == 65_536
    assert transports.MAX_RESPONSE_BYTES == 1_048_576
    assert transports.MAX_CONTROLLER_RESPONSE_BYTES == MAX_JSON_BYTES == 8_388_608
    known_hosts, fingerprint = _known_host(tmp_path)
    identity = _identity(tmp_path)

    def local(**kwargs):
        return transports.LocalTransport({"router-status": lambda args: {}}, **kwargs)

    def ssh(**kwargs):
        return transports.SSHRecoveryTransport(
            "ssh://100.64.0.10", adapters={"controller-recovery": ["recover"]},
            known_hosts_path=known_hosts, host_key_fingerprint=fingerprint,
            identity_file=identity, **kwargs,
        )

    for factory in (local, ssh):
        assert factory().max_response_bytes == 65_536
        assert factory(max_response_bytes=1_048_576).max_response_bytes == 1_048_576
        with pytest.raises(ValueError, match="between 1 and 1048576"):
            factory(max_response_bytes=1_048_577)
    controller = transports.ControllerTransport(
        "http://127.0.0.1:8765", auth_env="TOKEN", allowed_operations=["router-status"],
        environment={"TOKEN": TOKEN},
        opener=lambda request, timeout: Response(b" " * 65_537),
    )
    assert controller.max_response_bytes == 65_536
    with pytest.raises(transports.TransportError) as exc:
        controller.execute(transports.Operation("router-status", {}))
    assert exc.value.code == "response_too_large"


@pytest.mark.parametrize("body", [b"{}", b'{"ok":"yes"}'])
def test_controller_transport_rejects_missing_or_malformed_ok(body):
    transport = transports.ControllerTransport(
        "http://100.64.0.10:8765",
        auth_env="ANVIL_CONTROLLER_TOKEN",
        allowed_operations=["router-status"],
        environment={"ANVIL_CONTROLLER_TOKEN": TOKEN},
        opener=lambda request, timeout: Response(body),
    )

    with pytest.raises(transports.TransportError) as exc:
        transport.execute(transports.Operation("router-status", {}))

    assert exc.value.code == "bad_controller_response"
    assert exc.value.execution_state == "partial_result"


def test_controller_transport_rejects_non_finite_json_response():
    transport = transports.ControllerTransport(
        "http://100.64.0.10:8765",
        auth_env="ANVIL_CONTROLLER_TOKEN",
        allowed_operations=["router-status"],
        environment={"ANVIL_CONTROLLER_TOKEN": TOKEN},
        opener=lambda request, timeout: Response(b'{"ok":true,"data":{"value":NaN}}'),
    )

    with pytest.raises(transports.TransportError) as exc:
        transport.execute(transports.Operation("router-status", {}))

    assert exc.value.code == "bad_controller_response"


def test_local_transport_rejects_non_finite_result():
    transport = transports.LocalTransport(
        {"router-status": lambda arguments: {"value": float("nan")}}
    )

    with pytest.raises(transports.TransportError) as exc:
        transport.execute(transports.Operation("router-status", {}))

    assert exc.value.code == "bad_local_result"


def test_confirmed_controller_operation_requires_idempotency_before_token_read():
    class NoTokenEnvironment(dict):
        def get(self, key, default=None):
            raise AssertionError("token read before idempotency validation")

    transport = transports.ControllerTransport(
        "http://100.64.0.10:8765",
        auth_env="ANVIL_CONTROLLER_TOKEN",
        allowed_operations=["router-restart"],
        environment=NoTokenEnvironment(),
        opener=lambda *args, **kwargs: pytest.fail("no-key mutation dispatched"),
    )

    with pytest.raises(transports.TransportError) as exc:
        transport.execute(transports.Operation("router-restart", {"confirm": True}))

    assert exc.value.code == "idempotency_key_required"
    assert exc.value.execution_state == "not_started"


def test_local_transport_bounds_serialized_results():
    transport = transports.LocalTransport(
        {"router-status": lambda arguments: {"log": "x" * 16}},
        max_response_bytes=8,
    )

    with pytest.raises(transports.TransportError) as exc:
        transport.execute(transports.Operation("router-status", {}))

    assert exc.value.code == "response_too_large"
    assert exc.value.execution_state == "partial_result"


def test_controller_transport_classifies_ambiguous_and_remote_failures_without_tokens():
    unavailable = transports.ControllerTransport(
        "http://100.64.0.10:8765",
        auth_env="ANVIL_CONTROLLER_TOKEN",
        allowed_operations=["router-status"],
        environment={"ANVIL_CONTROLLER_TOKEN": TOKEN},
        opener=lambda request, timeout: (_ for _ in ()).throw(socket.timeout(TOKEN)),
    )
    with pytest.raises(transports.TransportError) as exc:
        unavailable.execute(transports.Operation("router-status", {}))
    assert exc.value.execution_state == "partial_result"
    assert exc.value.may_have_executed is True
    assert TOKEN not in json.dumps(exc.value.as_dict())


def test_controller_transport_sends_idempotency_header_and_reads_status():
    seen = []

    def opener(request, timeout):
        seen.append(
            (request.get_method(), request.full_url, dict(request.header_items()), request.data)
        )
        if request.get_method() == "GET":
            return Response(b'{"key":"mutation-1","status":"succeeded"}')
        return Response(b'{"ok":true,"data":{"done":true}}')

    transport = transports.ControllerTransport(
        "http://100.64.0.10:8765",
        auth_env="ANVIL_CONTROLLER_TOKEN",
        allowed_operations=["router-status"],
        environment={"ANVIL_CONTROLLER_TOKEN": TOKEN},
        opener=opener,
    )
    context = {
        "topology": "fakoli",
        "execution_host": "dark",
        "execution_runtime": "dark-native",
    }
    result = transport.execute(
        transports.Operation("router-status", {}),
        idempotency_key="mutation-1",
        idempotency_context=context,
    )
    assert result.data["ok"] is True
    status = transport.operation_status("mutation-1")
    assert status.operation == "operation-status"
    assert status.data["status"] == "succeeded"
    assert seen[0][2]["X-anvil-idempotency-key"] == "mutation-1"
    assert json.loads(seen[0][3].decode("utf-8"))["context"] == context
    assert seen[1][0] == "GET"
    assert seen[1][1].endswith("/operations/mutation-1")

    def http_error(request, timeout):
        raise urllib.error.HTTPError(
            request.full_url, 500, "boom", {}, io.BytesIO(TOKEN.encode("utf-8"))
        )

    failed = transports.ControllerTransport(
        "http://100.64.0.10:8765",
        auth_env="ANVIL_CONTROLLER_TOKEN",
        allowed_operations=["router-status"],
        environment={"ANVIL_CONTROLLER_TOKEN": TOKEN},
        opener=http_error,
    )
    with pytest.raises(transports.TransportError) as exc:
        failed.execute(transports.Operation("router-status", {}))
    assert exc.value.execution_state == "remote_failed"
    assert TOKEN not in json.dumps(exc.value.as_dict())


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"key": "mutation-1"},
        {"status": "unknown"},
        {"key": 1, "status": "unknown"},
        {"key": "mutation-1", "status": 1},
        {"key": "mutation-1", "status": "complete"},
        {"key": "different-key", "status": "unknown"},
        {"key": "mutation-1", "status": "running", "request_id": 1},
        {"key": "mutation-1", "status": "running", "fingerprint": "not-a-digest"},
        {"key": "mutation-1", "status": "running", "created_at": "now"},
        {"key": "mutation-1", "status": "failed", "error": []},
        {"key": "mutation-1", "status": "unknown", "undeclared": True},
    ],
)
def test_controller_operation_status_rejects_malformed_schema(body):
    transport = transports.ControllerTransport(
        "http://100.64.0.10:8765",
        auth_env="ANVIL_CONTROLLER_TOKEN",
        allowed_operations=["router-status"],
        environment={"ANVIL_CONTROLLER_TOKEN": TOKEN},
        opener=lambda request, timeout: Response(json.dumps(body).encode("utf-8")),
    )

    with pytest.raises(transports.TransportError) as exc:
        transport.operation_status("mutation-1")

    assert exc.value.code == "bad_controller_response"
    assert exc.value.execution_state == "partial_result"


def test_execute_plan_dispatches_selected_controller_and_refuses_endpoint_mismatch():
    command = type("Command", (), {"name": "router-status"})()
    host = type("Host", (), {"id": "dark", "address": "100.64.0.10"})()
    plan = type(
        "Plan",
        (),
        {
            "command": command,
            "transport": "controller",
            "transport_endpoint": "http://100.64.0.10:8765",
            "execution_host": host,
        },
    )()
    controller = transports.ControllerTransport(
        "http://100.64.0.10:8765",
        auth_env="ANVIL_CONTROLLER_TOKEN",
        allowed_operations=["router-status"],
        environment={"ANVIL_CONTROLLER_TOKEN": TOKEN},
        opener=lambda request, timeout: Response(b'{"ok":true}'),
    )
    result = transports.execute_plan(
        plan, transports.Operation("router-status", {}), controller=controller
    )
    assert result.transport == "controller"

    wrong = transports.ControllerTransport(
        "http://100.64.0.11:8765",
        auth_env="ANVIL_CONTROLLER_TOKEN",
        allowed_operations=["router-status"],
        environment={"ANVIL_CONTROLLER_TOKEN": TOKEN},
        opener=lambda request, timeout: Response(b'{"ok":true}'),
    )
    with pytest.raises(transports.TransportError) as exc:
        transports.execute_plan(plan, transports.Operation("router-status", {}), controller=wrong)
    assert exc.value.code == "controller_endpoint_mismatch"


def test_ssh_recovery_uses_verified_host_and_argument_array(tmp_path):
    known_hosts, fingerprint = _known_host(tmp_path, port=2222)
    identity = _identity(tmp_path)
    seen = []

    def runner(argv, **kwargs):
        pinned_option = next(item for item in argv if item.startswith("UserKnownHostsFile="))
        pinned_path = pinned_option.partition("=")[2]
        identity_option = [
            argv[index + 1]
            for index, token in enumerate(argv[:-1])
            if token == "-o" and argv[index + 1].startswith("IdentityFile=")
        ][-1]
        identity_path = identity_option.partition("=")[2]
        seen.append(
            (
                argv,
                kwargs,
                pinned_path,
                transports.Path(pinned_path).read_text(),
                identity_path,
                transports.Path(identity_path).read_bytes(),
            )
        )
        return subprocess.CompletedProcess(argv, 0, b'{"ok":true}\n', b"")

    transport = transports.SSHRecoveryTransport(
        "ssh://operator@100.64.0.10:2222",
        adapters={"controller-recovery": ["anvil-serving", "controller", "recover"]},
        known_hosts_path=known_hosts,
        host_key_fingerprint=fingerprint,
        identity_file=identity,
        timeout_seconds=4,
        max_response_bytes=128,
        runner=runner,
    )
    result = transport.execute(transports.Operation("controller-recovery", {}))

    assert result.transport == "ssh"
    assert seen[0][0][-5:] == [
        "--",
        "operator@100.64.0.10",
        "anvil-serving",
        "controller",
        "recover",
    ]
    assert seen[0][2] != known_hosts
    assert seen[0][3].startswith("[100.64.0.10]:2222 ssh-ed25519 ")
    assert not transports.Path(seen[0][2]).exists()
    assert "BatchMode=yes" in seen[0][0]
    assert "StrictHostKeyChecking=yes" in seen[0][0]
    # Literal on purpose, not transports._SSH_DEVNULL: the wire value must be
    # "/dev/null" on every platform (MSYS ssh rejects "nul"), and comparing
    # against the module's own constant would make this assertion tautological.
    assert seen[0][0][1:3] == ["-F", "/dev/null"]
    assert "GlobalKnownHostsFile=/dev/null" in seen[0][0]
    assert "ProxyCommand=none" in seen[0][0]
    assert "ProxyJump=none" in seen[0][0]
    assert "IdentityAgent=none" in seen[0][0]
    assert "IdentitiesOnly=yes" in seen[0][0]
    identity_options = [
        seen[0][0][index + 1]
        for index, token in enumerate(seen[0][0][:-1])
        if token == "-o" and seen[0][0][index + 1].startswith("IdentityFile=")
    ]
    assert identity_options[0] == "IdentityFile=none"
    assert seen[0][4] != identity
    assert seen[0][5] == b"synthetic private key"
    assert not transports.Path(seen[0][4]).exists()
    assert "-i" not in seen[0][0]
    assert "ForwardAgent=no" in seen[0][0]
    assert "ClearAllForwardings=yes" in seen[0][0]
    assert "PreferredAuthentications=publickey" in seen[0][0]
    assert seen[0][1] == {"timeout": 4.0, "max_output_bytes": 128}


@pytest.mark.parametrize("platform_name", ["windows", "posix"])
def test_ssh_recovery_process_contract_is_platform_invariant(tmp_path, platform_name):
    known_hosts, fingerprint = _known_host(tmp_path)
    identity = _identity(tmp_path)
    launches = []

    def runner(argv, **kwargs):
        launches.append((platform_name, list(argv), dict(kwargs)))
        return subprocess.CompletedProcess(argv, 0, b"ok", b"")

    transport = transports.SSHRecoveryTransport(
        "ssh://100.64.0.10",
        adapters={"controller-recovery": ["recover"]},
        known_hosts_path=known_hosts,
        host_key_fingerprint=fingerprint,
        identity_file=identity,
        runner=runner,
    )

    transport.execute(transports.Operation("controller-recovery", {}))

    _, argv, kwargs = launches[0]
    assert argv[0:3] == ["ssh", "-F", "/dev/null"]
    assert "IdentityFile=none" in argv
    explicit_identity = [item for item in argv if item.startswith("IdentityFile=")][-1]
    verified_identity = explicit_identity.partition("=")[2]
    assert os.path.normcase(os.path.abspath(verified_identity)) != os.path.normcase(identity)
    assert not os.path.exists(verified_identity)
    assert "GlobalKnownHostsFile=/dev/null" in argv
    assert "nul" not in argv
    assert "cmd" not in argv and "sh" not in argv
    assert kwargs == {"timeout": 10.0, "max_output_bytes": 65536}


def test_ssh_recovery_rejects_identity_disappearance_without_launch(tmp_path):
    known_hosts, fingerprint = _known_host(tmp_path)
    identity = _identity(tmp_path)
    transport = transports.SSHRecoveryTransport(
        "ssh://100.64.0.10",
        adapters={"controller-recovery": ["recover"]},
        known_hosts_path=known_hosts,
        host_key_fingerprint=fingerprint,
        identity_file=identity,
        runner=lambda *args, **kwargs: pytest.fail("missing identity launched"),
    )
    transports.Path(identity).unlink()

    with pytest.raises(transports.TransportError) as exc:
        transport.execute(transports.Operation("controller-recovery", {}))

    assert exc.value.code == "ssh_identity_unavailable"
    assert exc.value.execution_state == "not_started"


def test_ssh_recovery_rejects_changed_identity_contents_without_launch(tmp_path):
    known_hosts, fingerprint = _known_host(tmp_path)
    identity = _identity(tmp_path)
    transport = transports.SSHRecoveryTransport(
        "ssh://100.64.0.10",
        adapters={"controller-recovery": ["recover"]},
        known_hosts_path=known_hosts,
        host_key_fingerprint=fingerprint,
        identity_file=identity,
        runner=lambda *args, **kwargs: pytest.fail("changed identity launched"),
    )
    transports.Path(identity).write_bytes(b"replacement private key")

    with pytest.raises(transports.TransportError) as exc:
        transport.execute(transports.Operation("controller-recovery", {}))

    assert exc.value.code == "ssh_identity_changed"
    assert exc.value.execution_state == "not_started"


@pytest.mark.parametrize(
    ("changed_field", "expected_code"),
    [
        ("recovery_transport_id", "ssh_transport_identity_mismatch"),
        ("recovery_host_key_fingerprint", "ssh_host_key_binding_mismatch"),
        ("recovery_known_hosts_path", "ssh_known_hosts_binding_mismatch"),
    ],
)
def test_ssh_recovery_binds_full_selected_transport_record_before_launch(
    tmp_path, changed_field, expected_code
):
    known_hosts, fingerprint = _known_host(tmp_path)
    transport = transports.SSHRecoveryTransport(
        "ssh://100.64.0.10",
        adapters={"controller-recovery": ["recover"]},
        known_hosts_path=known_hosts,
        host_key_fingerprint=fingerprint,
        identity_file=_identity(tmp_path),
        transport_id="dark-ssh",
        runner=lambda *args, **kwargs: pytest.fail("mismatched SSH record launched"),
    )
    values = {
        "recovery_transport_id": "dark-ssh",
        "recovery_host_key_fingerprint": fingerprint,
        "recovery_known_hosts_path": known_hosts,
    }
    values[changed_field] = str(tmp_path / "different")
    command = type("Command", (), {"name": "controller-recovery", "recovery_capable": True})()
    plan = type(
        "Plan",
        (),
        {
            "command": command,
            "transport": "controller",
            "transport_endpoint": "http://100.64.0.10:8765",
            "recovery_transport_endpoint": "ssh://100.64.0.10",
            "execution_host": type("Host", (), {"id": "dark", "address": "100.64.0.10"})(),
            **values,
        },
    )()
    controller = transports.ControllerTransport(
        "http://100.64.0.10:8765",
        auth_env="TOKEN",
        allowed_operations=["controller-recovery"],
        environment={"TOKEN": TOKEN},
        opener=lambda *args, **kwargs: (_ for _ in ()).throw(ConnectionRefusedError()),
    )

    with pytest.raises(transports.TransportError) as exc:
        transports.execute_plan(
            plan,
            transports.Operation("controller-recovery", {}),
            controller=controller,
            ssh=transport,
            allow_ssh_fallback=True,
        )

    assert exc.value.code == expected_code


def test_transport_redaction_sanitizes_tuples_and_token_bearing_keys():
    secret = "known-controller-token"
    rendered = transports.redact(
        {f"prefix-{secret}-suffix": (secret, {secret: "value"})},
        secret,
    )

    assert secret not in json.dumps(rendered)


def test_ssh_recovery_rejects_missing_identity_and_unsafe_text_before_launch(tmp_path):
    known_hosts, fingerprint = _known_host(tmp_path)
    with pytest.raises(TypeError, match="identity_file"):
        transports.SSHRecoveryTransport(
            "ssh://100.64.0.10",
            adapters={"controller-recovery": ["recover"]},
            known_hosts_path=known_hosts,
            host_key_fingerprint=fingerprint,
        )
    with pytest.raises(ValueError, match="SSH identity path is required"):
        transports.SSHRecoveryTransport(
            "ssh://100.64.0.10",
            adapters={"controller-recovery": ["recover"]},
            known_hosts_path=known_hosts,
            host_key_fingerprint=fingerprint,
            identity_file="",
        )
    with pytest.raises(ValueError, match="known_hosts file does not exist"):
        transports.SSHRecoveryTransport(
            "ssh://100.64.0.10",
            adapters={"controller-recovery": ["recover"]},
            known_hosts_path=str(tmp_path / "missing"),
            host_key_fingerprint=fingerprint,
            identity_file=_identity(tmp_path),
        )
    with pytest.raises(ValueError, match="identity file does not exist"):
        transports.SSHRecoveryTransport(
            "ssh://100.64.0.10",
            adapters={"controller-recovery": ["recover"]},
            known_hosts_path=known_hosts,
            host_key_fingerprint=fingerprint,
            identity_file=str(tmp_path / "missing-key"),
        )
    with pytest.raises(ValueError, match="unsafe shell text"):
        transports.SSHRecoveryTransport(
            "ssh://100.64.0.10",
            adapters={"controller-recovery": ["recover; rm -rf /"]},
            known_hosts_path=known_hosts,
            host_key_fingerprint=fingerprint,
            identity_file=_identity(tmp_path),
            runner=lambda *args, **kwargs: pytest.fail("unsafe adapter launched"),
        )


def test_ssh_recovery_rejects_unknown_host_and_fingerprint_mismatch_without_launch(tmp_path):
    known_hosts, fingerprint = _known_host(tmp_path, host="100.64.0.11")
    transport = transports.SSHRecoveryTransport(
        "ssh://100.64.0.10",
        adapters={"controller-recovery": ["recover"]},
        known_hosts_path=known_hosts,
        host_key_fingerprint=fingerprint,
        identity_file=_identity(tmp_path),
        runner=lambda *args, **kwargs: pytest.fail("unknown host launched"),
    )
    with pytest.raises(transports.TransportError) as exc:
        transport.execute(transports.Operation("controller-recovery", {}))
    assert exc.value.code == "ssh_unknown_host"

    known_hosts, _ = _known_host(tmp_path)
    mismatch = transports.SSHRecoveryTransport(
        "ssh://100.64.0.10",
        adapters={"controller-recovery": ["recover"]},
        known_hosts_path=known_hosts,
        host_key_fingerprint="SHA256:" + "A" * 43,
        identity_file=_identity(tmp_path),
        runner=lambda *args, **kwargs: pytest.fail("mismatched host launched"),
    )
    with pytest.raises(transports.TransportError) as exc:
        mismatch.execute(transports.Operation("controller-recovery", {}))
    assert exc.value.code == "ssh_fingerprint_mismatch"


def test_ssh_recovery_rejects_oversized_known_hosts_before_launch(tmp_path):
    known_hosts = tmp_path / "known_hosts"
    known_hosts.write_bytes(b"x" * (transports._MAX_KNOWN_HOST_LINE_BYTES + 1))
    transport = transports.SSHRecoveryTransport(
        "ssh://100.64.0.10",
        adapters={"controller-recovery": ["recover"]},
        known_hosts_path=str(known_hosts),
        host_key_fingerprint="SHA256:" + "A" * 43,
        identity_file=_identity(tmp_path),
        runner=lambda *args, **kwargs: pytest.fail("oversized known_hosts launched"),
    )
    with pytest.raises(transports.TransportError) as exc:
        transport.execute(transports.Operation("controller-recovery", {}))
    assert exc.value.code == "ssh_known_hosts_too_large"


def test_controller_fallback_is_explicit_and_only_for_proven_predispatch_failure(tmp_path):
    known_hosts, fingerprint = _known_host(tmp_path)
    ssh_calls = []
    ssh = transports.SSHRecoveryTransport(
        "ssh://100.64.0.10",
        adapters={"controller-recovery": ["recover"]},
        known_hosts_path=known_hosts,
        host_key_fingerprint=fingerprint,
        identity_file=_identity(tmp_path),
        transport_id="dark-ssh",
        runner=lambda argv, **kwargs: (
            ssh_calls.append(argv) or subprocess.CompletedProcess(argv, 0, b"ok", b"")
        ),
    )
    command = type("Command", (), {"name": "controller-recovery", "recovery_capable": True})()
    host = type("Host", (), {"id": "dark", "address": "100.64.0.10"})()
    runtime = type("Runtime", (), {"id": "dark-native"})()
    plan = type(
        "Plan",
        (),
        {
            "command": command,
            "transport": "controller",
            "transport_endpoint": "http://100.64.0.10:8765",
            "topology_id": "fakoli",
            "recovery_transport_endpoint": "ssh://100.64.0.10",
            "recovery_transport_id": "dark-ssh",
            "recovery_host_key_fingerprint": fingerprint,
            "recovery_known_hosts_path": known_hosts,
            "execution_host": host,
            "execution_runtime": runtime,
        },
    )()
    controller = transports.ControllerTransport(
        "http://100.64.0.10:8765",
        auth_env="TOKEN",
        allowed_operations=["controller-recovery"],
        environment={"TOKEN": TOKEN},
        opener=lambda *args, **kwargs: (_ for _ in ()).throw(ConnectionRefusedError()),
    )
    operation = transports.Operation("controller-recovery", {})
    with pytest.raises(transports.TransportError) as exc:
        transports.execute_plan(plan, operation, controller=controller, ssh=ssh)
    assert exc.value.code == "controller_connect_failed"
    assert ssh_calls == []
    assert (
        transports.execute_plan(
            plan, operation, controller=controller, ssh=ssh, allow_ssh_fallback=True
        ).transport
        == "ssh"
    )

    ambiguous = transports.ControllerTransport(
        "http://100.64.0.10:8765",
        auth_env="TOKEN",
        allowed_operations=["controller-recovery"],
        environment={"TOKEN": TOKEN},
        opener=lambda *args, **kwargs: (_ for _ in ()).throw(socket.timeout()),
    )
    with pytest.raises(transports.TransportError) as exc:
        transports.execute_plan(
            plan,
            operation,
            controller=ambiguous,
            ssh=ssh,
            allow_ssh_fallback=True,
            idempotency_key="mutation-1",
        )
    assert exc.value.execution_state == "partial_result"
    assert len(ssh_calls) == 1


@pytest.mark.parametrize(
    ("selected", "transport_endpoint", "recovery_endpoint", "host_address", "expected_code"),
    [
        (
            "controller",
            "http://100.64.0.10:8765",
            "ssh://100.64.0.11",
            "100.64.0.10",
            "ssh_endpoint_mismatch",
        ),
        (
            "controller",
            "http://100.64.0.10:8765",
            "ssh://100.64.0.10",
            "100.64.0.11",
            "controller_execution_host_mismatch",
        ),
        ("ssh", "ssh://100.64.0.11", None, "100.64.0.10", "ssh_endpoint_mismatch"),
        (
            "ssh",
            "ssh://100.64.0.10",
            None,
            "100.64.0.11",
            "ssh_execution_host_mismatch",
        ),
    ],
)
def test_ssh_plan_binding_mismatches_fail_before_launch(
    tmp_path, selected, transport_endpoint, recovery_endpoint, host_address, expected_code
):
    known_hosts, fingerprint = _known_host(tmp_path)
    calls = []
    ssh = transports.SSHRecoveryTransport(
        "ssh://100.64.0.10",
        adapters={"controller-recovery": ["recover"]},
        known_hosts_path=known_hosts,
        host_key_fingerprint=fingerprint,
        identity_file=_identity(tmp_path),
        transport_id="dark-ssh",
        runner=lambda *args, **kwargs: calls.append(args),
    )
    command = type("Command", (), {"name": "controller-recovery", "recovery_capable": True})()
    plan = type(
        "Plan",
        (),
        {
            "command": command,
            "transport": selected,
            "transport_endpoint": transport_endpoint,
            "recovery_transport_endpoint": recovery_endpoint,
            "transport_id": "dark-ssh",
            "transport_host_key_fingerprint": fingerprint,
            "transport_known_hosts_path": known_hosts,
            "recovery_transport_id": "dark-ssh",
            "recovery_host_key_fingerprint": fingerprint,
            "recovery_known_hosts_path": known_hosts,
            "execution_host": type("Host", (), {"id": "dark", "address": host_address})(),
        },
    )()
    controller = transports.ControllerTransport(
        "http://100.64.0.10:8765",
        auth_env="TOKEN",
        allowed_operations=["controller-recovery"],
        environment={"TOKEN": TOKEN},
        opener=lambda *args, **kwargs: (_ for _ in ()).throw(ConnectionRefusedError()),
    )
    with pytest.raises(transports.TransportError) as exc:
        transports.execute_plan(
            plan,
            transports.Operation("controller-recovery", {}),
            controller=controller,
            ssh=ssh,
            allow_ssh_fallback=True,
        )
    assert exc.value.code == expected_code
    assert calls == []


def test_bounded_process_terminates_on_combined_output_overflow():
    script = "import os,time; os.write(1,b'a'*65536); os.write(2,b'b'*65536); time.sleep(30)"
    with pytest.raises(transports._ProcessOutputOverflow):
        transports._run_bounded_process(
            [subprocess.sys.executable, "-c", script], timeout=5, max_output_bytes=1024
        )


# --- ADR-0033: controller node-identity verification ------------------------


def _node_opener(node=None, calls=None):
    def opener(request, timeout):
        if calls is not None:
            calls.append(request.full_url)
        if request.full_url.endswith("/health"):
            payload = {
                "status": "ok",
                "service": "anvil-serving-controller",
                "request_id": "health-request-1",
            }
            if node is not None:
                payload["node"] = node
            return Response(json.dumps(payload).encode("utf-8"))
        return Response(b'{"ok":true,"data":{}}')

    return opener


def test_expected_node_match_verifies_once_then_dispatches():
    calls = []
    transport = transports.ControllerTransport(
        "http://100.64.0.10:8765",
        auth_env="ANVIL_CONTROLLER_TOKEN",
        allowed_operations=["router-status"],
        environment={"ANVIL_CONTROLLER_TOKEN": TOKEN},
        opener=_node_opener(node="fakoli-dark", calls=calls),
        expected_node="fakoli-dark",
    )
    transport.execute(transports.Operation("router-status", {}))
    transport.execute(transports.Operation("router-status", {}))

    health_calls = [url for url in calls if url.endswith("/health")]
    assert len(health_calls) == 1  # verified once, cached


def test_expected_node_mismatch_fails_closed_before_dispatch():
    calls = []
    transport = transports.ControllerTransport(
        "http://100.64.0.10:8765",
        auth_env="ANVIL_CONTROLLER_TOKEN",
        allowed_operations=["router-status"],
        environment={"ANVIL_CONTROLLER_TOKEN": TOKEN},
        opener=_node_opener(node="some-other-host", calls=calls),
        expected_node="fakoli-dark",
    )
    with pytest.raises(transports.TransportError) as excinfo:
        transport.execute(transports.Operation("router-status", {}))

    assert excinfo.value.code == "controller_node_mismatch"
    assert excinfo.value.execution_state == "not_started"
    assert excinfo.value.details == {}
    assert not any(url.endswith("/tools/call") for url in calls)


def test_expected_node_absent_from_health_fails_closed():
    transport = transports.ControllerTransport(
        "http://100.64.0.10:8765",
        auth_env="ANVIL_CONTROLLER_TOKEN",
        allowed_operations=["router-status"],
        environment={"ANVIL_CONTROLLER_TOKEN": TOKEN},
        opener=_node_opener(node=None),
        expected_node="fakoli-dark",
    )
    with pytest.raises(transports.TransportError) as excinfo:
        transport.execute(transports.Operation("router-status", {}))
    assert excinfo.value.code == "controller_node_mismatch"


def test_no_expected_node_skips_verification():
    calls = []
    transport = transports.ControllerTransport(
        "http://100.64.0.10:8765",
        auth_env="ANVIL_CONTROLLER_TOKEN",
        allowed_operations=["router-status"],
        environment={"ANVIL_CONTROLLER_TOKEN": TOKEN},
        opener=_node_opener(node="fakoli-dark", calls=calls),
    )
    transport.execute(transports.Operation("router-status", {}))
    assert not any(url.endswith("/health") for url in calls)


def _expected_node_transport(body, calls):
    def opener(request, timeout):
        calls.append(request.full_url)
        if request.full_url.endswith("/health"):
            return Response(body)
        return Response(b'{"ok":true,"data":{}}')

    return transports.ControllerTransport(
        "http://100.64.0.10:8765",
        auth_env="ANVIL_CONTROLLER_TOKEN",
        allowed_operations=["router-status"],
        environment={"ANVIL_CONTROLLER_TOKEN": TOKEN},
        opener=opener,
        expected_node="fakoli-dark",
    )


@pytest.mark.parametrize(
    "body",
    [
        b'{"status":"ok","service":"anvil-serving-controller",'
        b'"request_id":"request-1","node":"wrong","node":"fakoli-dark"}',
        b'{"status":"ok","service":"anvil-serving-controller",'
        b'"request_id":"request-1","node":"fakoli-dark","extra":"private-marker"}',
        b'{"service":"anvil-serving-controller","request_id":"request-1",'
        b'"node":"fakoli-dark"}',
        b'{"status":"starting","service":"anvil-serving-controller",'
        b'"request_id":"request-1","node":"fakoli-dark"}',
        b'{"status":"ok","service":"other","request_id":"request-1",'
        b'"node":"fakoli-dark"}',
        b'{"status":"ok","service":"anvil-serving-controller",'
        b'"request_id":"bad request","node":"fakoli-dark"}',
        b'{"status":"ok","service":"anvil-serving-controller",'
        b'"request_id":1,"node":"fakoli-dark"}',
        b'{"status":"ok","service":"anvil-serving-controller",'
        b'"request_id":"request-1","node":NaN}',
        b'[]',
        b'{not-json',
        b'\xff',
        b'[' * 1100 + b']' * 1100,
    ],
)
def test_expected_node_rejects_noncanonical_health_without_dispatch_or_cache(body):
    calls = []
    transport = _expected_node_transport(body, calls)

    with pytest.raises(transports.TransportError) as excinfo:
        transport.execute(transports.Operation("router-status", {}))

    assert excinfo.value.as_dict() == {
        "code": "controller_node_mismatch",
        "message": "controller did not assert the expected node identity",
        "execution_state": "not_started",
        "may_have_executed": False,
        "details": {},
    }
    assert transport._node_verified is False
    assert calls == ["http://100.64.0.10:8765/health"]
    assert "private-marker" not in json.dumps(excinfo.value.as_dict())


def test_expected_node_accepts_exact_65536_byte_health_and_caches():
    body = json.dumps(
        {
            "status": "ok",
            "service": "anvil-serving-controller",
            "request_id": "r" * 96,
            "node": "fakoli-dark",
        },
        separators=(",", ":"),
    ).encode("ascii")
    body += b" " * (65_536 - len(body))
    calls = []
    transport = _expected_node_transport(body, calls)

    transport.execute(transports.Operation("router-status", {}))
    transport.execute(transports.Operation("router-status", {}))

    assert calls == [
        "http://100.64.0.10:8765/health",
        "http://100.64.0.10:8765/tools/call",
        "http://100.64.0.10:8765/tools/call",
    ]


def test_expected_node_rejects_65537_bytes_before_parse_or_dispatch(monkeypatch):
    body = json.dumps(
        {
            "status": "ok",
            "service": "anvil-serving-controller",
            "request_id": "request-1",
            "node": "fakoli-dark",
        },
        separators=(",", ":"),
    ).encode("ascii")
    body += b" " * (65_537 - len(body))
    calls = []
    transport = _expected_node_transport(body, calls)
    monkeypatch.setattr(
        transports,
        "_strict_json_loads",
        lambda _value: pytest.fail("oversized health reached JSON parser"),
    )

    with pytest.raises(transports.TransportError) as excinfo:
        transport.execute(transports.Operation("router-status", {}))

    assert excinfo.value.code == "controller_node_mismatch"
    assert excinfo.value.execution_state == "not_started"
    assert excinfo.value.details == {}
    assert transport._node_verified is False
    assert calls == ["http://100.64.0.10:8765/health"]


def test_expected_node_rejects_97_byte_request_id_before_dispatch():
    body = json.dumps(
        {
            "status": "ok",
            "service": "anvil-serving-controller",
            "request_id": "r" * 97,
            "node": "fakoli-dark",
        },
        separators=(",", ":"),
    ).encode("ascii")
    calls = []
    transport = _expected_node_transport(body, calls)

    with pytest.raises(transports.TransportError) as excinfo:
        transport.execute(transports.Operation("router-status", {}))

    assert excinfo.value.code == "controller_node_mismatch"
    assert transport._node_verified is False
    assert calls == ["http://100.64.0.10:8765/health"]


def test_expected_node_connect_failure_is_fixed_and_input_free():
    def unavailable(request, timeout):
        raise urllib.error.URLError("private-controller-address-marker")

    transport = transports.ControllerTransport(
        "http://100.64.0.10:8765",
        auth_env="ANVIL_CONTROLLER_TOKEN",
        allowed_operations=["router-status"],
        environment={"ANVIL_CONTROLLER_TOKEN": TOKEN},
        opener=unavailable,
        expected_node="fakoli-dark",
    )

    with pytest.raises(transports.TransportError) as excinfo:
        transport.execute(transports.Operation("router-status", {}))

    assert excinfo.value.as_dict() == {
        "code": "controller_connect_failed",
        "message": "controller connection failed before dispatch",
        "execution_state": "not_started",
        "may_have_executed": False,
        "details": {},
    }
