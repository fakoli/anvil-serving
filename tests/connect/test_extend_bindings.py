"""Independent binding tests for managed Connect resource extension.

These tests deliberately retain the public ``manage.admin`` and
``manage.identity`` boundaries.  Only the native executable is represented by
the runner, so they cover the file/identity handoff that an extension must get
right on a real managed host.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import stat
import sys

import pytest

from anvil_serving.connect import extend, manage


pytestmark = pytest.mark.skipif(
    sys.platform == "win32",
    reason="Connect extension file handoff uses POSIX no-follow descriptors",
)

_DIGEST = "a" * 64
_FINGERPRINT = "A" * 43


def _data(tmp_path: Path) -> dict[str, object]:
    root = tmp_path / "owned"
    root.mkdir()
    return {
        "config_root": str(root),
        "binary": "/usr/bin/managed-connect",
        "gateway": {"state_directory": str(tmp_path / "gateway-state")},
        "connectors": [{"id": "reports", "resources": [
            {"envelope": {"rule": {"id": "reports"}}},
        ]}],
    }


def _admin_seams(monkeypatch: pytest.MonkeyPatch, data: dict[str, object], current) -> manage.ServiceIdentity:
    """Keep the public admin wrapper while isolating unrelated ownership setup."""
    identity = manage.ServiceIdentity(os.geteuid(), os.getegid())
    monkeypatch.setattr(manage, "read_manifest", lambda _: data)
    monkeypatch.setattr(manage, "_current", current)
    monkeypatch.setattr(manage, "_native_verified", lambda _: _DIGEST)
    monkeypatch.setattr(manage, "_verified_binaries", lambda *_: {"native": _DIGEST})
    monkeypatch.setattr(manage, "_bound_active", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(manage, "_role_service_identity", lambda *_args: identity)
    return identity


def test_admin_exchange_never_follows_precreated_request_or_response_symlinks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bearer request/response paths stay bounded no-follow private files."""
    data = _data(tmp_path)
    _admin_seams(monkeypatch, data, lambda _data: None)
    monkeypatch.setattr(extend.secrets, "token_hex", lambda _: "nofollow")
    state, _ = extend._role_directory(data, "gateway")
    request = state / "request-nofollow.json"
    request_target = tmp_path / "request-target"
    request_target.write_bytes(b"unchanged-request-target")
    request.symlink_to(request_target)
    response_target = tmp_path / "response-target"
    response_target.write_bytes(b"unchanged-response-target")

    called = False

    def runner(argv: tuple[str, ...], _timeout: float, handed: manage.ServiceIdentity | None) -> manage.RunResult:
        nonlocal called
        called = True
        return manage.RunResult(0)

    with pytest.raises(extend.ExtendError, match="already exists"):
        extend._admin_exchange(
            data, tmp_path / "manifest.json",
            {"operation": "invite", "installation": "reports", "role": "connector", "resources": ["reports"], "lifetime_seconds": 60},
            output_name="invitation", runner=runner,
        )

    assert request_target.read_bytes() == b"unchanged-request-target"
    assert response_target.read_bytes() == b"unchanged-response-target"
    assert request.is_symlink() and not called


def test_invitation_handoff_replaces_a_symlink_without_touching_its_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The connector receives a regular private bundle under its own state root."""
    data = _data(tmp_path)
    connector_state = tmp_path / "connector-state"
    data["connectors"] = [{"id": "reports", "state_directory": str(connector_state)}]
    identity = manage.ServiceIdentity(os.geteuid(), os.getegid())
    monkeypatch.setattr(extend, "_role_directory", lambda *_args: (connector_state, identity))
    monkeypatch.setattr(extend, "_admin_exchange", lambda *_args, **_kwargs: {"invitation": "opaque"})
    monkeypatch.setattr(extend.secrets, "token_hex", lambda _: "bundle")
    connector_state.mkdir()
    bundle = connector_state / "invitation-bundle.json"
    target = tmp_path / "bundle-target"
    target.write_bytes(b"unchanged-bundle-target")
    bundle.symlink_to(target)

    with pytest.raises(extend.ExtendError, match="already exists"):
        extend._invite(data, tmp_path / "manifest.json", "reports", ["reports"], runner=lambda *_: None)

    assert target.read_bytes() == b"unchanged-bundle-target"
    bundle.unlink()
    result = extend._invite(data, tmp_path / "manifest.json", "reports", ["reports"], runner=lambda *_: None)

    info = result.lstat()
    assert result == bundle and stat.S_ISREG(info.st_mode) and not result.is_symlink()
    assert stat.S_IMODE(info.st_mode) == 0o600
    assert json.loads(result.read_text(encoding="utf-8")) == {"invitation": "opaque"}
    assert target.read_bytes() == b"unchanged-bundle-target"
