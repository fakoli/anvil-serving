import hashlib
import json
import os
import subprocess
import time
from pathlib import Path

import pytest

from anvil_serving import cli, init, mcp, operator_config


_PEM_PRIVATE_KEY_TOML = (
    'privateKey = "-----BEGIN PRIVATE ' + 'KEY-----\\nreusable-secret"\n'
)
_PGP_PRIVATE_KEY_TOML = (
    'content = "-----BEGIN PGP PRIVATE ' + 'KEY BLOCK-----reusable-secret"\n'
)
_SSH2_PRIVATE_KEY_TOML = (
    'content = "---- BEGIN SSH2 ENCRYPTED PRIVATE ' + 'KEY ----reusable-secret"\n'
)
_PUTTY_PRIVATE_KEY = "PuTTY-User-" + "Key-File-3: ssh-rsa\nreusable-secret"
_PUTTY_PRIVATE_KEY_TOML = (
    'content = "PuTTY-User-' + 'Key-File-3: ssh-rsa\\nreusable-secret"\n'
)


def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_services_manifest_is_versionable_and_exports_definition_dependencies(tmp_path):
    _write(
        tmp_path / "services.toml",
        """schema = "anvil-services/v1"

[[service]]
id = "events"
source_definition = "definitions/voice.toml"
definition = "definitions/host.toml"
definition_sha256 = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
""",
    )
    _write(tmp_path / "definitions" / "voice.toml", "schema_version = 1\n")
    _write(tmp_path / "definitions" / "host.toml", "schema_version = 1\n")

    inventory = operator_config.inventory(str(tmp_path))
    by_path = {row["path"]: row for row in inventory["files"]}
    assert by_path["services.toml"]["classification"] == "versionable"
    assert by_path["services.toml"]["dependencies"] == [
        "definitions/voice.toml",
        "definitions/host.toml",
    ]

    exported = operator_config.export(str(tmp_path), paths=["services.toml"])
    assert exported["selected_paths"] == ["services.toml"]
    assert [item["path"] for item in exported["files"]] == [
        "definitions/host.toml",
        "definitions/voice.toml",
        "services.toml",
    ]


def test_inventory_refuses_symlink(tmp_path):
    target = _write(tmp_path / "target.toml", "schema_version = 1\n")
    link = tmp_path / "linked.toml"
    try:
        os.symlink(target, link)
    except OSError:
        pytest.skip("symlink creation is unavailable")

    with pytest.raises(operator_config.ConfigExportError, match="symlink"):
        operator_config.inventory(str(tmp_path))


@pytest.mark.skipif(os.name != "nt", reason="Windows directory share semantics")
def test_windows_operator_home_lock_blocks_rename(tmp_path):
    home = tmp_path / "home"
    _write(home / "host.toml", "schema_version = 1\n")
    moved = tmp_path / "moved-home"

    with operator_config._windows_directory_lock(home):
        with pytest.raises(OSError):
            os.replace(home, moved)


def test_export_refuses_candidate_swapped_to_symlink_during_read(
    tmp_path, monkeypatch
):
    candidate = _write(tmp_path / "host.toml", "schema_version = 1\n")
    outside = _write(
        tmp_path.parent / f"{tmp_path.name}-outside.toml",
        'api_key = "must-not-export"\n',
    )
    prepared_link = tmp_path.parent / f"{tmp_path.name}-prepared-link.toml"
    try:
        os.symlink(outside, prepared_link)
    except OSError:
        pytest.skip("symlink creation is unavailable")
    swap_performed = False
    if os.name == "nt":
        real_descriptor = operator_config._windows_candidate_descriptor

        def swapping_descriptor(path, expected_identity):
            nonlocal swap_performed
            if Path(path) == candidate and not swap_performed:
                os.replace(prepared_link, candidate)
                swap_performed = True
            return real_descriptor(path, expected_identity)

        monkeypatch.setattr(
            operator_config, "_windows_candidate_descriptor", swapping_descriptor
        )
    else:
        real_open = os.open

        def swapping_open(path, flags, mode=0o777, *, dir_fd=None):
            nonlocal swap_performed
            candidate_open = (
                Path(path) == candidate if dir_fd is None else path == candidate.name
            )
            if candidate_open and not swap_performed:
                os.replace(prepared_link, candidate)
                swap_performed = True
            if dir_fd is None:
                return real_open(path, flags, mode)
            return real_open(path, flags, mode, dir_fd=dir_fd)

        monkeypatch.setattr(operator_config.os, "open", swapping_open)

    with pytest.raises(
        operator_config.ConfigExportError,
        match="symlink|symbolic links|changed during validation|enumeration|unreadable",
    ):
        operator_config.export(str(tmp_path))
    assert swap_performed


@pytest.mark.skipif(os.name != "nt", reason="Windows candidate identity integration")
@pytest.mark.parametrize("selected", [False, True])
def test_windows_export_binds_reads_to_enumerated_candidate_identity(
    tmp_path, monkeypatch, selected
):
    candidate = _write(tmp_path / "router.toml", '[router]\nname = "original"\n')
    parked = tmp_path.parent / f"{tmp_path.name}-parked.toml"
    replacement = _write(
        tmp_path.parent / f"{tmp_path.name}-replacement.toml",
        '[router]\nname = "transient"\n',
    )
    real_read = operator_config._read_bounded
    swapped = False

    def swapping_read(path, **kwargs):
        nonlocal swapped
        if Path(path) == candidate and not swapped:
            swapped = True
            os.replace(candidate, parked)
            os.replace(replacement, candidate)
            try:
                return real_read(path, **kwargs)
            finally:
                os.replace(candidate, replacement)
                os.replace(parked, candidate)
        return real_read(path, **kwargs)

    monkeypatch.setattr(operator_config, "_read_bounded", swapping_read)

    with pytest.raises(operator_config.ConfigExportError, match="enumeration"):
        operator_config.export(
            str(tmp_path), paths=["router.toml"] if selected else None
        )

    assert swapped
    assert "original" in candidate.read_text(encoding="utf-8")


@pytest.mark.skipif(os.name != "nt", reason="Windows ChangeTime integration")
@pytest.mark.parametrize("selected", [False, True])
def test_windows_export_rejects_same_inode_rewrite_with_restored_mtime(
    tmp_path, monkeypatch, selected
):
    candidate = _write(tmp_path / "router.toml", "value = 1\n")
    before = candidate.stat()
    real_descriptor = operator_config._windows_candidate_descriptor
    mutated = False

    def mutating_descriptor(path, expected_identity):
        nonlocal mutated
        if Path(path) == candidate and not mutated:
            # NTFS timestamps advance in coarse timer ticks (~15.6 ms). A
            # rewrite landing in the same tick as the original creation keeps
            # ChangeTime identical, so the same-size + restored-mtime rewrite
            # would be indistinguishable from the enumerated identity. Rewrite
            # until ChangeTime actually moves so the test always exercises the
            # detection path.
            deadline = time.monotonic() + 5.0
            while True:
                candidate.write_text("value = 2\n", encoding="utf-8")
                os.utime(candidate, ns=(before.st_atime_ns, before.st_mtime_ns))
                if (
                    operator_config._windows_file_identity(candidate)
                    != expected_identity
                ):
                    break
                if time.monotonic() > deadline:
                    pytest.fail("ChangeTime tick never advanced past enumeration")
                time.sleep(0.02)
            mutated = True
        return real_descriptor(path, expected_identity)

    monkeypatch.setattr(
        operator_config, "_windows_candidate_descriptor", mutating_descriptor
    )

    with pytest.raises(operator_config.ConfigExportError, match="enumeration"):
        operator_config.export(
            str(tmp_path), paths=["router.toml"] if selected else None
        )

    assert mutated


def test_export_refuses_gateway_symlink_before_resolution(tmp_path):
    _write(tmp_path / "host.toml", "schema_version = 1\n")
    target = _write(
        tmp_path.parent / "gateway-target" / "openclaw.json",
        json.dumps({"models": {"providers": {"anvil": {"baseUrl": "http://127.0.0.1:8000/v1"}}}}),
    )
    link = tmp_path.parent / f"{tmp_path.name}-gateway-link" / "openclaw.json"
    link.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.symlink(target, link)
    except OSError:
        pytest.skip("symlink creation is unavailable")

    with pytest.raises(operator_config.ConfigExportError, match="gateway.*symlink"):
        operator_config.export(str(tmp_path), gateway_path=str(link))


def test_export_refuses_gateway_with_symlinked_parent(tmp_path):
    _write(tmp_path / "host.toml", "schema_version = 1\n")
    target_parent = tmp_path.parent / f"{tmp_path.name}-gateway-parent-target"
    _write(
        target_parent / "openclaw.json",
        json.dumps(
            {"models": {"providers": {"anvil": {"baseUrl": "http://127.0.0.1:8000/v1"}}}}
        ),
    )
    linked_parent = tmp_path.parent / f"{tmp_path.name}-gateway-parent-link"
    try:
        os.symlink(target_parent, linked_parent, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlink creation is unavailable")

    with pytest.raises(operator_config.ConfigExportError, match="symlink or junction"):
        operator_config.export(
            str(tmp_path), gateway_path=str(linked_parent / "openclaw.json")
        )


@pytest.mark.skipif(os.name != "nt", reason="Windows junction integration")
def test_export_refuses_gateway_with_junction_parent_on_windows(tmp_path):
    home = tmp_path / "home"
    _write(home / "host.toml", "schema_version = 1\n")
    target_parent = tmp_path / "gateway-junction-target"
    _write(
        target_parent / "openclaw.json",
        json.dumps(
            {"models": {"providers": {"anvil": {"baseUrl": "http://127.0.0.1:8000/v1"}}}}
        ),
    )
    junction_parent = tmp_path / "gateway-junction"
    environment = {
        **os.environ,
        "ANVIL_TEST_JUNCTION": str(junction_parent),
        "ANVIL_TEST_TARGET": str(target_parent),
    }
    created = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            "$ErrorActionPreference='Stop'; "
            "New-Item -ItemType Junction -Path $env:ANVIL_TEST_JUNCTION "
            "-Target $env:ANVIL_TEST_TARGET | Out-Null",
        ],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    if created.returncode != 0:
        pytest.skip("junction creation is unavailable")
    try:
        with pytest.raises(operator_config.ConfigExportError, match="symlink or junction"):
            operator_config.export(
                str(home), gateway_path=str(junction_parent / "openclaw.json")
            )
    finally:
        junction_parent.rmdir()


@pytest.mark.skipif(os.name != "nt", reason="Windows junction integration")
def test_inventory_refuses_empty_directory_junction_on_windows(tmp_path):
    target = tmp_path.parent / f"{tmp_path.name}-empty-target"
    target.mkdir()
    junction = tmp_path / "linked-directory"
    environment = {
        **os.environ,
        "ANVIL_TEST_JUNCTION": str(junction),
        "ANVIL_TEST_TARGET": str(target),
    }
    created = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            "$ErrorActionPreference='Stop'; "
            "New-Item -ItemType Junction -Path $env:ANVIL_TEST_JUNCTION "
            "-Target $env:ANVIL_TEST_TARGET | Out-Null",
        ],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    if created.returncode != 0:
        pytest.skip("junction creation is unavailable")
    try:
        with pytest.raises(operator_config.ConfigExportError, match="symlink or junction"):
            operator_config.inventory(str(tmp_path))
    finally:
        junction.rmdir()


def test_inventory_records_external_product_registry_without_reading_it(tmp_path):
    product_registry = (
        Path(operator_config.__file__).resolve().parent.parent
        / "configs"
        / "deepseek-v4-flash-0731-nvfp4-dspark5-w4a16-128k-recipe.toml"
    )
    _write(
        tmp_path / "serves.toml",
        'up = "anvil-serving models recipes load --registry '
        f'{product_registry.as_posix()} candidate"\n',
    )

    result = operator_config.inventory(str(tmp_path))

    assert result["dependency_edges"] == [
        {
            "source": "serves.toml",
            "target": "<external-product-registry>",
            "scope": "external-product",
        }
    ]
    assert result["files"][0]["dependencies"] == []


@pytest.mark.skipif(os.name != "nt", reason="Windows lazy identity integration")
def test_selected_export_does_not_open_unselected_secret_identity(tmp_path, monkeypatch):
    _write(tmp_path / "router.toml", "[router]\n")
    _write(tmp_path / ".env", "TOKEN=private\n")
    real_identity = operator_config._windows_file_identity

    def guarded_identity(path):
        if Path(path).name == ".env":
            raise AssertionError("unselected secret identity must not be opened")
        return real_identity(path)

    monkeypatch.setattr(operator_config, "_windows_file_identity", guarded_identity)

    result = operator_config.export(str(tmp_path), paths=["router.toml"])

    assert [row["path"] for row in result["files"]] == ["router.toml"]


@pytest.mark.skipif(os.name != "nt", reason="Windows case-insensitive paths")
def test_inventory_preserves_canonical_dependency_spelling_on_windows(tmp_path):
    _write(tmp_path / "router.toml", "[router]\n")
    _write(tmp_path / "serves.toml", 'router_config = "ROUTER.TOML"\n')

    result = operator_config.inventory(str(tmp_path))

    assert result["dependency_edges"] == [
        {"source": "serves.toml", "target": "router.toml"}
    ]


@pytest.mark.skipif(os.name != "nt", reason="Windows case-sensitive directories")
def test_case_sensitive_windows_directory_preserves_exact_dependency(tmp_path):
    enabled = subprocess.run(
        ["fsutil.exe", "file", "SetCaseSensitiveInfo", str(tmp_path), "enable"],
        capture_output=True,
        text=True,
        check=False,
    )
    if enabled.returncode != 0:
        pytest.skip("per-directory case sensitivity is unavailable")
    _write(tmp_path / "router.toml", '[router]\nname = "lower"\n')
    _write(tmp_path / "ROUTER.TOML", '[router]\nname = "upper"\n')
    _write(tmp_path / "serves.toml", 'router_config = "ROUTER.TOML"\n')

    inventory = operator_config.inventory(str(tmp_path))
    exported = operator_config.export(str(tmp_path), paths=["serves.toml"])

    assert inventory["dependency_edges"] == [
        {"source": "serves.toml", "target": "ROUTER.TOML"}
    ]
    assert {row["path"] for row in exported["files"]} == {
        "ROUTER.TOML",
        "serves.toml",
    }

def test_export_returns_safe_config_and_only_sanitized_anvil_gateway_fragment(tmp_path):
    _write(tmp_path / "router.toml", "[router]\n")
    _write(
        tmp_path / "serves.toml",
        'router_config = "{dir}/router.toml"\nauth_env = "ANVIL_ROUTER_TOKEN"\n',
    )
    _write(tmp_path / ".env", "ANVIL_ROUTER_TOKEN=never-return-this\n")
    gateway = {
        "models": {
            "providers": {
                "anvil": {
                    "baseUrl": "http://127.0.0.1:8000/v1",
                    "apiKey": "raw-secret",
                    "models": [{"id": "llm.primary"}],
                },
                "unrelated": {"apiKey": "other-secret"},
            }
        },
        "agents": {
            "defaults": {
                "models": {"anvil/llm.primary": {}, "unrelated/model": {}}
            }
        },
        "talk": {
            "realtime": {
                "provider": "anvil",
                "providers": {
                    "anvil": {
                        "realtimeUrl": "ws://127.0.0.1:30110/v1/realtime",
                        "apiKey": {"source": "env", "provider": "default", "id": "VOICE_KEY"},
                    }
                },
            }
        },
        "mcpServers": {
            "anvil-serving": {
                "command": "anvil-serving",
                "args": ["mcp", "serve"],
                "env": {"ANVIL_CONTROLLER_TOKEN": "raw-controller-secret"},
            },
            "unrelated": {"command": "other"},
        },
        "unrelated": {"private": "must-not-return"},
    }
    gateway_path = _write(
        tmp_path.parent / f"{tmp_path.name}-gateway" / "openclaw.json",
        json.dumps(gateway),
    )

    result = operator_config.export(str(tmp_path), gateway_path=str(gateway_path))

    files = {row["path"]: row for row in result["files"]}
    assert set(files) == {"router.toml", "serves.toml"}
    assert files["serves.toml"]["content"].splitlines()[-1] == (
        'auth_env = "ANVIL_ROUTER_TOKEN"'
    )
    rendered = json.dumps(result, sort_keys=True)
    assert "never-return-this" not in rendered
    assert "raw-secret" not in rendered
    assert "other-secret" not in rendered
    assert "raw-controller-secret" not in rendered
    assert "must-not-return" not in rendered
    assert result["gateway_fragment"]["models"]["providers"].keys() == {"anvil"}
    assert result["gateway_fragment"]["agents"]["defaults"]["models"].keys() == {
        "anvil/llm.primary"
    }
    assert result["gateway_fragment"]["mcpServers"].keys() == {"anvil-serving"}
    assert result["redaction_count"] == 2


def test_gateway_sanitizer_closes_structural_credential_bypasses(tmp_path):
    marker = "reusable-secret-marker"
    _write(tmp_path / "host.toml", "schema_version = 1\n")
    gateway = {
        "models": {
            "providers": {
                "anvil": {
                    "baseUrl": "http://127.0.0.1:8000/v1",
                    "apiKey": {
                        "source": "env",
                        "provider": "default",
                        "id": marker,
                    },
                    "apiKeyEnv": f"literal-{marker}",
                    "accessTokenEnv": "ANVIL_ACCESS_TOKEN",
                    "accessToken": marker,
                    "headerPairs": [["Cookie", f"session={marker}"]],
                    "headersList": [["X-Auth-Token", marker]],
                    "defaultHeaders": ["Cookie", f"session={marker}"],
                    "customHeaderPairs": [["X-Auth-Token", marker]],
                    "relativeCallback": f"/cb?sig={marker}",
                    "oauthRedirect": f"?code={marker}",
                    "stateCallback": f"/cb?state={marker}",
                    "ticketCallback": f"?ticket={marker}",
                    "pathSession": f"/cb;session={marker}",
                    "encodedSignature": f"?%73ig={marker}",
                    "encodedToken": f"?access_%74oken={marker}",
                    "fullyEncodedRelative": f"%2Fcb%3Ftoken%3D{marker}",
                    "fullyEncodedAbsolute": (
                        f"https%3A%2F%2Fexample.invalid%2Fcb%3Ftoken%3D{marker}"
                    ),
                    "keyMaterial": _PUTTY_PRIVATE_KEY.replace(
                        "reusable-secret", marker
                    ),
                    "badUrl": "//[",
                    "headerObjects": [
                        {"Name": "Cookie", "Values": [f"session={marker}"]},
                        {"headerName": "Cookie", "headerValue": marker},
                        {
                            "name": "Cookie",
                            "value": {
                                "source": "env",
                                "provider": "default",
                                "id": "COOKIE_REF",
                            },
                            "values": [marker],
                        },
                        {"name": "Cookie", "Name": "Cookie", "value": marker},
                        {
                            "name": "Cookie",
                            "key": "X-Auth-Token",
                            "value": marker,
                        },
                        {
                            "name": "Cookie",
                            "value": {
                                "source": "env",
                                "provider": "default",
                                "id": "COOKIE_REF",
                            },
                            "headerName": "X-Auth-Token",
                            "headerValue": marker,
                        },
                    ],
                }
            }
        },
        "mcpServers": {
            "anvil-serving": {
                "command": "anvil-serving",
                "args": [
                    f"--callback=https://example.invalid/cb?token={marker}",
                    f"Authorization: Bearer {marker}",
                    f"--header=Cookie: session={marker}",
                    f"-HCookie: session={marker}",
                    f"X-Auth-Token: {marker}",
                    f"//user:{marker}@example.invalid/path",
                    f"--dsn=postgresql://user:{marker}@example.invalid/database",
                    f"/cb?token={marker}",
                    f"?token={marker}",
                    f"https:opaque?token={marker}",
                ],
                "headers": [["Authorization", f"Bearer {marker}"]],
                "headerObjects": [
                    {"name": "Cookie", "value": f"session={marker}"},
                    {"key": "X-Api-Key", "value": marker},
                    {"Name": "Cookie", "Value": f"session={marker}"},
                ],
            }
        },
    }
    gateway_path = _write(
        tmp_path.parent / f"{tmp_path.name}-gateway-structural" / "openclaw.json",
        json.dumps(gateway),
    )

    result = operator_config.export(str(tmp_path), gateway_path=str(gateway_path))

    rendered = json.dumps(result, sort_keys=True)
    assert marker not in rendered
    assert "mcpServers" not in result["gateway_fragment"]
    provider = result["gateway_fragment"]["models"]["providers"]["anvil"]
    assert provider["apiKeyEnv"] == "<redacted>"
    assert provider["accessTokenEnv"] == "ANVIL_ACCESS_TOKEN"
    assert result["redaction_count"] == 25


def test_export_refuses_secret_literal_in_versionable_config(tmp_path):
    _write(tmp_path / "voice.toml", 'api_key = "raw-secret"\n')
    with pytest.raises(operator_config.ConfigExportError, match="secret-like field"):
        operator_config.export(str(tmp_path))


@pytest.mark.parametrize(
    "content",
    [
        '[headers]\nAuthorization = "Bearer reusable-secret"\n',
        '[headers]\nCookie = "session=reusable-secret"\n',
        '[headers]\nproxy-authorization = "Basic reusable-secret"\n',
        '[headers]\nset-cookie = "session=reusable-secret"\n',
    ],
)
def test_export_refuses_http_credential_literals(tmp_path, content):
    _write(tmp_path / "router.toml", content)

    with pytest.raises(operator_config.ConfigExportError, match="secret-like field"):
        operator_config.export(str(tmp_path))


def test_export_accepts_http_credential_secret_reference(tmp_path):
    _write(
        tmp_path / "router.toml",
        "[headers.Authorization]\n"
        'source = "env"\n'
        'provider = "default"\n'
        'id = "ROUTER_AUTHORIZATION"\n',
    )

    result = operator_config.export(str(tmp_path))

    assert result["files"][0]["content"]


@pytest.mark.parametrize(
    "content",
    [
        'accessToken = "reusable-secret"\n',
        'headers = [["Authorization", "Bearer reusable-secret"]]\n',
        'headers = ["Cookie", "session=reusable-secret"]\n',
        'headerPairs = [["Cookie", "session=reusable-secret"]]\n',
        'headersList = [["X-Auth-Token", "reusable-secret"]]\n',
        'defaultHeaders = [["Cookie", "session=reusable-secret"]]\n',
        'customHeaderPairs = [["X-Auth-Token", "reusable-secret"]]\n',
        _PEM_PRIVATE_KEY_TOML,
        'args = ["--header=Authorization: Bearer reusable-secret"]\n',
        'args = ["Cookie: session=reusable-secret"]\n',
        'args = ["--cookie session=reusable-secret"]\n',
        'args = ["-HCookie: session=reusable-secret"]\n',
        'args = ["X-Auth-Token: reusable-secret"]\n',
        'args = ["--callback=https://example.invalid/cb?token=reusable-secret"]\n',
        'args = ["//user:reusable-secret@example.invalid/path"]\n',
        'args = ["/cb?token=reusable-secret"]\n',
        'args = ["?token=reusable-secret"]\n',
        'args = ["https:opaque?token=reusable-secret"]\n',
        'args = ["/cb?sig=reusable-secret"]\n',
        'args = ["?code=reusable-secret"]\n',
        'args = ["/cb?state=reusable-secret"]\n',
        'args = ["?ticket=reusable-secret"]\n',
        'args = ["/cb;session=reusable-secret"]\n',
        'args = ["?%73ig=reusable-secret"]\n',
        'args = ["?access_%74oken=reusable-secret"]\n',
        'args = ["%2Fcb%3Ftoken%3Dreusable-secret"]\n',
        'args = ["https%3A%2F%2Fexample.invalid%2Fcb%3Ftoken%3Dreusable-secret"]\n',
        'args = ["//["]\n',
        'headerObjects = [{name = "Cookie", value = "session=reusable-secret"}]\n',
        'headerObjects = [{key = "X-Api-Key", value = "reusable-secret"}]\n',
        'headerObjects = [{Name = "Cookie", Value = "session=reusable-secret"}]\n',
        'headerObjects = [{Name = "Cookie", Values = ["session=reusable-secret"]}]\n',
        'headerObjects = [{headerName = "Cookie", headerValue = "reusable-secret"}]\n',
        'headerObjects = [{name = "Cookie", value = {source = "env", provider = "default", id = "COOKIE_REF"}, values = ["reusable-secret"]}]\n',
        'headerObjects = [{name = "Cookie", Name = "Cookie", value = "reusable-secret"}]\n',
        'headerObjects = [{name = "Cookie", key = "X-Auth-Token", value = "reusable-secret"}]\n',
        'headerObjects = [{name = "Cookie", value = {source = "env", provider = "default", id = "COOKIE_REF"}, headerName = "X-Auth-Token", headerValue = "reusable-secret"}]\n',
        _PGP_PRIVATE_KEY_TOML,
        _SSH2_PRIVATE_KEY_TOML,
        _PUTTY_PRIVATE_KEY_TOML,
    ],
)
def test_export_refuses_alternate_credential_shapes(tmp_path, content):
    _write(tmp_path / "router.toml", content)

    with pytest.raises(operator_config.ConfigExportError):
        operator_config.export(str(tmp_path))


def test_export_refuses_invalid_secret_reference(tmp_path):
    _write(
        tmp_path / "router.toml",
        "[headers.Authorization]\n"
        'source = "env"\n'
        'provider = "default"\n'
        'id = "literal reusable secret"\n',
    )

    with pytest.raises(operator_config.ConfigExportError, match="invalid SecretRef"):
        operator_config.export(str(tmp_path))


def test_export_accepts_header_pair_secret_reference(tmp_path):
    _write(
        tmp_path / "router.toml",
        "headers = [[\"Authorization\", "
        '{source = "env", provider = "default", id = "ROUTER_AUTHORIZATION"}]]\n',
    )

    result = operator_config.export(str(tmp_path))

    assert result["files"][0]["content"]


@pytest.mark.parametrize("reference_id", ["X", "AB", "_TOKEN"])
def test_export_accepts_product_valid_env_secret_reference(tmp_path, reference_id):
    _write(
        tmp_path / "router.toml",
        "[headers.Authorization]\n"
        'source = "env"\n'
        'provider = "default"\n'
        f'id = "{reference_id}"\n',
    )

    result = operator_config.export(str(tmp_path))

    assert result["files"][0]["content"]


def test_export_accepts_bounded_file_secret_reference(tmp_path):
    _write(
        tmp_path / "router.toml",
        "[headers.Authorization]\n"
        'source = "file"\n'
        'provider = "default"\n'
        'id = "/gateway/authToken"\n',
    )

    result = operator_config.export(str(tmp_path))

    assert result["files"][0]["content"]


def test_export_refuses_secret_literals_in_env_example(tmp_path):
    _write(tmp_path / ".env.example", "API_TOKEN=raw-secret\n")
    with pytest.raises(operator_config.ConfigExportError, match="secret-like field"):
        operator_config.export(str(tmp_path))


@pytest.mark.parametrize(
    ("name", "content"),
    [
        ("router.toml", '[router]\n# Authorization: Bearer reusable-secret\n'),
        ("router.toml", '# password = "reusable-secret"\n'),
        ("router.toml", '# token template ${PASSWORD:-reusable-secret}\n'),
        (".env.example", "API_TOKEN=${MISSING:-reusable-secret}\n"),
    ],
)
def test_export_refuses_credentials_hidden_in_raw_text(tmp_path, name, content):
    _write(tmp_path / name, content)

    with pytest.raises(operator_config.ConfigExportError, match="credential|secret-like"):
        operator_config.export(str(tmp_path))


@pytest.mark.parametrize(
    "value",
    [
        "${PASSWORD:-synthetic-fallback}",
        "${TOKEN-synthetic-fallback}",
        "${API_KEY:=synthetic-fallback}",
        "${CREDENTIAL:-synthetic-fallback}",
    ],
)
def test_export_refuses_secret_named_shell_fallbacks(tmp_path, value):
    _write(tmp_path / "router.toml", f'description = "{value}"\n')

    with pytest.raises(operator_config.ConfigExportError, match="credential"):
        operator_config.export(str(tmp_path))


def test_gateway_sanitizer_redacts_secret_named_shell_fallback(tmp_path):
    _write(tmp_path / "host.toml", "schema_version = 1\n")
    gateway_path = tmp_path.parent / "openclaw.json"
    marker = "synthetic-gateway-fallback"
    _write(
        gateway_path,
        json.dumps(
            {
                "models": {
                    "providers": {
                        "anvil": {
                            "baseUrl": "http://127.0.0.1:8000/v1",
                            "description": "${CLIENT_SECRET:-%s}" % marker,
                        }
                    }
                }
            }
        ),
    )

    result = operator_config.export(str(tmp_path), gateway_path=str(gateway_path))

    assert marker not in json.dumps(result)
    assert result["redaction_count"] == 1


@pytest.mark.parametrize("path", ["docker-compose.yml", "../router.toml", ".env"])
def test_selected_export_refuses_unsupported_escaping_and_secret_paths(tmp_path, path):
    _write(tmp_path / "docker-compose.yml", "services: {}\n")
    _write(tmp_path / ".env", "TOKEN=secret\n")
    with pytest.raises(operator_config.ConfigExportError):
        operator_config.export(str(tmp_path), paths=[path])


def test_export_allows_non_secret_ssh_username_in_topology(tmp_path):
    _write(
        tmp_path / "operator-topology.toml",
        'endpoint = "ssh://operator@192.0.2.10"\n',
    )

    result = operator_config.export(
        str(tmp_path), paths=["operator-topology.toml"]
    )

    assert result["files"][0]["path"] == "operator-topology.toml"


def test_mcp_inventory_and_export_are_read_only_typed_tools(tmp_path, monkeypatch):
    _write(tmp_path / "host.toml", "schema_version = 1\n")
    _write(tmp_path / "docker-compose.yml", "services: {}\n")
    monkeypatch.setenv("ANVIL_SERVING_HOME", str(tmp_path))
    monkeypatch.setattr(operator_config, "default_gateway_path", lambda: None)

    inventory = mcp.call_tool("operator_config_inventory", {})
    exported = mcp.call_tool("operator_config_export", {"paths": ["host.toml"]})

    assert inventory["ok"] is True
    inventory_by_path = {row["path"]: row for row in inventory["data"]["files"]}
    assert inventory_by_path["host.toml"]["classification"] == "versionable"
    assert inventory_by_path["docker-compose.yml"]["classification"] == "unsupported"
    assert exported["ok"] is True
    assert exported["data"]["files"][0]["content"].splitlines() == [
        "schema_version = 1"
    ]


def test_local_cli_inventory_is_read_only_and_machine_parseable(tmp_path, capsys):
    _write(tmp_path / "operator-topology.toml", init.render_starter_topology())
    _write(tmp_path / "host.toml", "schema_version = 1\n")
    before = {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in tmp_path.iterdir()
    }

    rc = cli.main(
        [
            "host", "config", "inventory",
            "--home", str(tmp_path),
            "--topology", str(tmp_path / "operator-topology.toml"),
            "--transport", "local",
            "--json",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert payload["ok"] is True
    assert payload["command"].startswith("host config inventory ")
    assert json.loads(payload["data"])["schema"] == "operator-config-inventory/v1"
    after = {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in tmp_path.iterdir()
    }
    assert after == before
