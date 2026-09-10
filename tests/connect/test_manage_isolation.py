from __future__ import annotations

import json
import os
import stat
from pathlib import Path
from types import SimpleNamespace

import pytest

from anvil_serving.connect import manage
from anvil_serving.connect.config import ManifestError, validate_manifest


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


def test_gateway_preflight_uses_three_distinct_role_identities(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    data = isolated_data(tmp_path)
    install_nss(monkeypatch, data)
    monkeypatch.setattr(manage, "_validate_isolated_runtime", lambda *_: None)
    monkeypatch.setattr(manage, "_validate_environment_files", lambda *_: None)
    monkeypatch.setattr(manage, "_verified_binaries", lambda *_: {"native": "a" * 64, "caddy": "b" * 64, "authelia": "c" * 64})
    monkeypatch.setattr(manage, "render_config", lambda *_: {"files": {}, "generation": "d" * 64})
    monkeypatch.setattr(manage, "_make_public", lambda *_: None)
    observed: list[tuple[str, manage.ServiceIdentity | None]] = []

    def runner(argv, timeout, identity):  # type: ignore[no-untyped-def]
        observed.append((argv[0], identity))
        return manage.RunResult(0)

    checked = manage._validate_data(data, manage.Target("gateway"), runner)
    assert checked["targets"] == ["gateway"]
    assert observed == [
        (data["binary"], manage.ServiceIdentity(1201, 2201)),
        (data["components"]["caddy"], manage.ServiceIdentity(1202, 2202)),
        (data["components"]["authelia"], manage.ServiceIdentity(1203, 2203)),
    ]


def test_environment_metadata_is_role_specific_and_root_identity_is_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    data = isolated_data(tmp_path)
    gateway = tmp_path / "gateway.env"
    identity = tmp_path / "gateway-identity.env"
    connector = tmp_path / "connector.env"
    client = tmp_path / "client.env"
    data["environment_files"] = {
        "gateway": str(gateway), "gateway_identity": str(identity),
        "connectors": {"dashboard": str(connector)}, "clients": {"dashboard-api": str(client)},
    }
    metadata = {
        gateway: (0, 0, 0o600), identity: (0, 0, 0o600),
        connector: (0, 2204, 0o640), client: (1205, 2205, 0o600),
    }
    original = Path.lstat

    def lstat(path: Path):
        if path in metadata:
            uid, gid, mode = metadata[path]
            return os.stat_result((stat.S_IFREG | mode, 0, 0, 1, uid, gid, 0, 0, 0, 0))
        return original(path)

    monkeypatch.setattr(Path, "lstat", lstat)
    monkeypatch.setattr(manage, "_safe_root_ancestors", lambda *_: None)
    manage._validate_environment_files(data, manage.Target("connector", "dashboard"))
    metadata[connector] = (0, 2203, 0o640)
    with pytest.raises(manage.ManageError, match="EnvironmentFile"):
        manage._validate_environment_files(data, manage.Target("connector", "dashboard"))
    metadata[connector] = (0, 2204, 0o640)
    metadata[identity] = (1201, 2201, 0o600)
    with pytest.raises(manage.ManageError, match="EnvironmentFile"):
        manage._validate_environment_files(data, manage.Target("gateway"))



def test_environment_file_rejects_real_hardlink_alias(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    data = isolated_data(tmp_path)
    gateway = tmp_path / "gateway.env"
    connector = tmp_path / "connector.env"
    gateway.write_bytes(b"DECLARED_ONLY=1\n")
    gateway.chmod(0o600)
    os.link(gateway, connector)
    assert gateway.lstat().st_ino == connector.lstat().st_ino
    assert gateway.lstat().st_nlink == 2
    data["environment_files"]["gateway"] = str(gateway)
    data["environment_files"]["connectors"]["dashboard"] = str(connector)
    monkeypatch.setattr(manage, "role_identity", lambda *_: (os.geteuid(), os.getegid()))
    monkeypatch.setattr(manage, "_safe_root_ancestors", lambda *_: None)
    with pytest.raises(manage.ManageError, match="EnvironmentFile"):
        manage._validate_environment_files(data, manage.Target("gateway"))

def test_runtime_metadata_requires_exact_private_and_ingress_modes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    data = isolated_data(tmp_path)
    leaf = Path(data["gateway"]["state_directory"])
    ingress = Path(data["service_identities"]["ingress"]["directory"])
    metadata: dict[Path, tuple[int, int, int]] = {}
    for path in [*leaf.parents, *ingress.parents]:
        metadata[path] = (0, 0, 0o755)
    metadata[leaf] = (1201, 2201, 0o700)
    metadata[ingress] = (1201, 2290, 0o2710)
    original = Path.lstat

    def lstat(path: Path):
        if path in metadata:
            uid, gid, mode = metadata[path]
            return os.stat_result((stat.S_IFDIR | mode, 0, 0, 1, uid, gid, 0, 0, 0, 0))
        return original(path)

    monkeypatch.setattr(Path, "lstat", lstat)
    manage._safe_private_runtime_directory(leaf, 1201, 2201)
    manage._safe_ingress_directory(data)
    metadata[ingress] = (1201, 2290, 0o2750)
    with pytest.raises(manage.ManageError, match="ingress directory"):
        manage._safe_ingress_directory(data)


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


def test_unproven_identity_migration_requires_all_targets(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    data = isolated_data(tmp_path)
    monkeypatch.setattr(manage, "_active_generation_isolated", lambda _: False)
    report = {"state": "update"}
    Path(data["config_root"]).mkdir()
    with pytest.raises(manage.ManageError, match="every declared target"):
        manage._require_complete_isolated_migration(data, (manage.Target("gateway"),), report)
    manage._require_complete_isolated_migration(data, manage._targets(data, None), report)


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



def test_current_role_execution_requires_exact_effective_groups(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    data = isolated_data(tmp_path)
    install_nss(monkeypatch, data)
    monkeypatch.setattr(manage.os, "geteuid", lambda: 1204)
    monkeypatch.setattr(manage.os, "getegid", lambda: 2204)
    monkeypatch.setattr(manage.os, "getgroups", lambda: [])
    assert manage._role_service_identity(data, "connector", "dashboard") is None
    monkeypatch.setattr(manage.os, "getgroups", lambda: [2290])
    with pytest.raises(manage.ManageError, match="current service credentials are unsafe"):
        manage._role_service_identity(data, "connector", "dashboard")
    monkeypatch.setattr(manage.os, "geteuid", lambda: 1202)
    monkeypatch.setattr(manage.os, "getegid", lambda: 2202)
    monkeypatch.setattr(manage.os, "getgroups", lambda: [2290])
    assert manage._role_service_identity(data, "edge") is None
    monkeypatch.setattr(manage.os, "getegid", lambda: 2201)
    with pytest.raises(manage.ManageError, match="current service credentials are unsafe"):
        manage._role_service_identity(data, "edge")

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
    for path in paths[2:]:
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
    metadata[Path(tls["key_file"])] = (0, 0, 0o600)
    with pytest.raises(manage.ManageError, match="service file is unsafe"):
        manage._validate_gateway_files(data)
    metadata[Path(tls["key_file"])] = (1202, 2202, 0o600)
    metadata[secrets] = (1202, 2202, 0o700)
    with pytest.raises(manage.ManageError, match="runtime directory is unsafe"):
        manage._validate_gateway_files(data)


def test_environment_file_rejects_role_owned_parent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    data = isolated_data(tmp_path)
    environment = tmp_path / "secrets" / "connector.env"
    data["environment_files"] = {
        "gateway": str(tmp_path / "gateway.env"), "connectors": {"dashboard": str(environment)},
        "clients": {"dashboard-api": str(tmp_path / "client.env")},
    }
    metadata: dict[Path, tuple[int, int, int]] = {}
    for parent in environment.parents:
        metadata[parent] = (0, 0, 0o755)
    metadata[environment.parent] = (1204, 2204, 0o700)
    metadata[environment] = (1204, 2204, 0o600)
    original = Path.lstat

    def lstat(path: Path):
        if path in metadata:
            uid, gid, mode = metadata[path]
            kind = stat.S_IFDIR if path != environment else stat.S_IFREG
            return os.stat_result((kind | mode, 0, 0, 1, uid, gid, 0, 0, 0, 0))
        return original(path)

    monkeypatch.setattr(Path, "lstat", lstat)
    with pytest.raises(manage.ManageError, match="runtime directory is unsafe"):
        manage._validate_environment_files(data, manage.Target("connector", "dashboard"))
