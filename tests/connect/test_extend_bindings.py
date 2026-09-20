"""Independent binding tests for managed Connect resource extension.

These tests deliberately retain the public ``manage.admin`` and
``manage.identity`` boundaries.  Only the native executable is represented by
the runner, so they cover the file/identity handoff that an extension must get
right on a real managed host.
"""

from __future__ import annotations

from contextlib import contextmanager
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


def test_extension_publishes_new_activation_before_real_admin_handoff(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The first native admin call sees the new generation and gateway identity."""
    data = _data(tmp_path)
    root = Path(data["config_root"])
    record = {
        "schema": "anvil-connect.activation/v1",
        "generation": "b" * 64,
        "native_sha256": _DIGEST,
        "components": {"caddy": _DIGEST, "authelia": _DIGEST},
    }
    observed: list[Path] = []

    def current(_data: dict[str, object]) -> None:
        active = json.loads(manage._activation_record(root).read_text(encoding="utf-8"))
        assert active == record

    identity = _admin_seams(monkeypatch, data, current)
    monkeypatch.setattr(extend.secrets, "token_hex", lambda _: "binding")
    manage._write_activation_record(root, record)

    def runner(argv: tuple[str, ...], _timeout: float, handed: manage.ServiceIdentity | None) -> manage.RunResult:
        assert argv[:2] == ("/usr/bin/managed-connect", "admin")
        assert handed == identity
        request = Path(argv[argv.index("--request") + 1])
        info = request.lstat()
        assert stat.S_ISREG(info.st_mode) and not stat.S_ISLNK(info.st_mode)
        assert stat.S_IMODE(info.st_mode) == 0o600
        parent = request.parent.lstat()
        # The request remains manager-owned; the exact gateway group gets
        # traversal/read access only for the published handoff file.
        assert (parent.st_uid == os.geteuid() and parent.st_gid == identity.gid
                and stat.S_IMODE(parent.st_mode) == 0o730)
        assert json.loads(request.read_text(encoding="utf-8")) == {
            "installation": "reports", "operation": "installation-revoke",
        }
        observed.append(request)
        return manage.RunResult(0)

    result = extend._admin_exchange(
        data, tmp_path / "manifest.json",
        {"operation": "installation-revoke", "installation": "reports"},
        output_name=None, runner=runner,
    )

    assert result["action"] == "admin"
    assert observed and not observed[0].exists()


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


def test_admin_exchange_rejects_and_cleans_an_unsafe_native_response(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A native response cannot redirect a manager read through a symlink."""
    data = _data(tmp_path)
    identity = _admin_seams(monkeypatch, data, lambda _data: None)
    monkeypatch.setattr(extend.secrets, "token_hex", lambda _: "response")
    gateway_state = Path(data["gateway"]["state_directory"])
    gateway_state.mkdir(mode=0o700)
    gateway_state.chmod(0o700)
    response_target = tmp_path / "response-target"
    response_target.write_bytes(b"unchanged-response-target")

    def runner(argv: tuple[str, ...], _timeout: float, handed: manage.ServiceIdentity | None) -> manage.RunResult:
        assert handed == identity
        output = Path(argv[argv.index("--output") + 1])
        output.symlink_to(response_target)
        return manage.RunResult(0)

    with pytest.raises(extend.ExtendError, match="response is unsafe"):
        extend._admin_exchange(
            data, tmp_path / "manifest.json",
            {"operation": "invite", "installation": "reports", "role": "connector", "resources": ["reports"], "lifetime_seconds": 60},
            output_name="invitation", runner=runner,
        )

    state, _ = extend._role_directory(data, "gateway")
    assert response_target.read_bytes() == b"unchanged-response-target"
    assert not (state / "request-response.json").exists()
    assert not (gateway_state / "extend-response-response.json").exists()


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


def test_extension_requires_the_actual_nested_manage_identity_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    """A flattened compatibility fake cannot advance retained native init."""
    target = manage.Target("connector", "reports")
    nested = {"identity": {"fingerprint": _FINGERPRINT}}
    monkeypatch.setattr(manage, "identity", lambda *_args, **_kwargs: nested)
    assert extend._identity_fingerprint("manifest.json", target, runner=lambda *_: None) == _FINGERPRINT
    monkeypatch.setattr(manage, "identity", lambda *_args, **_kwargs: {"fingerprint": _FINGERPRINT})
    with pytest.raises(extend.ExtendError, match="did not report a fingerprint"):
        extend._identity_fingerprint("manifest.json", target, runner=lambda *_: None)


def test_manage_identity_emits_the_nested_shape_consumed_by_extension(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The extension consumes the public manager result, never a native flat fake."""
    data = _data(tmp_path)
    data["clients"] = []
    target = manage.Target("connector", "reports")
    identity = manage.ServiceIdentity(os.geteuid(), os.getegid())
    monkeypatch.setattr(manage, "read_manifest", lambda _: data)
    monkeypatch.setattr(manage, "_native_verified", lambda _: _DIGEST)
    monkeypatch.setattr(manage, "_target_identity", lambda *_args: identity)

    @contextmanager
    def declaration(*_args):
        yield tmp_path / "connector.json"

    monkeypatch.setattr(manage, "_temporary_declaration", declaration)
    native_identity = {
        "id": "reports", "status": "enrolled", "fingerprint": _FINGERPRINT,
        "epoch": _DIGEST, "generation": 1, "resources": ["reports"],
    }

    def runner(argv: tuple[str, ...], _timeout: float, handed: manage.ServiceIdentity | None) -> manage.RunResult:
        assert argv == ("/usr/bin/managed-connect", "identity", "--config", str(tmp_path / "connector.json"))
        assert handed == identity
        return manage.RunResult(0, json.dumps(native_identity).encode("utf-8"))

    observed = manage.identity(tmp_path / "manifest.json", target, runner=runner)
    assert observed == {
        "schema": "anvil-connect.manage/v1", "action": "identity", "target": "connector:reports",
        "applied": False, "native_sha256": _DIGEST, "identity": native_identity,
    }
