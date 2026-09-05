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


def test_inventory_classifies_files_without_returning_contents(tmp_path):
    _write(tmp_path / "operator-topology.toml", "schema_version = 1\n")
    _write(tmp_path / ".env", "TOKEN=do-not-return\n")
    _write(tmp_path / "controller-operations.sqlite3", "runtime\n")
    _write(tmp_path / "voice.toml.anvil.bak.1", "backup\n")
    _write(tmp_path / "cache" / "catalog.lock", "cache\n")
    _write(tmp_path / "notes.txt", "unknown\n")

    result = operator_config.inventory(str(tmp_path))

    by_path = {row["path"]: row for row in result["files"]}
    assert by_path["operator-topology.toml"]["classification"] == "versionable"
    assert by_path[".env"]["classification"] == "secret"
    assert by_path["controller-operations.sqlite3"]["classification"] == "runtime"
    assert by_path["voice.toml.anvil.bak.1"]["classification"] == "backup"
    assert by_path["cache/catalog.lock"]["classification"] == "cache"
    assert by_path["notes.txt"]["classification"] == "unknown"
    assert by_path["operator-topology.toml"]["sha256"] == hashlib.sha256(
        (tmp_path / "operator-topology.toml").read_bytes()
    ).hexdigest()
    assert by_path[".env"]["sha256"] is None
    assert by_path["notes.txt"]["sha256"] is None
    assert all("content" not in row for row in result["files"])
    assert result["effective_home"] == str(tmp_path.resolve())
    assert result["installed_revisions"]["anvil_serving"]


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


def test_inventory_refuses_operator_home_swapped_during_resolution(
    tmp_path, monkeypatch
):
    home = tmp_path / "home"
    _write(home / "host.toml", "schema_version = 1\n")
    outside = tmp_path / "outside-home"
    _write(outside / "host.toml", 'api_key = "must-not-export"\n')
    prepared_link = tmp_path / "prepared-home-link"
    try:
        os.symlink(outside, prepared_link, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlink creation is unavailable")
    original_home = tmp_path / "original-home"
    real_resolve = Path.resolve
    swap_performed = False

    def swapping_resolve(path, *args, **kwargs):
        nonlocal swap_performed
        if path == home and not swap_performed:
            os.replace(home, original_home)
            os.replace(prepared_link, home)
            swap_performed = True
        return real_resolve(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", swapping_resolve)

    with pytest.raises(
        operator_config.ConfigExportError,
        match="symlink or junction|changed during validation",
    ):
        operator_config.inventory(str(home))
    assert swap_performed


@pytest.mark.skipif(os.name != "nt", reason="Windows directory share semantics")
def test_windows_operator_home_lock_blocks_rename(tmp_path):
    home = tmp_path / "home"
    _write(home / "host.toml", "schema_version = 1\n")
    moved = tmp_path / "moved-home"

    with operator_config._windows_directory_lock(home):
        with pytest.raises(OSError):
            os.replace(home, moved)


@pytest.mark.skipif(os.name == "nt", reason="POSIX descriptor-relative traversal")
def test_posix_inventory_uses_anchored_root_during_aba_swap(tmp_path, monkeypatch):
    home = tmp_path / "home"
    original_content = "schema_version = 1\n"
    _write(home / "host.toml", original_content)
    replacement_home = tmp_path / "replacement-home"
    _write(replacement_home / "host.toml", 'api_key = "must-not-export"\n')
    original_home = tmp_path / "original-home"
    real_fwalk = os.fwalk
    swap_performed = False

    def swapping_fwalk(*args, **kwargs):
        nonlocal swap_performed
        os.replace(home, original_home)
        os.replace(replacement_home, home)
        swap_performed = True
        try:
            yield from real_fwalk(*args, **kwargs)
        finally:
            os.replace(home, replacement_home)
            os.replace(original_home, home)

    monkeypatch.setattr(operator_config.os, "fwalk", swapping_fwalk)

    result = operator_config.inventory(str(home))

    assert swap_performed
    assert result["files"][0]["sha256"] == hashlib.sha256(
        original_content.encode()
    ).hexdigest()


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


def test_export_refuses_cross_file_snapshot_mutation(tmp_path, monkeypatch):
    candidate = _write(tmp_path / "host.toml", "schema_version = 1\n")
    real_finalize = operator_config._finalize_inventory_rows
    snapshot_finalized = False

    def replacing_finalize(root, rows, contents, parsed_by_path):
        nonlocal snapshot_finalized
        result = real_finalize(root, rows, contents, parsed_by_path)
        if not snapshot_finalized:
            candidate.write_text("schema_version = 2\n", encoding="utf-8")
            snapshot_finalized = True
        return result

    monkeypatch.setattr(
        operator_config,
        "_finalize_inventory_rows",
        replacing_finalize,
    )

    with pytest.raises(operator_config.ConfigExportError, match="coherent snapshot"):
        operator_config.export(str(tmp_path))

    assert snapshot_finalized


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


def test_read_bounded_refuses_same_size_torn_snapshot(tmp_path, monkeypatch):
    candidate = tmp_path / "host.toml"
    original = b"A" * (128 * 1024)
    replacement = b"B" * len(original)
    candidate.write_bytes(original)
    initial = candidate.stat()
    real_read = os.read
    mutated = False

    def mutating_read(descriptor, size):
        nonlocal mutated
        chunk = real_read(descriptor, size)
        if chunk and not mutated:
            candidate.write_bytes(replacement)
            os.utime(candidate, ns=(initial.st_atime_ns, initial.st_mtime_ns))
            mutated = True
        return chunk

    monkeypatch.setattr(operator_config.os, "read", mutating_read)

    with pytest.raises(operator_config.ConfigExportError, match="changed while reading"):
        operator_config._read_bounded(candidate, max_bytes=len(original))
    assert mutated


def test_metadata_inventory_discards_captured_contents(tmp_path):
    _write(tmp_path / "host.toml", "schema_version = 1\n")
    _write(tmp_path / "notes.txt", "x" * 1024)

    report, contents = operator_config._inventory_with_anchor(
        str(tmp_path),
        max_bytes=2048,
        capture_contents=False,
    )

    assert len(report["files"]) == 2
    assert contents == {}


def test_inventory_enforces_aggregate_versionable_snapshot_limit(
    tmp_path, monkeypatch
):
    _write(tmp_path / "host.toml", "value = 1\n")
    _write(tmp_path / "router.toml", "value = 2\n")
    monkeypatch.setattr(operator_config, "MAX_EXPORT_SNAPSHOT_BYTES", 12)

    with pytest.raises(operator_config.ConfigExportError, match="aggregate snapshot"):
        operator_config.inventory(str(tmp_path))


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


def test_inventory_refuses_oversized_file(tmp_path):
    _write(tmp_path / "voice.toml", "x" * 32)
    with pytest.raises(operator_config.ConfigExportError, match="size limit"):
        operator_config.inventory(str(tmp_path), max_bytes=16)


def test_inventory_refuses_missing_and_outside_dependencies(tmp_path):
    _write(
        tmp_path / "serves.toml",
        'router_config = "{dir}/missing-router.toml"\n',
    )
    with pytest.raises(operator_config.ConfigExportError, match="unresolved dependency"):
        operator_config.inventory(str(tmp_path))

    outside = _write(tmp_path.parent / "outside-router.toml", "[router]\n")
    _write(tmp_path / "serves.toml", f'router_config = "{outside.as_posix()}"\n')
    with pytest.raises(operator_config.ConfigExportError, match="outside approved root"):
        operator_config.inventory(str(tmp_path))


def test_inventory_follows_compose_dependency_in_lifecycle_command(tmp_path):
    _write(
        tmp_path / "serves.toml",
        'up = "docker compose -f {dir}/voice-compose.yml up -d proxy"\n',
    )
    with pytest.raises(operator_config.ConfigExportError, match="unresolved dependency"):
        operator_config.inventory(str(tmp_path))

    _write(tmp_path / "voice-compose.yml", "services: {}\n")
    result = operator_config.inventory(str(tmp_path))
    assert result["dependency_edges"] == [
        {"source": "serves.toml", "target": "voice-compose.yml"}
    ]


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


def test_inventory_refuses_arbitrary_external_registry(tmp_path):
    marker = "private-operator-recipes"
    _write(
        tmp_path / "serves.toml",
        f'up = "command --registry C:/{marker}.toml"\n',
    )

    with pytest.raises(operator_config.ConfigExportError) as exc_info:
        operator_config.inventory(str(tmp_path))

    assert marker not in str(exc_info.value)


def test_inventory_refuses_missing_declared_product_registry(tmp_path):
    marker = "definitely-missing-product-recipe"
    missing = (
        Path(operator_config.__file__).resolve().parent.parent
        / "configs"
        / f"{marker}.toml"
    )
    _write(
        tmp_path / "serves.toml",
        f'up = "command --registry {missing.as_posix()}"\n',
    )

    with pytest.raises(operator_config.ConfigExportError) as exc_info:
        operator_config.export(str(tmp_path), paths=["serves.toml"])

    assert marker not in str(exc_info.value)


def test_inventory_covers_product_manifest_and_registry_dependencies(tmp_path):
    _write(tmp_path / "serves.voice.toml", "[serves]\n")
    _write(tmp_path / "serves.fallback.toml", "[serves]\n")
    _write(tmp_path / "serve-recipes.toml", "[recipes]\n")
    _write(
        tmp_path / "voice.toml",
        'manifest_path = "serves.voice.toml"\n'
        'serves_manifest = "serves.fallback.toml"\n'
        'up = "anvil-serving models recipes load --registry '
        '{dir}/serve-recipes.toml candidate"\n',
    )

    result = operator_config.inventory(str(tmp_path))

    assert result["dependency_edges"] == [
        {"source": "voice.toml", "target": "serves.voice.toml"},
        {"source": "voice.toml", "target": "serves.fallback.toml"},
        {"source": "voice.toml", "target": "serve-recipes.toml"},
    ]


@pytest.mark.parametrize(
    "flag",
    ["--registry={dir}/serve-recipes.toml", "--file={dir}/serve-recipes.toml"],
)
def test_inventory_covers_inline_lifecycle_dependency_flags(tmp_path, flag):
    _write(tmp_path / "serve-recipes.toml", "[recipes]\n")
    _write(tmp_path / "serves.toml", f'up = "command {flag} candidate"\n')

    result = operator_config.export(str(tmp_path), paths=["serves.toml"])

    assert [row["path"] for row in result["files"]] == [
        "serve-recipes.toml",
        "serves.toml",
    ]


def test_selected_export_ignores_unrelated_invalid_dependency(tmp_path):
    _write(tmp_path / "router.toml", "[router]\n")
    _write(tmp_path / "serves.toml", 'router_config = "missing.toml"\n')

    result = operator_config.export(str(tmp_path), paths=["router.toml"])

    assert [row["path"] for row in result["files"]] == ["router.toml"]


def test_selected_export_reads_only_selected_dependency_closure(tmp_path, monkeypatch):
    _write(tmp_path / "router.toml", "[router]\n")
    for index in range(32):
        _write(tmp_path / f"unrelated-{index}.txt", "x" * 4096)
    reads: list[str] = []

    if os.name == "nt":
        real_read = operator_config._read_bounded

        def recording_read(path, **kwargs):
            reads.append(Path(path).name)
            return real_read(path, **kwargs)

        monkeypatch.setattr(operator_config, "_read_bounded", recording_read)
    else:
        real_read = operator_config._read_relative_posix

        def recording_read(_descriptor, relative, **kwargs):
            reads.append(Path(relative).name)
            return real_read(_descriptor, relative, **kwargs)

        monkeypatch.setattr(operator_config, "_read_relative_posix", recording_read)

    result = operator_config.export(str(tmp_path), paths=["router.toml"])

    assert [row["path"] for row in result["files"]] == ["router.toml"]
    assert reads == ["router.toml", "router.toml"]


def test_selected_export_tolerates_unrelated_runtime_mutation(tmp_path, monkeypatch):
    _write(tmp_path / "router.toml", "[router]\n")
    runtime = _write(tmp_path / "controller.log", "before\n")
    real_enumerate = operator_config._enumerate_anchored
    calls = 0

    def mutating_enumerate(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            runtime.write_text("after\n", encoding="utf-8")
        return real_enumerate(*args, **kwargs)

    monkeypatch.setattr(operator_config, "_enumerate_anchored", mutating_enumerate)

    result = operator_config.export(str(tmp_path), paths=["router.toml"])

    assert [row["path"] for row in result["files"]] == ["router.toml"]


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


def test_inventory_tolerates_unrelated_runtime_mutation(tmp_path, monkeypatch):
    _write(tmp_path / "router.toml", "[router]\n")
    runtime = _write(tmp_path / "controller.log", "before\n")
    function_name = "_inventory_rows" if os.name == "nt" else "_inventory_rows_posix"
    real_capture = getattr(operator_config, function_name)
    calls = 0

    def mutating_capture(*args, **kwargs):
        nonlocal calls
        result = real_capture(*args, **kwargs)
        calls += 1
        if calls == 1:
            runtime.write_text("after\n", encoding="utf-8")
        return result

    monkeypatch.setattr(operator_config, function_name, mutating_capture)

    result = operator_config.inventory(str(tmp_path))

    assert {row["path"] for row in result["files"]} == {
        "controller.log",
        "router.toml",
    }


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


def test_export_preserves_benign_pairs_and_descriptions(tmp_path):
    _write(
        tmp_path / "router.toml",
        'description = "Supports basic authentication mode; Cookie policy is disabled"\n'
        'dimensions = [["token", "count"]]\n'
        'tokenizer = "Qwen/Qwen3"\n'
        'tokenizer_id = "cl100k_base"\n'
        'keyboard = "us"\n'
        'monkey = "banana"\n'
        'auth_mode = "auth=none"\n'
        'session_mode = "session=default"\n'
        'code_mode = "code=python"\n',
    )

    result = operator_config.export(str(tmp_path))

    assert "Supports basic authentication mode" in result["files"][0]["content"]
    assert 'dimensions = [["token", "count"]]' in result["files"][0]["content"]


def test_capability_url_detection_is_bounded_for_large_benign_values():
    benign = "x" * 65_536

    started = time.perf_counter()
    assert operator_config._is_capability_url(benign) is False
    assert operator_config._is_capability_url(benign + "/") is False

    assert time.perf_counter() - started < 2.0


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


def test_cookie_and_arbitrary_json_are_not_versionable(tmp_path):
    marker = "reusable-secret-cookie"
    _write(
        tmp_path / "cookies.json",
        json.dumps([{"name": "session", "value": marker}]),
    )
    _write(tmp_path / "arbitrary.json", json.dumps({"value": marker}))

    inventory = operator_config.inventory(str(tmp_path))
    by_path = {row["path"]: row for row in inventory["files"]}
    assert by_path["cookies.json"]["classification"] == "secret"
    assert by_path["arbitrary.json"]["classification"] == "unknown"

    result = operator_config.export(str(tmp_path))
    assert marker not in json.dumps(result)


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


def test_inventory_enforces_file_count_limit(tmp_path):
    _write(tmp_path / "router.toml", "[router]\n")
    _write(tmp_path / "host.toml", "[host]\n")

    with pytest.raises(operator_config.ConfigExportError, match="inventory limit"):
        operator_config.inventory(str(tmp_path), max_files=1)


def test_inventory_entry_limit_counts_empty_directories(tmp_path):
    (tmp_path / "one").mkdir()
    (tmp_path / "two").mkdir()

    with pytest.raises(operator_config.ConfigExportError, match="entry inventory limit"):
        operator_config.inventory(str(tmp_path), max_files=1)


def test_inventory_dependency_error_does_not_echo_private_value(tmp_path):
    marker = "private-token-marker"
    _write(tmp_path / "serves.toml", f'router_config = "C:/{marker}/router.toml"\n')

    with pytest.raises(operator_config.ConfigExportError) as exc_info:
        operator_config.inventory(str(tmp_path))

    assert marker not in str(exc_info.value)


@pytest.mark.parametrize(
    "content",
    [
        "broken: [\n",
        "router_config: router.toml\n",
    ],
)
def test_inventory_marks_yaml_unsupported_and_export_fails_closed(tmp_path, content):
    _write(tmp_path / "config.yaml", content)
    _write(tmp_path / "router.toml", "[router]\n")

    result = operator_config.inventory(str(tmp_path))
    row = next(row for row in result["files"] if row["path"] == "config.yaml")
    assert row["classification"] == "unsupported"
    assert row["parser"] == "yaml"
    assert row["dependencies"] == []

    with pytest.raises(operator_config.ConfigExportError, match="does not support YAML"):
        operator_config.export(str(tmp_path))


def test_selected_export_ignores_unselected_yaml_and_closes_dependencies(tmp_path):
    _write(tmp_path / "docker-compose.yml", "services: {}\n")
    _write(tmp_path / "router.toml", "[router]\n")
    _write(tmp_path / "serves.toml", 'router_config = "router.toml"\n')

    result = operator_config.export(str(tmp_path), paths=["serves.toml"])

    assert result["selected_paths"] == ["serves.toml"]
    assert result["dependency_complete"] is True
    assert [row["path"] for row in result["files"]] == [
        "router.toml",
        "serves.toml",
    ]
    assert result["excluded_counts"]["unsupported"] == 1


@pytest.mark.parametrize("path", ["docker-compose.yml", "../router.toml", ".env"])
def test_selected_export_refuses_unsupported_escaping_and_secret_paths(tmp_path, path):
    _write(tmp_path / "docker-compose.yml", "services: {}\n")
    _write(tmp_path / ".env", "TOKEN=secret\n")
    with pytest.raises(operator_config.ConfigExportError):
        operator_config.export(str(tmp_path), paths=[path])


def test_full_openclaw_document_inside_home_is_never_exported(tmp_path):
    _write(tmp_path / "openclaw.json", json.dumps({"unrelated": {"private": "value"}}))
    result = operator_config.export(str(tmp_path))
    assert result["files"] == []
    assert result["excluded_counts"]["secret"] == 1


def test_export_refuses_capability_bearing_url_in_versionable_config(tmp_path):
    _write(
        tmp_path / "voice.toml",
        'endpoint = "https://example.invalid/path?token=hidden"\n',
    )
    with pytest.raises(operator_config.ConfigExportError, match="capability-bearing URL"):
        operator_config.export(str(tmp_path))


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


def test_mcp_refuses_remote_filesystem_root_overrides():
    for tool, argument in (
        ("operator_config_inventory", {"home": "C:/other"}),
        ("operator_config_export", {"gateway_path": "C:/other/openclaw.json"}),
    ):
        result = mcp.call_tool(tool, argument)
        assert result["ok"] is False
        assert result["error"]["code"] == "bad_argument"


def test_mcp_operator_export_bounds_serialized_remote_result(monkeypatch):
    monkeypatch.setattr(operator_config, "default_gateway_path", lambda: None)
    monkeypatch.setattr(
        operator_config,
        "export",
        lambda **_kwargs: {"content": "x" * (257 * 1024)},
    )

    result = mcp.call_tool("operator_config_export", {"paths": ["host.toml"]})

    assert result["ok"] is False
    assert result["error"]["code"] == "result_too_large"


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
