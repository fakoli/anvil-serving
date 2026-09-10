#!/usr/bin/env python3
"""Offline root-only fixture for the disposable Connect isolation guest.

This is test payload code.  It must be run only by the isolated VM harness and
never performs host discovery, network setup, or operational deployment work.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import stat
import secrets
import socket
import subprocess
import sys
import tempfile
from typing import Any, Callable

PAYLOAD_ROOT = Path("/opt/anvil-test")
RESULT_PATH = Path("/var/lib/anvil-test/isolation-results.json")
PAYLOAD_SCHEMA = "anvil-connect.isolation-guest-payload/v1"
RESULT_SCHEMA = "anvil-connect.isolation-guest/v1"
MARKER = "ANVIL_CONNECT_ISOLATION_GUEST_RESULT"
SERIAL_DEVICE = "/dev/ttyS0"
MAX_RESULT_FRAME = 64 * 1024
MAX_PAYLOAD_FILE = 64 * 1024 * 1024
MAX_PAYLOAD_ENTRIES = 4096
MAX_PAYLOAD_BYTES = 512 * 1024 * 1024
COMMAND_TIMEOUT = 20
_ROLE_TRAVERSABLE_DIRECTORY_MODE = 0o711
SERVICE_UNITS = (
    "anvil-connect-gateway.service",
    "anvil-connect-caddy.service",
    "anvil-connect-authelia.service",
    "anvil-connect-connector-dashboard.service",
)
CASES = (
    "legacy_activation_rejected_pre_state",
    "rendered_units_isolated",
    "managed_gateway_connector_readiness",
    "caddy_tls_authelia_discovery",
    "ingress_peer_denials_then_edge_success",
    "ingress_socket_ownership",
    "role_private_state_and_admin_denials",
    "managed_restart_and_rollback",
)
_IDENTITIES = {
    "gateway": ("acq-gateway", 21001, 21001),
    "edge": ("acq-edge", 21002, 21002),
    "idp": ("acq-idp", 21003, 21003),
    "connector": ("acq-connector", 21004, 21004),
    "client": ("acq-client", 21005, 21005),
    "hostile": ("acq-hostile", 21006, 21006),
}
_INGRESS_GROUP = ("acq-ingress", 21010)
_REQUIRED = frozenset({
    "bin/anvil-connect-ctl", "bin/caddy", "bin/authelia", "bin/wstunnel", "bin/cli-tests",
    "python/anvil_serving/connect/manage.py", "deployment.json",
    "rendered/gateway.json", "rendered/caddy.json", "rendered/authelia/configuration.yml", "rendered/managed.json",
    "rendered/systemd/anvil-connect-gateway.service", "rendered/systemd/anvil-connect-caddy.service",
    "rendered/systemd/anvil-connect-authelia.service", "rendered/systemd/anvil-connect-connector-dashboard.service",
})


class GuestFailure(RuntimeError):
    """A fixture failure whose public result deliberately has no diagnostics."""


def _limits() -> dict[str, int]:
    return {"request_bytes": 1_048_576, "concurrent": 3, "buffer_bytes": 65_536, "idle_seconds": 30, "duration_seconds": 90}


def _rule(identifier: str, host: str, access: str, methods: list[str], path_prefix: str, native_auth: str) -> dict[str, Any]:
    return {"id": identifier, "host": host, "access": access, "methods": methods, "path_prefix": path_prefix, "native_auth": native_auth, "limits": _limits()}


def build_manifest() -> dict[str, Any]:
    """Return the closed, public synthetic isolated declaration for the host ISO."""
    browser = _rule("dashboard", "dash.example.test", "browser", ["GET", "POST"], "/", "passthrough")
    api = _rule("dashboard-api", "api.example.test", "api", ["GET"], "/v1", "delegate-bearer")
    return {
        "schema": "anvil-connect.deployment/v1",
        "binary": "/opt/anvil-test/bin/anvil-connect-ctl",
        "components": {"caddy": "/opt/anvil-test/bin/caddy", "authelia": "/opt/anvil-test/bin/authelia"},
        "config_root": "/etc/anvil-test/rendered",
        "environment_files": {
            "gateway": "/etc/anvil-test/secrets/gateway.env",
            "connectors": {"dashboard": "/etc/anvil-test/secrets/connectors/dashboard.env"},
            "clients": {"dashboard-api": "/etc/anvil-test/secrets/clients/dashboard-api.env"},
        },
        "service_identities": {
            "gateway": {"uid": 21001, "gid": 21001}, "edge": {"uid": 21002, "gid": 21002},
            "idp": {"uid": 21003, "gid": 21003}, "connectors": {"dashboard": {"uid": 21004, "gid": 21004}},
            "clients": {"dashboard-api": {"uid": 21005, "gid": 21005}},
            "ingress": {"group_id": 21010, "directory": "/run/anvil-test/ingress"},
        },
        "gateway": {
            "schema": "anvil-connect.gateway-runtime/v1", "control_host": "control.example.test",
            "tunnel_host": "tunnel.example.test", "state_directory": "/var/lib/anvil-test/gateway",
            "tunnel_binary": "/opt/anvil-test/bin/wstunnel", "tunnel_listen": "127.0.0.1:27181",
            "ingress": {"directory": "/run/anvil-test/ingress", "gateway_uid": 21001, "edge_uid": 21002, "group_id": 21010},
            "oidc": {"issuer": "https://auth.example.test", "client_id": "anvil-connect-guest", "client_secret_env": "ANVIL_CONNECT_OIDC_CLIENT_SECRET"},
            "gateway": {"schema": "anvil-connect.gateway/v1", "listen": "127.0.0.1:27180", "max_concurrent": 16,
                        "resources": [{"rule": browser, "connector": "dashboard", "tunnel_address": "127.0.0.1:27101"},
                                      {"rule": api, "connector": "dashboard", "tunnel_address": "127.0.0.1:27102"}]},
        },
        "connectors": [{
            "schema": "anvil-connect.connector-runtime/v1", "id": "dashboard", "control_host": "control.example.test", "tunnel_host": "tunnel.example.test",
            "state_directory": "/var/lib/anvil-test/connectors/dashboard", "tunnel_binary": "/opt/anvil-test/bin/wstunnel", "public_trust_file": "/etc/anvil-test/trust/public.pem", "http_proxy_url": "",
            "resources": [
                {"reverse_address": "127.0.0.1:27101", "envelope": {"rule": browser, "listen": "127.0.0.1:28181", "origin_url": "http://127.0.0.1:28080", "token_env": ""}},
                {"reverse_address": "127.0.0.1:27102", "envelope": {"rule": api, "listen": "127.0.0.1:28182", "origin_url": "http://127.0.0.1:28080", "token_env": "ANVIL_CONNECT_DASHBOARD_API_TOKEN"}},
            ],
        }],
        "clients": [{"schema": "anvil-connect.client-runtime/v1", "listen": "127.0.0.1:28190", "local_key_env": "ANVIL_CONNECT_DASHBOARD_LOCAL_KEY", "remote_key_env": "ANVIL_CONNECT_DASHBOARD_REMOTE_KEY", "rule": api}],
        "caddy": {"service_name": "anvil-connect-caddy", "state_directory": "/var/lib/anvil-test/caddy", "tls": {"mode": "provided", "certificate_file": "/etc/anvil-test/tls/service.pem", "key_file": "/etc/anvil-test/tls/service-key.pem"}},
        "authelia": {
            "service_name": "anvil-connect-authelia", "host": "auth.example.test", "listen": "127.0.0.1:29091", "state_directory": "/var/lib/anvil-test/authelia", "users_file": "/etc/anvil-test/users.yml",
            "client_secret_file": "/etc/anvil-test/secrets/oidc-client-secret-hash", "session_secret_file": "/etc/anvil-test/secrets/authelia-session", "storage_encryption_key_file": "/etc/anvil-test/secrets/authelia-storage",
            "identity_validation_secret_file": "/etc/anvil-test/secrets/authelia-identity-validation", "oidc_hmac_secret_file": "/etc/anvil-test/secrets/oidc-hmac", "oidc_rsa_private_key_file": "/etc/anvil-test/secrets/oidc-rs256-private-key",
        },
    }


def _safe_relative(name: Any) -> bool:
    path = Path(name) if isinstance(name, str) else Path("/")
    return isinstance(name, str) and bool(name) and not path.is_absolute() and ".." not in path.parts and all(part not in {"", "."} for part in path.parts)


def _digest(path: Path) -> str:
    descriptor: int | None = None
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_PAYLOAD_FILE:
            raise OSError
        digest = hashlib.sha256()
        total = 0
        while total <= MAX_PAYLOAD_FILE:
            block = os.read(descriptor, min(64 * 1024, MAX_PAYLOAD_FILE + 1 - total))
            if not block:
                return digest.hexdigest()
            total += len(block)
            digest.update(block)
        raise OSError
    except OSError as exc:
        raise GuestFailure from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _payload_file_names(root: Path) -> set[str]:
    """Enumerate a bounded, all-regular payload tree without following links."""
    names: set[str] = set()
    pending = [root]
    total = 0
    while pending:
        directory = pending.pop()
        try:
            entries = list(os.scandir(directory))
        except OSError as exc:
            raise GuestFailure from exc
        for entry in entries:
            try:
                info = entry.stat(follow_symlinks=False)
            except OSError as exc:
                raise GuestFailure from exc
            if stat.S_ISLNK(info.st_mode):
                raise GuestFailure
            path = Path(entry.path)
            if stat.S_ISDIR(info.st_mode):
                pending.append(path)
                continue
            if not stat.S_ISREG(info.st_mode):
                raise GuestFailure
            name = str(path.relative_to(root))
            if name == "payload.json" and directory == root:
                continue
            if not _safe_relative(name) or info.st_size > MAX_PAYLOAD_FILE:
                raise GuestFailure
            total += info.st_size
            names.add(name)
            if len(names) > MAX_PAYLOAD_ENTRIES or total > MAX_PAYLOAD_BYTES:
                raise GuestFailure
    return names


def verify_payload(root: Path = PAYLOAD_ROOT) -> dict[str, str]:
    """Verify every mounted payload byte before importing or executing it."""
    try:
        root_info = root.lstat()
        if root.is_symlink() or not stat.S_ISDIR(root_info.st_mode):
            raise OSError
        raw = _read_regular(root / "payload.json", 256 * 1024)
        payload = json.loads(raw.decode("utf-8"))
        files = payload["files"]
        source = payload["source"]
        build = payload["build"]
        if set(payload) != {"schema", "source", "build", "files"} or payload["schema"] != PAYLOAD_SCHEMA or not isinstance(source, dict) or not isinstance(build, dict) or not isinstance(files, dict):
            raise ValueError
        if set(source) != {"revision", "dirty"} or not isinstance(source["revision"], str) or len(source["revision"]) != 40 or any(character not in "0123456789abcdef" for character in source["revision"]) or source["dirty"] is not False:
            raise ValueError
        if build != {"schema": "anvil-connect.isolation-guest-build/v1", "platform": "linux/amd64"}:
            raise ValueError
        if not _REQUIRED <= set(files) or len(files) > MAX_PAYLOAD_ENTRIES or any(not _safe_relative(name) or not isinstance(digest, str) or len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest) for name, digest in files.items()):
            raise ValueError
        if _payload_file_names(root) != set(files):
            raise ValueError
        for name, digest in files.items():
            if _digest(root / name) != digest:
                raise ValueError
        return dict(files)
    except (OSError, UnicodeError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise GuestFailure from exc


def _command(argv: list[str], *, timeout: int = COMMAND_TIMEOUT, check: bool = True) -> int:
    try:
        result = subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=timeout, check=False, env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C", "LC_ALL": "C", "HOME": "/root"})
    except (OSError, subprocess.SubprocessError) as exc:
        raise GuestFailure from exc
    if check and result.returncode != 0:
        raise GuestFailure
    return result.returncode


def _provision_identity(name: str, uid: int, gid: int, *, ingress: bool = False) -> None:
    _command(["/usr/sbin/groupadd", "--gid", str(gid), name])
    args = ["/usr/sbin/useradd", "--uid", str(uid), "--gid", str(gid), "--shell", "/usr/sbin/nologin", "--no-create-home", "--password", "!", name]
    if ingress:
        args.extend(["--groups", _INGRESS_GROUP[0]])
    _command(args)


def assert_guest_prerequisites() -> None:
    """Fail before provisioning when the closed offline guest base is incomplete."""
    for value in ("/usr/sbin/groupadd", "/usr/sbin/useradd", "/usr/bin/openssl", "/usr/bin/systemctl", "/usr/bin/curl", "/usr/bin/setpriv", "/usr/bin/python3"):
        try:
            if not os.access(value, os.X_OK):
                raise OSError
        except OSError as exc:
            raise GuestFailure from exc


def assert_guest_isolation() -> None:
    interfaces = [path.name for path in Path("/sys/class/net").iterdir() if path.name != "lo"]
    if interfaces:
        raise GuestFailure
    for unit in ("ssh.service", "ssh.socket", "sshd.service", "sshd.socket"):
        if _command(["/usr/bin/systemctl", "is-active", "--quiet", unit], check=False) == 0:
            raise GuestFailure


def provision_identities() -> None:
    _command(["/usr/sbin/groupadd", "--gid", str(_INGRESS_GROUP[1]), _INGRESS_GROUP[0]])
    for role, (name, uid, gid) in _IDENTITIES.items():
        _provision_identity(name, uid, gid, ingress=role == "edge")


def _owner(path: Path, uid: int, gid: int, mode: int) -> None:
    os.chown(path, uid, gid)
    os.chmod(path, mode)


def _mkdir(path: Path, uid: int, gid: int, mode: int) -> None:
    path.mkdir(mode=mode, parents=False, exist_ok=False)
    _owner(path, uid, gid, mode)


def _write_file(path: Path, data: bytes, uid: int, gid: int, mode: int) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
    try:
        pending = memoryview(data)
        while pending:
            written = os.write(descriptor, pending)
            if written <= 0:
                raise GuestFailure
            pending = pending[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    _owner(path, uid, gid, mode)


def _capture(argv: list[str], *, timeout: int = COMMAND_TIMEOUT, maximum: int = 64 * 1024) -> bytes:
    """Capture one bounded trusted-tool result without ever printing it."""
    temporary: Path | None = None
    try:
        fd, name = tempfile.mkstemp(prefix="acq-output-", dir="/run")
        temporary = Path(name)
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb", closefd=True) as output:
            try:
                result = subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.DEVNULL,
                                        timeout=timeout, check=False,
                                        env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C", "LC_ALL": "C", "HOME": "/root"})
            except (OSError, subprocess.SubprocessError) as exc:
                raise GuestFailure from exc
        if result.returncode != 0:
            raise GuestFailure
        info = temporary.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_size > maximum:
            raise GuestFailure
        return _read_regular(temporary, maximum)
    finally:
        if temporary is not None:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass


def _read_regular(path: Path, maximum: int) -> bytes:
    descriptor: int | None = None
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_size > maximum:
            raise GuestFailure
        data = bytearray()
        while len(data) <= maximum:
            block = os.read(descriptor, min(64 * 1024, maximum + 1 - len(data)))
            if not block:
                return bytes(data)
            data.extend(block)
        raise GuestFailure
    except OSError as exc:
        raise GuestFailure from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _parse_hash_pair(raw: bytes) -> tuple[str, str]:
    try:
        values = dict(line.split(": ", 1) for line in raw.decode("utf-8").splitlines() if ": " in line)
        password, digest = values["Random Password"], values["Digest"]
    except (UnicodeDecodeError, KeyError, ValueError) as exc:
        raise GuestFailure from exc
    if len(password) != 40 or not password.isascii() or not digest.startswith("$argon2") or len(digest) > 1024:
        raise GuestFailure
    return password, digest


def _hash_pair() -> tuple[str, str]:
    """Use the pinned Authelia CLI so plaintext and Argon2 digest are paired."""
    return _parse_hash_pair(_capture(["/opt/anvil-test/bin/authelia", "crypto", "hash", "generate", "argon2", "--random", "--random.length", "40"]))


def _manager() -> Any:
    source = PAYLOAD_ROOT / "python"
    if not source.is_dir() or source.is_symlink():
        raise GuestFailure
    source_text = str(source)
    if source_text not in sys.path:
        sys.path.insert(0, source_text)
    try:
        from anvil_serving.connect import manage
    except (ImportError, ValueError) as exc:
        raise GuestFailure from exc
    return manage


def _prepare_runtime_paths() -> None:
    base = Path("/var/lib/anvil-test")
    base.mkdir(mode=0o711, parents=True, exist_ok=True)
    _owner(base, 0, 0, 0o711)
    _mkdir(base / "gateway", 21001, 21001, 0o700)
    _mkdir(base / "caddy", 21002, 21002, 0o700)
    _mkdir(base / "authelia", 21003, 21003, 0o700)
    connectors = base / "connectors"
    _mkdir(connectors, 0, 0, 0o711)
    _mkdir(connectors / "dashboard", 21004, 21004, 0o700)
    ingress_parent = Path("/run/anvil-test")
    ingress_parent.mkdir(mode=0o755, parents=True, exist_ok=True)
    _owner(ingress_parent, 0, 0, 0o755)
    _mkdir(ingress_parent / "ingress", 21001, 21010, 0o2710)


def _provision_hosts() -> None:
    hosts = Path("/etc/hosts")
    line = "127.0.0.1 auth.example.test dash.example.test api.example.test control.example.test tunnel.example.test\n"
    try:
        current = _read_regular(hosts, 64 * 1024).decode("utf-8")
    except (GuestFailure, UnicodeDecodeError) as exc:
        raise GuestFailure from exc
    if line not in current:
        descriptor = os.open(hosts, os.O_WRONLY | os.O_APPEND | os.O_NOFOLLOW)
        try:
            if os.write(descriptor, line.encode("ascii")) != len(line):
                raise GuestFailure
            os.fsync(descriptor)
        except OSError as exc:
            raise GuestFailure from exc
        finally:
            os.close(descriptor)


def _provision_certificate(tls: Path) -> None:
    root_config = tls / "root.cnf"
    leaf_config = tls / "leaf.cnf"
    _write_file(root_config, b"[req]\ndistinguished_name=dn\nx509_extensions=v3\nprompt=no\n[dn]\nCN=Anvil Connect Guest CA\n[v3]\nbasicConstraints=critical,CA:true\nkeyUsage=critical,keyCertSign,digitalSignature\n", 0, 0, 0o600)
    _write_file(leaf_config, b"[req]\ndistinguished_name=dn\nprompt=no\n[dn]\nCN=auth.example.test\n[v3]\nsubjectAltName=DNS:auth.example.test,DNS:dash.example.test,DNS:api.example.test,DNS:control.example.test,DNS:tunnel.example.test\nbasicConstraints=critical,CA:false\nkeyUsage=critical,digitalSignature,keyEncipherment\nextendedKeyUsage=serverAuth\n", 0, 0, 0o600)
    _command(["/usr/bin/openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1", "-config", str(root_config), "-keyout", str(tls / "root-key.pem"), "-out", str(tls / "root.pem")], timeout=20)
    _command(["/usr/bin/openssl", "req", "-newkey", "rsa:2048", "-nodes", "-config", str(leaf_config), "-keyout", str(tls / "service-key.pem"), "-out", str(tls / "service.csr")], timeout=20)
    _command(["/usr/bin/openssl", "x509", "-req", "-days", "1", "-in", str(tls / "service.csr"), "-CA", str(tls / "root.pem"), "-CAkey", str(tls / "root-key.pem"), "-CAcreateserial", "-extfile", str(leaf_config), "-extensions", "v3", "-out", str(tls / "service.pem")], timeout=20)
    _owner(tls / "root-key.pem", 0, 0, 0o600)
    _owner(tls / "root.pem", 0, 0, 0o644)
    _owner(tls / "service.pem", 0, 0, 0o644)
    _owner(tls / "service-key.pem", 0, 21002, 0o640)
    for path in (root_config, leaf_config, tls / "service.csr", tls / "root.srl"):
        path.unlink()


def provision_secrets() -> None:
    """Create valid, role-readable synthetic state only inside the guest."""
    root = Path("/etc/anvil-test")
    root.mkdir(mode=0o711, parents=True, exist_ok=True)
    _owner(root, 0, 0, 0o711)
    secrets_root, connector_root, client_root, tls, trust = (root / "secrets", root / "secrets/connectors", root / "secrets/clients", root / "tls", root / "trust")
    _mkdir(secrets_root, 0, 0, 0o711)
    _mkdir(connector_root, 0, 0, 0o711)
    _mkdir(client_root, 0, 0, 0o711)
    _mkdir(tls, 0, 0, _ROLE_TRAVERSABLE_DIRECTORY_MODE)
    _mkdir(trust, 0, 0, _ROLE_TRAVERSABLE_DIRECTORY_MODE)
    client_secret, client_digest = _hash_pair()
    _, user_digest = _hash_pair()
    _write_file(root / "users.yml", ("users:\n  fixture-guest:\n    displayname: Fixture Guest\n    password: " + json.dumps(user_digest) + "\n    email: fixture-guest@example.test\n    groups: []\n").encode("utf-8"), 0, 21003, 0o640)
    _write_file(secrets_root / "oidc-client-secret-hash", (client_digest + "\n").encode("ascii"), 0, 21003, 0o640)
    for name in ("authelia-session", "authelia-storage", "authelia-identity-validation", "oidc-hmac"):
        _write_file(secrets_root / name, (secrets.token_urlsafe(48) + "\n").encode("ascii"), 0, 21003, 0o640)
    rsa_path = secrets_root / "oidc-rs256-private-key"
    _command(["/usr/bin/openssl", "genpkey", "-algorithm", "RSA", "-pkeyopt", "rsa_keygen_bits:2048", "-out", str(rsa_path)], timeout=20)
    _owner(rsa_path, 0, 21003, 0o640)
    _provision_certificate(tls)
    _write_file(trust / "public.pem", _read_regular(tls / "root.pem", 64 * 1024), 0, 21004, 0o640)
    _write_file(secrets_root / "gateway.env", ("ANVIL_CONNECT_OIDC_CLIENT_SECRET=" + client_secret + "\n").encode("ascii"), 0, 21001, 0o640)
    _write_file(connector_root / "dashboard.env", ("ANVIL_CONNECT_DASHBOARD_API_TOKEN=" + secrets.token_urlsafe(32) + "\n").encode("ascii"), 0, 21004, 0o640)
    _write_file(client_root / "dashboard-api.env", ("ANVIL_CONNECT_DASHBOARD_LOCAL_KEY=" + secrets.token_urlsafe(32) + "\nANVIL_CONNECT_DASHBOARD_REMOTE_KEY=" + secrets.token_urlsafe(32) + "\n").encode("ascii"), 0, 21005, 0o640)
    _provision_hosts()


def _manager_target(value: str) -> Any:
    return _manager().Target.parse(value)


def _write_request(name: str, request: dict[str, Any]) -> Path:
    directory = Path("/var/lib/anvil-test/gateway/requests")
    if not directory.exists():
        _mkdir(directory, 21001, 21001, 0o700)
    path = directory / name
    _write_file(path, (json.dumps(request, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8"), 21001, 21001, 0o600)
    return path


def _legacy_rejected_before_state(_: Path) -> None:
    legacy = build_manifest()
    legacy["service_user"] = "acq-gateway"
    del legacy["service_identities"]
    del legacy["gateway"]["ingress"]
    legacy["gateway"]["state_directory"] = "/var/lib/anvil-test/legacy-gateway"
    with tempfile.TemporaryDirectory(prefix="legacy-", dir="/run") as directory:
        path = Path(directory) / "deployment.json"
        path.write_text(json.dumps(legacy), encoding="utf-8")
        manager = _manager()
        normalized = manager.read_manifest(path)
        if "service_identities" in normalized:
            raise GuestFailure
        try:
            manager.native_init(path, _manager_target("gateway"), apply=True)
        except Exception as exc:
            if "isolated service identities are required" not in str(exc):
                raise GuestFailure from exc
        else:
            raise GuestFailure
    if Path("/var/lib/anvil-test/legacy-gateway").exists():
        raise GuestFailure


def _installed_units_match_payload() -> None:
    rendered = PAYLOAD_ROOT / "rendered"
    if _read_regular(Path("/etc/anvil-test/rendered/managed.json"), MAX_PAYLOAD_FILE) != _read_regular(rendered / "managed.json", MAX_PAYLOAD_FILE):
        raise GuestFailure
    root = rendered / "systemd"
    for unit in SERVICE_UNITS:
        expected = _read_regular(root / unit, MAX_PAYLOAD_FILE)
        actual_path = Path("/etc/systemd/system") / unit
        if _read_regular(actual_path, MAX_PAYLOAD_FILE) != expected:
            raise GuestFailure
        raw = _capture(["/usr/bin/systemctl", "show", "--property=FragmentPath,DropInPaths", unit], timeout=10, maximum=4096)
        fields = dict(line.split("=", 1) for line in raw.decode("utf-8").splitlines() if "=" in line)
        if fields != {"FragmentPath": str(actual_path), "DropInPaths": ""}:
            raise GuestFailure


def _assert_rendered_units(root: Path) -> None:
    expected = {"gateway": ("21001", "21001"), "caddy": ("21002", "21002"), "authelia": ("21003", "21003"), "connector-dashboard": ("21004", "21004")}
    common = ("UMask=0077", "NoNewPrivileges=true", "ProtectSystem=strict", "ProtectHome=true", "PrivateTmp=true", "RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6")
    for role, (uid, gid) in expected.items():
        text = _read_regular(root / f"systemd/anvil-connect-{role}.service", MAX_PAYLOAD_FILE).decode("utf-8")
        if f"User={uid}" not in text or f"Group={gid}" not in text or any(value not in text for value in common):
            raise GuestFailure
    edge = _read_regular(root / "systemd/anvil-connect-caddy.service", MAX_PAYLOAD_FILE).decode("utf-8")
    if "SupplementaryGroups=21010" not in edge or "CAP_NET_BIND_SERVICE" not in edge:
        raise GuestFailure


def _admin(manifest: Path, request: dict[str, Any], *, output: Path | None = None) -> None:
    request_path = _write_request("request-" + secrets.token_hex(8) + ".json", request)
    _manager().admin(manifest, request_path=request_path, output_path=output, apply=True)


def _initialize_authelia_storage() -> None:
    _command([
        "/usr/bin/setpriv", "--reuid=21003", "--regid=21003", "--clear-groups", "/usr/bin/env",
        "HOME=/var/lib/anvil-test/authelia", "XDG_CONFIG_HOME=/var/lib/anvil-test/authelia",
        "/opt/anvil-test/bin/authelia", "--config", "/opt/anvil-test/rendered/authelia/configuration.yml",
        "--config.experimental.filters", "template", "storage", "migrate", "up",
    ], timeout=30)


def _role_open(path: Path, uid: int) -> None:
    """Prove one declared role can open a required file without reading it."""
    action = "import os; f=os.open(" + repr(str(path)) + ", os.O_RDONLY); os.close(f)"
    _command([
        "/usr/bin/setpriv", f"--reuid={uid}", f"--regid={uid}", "--clear-groups",
        "/usr/bin/python3", "-c", action,
    ])


def _role_denied(path: Path, uid: int) -> None:
    """Prove one non-owner role receives EACCES without reading the file."""
    action = (
        "import errno,os,sys\ntry: f=os.open(" + repr(str(path)) + ", os.O_RDONLY); os.close(f)"
        "\nexcept OSError as e: raise SystemExit(0 if e.errno == errno.EACCES else 1)\nraise SystemExit(1)"
    )
    _command([
        "/usr/bin/setpriv", f"--reuid={uid}", f"--regid={uid}", "--clear-groups",
        "/usr/bin/python3", "-c", action,
    ])


def _role_read_probes() -> None:
    _role_open(Path("/etc/anvil-test/tls/service-key.pem"), 21002)
    _role_open(Path("/etc/anvil-test/trust/public.pem"), 21004)


def _managed_readiness(manifest: Path) -> None:
    _role_read_probes()
    manager = _manager()
    gateway = _manager_target("gateway")
    connector = _manager_target("connector:dashboard")
    _initialize_authelia_storage()
    manager.native_init(manifest, gateway, apply=True)
    manager.up_many(manifest, (gateway,), apply=True)
    _admin(manifest, {"operation": "principal-set", "principal": "fixture-api", "grants": [{"resource": "dashboard-api", "methods": ["GET"]}], "disabled": False})
    invitation = Path("/var/lib/anvil-test/gateway/requests/invitation.json")
    _admin(manifest, {"operation": "invite", "installation": "dashboard", "role": "connector", "resources": ["dashboard", "dashboard-api"], "lifetime_seconds": 300}, output=invitation)
    bundle = Path("/var/lib/anvil-test/connectors/dashboard/invitation.json")
    _write_file(bundle, _read_regular(invitation, 64 * 1024), 21004, 21004, 0o600)
    manager.native_init(manifest, connector, bundle=bundle, apply=True)
    identity = manager.identity(manifest, connector)["identity"]
    _admin(manifest, {"operation": "approve", "installation": "dashboard", "fingerprint": identity["fingerprint"]})
    manager.up_many(manifest, (connector,), apply=True)
    _command(["/usr/bin/systemctl", "is-active", "--quiet", *SERVICE_UNITS], timeout=10)
    _installed_units_match_payload()


def _caddy_and_authelia() -> None:
    common = ["/usr/bin/curl", "--fail", "--silent", "--show-error", "--cacert", "/etc/anvil-test/tls/root.pem"]
    _command([*common, "https://auth.example.test/.well-known/openid-configuration"], timeout=10)
    # The browser route redirects to Authelia; a successful non-followed 302
    # proves the edge TLS listener and ingress route without claiming login.
    _command([*common, "--output", "/dev/null", "https://dash.example.test/_anvil-connect/login"], timeout=10)


def _managed_authority_ready(manifest: Path) -> None:
    _admin(manifest, {"operation": "status"})
    identity = _manager().identity(manifest, _manager_target("connector:dashboard"))["identity"]
    if identity["status"] != "enrolled":
        raise GuestFailure


def _ingress_checks() -> None:
    hostile = "import socket; s=socket.socket(socket.AF_UNIX); s.connect('/run/anvil-test/ingress/ingress.sock'); s.sendall(b'GET / HTTP/1.1\\r\\nHost: dash.example.test\\r\\n\\r\\n'); raise SystemExit(0 if not s.recv(1) else 1)"
    for _ in range(2):
        _command(["/usr/bin/setpriv", "--reuid=21006", "--regid=21006", "--groups=21010", "/usr/bin/python3", "-c", hostile])
    edge = "import socket; s=socket.socket(socket.AF_UNIX); s.connect('/run/anvil-test/ingress/ingress.sock'); s.sendall(b'GET /_anvil-connect/login HTTP/1.1\\r\\nHost: dash.example.test\\r\\n\\r\\n'); raise SystemExit(0 if s.recv(1) else 1)"
    _command(["/usr/bin/setpriv", "--reuid=21002", "--regid=21002", "--groups=21010", "/usr/bin/python3", "-c", edge])


def _socket_ownership() -> None:
    info = Path("/run/anvil-test/ingress/ingress.sock").stat()
    if not stat.S_ISSOCK(info.st_mode) or info.st_uid != 21001 or info.st_gid != 21010 or stat.S_IMODE(info.st_mode) != 0o660:
        raise GuestFailure


def _private_denials() -> None:
    private_files = (
        (Path("/var/lib/anvil-test/gateway/authorities.json"), 21001),
        (Path("/etc/anvil-test/secrets/oidc-rs256-private-key"), 21003),
        (Path("/etc/anvil-test/tls/service-key.pem"), 21002),
    )
    roles = (21001, 21002, 21003, 21004, 21005)
    for path, owner in private_files:
        _role_open(path, owner)
        for uid in roles:
            if uid != owner:
                _role_denied(path, uid)
    admin_socket = Path("/var/lib/anvil-test/gateway/admin.sock")
    info = admin_socket.stat()
    if not stat.S_ISSOCK(info.st_mode) or info.st_uid != 21001 or stat.S_IMODE(info.st_mode) != 0o600:
        raise GuestFailure
    owner_socket = "import socket; s=socket.socket(socket.AF_UNIX); s.connect(" + repr(str(admin_socket)) + "); s.close()"
    _command(["/usr/bin/setpriv", "--reuid=21001", "--regid=21001", "--clear-groups", "/usr/bin/python3", "-c", owner_socket])
    denied_socket = "import errno,socket\ns=socket.socket(socket.AF_UNIX)\ntry: s.connect(" + repr(str(admin_socket)) + ")\nexcept OSError as e: raise SystemExit(0 if e.errno == errno.EACCES else 1)\nraise SystemExit(1)"
    for uid in (21002, 21003, 21004, 21005):
        _command(["/usr/bin/setpriv", f"--reuid={uid}", f"--regid={uid}", "--clear-groups", "/usr/bin/python3", "-c", denied_socket])


def _bound_loopback(port: int) -> tuple[subprocess.Popen[bytes], socket.socket]:
    parent, child = socket.socketpair()
    script = "import os,socket,sys; s=socket.socket(); s.bind(('127.0.0.1',int(sys.argv[1]))); os.write(int(sys.argv[2]),b'1'); sys.stdin.buffer.read(1); s.close()"
    try:
        process = subprocess.Popen(["/usr/bin/python3", "-c", script, str(port), str(child.fileno())], stdin=subprocess.PIPE,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True, pass_fds=(child.fileno(),),
                                   env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C", "LC_ALL": "C", "HOME": "/root"})
        child.close()
        parent.settimeout(2)
        if parent.recv(1) != b"1":
            raise GuestFailure
        return process, parent
    except (OSError, subprocess.SubprocessError) as exc:
        parent.close()
        child.close()
        raise GuestFailure from exc


def _release_loopback(process: subprocess.Popen[bytes], channel: socket.socket) -> None:
    try:
        if process.stdin is not None:
            process.stdin.close()
        process.wait(timeout=5)
    except (OSError, subprocess.SubprocessError):
        process.kill()
        try:
            process.wait(timeout=5)
        except (OSError, subprocess.SubprocessError) as exc:
            raise GuestFailure from exc
    finally:
        channel.close()


def _rotate_tls_leaf() -> None:
    tls = Path("/etc/anvil-test/tls")
    config, key, csr, leaf = tls / "rotate.cnf", tls / "next-key.pem", tls / "next.csr", tls / "next.pem"
    _write_file(config, b"[req]\ndistinguished_name=dn\nprompt=no\n[dn]\nCN=auth.example.test\n[v3]\nsubjectAltName=DNS:auth.example.test,DNS:dash.example.test,DNS:api.example.test,DNS:control.example.test,DNS:tunnel.example.test\nbasicConstraints=critical,CA:false\nkeyUsage=critical,digitalSignature,keyEncipherment\nextendedKeyUsage=serverAuth\n", 0, 0, 0o600)
    try:
        _command(["/usr/bin/openssl", "req", "-newkey", "rsa:2048", "-nodes", "-config", str(config), "-keyout", str(key), "-out", str(csr)], timeout=20)
        _command(["/usr/bin/openssl", "x509", "-req", "-days", "1", "-in", str(csr), "-CA", str(tls / "root.pem"), "-CAkey", str(tls / "root-key.pem"), "-CAcreateserial", "-extfile", str(config), "-extensions", "v3", "-out", str(leaf)], timeout=20)
        _owner(key, 0, 21002, 0o640)
        _owner(leaf, 0, 0, 0o644)
        os.replace(key, tls / "service-key.pem")
        os.replace(leaf, tls / "service.pem")
    finally:
        for path in (config, csr, tls / "root.srl"):
            try:
                path.unlink()
            except FileNotFoundError:
                pass


def _restart_and_rollback(manifest: Path) -> None:
    manager = _manager()
    targets = (_manager_target("gateway"), _manager_target("connector:dashboard"))
    _rotate_tls_leaf()
    manager.up_many(manifest, targets, apply=True)
    _command(["/usr/bin/systemctl", "is-active", "--quiet", *SERVICE_UNITS], timeout=10)
    _caddy_and_authelia()
    before = {unit: _capture(["/usr/bin/systemctl", "show", "--property=ActiveState,FragmentPath", unit], maximum=4096) for unit in SERVICE_UNITS}
    # Keep an unrelated loopback listener open while a valid, new gateway
    # generation is rendered. Preflight succeeds; activation fails only after
    # manager publication/restart reaches gateway bind, so rollback must restore
    # the prior generation and service set.
    rejected = build_manifest()
    rejected["gateway"]["gateway"]["listen"] = "127.0.0.1:27179"
    process, channel = _bound_loopback(27179)
    try:
        with tempfile.TemporaryDirectory(prefix="failed-activation-", dir="/run") as directory:
            bad = Path(directory) / "deployment.json"
            bad.write_text(json.dumps(rejected), encoding="utf-8")
            try:
                manager.up_many(bad, targets, apply=True)
            except Exception:
                pass
            else:
                raise GuestFailure
    finally:
        _release_loopback(process, channel)
    _command(["/usr/bin/systemctl", "is-active", "--quiet", *SERVICE_UNITS], timeout=10)
    _managed_authority_ready(manifest)
    _caddy_and_authelia()
    if before != {unit: _capture(["/usr/bin/systemctl", "show", "--property=ActiveState,FragmentPath", unit], maximum=4096) for unit in SERVICE_UNITS}:
        raise GuestFailure
    _installed_units_match_payload()
    if not _cleanup():
        raise GuestFailure
    _run_cli_cleanup_regression()


def _run_cli_cleanup_regression() -> None:
    policy = Path("/var/lib/anvil-test/gateway/cli-ingress-policy.json")
    _write_file(policy, b'{"gateway_uid":21001,"edge_uid":21002,"group_id":21010,"directory":"/run/anvil-test/ingress"}\n', 21001, 21001, 0o600)
    output = _capture([
        "/usr/bin/setpriv", "--reuid=21001", "--regid=21001", "--clear-groups", "/usr/bin/env",
        "ANVIL_CONNECT_TEST_INGRESS_POLICY_FILE=" + str(policy),
        "ANVIL_CONNECT_WSTUNNEL=/opt/anvil-test/bin/wstunnel",
        "/opt/anvil-test/bin/cli-tests", "-test.run", "^TestGatewayStoppedStatusFollowsOwnedCleanup$", "-test.v", "-test.timeout", "30s",
    ], timeout=40, maximum=32 * 1024)
    if b"--- PASS: TestGatewayStoppedStatusFollowsOwnedCleanup" not in output or b"SKIP" in output:
        raise GuestFailure


def _cleanup() -> bool:
    try:
        _command(["/usr/bin/systemctl", "stop", *SERVICE_UNITS], timeout=30, check=False)
        for unit in SERVICE_UNITS:
            if _command(["/usr/bin/systemctl", "is-active", "--quiet", unit], check=False) == 0:
                return False
        return True
    except Exception:
        return False


def _case(name: str, operation: Callable[[], None]) -> dict[str, str]:
    try:
        operation()
        return {"name": name, "status": "passed"}
    except Exception:
        return {"name": name, "status": "failed"}


def _write_result(cases: list[dict[str, str]]) -> dict[str, Any]:
    if tuple(item.get("name") for item in cases) != CASES or any(item.get("status") not in {"passed", "failed"} or set(item) != {"name", "status"} for item in cases):
        raise GuestFailure
    RESULT_PATH.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    data = {"schema": RESULT_SCHEMA, "cases": cases, "ok": all(item["status"] == "passed" for item in cases)}
    RESULT_PATH.write_text(json.dumps(data, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
    os.chmod(RESULT_PATH, 0o600)
    return data


def _result_line(result: dict[str, Any]) -> str:
    return MARKER + " " + json.dumps(result, sort_keys=True, separators=(",", ":"))


def _write_serial_result(result: dict[str, Any]) -> None:
    """Write one bounded canonical result frame directly to the guest serial port."""
    try:
        frame = ("\n" + _result_line(result) + "\n").encode("ascii")
    except UnicodeEncodeError as exc:
        raise GuestFailure from exc
    if not 0 < len(frame) <= MAX_RESULT_FRAME:
        raise GuestFailure
    descriptor: int | None = None
    try:
        descriptor = os.open(
            SERIAL_DEVICE,
            os.O_WRONLY | os.O_NOFOLLOW | os.O_NOCTTY | os.O_CLOEXEC,
        )
        if not stat.S_ISCHR(os.fstat(descriptor).st_mode):
            raise GuestFailure
        pending = memoryview(frame)
        while pending:
            written = os.write(descriptor, pending)
            if written <= 0:
                raise GuestFailure
            pending = pending[written:]
    except OSError as exc:
        raise GuestFailure from exc
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass


def main() -> int:
    if os.geteuid() != 0 or not Path("/run/systemd/system").is_dir():
        return 1
    cases: list[dict[str, str]] = []
    try:
        verify_payload()
        assert_guest_prerequisites()
        assert_guest_isolation()
        provision_identities()
        provision_secrets()
        _prepare_runtime_paths()
        manifest = PAYLOAD_ROOT / "deployment.json"
        rendered = PAYLOAD_ROOT / "rendered"
        cases.append(_case(CASES[0], lambda: _legacy_rejected_before_state(manifest)))
        cases.append(_case(CASES[1], lambda: _assert_rendered_units(rendered)))
        cases.append(_case(CASES[2], lambda: _managed_readiness(manifest)))
        cases.append(_case(CASES[3], _caddy_and_authelia))
        cases.append(_case(CASES[4], _ingress_checks))
        cases.append(_case(CASES[5], _socket_ownership))
        cases.append(_case(CASES[6], _private_denials))
        cases.append(_case(CASES[7], lambda: _restart_and_rollback(manifest)))
    except Exception:
        cases.extend({"name": name, "status": "failed"} for name in CASES[len(cases):])
    finally:
        if not _cleanup() and cases:
            cases[-1] = {"name": cases[-1]["name"], "status": "failed"}
        result = _write_result(cases)
    _write_serial_result(result)
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
