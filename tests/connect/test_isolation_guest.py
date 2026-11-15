"""Pure contract tests for the offline root-only isolation guest fixture."""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import sys

import pytest


pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="requires Linux isolation guest controls")


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
