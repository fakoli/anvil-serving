from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from anvil_serving.connect import manage
from anvil_serving.connect.config import ManifestError, validate_manifest


pytestmark = pytest.mark.skipif(
    sys.platform != "linux",
    reason="requires Linux service ownership and filesystem custody controls",
)


ROOT = Path(__file__).parents[2]


def isolated_data(tmp_path: Path) -> dict:
    value = json.loads((ROOT / "connect/examples/deployment.json").read_text(encoding="utf-8"))
    value.pop("service_user")
    value["config_root"] = str(tmp_path / "rendered")
    value["gateway"]["state_directory"] = str(tmp_path / "gateway")
    value["authelia"]["state_directory"] = str(tmp_path / "idp")
    value["caddy"]["state_directory"] = str(tmp_path / "edge")
    value["connectors"][0]["state_directory"] = str(tmp_path / "connector")
    value["service_identities"] = {
        "gateway": {"uid": 1201, "gid": 2201},
        "edge": {"uid": 1202, "gid": 2202},
        "idp": {"uid": 1203, "gid": 2203},
        "connectors": {"dashboard": {"uid": 1204, "gid": 2204}},
        "clients": {"dashboard-api": {"uid": 1205, "gid": 2205}},
        "ingress": {"group_id": 2290, "directory": str(tmp_path / "ingress")},
    }
    value["service_limits"] = {
        "gateway": {"memory_max_bytes": 805306368, "tasks_max": 128},
        "edge": {"memory_max_bytes": 536870912, "tasks_max": 64},
        "idp": {"memory_max_bytes": 536870912, "tasks_max": 64},
        "connectors": {"dashboard": {"memory_max_bytes": 402653184, "tasks_max": 64}},
        "clients": {"dashboard-api": {"memory_max_bytes": 268435456, "tasks_max": 32}},
    }
    return validate_manifest(value)


def install_nss(monkeypatch: pytest.MonkeyPatch, data: dict) -> None:
    identities = {
        1201: (2201, "gateway"), 1202: (2202, "edge"), 1203: (2203, "idp"),
        1204: (2204, "connector"), 1205: (2205, "client"),
    }
    monkeypatch.setattr(manage.os, "geteuid", lambda: 0)
    monkeypatch.setattr(
        manage.pwd,
        "getpwuid",
        lambda uid: SimpleNamespace(pw_uid=uid, pw_gid=identities[uid][0], pw_name=identities[uid][1]),
    )
    monkeypatch.setattr(manage.grp, "getgrgid", lambda gid: SimpleNamespace(gr_gid=gid, gr_mem=[]))


def test_ingress_membership_allows_only_edge_account(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    data = isolated_data(tmp_path)
    install_nss(monkeypatch, data)
    groups = [
        SimpleNamespace(gr_gid=2201, gr_mem=[]), SimpleNamespace(gr_gid=2202, gr_mem=[]),
        SimpleNamespace(gr_gid=2203, gr_mem=[]), SimpleNamespace(gr_gid=2204, gr_mem=[]),
        SimpleNamespace(gr_gid=2205, gr_mem=[]), SimpleNamespace(gr_gid=2290, gr_mem=["edge"]),
    ]
    monkeypatch.setattr(manage.grp, "getgrall", lambda: groups)
    manage._validate_closed_memberships(data)
    groups[-1] = SimpleNamespace(gr_gid=2290, gr_mem=["edge", "connector"])
    with pytest.raises(manage.ManageError, match="ingress membership"):
        manage._validate_closed_memberships(data)


def test_legacy_operations_are_inspectable_but_not_activatable(tmp_path: Path) -> None:
    value = json.loads((ROOT / "connect/examples/deployment.json").read_text(encoding="utf-8"))
    value["config_root"] = str(tmp_path / "rendered")
    manifest = tmp_path / "legacy.json"
    manifest.write_text(json.dumps(value), encoding="utf-8")
    checked = manage.validate(manifest, manage.Target("gateway"))
    assert checked["migration_required"] is True
    assert checked["targets"] == ["gateway"]
    assert manage.status(manifest, manage.Target("gateway"))["plan"]["state"] == "absent"
    assert manage.down(manifest, manage.Target("gateway"))["plan"]["state"] == "absent"
    with pytest.raises(ManifestError, match="isolated service identities"):
        manage.render(manifest)
    with pytest.raises(ManifestError, match="isolated service identities"):
        manage.up(manifest, manage.Target("gateway"))
    with pytest.raises(ManifestError, match="isolated service identities"):
        manage.native_init(manifest, manage.Target("gateway"))


def test_selected_role_memberships_are_exact_and_nss_failures_close(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    data = isolated_data(tmp_path)
    install_nss(monkeypatch, data)
    memberships = {
        "gateway": [2201], "edge": [2202, 2290], "idp": [2203],
        "connector": [2204], "client": [2205],
    }
    monkeypatch.setattr(manage.os, "getgrouplist", lambda account, primary: memberships[account])
    manage._validate_target_memberships(data, (manage.Target("gateway"), manage.Target("connector", "dashboard"), manage.Target("client", "dashboard-api")))
    memberships["connector"] = [2204, 2290]
    with pytest.raises(manage.ManageError, match="membership is unsafe"):
        manage._validate_target_memberships(data, (manage.Target("connector", "dashboard"),))
    monkeypatch.setattr(manage.os, "getgrouplist", lambda *_: (_ for _ in ()).throw(OSError("nss unavailable")))
    with pytest.raises(manage.ManageError, match="membership is unavailable"):
        manage._validate_target_memberships(data, (manage.Target("client", "dashboard-api"),))


def test_gateway_direct_files_require_role_readability_and_safe_ancestry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    data = isolated_data(tmp_path)
    secrets = tmp_path / "secrets"
    tls = data["caddy"]["tls"]
    tls["certificate_file"] = str(secrets / "edge.pem")
    tls["key_file"] = str(secrets / "edge-key.pem")
    paths = [Path(tls["certificate_file"]), Path(tls["key_file"])]
    for name in (
        "users_file", "client_secret_file", "session_secret_file", "storage_encryption_key_file",
        "identity_validation_secret_file", "oidc_hmac_secret_file", "oidc_rsa_private_key_file",
    ):
        data["authelia"][name] = str(secrets / name)
        paths.append(Path(data["authelia"][name]))
    metadata: dict[Path, tuple[int, int, int]] = {}
    directories: set[Path] = set()
    for path in paths:
        for parent in path.parents:
            directories.add(parent)
            metadata[parent] = (0, 0, 0o755)
    metadata[Path(tls["certificate_file"])] = (0, 0, 0o644)
    metadata[Path(tls["key_file"])] = (1202, 2202, 0o600)
    metadata[paths[2]] = (1203, 2203, 0o600)
    for path in paths[3:]:
        metadata[path] = (0, 2203, 0o640)
    original = Path.lstat

    def lstat(path: Path):
        if path in metadata:
            uid, gid, mode = metadata[path]
            kind = stat.S_IFDIR if path in directories else stat.S_IFREG
            return os.stat_result((kind | mode, 0, 0, 1, uid, gid, 0, 0, 0, 0))
        return original(path)

    monkeypatch.setattr(Path, "lstat", lstat)
    manage._validate_gateway_files(data)
    metadata[paths[2]] = (0, 2203, 0o640)
    with pytest.raises(manage.ManageError, match="idp-owned with mode 0600"):
        manage._validate_gateway_files(data)
    metadata[paths[2]] = (1203, 2203, 0o600)
    metadata[Path(tls["key_file"])] = (0, 0, 0o600)
    with pytest.raises(manage.ManageError, match="service file is unsafe"):
        manage._validate_gateway_files(data)
    metadata[Path(tls["key_file"])] = (1202, 2202, 0o600)
    metadata[secrets] = (1202, 2202, 0o700)
    with pytest.raises(manage.ManageError, match="runtime directory is unsafe"):
        manage._validate_gateway_files(data)


@pytest.mark.parametrize("role", ["gateway", "connector"])
@pytest.mark.parametrize("fault", [None, "missing", "symlink", "directory", "fifo", "owner", "writable", "unreadable", "empty", "oversized"])
def test_local_material_metadata_matches_native_owner_contract(tmp_path, monkeypatch, role, fault):
    from tests.connect.test_render import local_tunnel_manifest

    data = local_tunnel_manifest(isolated_data(tmp_path))
    target = manage.Target(role, "dashboard" if role == "connector" else None)
    declaration = data["gateway"] if role == "gateway" else data["connectors"][0]
    paths = {Path(value) for key, value in declaration["local_tunnel"].items() if key.endswith("_file")}
    trust = Path(declaration["local_tunnel"]["trust_file"])
    key = Path(data["gateway"]["local_tunnel"]["private_key_file"])
    seen = set()

    def metadata(path):
        assert path in paths  # Connector validation must never inspect the leaf key.
        seen.add(path)
        uid, gid, mode, size = (1201, 2201, 0o600, 100) if path == key else (0, 0, 0o644, 100)
        kind = stat.S_IFREG
        if path == trust:
            if fault == "missing":
                raise FileNotFoundError()
            if fault in {"symlink", "directory", "fifo"}:
                kind = {"symlink": stat.S_IFLNK, "directory": stat.S_IFDIR, "fifo": stat.S_IFIFO}[fault]
            if fault == "owner":
                uid = 1202
            if fault == "writable":
                mode = 0o664
            if fault == "unreadable":
                mode = 0o600
            if fault == "empty":
                size = 0
            if fault == "oversized":
                size = 1024 * 1024 + 1
        return os.stat_result((kind | mode, 1, 1, 1, uid, gid, size, 0, 0, 0))

    monkeypatch.setattr(Path, "lstat", metadata)
    monkeypatch.setattr(manage, "_safe_root_ancestors", lambda *_: None)
    monkeypatch.setattr(Path, "open", lambda *_: pytest.fail("Python must not read local material"))
    if fault:
        with pytest.raises(manage.ManageError, match="local trust material"):
            manage._validate_local_files(data, target)
    else:
        manage._validate_local_files(data, target)
        assert seen == paths
