"""Pure contract tests for the offline root-only isolation guest fixture."""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import stat
from types import SimpleNamespace

import pytest


_GUEST = Path(__file__).parents[2] / "connect/test/vm/guest.py"
_SPEC = importlib.util.spec_from_file_location("connect_isolation_guest", _GUEST)
assert _SPEC is not None and _SPEC.loader is not None
guest = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(guest)


def _payload(root: Path) -> None:
    files: dict[str, str] = {}
    for name in sorted(guest._REQUIRED):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        content = ("fixture:" + name).encode("ascii")
        path.write_bytes(content)
        files[name] = hashlib.sha256(content).hexdigest()
    (root / "payload.json").write_text(json.dumps({
        "schema": guest.PAYLOAD_SCHEMA,
        "source": {"revision": "a" * 40, "dirty": False},
        "build": {"schema": "anvil-connect.isolation-guest-build/v1", "platform": "linux/amd64"},
        "files": files,
    }), encoding="utf-8")


def test_build_manifest_is_closed_synthetic_and_service_isolated() -> None:
    value = guest.build_manifest()
    assert value["schema"] == "anvil-connect.deployment/v1"
    assert "service_user" not in value
    identities = value["service_identities"]
    assert identities["gateway"] == {"uid": 21001, "gid": 21001}
    assert identities["edge"] == {"uid": 21002, "gid": 21002}
    assert identities["ingress"] == {"group_id": 21010, "directory": "/run/anvil-test/ingress"}
    assert value["gateway"]["ingress"] == {"directory": "/run/anvil-test/ingress", "gateway_uid": 21001, "edge_uid": 21002, "group_id": 21010}
    assert value["binary"] == "/opt/anvil-test/bin/anvil-connect-ctl"
    assert all(not host.startswith("127.") for host in (value["gateway"]["control_host"], value["gateway"]["tunnel_host"]))


def test_payload_verification_requires_exact_regular_hashed_file_set(tmp_path: Path) -> None:
    root = tmp_path / "payload"
    root.mkdir()
    _payload(root)
    verified = guest.verify_payload(root)
    assert set(verified) == guest._REQUIRED
    (root / "unexpected").write_text("no", encoding="utf-8")
    with pytest.raises(guest.GuestFailure):
        guest.verify_payload(root)
    (root / "unexpected").unlink()
    (root / "bin/caddy").write_text("tampered", encoding="utf-8")
    with pytest.raises(guest.GuestFailure):
        guest.verify_payload(root)
    _payload(root)
    payload = json.loads((root / "payload.json").read_text(encoding="utf-8"))
    payload["build"]["platform"] = "other"
    (root / "payload.json").write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(guest.GuestFailure):
        guest.verify_payload(root)
    _payload(root)
    payload = json.loads((root / "payload.json").read_text(encoding="utf-8"))
    payload["source"]["dirty"] = True
    (root / "payload.json").write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(guest.GuestFailure):
        guest.verify_payload(root)


def test_payload_verification_refuses_symlinked_bytes(tmp_path: Path) -> None:
    root = tmp_path / "payload"
    root.mkdir()
    _payload(root)
    original = root / "bin/caddy"
    outside = tmp_path / "outside"
    outside.write_text(original.read_text(encoding="utf-8"), encoding="utf-8")
    original.unlink()
    original.symlink_to(outside)
    with pytest.raises(guest.GuestFailure):
        guest.verify_payload(root)


def test_result_schema_is_fixed_and_never_carries_diagnostics(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    result = tmp_path / "result.json"
    monkeypatch.setattr(guest, "RESULT_PATH", result)
    cases = [{"name": name, "status": "passed"} for name in guest.CASES]
    guest._write_result(cases)
    value = json.loads(result.read_text(encoding="utf-8"))
    assert value == {"schema": guest.RESULT_SCHEMA, "cases": cases, "ok": True}
    assert result.stat().st_mode & 0o777 == 0o600
    with pytest.raises(guest.GuestFailure):
        guest._write_result(cases[:-1])


def test_guest_refuses_to_run_outside_a_root_systemd_guest(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(guest.os, "geteuid", lambda: 1000)
    assert guest.main() == 1


def test_case_list_is_closed_and_names_the_limited_scope() -> None:
    assert guest.CASES == (
        "legacy_activation_rejected_pre_state",
        "rendered_units_isolated",
        "managed_gateway_connector_readiness",
        "caddy_tls_authelia_discovery",
        "ingress_peer_denials_then_edge_success",
        "ingress_socket_ownership",
        "role_private_state_and_admin_denials",
        "managed_restart_and_rollback",
    )


def test_payload_verification_rejects_nested_symlink_and_bounded_growth(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "payload"
    root.mkdir()
    _payload(root)
    nested = root / "python/anvil_serving"
    outside = tmp_path / "outside"
    outside.mkdir()
    (nested / "connect").rename(outside / "connect")
    (nested / "connect").symlink_to(outside / "connect", target_is_directory=True)
    with pytest.raises(guest.GuestFailure):
        guest.verify_payload(root)

    root = tmp_path / "bounded"
    root.mkdir()
    _payload(root)
    monkeypatch.setattr(guest, "MAX_PAYLOAD_ENTRIES", len(guest._REQUIRED) - 1)
    with pytest.raises(guest.GuestFailure):
        guest.verify_payload(root)


def test_authelia_hash_pair_parser_is_closed() -> None:
    password = "A" * 40
    digest = "$argon2id$v=19$m=65536,t=3,p=4$fixture$fixture"
    assert guest._parse_hash_pair(("Random Password: " + password + "\nDigest: " + digest + "\n").encode("ascii")) == (password, digest)
    for raw in (b"Random Password: short\nDigest: $argon2id$x\n", b"Random Password: " + password.encode("ascii") + b"\nDigest: plain\n", b"Digest: $argon2id$x\n"):
        with pytest.raises(guest.GuestFailure):
            guest._parse_hash_pair(raw)


def test_manifest_declares_only_synthetic_role_paths_and_separate_ingress() -> None:
    value = guest.build_manifest()
    assert value["gateway"]["state_directory"] != value["gateway"]["ingress"]["directory"]
    assert value["gateway"]["ingress"] == {"directory": "/run/anvil-test/ingress", "gateway_uid": 21001, "edge_uid": 21002, "group_id": 21010}
    assert value["caddy"]["state_directory"] == "/var/lib/anvil-test/caddy"
    assert value["authelia"]["state_directory"] == "/var/lib/anvil-test/authelia"
    assert value["connectors"][0]["state_directory"] == "/var/lib/anvil-test/connectors/dashboard"
    rendered = json.dumps(value, sort_keys=True)
    assert "$argon2" not in rendered
    assert "BEGIN PRIVATE KEY" not in rendered
    assert "ANVIL_CONNECT_OIDC_CLIENT_SECRET" in rendered


def test_guest_prerequisites_are_closed_offline_base_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str] = []

    def access(path: str, mode: int) -> bool:
        seen.append(path)
        return True

    monkeypatch.setattr(guest.os, "access", access)
    guest.assert_guest_prerequisites()
    assert seen == [
        "/usr/sbin/groupadd", "/usr/sbin/useradd", "/usr/bin/openssl",
        "/usr/bin/systemctl", "/usr/bin/curl", "/usr/bin/setpriv", "/usr/bin/python3",
        "/usr/sbin/update-ca-certificates",
    ]


def test_role_read_probes_open_without_reading_or_host_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    commands: list[list[str]] = []
    monkeypatch.setattr(guest, "_command", lambda argv, **kwargs: commands.append(argv))

    guest._role_read_probes()

    assert commands == [
        ["/usr/bin/setpriv", "--reuid=21002", "--regid=21002", "--clear-groups", "/usr/bin/python3", "-c",
         "import os; f=os.open('/etc/anvil-test/tls/service-key.pem', os.O_RDONLY); os.close(f)"],
        ["/usr/bin/setpriv", "--reuid=21004", "--regid=21004", "--clear-groups", "/usr/bin/python3", "-c",
         "import os; f=os.open('/etc/anvil-test/trust/public.pem', os.O_RDONLY); os.close(f)"],
    ]
    assert all("os.read" not in command[-1] for command in commands)


def test_private_denials_cover_idp_and_tls_private_material_without_reads(monkeypatch: pytest.MonkeyPatch) -> None:
    commands: list[list[str]] = []
    monkeypatch.setattr(guest, "_command", lambda argv, **kwargs: commands.append(argv))
    monkeypatch.setattr(guest.Path, "stat", lambda self: SimpleNamespace(st_mode=stat.S_IFSOCK | 0o600, st_uid=21001))

    guest._private_denials()

    actions = [command[-1] for command in commands if command[-2] == "-c"]
    assert any("oidc-rs256-private-key" in action for action in actions)
    assert any("tls/service-key.pem" in action for action in actions)
    assert all("os.read" not in action for action in actions)
    assert all("--clear-groups" in command for command in commands)


def test_role_read_probes_precede_managed_readiness(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(guest, "_role_read_probes", lambda: calls.append("probes"))
    monkeypatch.setattr(guest, "_install_guest_trust", lambda: calls.append("trust"))

    def manager() -> SimpleNamespace:
        calls.append("manager")
        return SimpleNamespace(
            Target=SimpleNamespace(parse=lambda value: value),
            native_init=lambda *args, **kwargs: (_ for _ in ()).throw(guest.GuestFailure()),
        )

    monkeypatch.setattr(guest, "_manager", manager)
    with pytest.raises(guest.GuestFailure):
        guest._managed_readiness(Path("synthetic-manifest"))
    assert calls[:3] == ["probes", "trust", "manager"]


def test_result_line_is_one_closed_json_record() -> None:
    result = {
        "schema": guest.RESULT_SCHEMA,
        "cases": [{"name": name, "status": "passed"} for name in guest.CASES],
        "ok": True,
    }
    line = guest._result_line(result)
    marker, encoded = line.split(" ", 1)
    assert marker == guest.MARKER
    assert "\n" not in line
    assert json.loads(encoded) == result


def test_serial_result_is_one_canonical_frame_and_retries_short_writes(monkeypatch: pytest.MonkeyPatch) -> None:
    result = {
        "schema": guest.RESULT_SCHEMA,
        "cases": [{"name": name, "status": "passed"} for name in guest.CASES],
        "ok": True,
    }
    writes: list[bytes] = []
    flags: list[int] = []
    closed: list[int] = []

    def open_serial(path: str, value: int) -> int:
        assert path == guest.SERIAL_DEVICE
        flags.append(value)
        return 71

    def short_write(descriptor: int, data: bytes | memoryview) -> int:
        assert descriptor == 71
        block = bytes(data[:7])
        writes.append(block)
        return len(block)

    monkeypatch.setattr(guest.os, "open", open_serial)
    monkeypatch.setattr(guest.os, "fstat", lambda descriptor: SimpleNamespace(st_mode=stat.S_IFCHR | 0o600))
    monkeypatch.setattr(guest.os, "write", short_write)
    monkeypatch.setattr(guest.os, "close", lambda descriptor: closed.append(descriptor))

    guest._write_serial_result(result)

    assert b"".join(writes) == ("\n" + guest._result_line(result) + "\n").encode("ascii")
    assert flags == [guest.os.O_WRONLY | guest.os.O_NOFOLLOW | guest.os.O_NOCTTY | guest.os.O_CLOEXEC]
    assert closed == [71]


def test_serial_result_rejects_invalid_device_and_failed_write(monkeypatch: pytest.MonkeyPatch) -> None:
    result = {
        "schema": guest.RESULT_SCHEMA,
        "cases": [{"name": name, "status": "passed"} for name in guest.CASES],
        "ok": True,
    }
    monkeypatch.setattr(guest.os, "open", lambda *args: 72)
    monkeypatch.setattr(guest.os, "fstat", lambda descriptor: SimpleNamespace(st_mode=stat.S_IFREG | 0o600))
    monkeypatch.setattr(guest.os, "close", lambda descriptor: None)
    with pytest.raises(guest.GuestFailure):
        guest._write_serial_result(result)

    monkeypatch.setattr(guest.os, "fstat", lambda descriptor: SimpleNamespace(st_mode=stat.S_IFCHR | 0o600))
    monkeypatch.setattr(guest.os, "write", lambda descriptor, data: 0)
    with pytest.raises(guest.GuestFailure):
        guest._write_serial_result(result)


def test_main_wires_all_cases_without_guest_commands(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    calls: list[str] = []
    monkeypatch.setattr(guest.os, "geteuid", lambda: 0)
    original_is_dir = guest.Path.is_dir
    monkeypatch.setattr(guest.Path, "is_dir", lambda self: str(self) == "/run/systemd/system" or original_is_dir(self))
    monkeypatch.setattr(guest, "RESULT_PATH", tmp_path / "result.json")
    for name in (
        "verify_payload", "assert_guest_prerequisites", "assert_guest_isolation", "provision_identities",
        "provision_secrets", "_prepare_runtime_paths", "_legacy_rejected_before_state", "_assert_rendered_units",
        "_managed_readiness", "_caddy_and_authelia", "_ingress_checks", "_socket_ownership",
        "_private_denials", "_restart_and_rollback",
    ):
        monkeypatch.setattr(guest, name, lambda *args, _name=name, **kwargs: calls.append(_name))
    monkeypatch.setattr(guest, "_cleanup", lambda: True)
    reported: list[dict[str, object]] = []
    monkeypatch.setattr(guest, "_write_serial_result", lambda result: reported.append(result))

    assert guest.main() == 0
    assert calls == [
        "verify_payload", "assert_guest_prerequisites", "assert_guest_isolation", "provision_identities",
        "provision_secrets", "_prepare_runtime_paths", "_legacy_rejected_before_state", "_assert_rendered_units",
        "_managed_readiness", "_caddy_and_authelia", "_ingress_checks", "_socket_ownership",
        "_private_denials", "_restart_and_rollback",
    ]
    assert capsys.readouterr().out == ""
    assert reported == [{
        "schema": guest.RESULT_SCHEMA,
        "cases": [{"name": name, "status": "passed"} for name in guest.CASES],
        "ok": True,
    }]


def test_write_file_retries_short_writes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "value"
    real_write = guest.os.write
    calls = 0

    def short_write(fd: int, data: bytes | memoryview) -> int:
        nonlocal calls
        calls += 1
        return real_write(fd, data[:1])

    monkeypatch.setattr(guest.os, "write", short_write)
    monkeypatch.setattr(guest, "_owner", lambda *args: None)
    guest._write_file(path, b"three", 0, 0, 0o600)
    assert calls == 5
    assert path.read_bytes() == b"three"


def test_failure_location_never_retains_exception_text_arguments_or_foreign_paths():
    namespace = {}
    exec(compile("def fail():\n    raise PermissionError(13, 'SECRET_MARKER', '/private/SECRET_MARKER')\n", '/private/SECRET_MARKER.py', 'exec'), namespace)
    case = guest._case(guest.CASES[2], namespace['fail'])
    assert case['status'] == 'failed'
    failure = case['failure']
    assert failure['kind'] == 'os' and failure['errno'] == 13
    assert failure['frames'] and all(frame['source'] == 'guest' for frame in failure['frames'])
    assert 'SECRET_MARKER' not in json.dumps(case)
    assert '/private' not in json.dumps(case)


def test_failed_command_retains_only_bounded_returncode_and_source_locations(monkeypatch):
    monkeypatch.setattr(guest.subprocess, 'run', lambda *args, **kwargs: SimpleNamespace(returncode=7))
    case = guest._case(guest.CASES[2], lambda: guest._command(['/private/SECRET_MARKER', 'SECRET_ARGUMENT']))
    assert case['failure']['returncode'] == 7
    assert case['failure']['kind'] == 'fixture'
    assert len(case['failure']['frames']) <= 8
    assert 'SECRET' not in json.dumps(case)


@pytest.mark.parametrize("existing", [False, True])
def test_guest_trust_installs_public_ca_and_verifies_system_bundle(monkeypatch, existing):
    destination = guest.Path("/usr/local/share/ca-certificates/anvil-connect-fixture.crt")
    writes, commands = [], []
    certificate = b"synthetic-public-certificate"

    def metadata(path):
        if path == destination.parent:
            return SimpleNamespace(st_mode=stat.S_IFDIR | 0o755, st_uid=0)
        assert path == destination
        if not existing:
            raise FileNotFoundError
        return SimpleNamespace(st_mode=stat.S_IFREG | 0o644, st_uid=0, st_gid=0, st_nlink=1)

    monkeypatch.setattr(guest.Path, "lstat", metadata)
    monkeypatch.setattr(guest, "_read_regular", lambda path, bound: certificate)
    monkeypatch.setattr(guest, "_write_file", lambda *args: writes.append(args))
    monkeypatch.setattr(guest, "_command", lambda argv, **kwargs: commands.append((argv, kwargs)))
    guest._install_guest_trust()
    assert writes == ([] if existing else [(destination, certificate, 0, 0, 0o644)])
    assert commands == [
        (["/usr/sbin/update-ca-certificates"], {"timeout": 30}),
        (["/usr/bin/openssl", "verify", "-CAfile", "/etc/ssl/certs/ca-certificates.crt",
          "-verify_hostname", "auth.example.test", "/etc/anvil-test/tls/service.pem"], {}),
    ]


def test_guest_trust_refuses_conflicting_existing_certificate(monkeypatch):
    destination = guest.Path("/usr/local/share/ca-certificates/anvil-connect-fixture.crt")
    monkeypatch.setattr(guest.Path, "lstat", lambda path: SimpleNamespace(
        st_mode=(stat.S_IFDIR | 0o755) if path == destination.parent else (stat.S_IFREG | 0o644),
        st_uid=0, st_gid=0, st_nlink=1))
    monkeypatch.setattr(guest, "_read_regular", lambda path, bound: b"foreign" if path == destination else b"fixture")
    monkeypatch.setattr(guest, "_command", lambda *args, **kwargs: pytest.fail("trust update ran after conflict"))
    with pytest.raises(guest.GuestFailure):
        guest._install_guest_trust()
